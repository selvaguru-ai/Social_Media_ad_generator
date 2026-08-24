"""Read/write access to the pipeline's on-disk artifacts.

The pipeline persists everything the dashboard needs as JSON:

    data/pipeline_state.json        live (resumable) run context
    data/report_<ts>.json           completed run snapshots
    data/approvals.json             approval-gate decisions (owned by this layer)

Nothing here re-runs an agent or recomputes a score. Numbers shown in the
dashboard are the numbers the pipeline wrote.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils import config, settings
from utils.config import DATA_DIR, LOGS_DIR

REPORT_GLOB = "report_*.json"
APPROVALS_FILE = "approvals.json"
PAGE_ID_CACHE_FILE = "page_id_cache.json"
CURRENT_RUN_ID = "current"
VERIFY_STATUSES = {"quiet", "has_ads", "skip", "pending"}

# The agents treat these literal placeholder values as "not configured" and fall
# back to mock data. Mirror that logic so the dashboard reports the same truth.
_PLACEHOLDER_RE = re.compile(r"^your_.*_here$|^your_[a-z_]+$|example\.com$", re.IGNORECASE)

# Stage order matches PipelineOrchestrator.run_pipeline.
STAGES: Tuple[Dict[str, str], ...] = (
    {
        "key": "ad_discovery",
        "label": "Ad discovery",
        "agent": "AdDiscoveryAgent",
        "produces": "active_ads",
        "summary": "Finds what is already running in the niche.",
    },
    {
        "key": "prospect",
        "label": "Prospect",
        "agent": "ProspectAgent",
        "produces": "leads",
        "summary": "Scores brands with little or no ad footprint.",
    },
    {
        "key": "contact_enrichment",
        "label": "Contact enrichment",
        "agent": "ContactEnrichmentAgent",
        "produces": "enriched_leads",
        "summary": "Attaches a named contact to each lead.",
    },
    {
        "key": "creative",
        "label": "Creative",
        "agent": "CreativeAgent",
        "produces": "leads_with_videos",
        "summary": "Generates one sample video per contactable lead.",
    },
    {
        "key": "outreach",
        "label": "Outreach",
        "agent": "OutreachAgent",
        "produces": "drafted_messages",
        "summary": "Drafts the pitch. Sending stays behind the approval gate.",
    },
)


def _is_configured(value: Optional[Any]) -> bool:
    """True when an env value looks like a real credential, not a placeholder."""
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    return not _PLACEHOLDER_RE.search(text)


def slugify(value: Any, fallback: str = "item") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return text or fallback


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _as_list(context: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    value = context.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


class PipelineStore:
    """Reads pipeline artifacts; owns the approval-decision file."""

    def __init__(self, data_dir: Path = DATA_DIR, logs_dir: Path = LOGS_DIR):
        self.data_dir = Path(data_dir)
        self.logs_dir = Path(logs_dir)
        self.state_path = Path(config.orchestrator.state_file)
        if not self.state_path.is_absolute():
            self.state_path = self.data_dir.parent / self.state_path
        self.approvals_path = self.data_dir / APPROVALS_FILE
        self._write_lock = threading.Lock()

    # ---------------------------------------------------------------- sources

    def state(self) -> Optional[Dict[str, Any]]:
        return _read_json(self.state_path)

    def report_paths(self) -> List[Path]:
        if not self.data_dir.exists():
            return []
        return sorted(self.data_dir.glob(REPORT_GLOB), key=_mtime, reverse=True)

    def report(self, run_id: str) -> Optional[Dict[str, Any]]:
        path = self.data_dir / f"{run_id}.json"
        if path.parent != self.data_dir or not path.exists():
            return None
        return _read_json(path)

    def snapshot(self, run_id: Optional[str] = None) -> Tuple[str, Optional[Dict[str, Any]]]:
        """Resolve a run id to its context.

        With no run id, prefers whichever of the live state file / newest report
        was written last, so a run in progress is not masked by an older report.
        """
        if run_id and run_id != CURRENT_RUN_ID:
            return run_id, self.report(run_id)
        if run_id == CURRENT_RUN_ID:
            return CURRENT_RUN_ID, self.state()

        reports = self.report_paths()
        newest_report = reports[0] if reports else None
        if newest_report and _mtime(newest_report) >= _mtime(self.state_path):
            return newest_report.stem, _read_json(newest_report)
        state = self.state()
        if state is not None:
            return CURRENT_RUN_ID, state
        if newest_report:
            return newest_report.stem, _read_json(newest_report)
        return CURRENT_RUN_ID, None

    # ------------------------------------------------------------------ leads

    @staticmethod
    def merge_leads(context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """One lead list carrying score, contact and video.

        The agents mutate lead dicts in place, so `leads` usually already holds
        the contact and video. On a partially-resumed run it may not, so the
        later stages are overlaid by brand name.
        """
        merged: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []

        for key in ("prospect_leads", "leads", "enriched_leads", "leads_with_videos"):
            for lead in _as_list(context, key):
                name = lead.get("brand_name")
                if not name:
                    continue
                if name not in merged:
                    merged[name] = dict(lead)
                    order.append(name)
                    continue
                target = merged[name]
                for field, value in lead.items():
                    if value is not None or field not in target:
                        target[field] = value

        return [merged[name] for name in order]

    def lead_views(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        leads = self.merge_leads(context)
        for index, lead in enumerate(leads):
            lead["id"] = slugify(lead.get("brand_name"), f"lead-{index}")
            lead["has_contact"] = bool(lead.get("contact"))
            lead["has_video"] = bool(lead.get("video"))
            verification = lead.get("verification") if isinstance(lead.get("verification"), dict) else {}
            status = verification.get("status") or "pending"
            lead["verification"] = {
                "status": status,
                "ads_count_ui": verification.get("ads_count_ui"),
                "decided_at": verification.get("decided_at"),
                "note": verification.get("note") or "",
            }
            lead["company"] = lead.get("company") if isinstance(lead.get("company"), dict) else {}
            if lead["has_contact"] and lead["has_video"]:
                lead["stage"] = "pitch-ready"
            elif lead["has_contact"]:
                lead["stage"] = "contacted"
            elif status == "quiet":
                lead["stage"] = "quiet"
            elif status in {"has_ads", "skip"}:
                lead["stage"] = "rejected"
            else:
                lead["stage"] = "scored"
            lead["can_continue"] = status == "quiet"
        return leads

    def record_verification(
        self,
        lead_id: str,
        status: str,
        ads_count_ui: Optional[int] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        """Mark a scored lead Quiet / Has ads / Skip on the live state file."""
        if status not in VERIFY_STATUSES:
            raise ValueError(f"unsupported verification status: {status}")

        with self._write_lock:
            state = self.state()
            if not state:
                raise FileNotFoundError("No live pipeline state to verify against.")

            decision = {
                "status": status,
                "ads_count_ui": ads_count_ui,
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "note": note,
            }
            matched = False
            for key in ("prospect_leads", "leads"):
                for lead in _as_list(state, key):
                    if slugify(lead.get("brand_name")) == lead_id:
                        lead["verification"] = decision
                        matched = True
            if not matched:
                raise KeyError(lead_id)

            if status == "has_ads" and ads_count_ui is not None:
                self._write_ads_floor(state, lead_id, ads_count_ui)

            if state.get("status") == "running" and "contact_enrichment" not in set(
                state.get("completed_stages") or []
            ):
                state["status"] = "awaiting_verification"

            tmp = self.state_path.with_suffix(".json.tmp")
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
            tmp.replace(self.state_path)

        return decision

    def prepare_continue(self) -> Dict[str, Any]:
        """Filter live state to Quiet leads so a resume spends credits only on them."""
        with self._write_lock:
            state = self.state()
            if not state:
                raise FileNotFoundError("No live pipeline state to continue.")

            pool = _as_list(state, "prospect_leads") or _as_list(state, "leads")
            if not state.get("prospect_leads"):
                state["prospect_leads"] = [dict(lead) for lead in pool]

            quiet = [
                lead
                for lead in pool
                if (lead.get("verification") or {}).get("status") == "quiet"
            ]
            if not quiet:
                raise ValueError(
                    "Mark at least one lead Quiet after opening its Ad Library page."
                )

            # Later stages must not re-process the rejected set.
            state["leads"] = quiet
            state["status"] = "running"
            for stage in ("contact_enrichment", "creative", "outreach"):
                completed = state.get("completed_stages") or []
                if stage in completed:
                    state["completed_stages"] = [item for item in completed if item != stage]

            tmp = self.state_path.with_suffix(".json.tmp")
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
            tmp.replace(self.state_path)

        return {
            "quiet_count": len(quiet),
            "brand_names": [lead.get("brand_name") for lead in quiet],
        }

    def _write_ads_floor(self, state: Dict[str, Any], lead_id: str, ads_count: int) -> None:
        """Remember a UI ad count so the next Prospect run does not false-zero it."""
        page_id = None
        brand_name = None
        for lead in _as_list(state, "prospect_leads") + _as_list(state, "leads"):
            if slugify(lead.get("brand_name")) == lead_id:
                brand_name = lead.get("brand_name")
                page_id = (lead.get("ad_presence_summary") or {}).get("page_id")
                break
        if not brand_name or not page_id:
            return

        cache_path = self.data_dir / PAGE_ID_CACHE_FILE
        cache = _read_json(cache_path) or {}
        cache[brand_name] = {"page_id": str(page_id), "ads_count": int(ads_count)}
        tmp = cache_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=2)
        tmp.replace(cache_path)

    # --------------------------------------------------------------- messages

    def message_views(self, run_id: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Drafted messages joined with their approval decision and lead score."""
        decisions = self.decisions(run_id)
        leads = {lead.get("brand_name"): lead for lead in self.merge_leads(context)}
        sent_emails = {
            message.get("to_email")
            for message in _as_list(context, "sent_messages")
            if message.get("to_email")
        }

        seen: Dict[str, int] = {}
        messages: List[Dict[str, Any]] = []

        for index, message in enumerate(_as_list(context, "drafted_messages")):
            base = slugify(message.get("to_email"), f"message-{index}")
            seen[base] = seen.get(base, 0) + 1
            message_id = base if seen[base] == 1 else f"{base}-{seen[base]}"

            lead = leads.get(message.get("lead_id")) or {}
            decision = decisions.get(message_id) or {}
            sent = message.get("sent") is True or message.get("to_email") in sent_emails

            # A decision recorded here wins over the draft's own flag: the CLI
            # gate writes `approved` at draft time, the dashboard writes later.
            status = decision.get("status")
            if not status:
                status = "approved" if message.get("approved") else "pending"

            messages.append(
                {
                    "id": message_id,
                    "lead_id": message.get("lead_id"),
                    "to_name": message.get("to_name"),
                    "to_email": message.get("to_email"),
                    "subject": message.get("subject"),
                    "body": message.get("body"),
                    "video_url": message.get("video_url"),
                    "region": message.get("region"),
                    "status": "sent" if sent else status,
                    "decided_at": decision.get("decided_at"),
                    "note": decision.get("note") or "",
                    "qualification_score": lead.get("qualification_score"),
                    "contact_title": (lead.get("contact") or {}).get("title"),
                    "contact_confidence": (lead.get("contact") or {}).get("confidence"),
                }
            )

        return messages

    # -------------------------------------------------------------- decisions

    def _approvals_document(self) -> Dict[str, Any]:
        document = _read_json(self.approvals_path) or {}
        runs = document.get("runs")
        return {"runs": runs if isinstance(runs, dict) else {}}

    def decisions(self, run_id: str) -> Dict[str, Dict[str, Any]]:
        run = self._approvals_document()["runs"].get(run_id)
        return run if isinstance(run, dict) else {}

    def record_decision(
        self,
        run_id: str,
        message_id: str,
        status: str,
        note: str = "",
        decided_by: str = "dashboard",
    ) -> Dict[str, Any]:
        """Persist one approve/reject/reset decision.

        Also mirrors the decision into the live pipeline state so the CLI and a
        resumed orchestrator run see the same approval set.
        """
        if status not in {"approved", "rejected", "pending"}:
            raise ValueError(f"unsupported status: {status}")

        with self._write_lock:
            document = self._approvals_document()
            run = document["runs"].setdefault(run_id, {})

            if status == "pending":
                run.pop(message_id, None)
                decision: Dict[str, Any] = {"status": "pending"}
            else:
                decision = {
                    "status": status,
                    "decided_at": datetime.now(timezone.utc).isoformat(),
                    "decided_by": decided_by,
                    "note": note,
                }
                run[message_id] = decision

            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.approvals_path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2)
            tmp.replace(self.approvals_path)

            self._sync_state_approvals(run_id, document["runs"].get(run_id, {}))

        return decision

    def _sync_state_approvals(self, run_id: str, decisions: Dict[str, Any]) -> None:
        """Write approved flags back into data/pipeline_state.json.

        Only touches the run the state file actually holds, and only the fields
        the outreach stage owns. Silently skips when the state file is absent or
        describes a different run.
        """
        state = self.state()
        if not state:
            return
        state_run_id, _ = self.snapshot(CURRENT_RUN_ID)
        if run_id not in {CURRENT_RUN_ID, state_run_id}:
            return

        drafted = _as_list(state, "drafted_messages")
        if not drafted:
            return

        seen: Dict[str, int] = {}
        approved: List[Dict[str, Any]] = []
        for index, message in enumerate(drafted):
            base = slugify(message.get("to_email"), f"message-{index}")
            seen[base] = seen.get(base, 0) + 1
            message_id = base if seen[base] == 1 else f"{base}-{seen[base]}"
            decision = decisions.get(message_id) or {}
            if decision.get("status") == "approved":
                message["approved"] = True
                approved.append(message)
            else:
                # Covers rejected and cleared alike: without a standing approval
                # the draft must not stay flagged from an earlier decision.
                message["approved"] = False

        state["approved_messages"] = approved
        summary = state.get("send_summary")
        if isinstance(summary, dict):
            summary["approved_count"] = len(approved)

        tmp = self.state_path.with_suffix(".json.tmp")
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
            tmp.replace(self.state_path)
        except OSError:
            pass

    # ----------------------------------------------------------------- funnel

    def funnel(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Stage-by-stage counts, straight from the summaries the agents wrote."""
        completed = set(context.get("completed_stages") or [])
        leads = self.merge_leads(context)
        qualification = context.get("qualification_summary") or {}
        enrichment = context.get("enrichment_summary") or {}
        generation = context.get("generation_summary") or {}
        send = context.get("send_summary") or {}

        counts: Dict[str, Optional[int]] = {
            "ad_discovery": len(_as_list(context, "active_ads")) or None,
            "prospect": qualification.get("total_qualified") or (len(leads) or None),
            "contact_enrichment": enrichment.get("successfully_enriched"),
            "creative": generation.get("successful_generations"),
            "outreach": send.get("total_drafted"),
        }
        totals: Dict[str, Optional[int]] = {
            # Ad discovery has no denominator -- the market is however big it is.
            "ad_discovery": None,
            "prospect": context.get("total_brands_analyzed"),
            "contact_enrichment": enrichment.get("total_leads"),
            "creative": generation.get("total_attempts"),
            "outreach": send.get("total_drafted"),
        }

        stages: List[Dict[str, Any]] = []
        for stage in STAGES:
            key = stage["key"]
            done = key in completed
            stages.append(
                {
                    **stage,
                    "status": "complete" if done else "pending",
                    "count": counts.get(key),
                    "total": totals.get(key),
                }
            )

        if context.get("status") == "awaiting_verification":
            for stage in stages:
                if stage["key"] == "prospect" and stage["status"] == "complete":
                    stage["status"] = "awaiting_verification"
                    break
        elif context.get("status") == "running":
            completed = set(context.get("completed_stages") or [])
            if "prospect" in completed and "contact_enrichment" not in completed:
                for stage in stages:
                    if stage["key"] == "prospect":
                        stage["status"] = "awaiting_verification"
                        break
            else:
                for stage in stages:
                    if stage["status"] == "pending":
                        stage["status"] = "running"
                        break

        return stages

    # ------------------------------------------------------------ environment

    @staticmethod
    def integrations() -> List[Dict[str, Any]]:
        """Which credentials are live. Drives the mock-data banner."""
        return [
            {
                "key": "meta",
                "label": "Meta Ad Library",
                "purpose": "Ad discovery, ad-presence checks",
                "configured": _is_configured(settings.meta_access_token),
                "env_var": "META_ACCESS_TOKEN",
            },
            {
                "key": "business_data",
                "label": "Apollo / Crunchbase",
                "purpose": "Brand list for the niche",
                "configured": _is_configured(settings.apollo_api_key)
                or _is_configured(settings.crunchbase_api_key),
                "env_var": "APOLLO_API_KEY",
            },
            {
                "key": "enrichment",
                "label": "Apollo / Hunter / Clearbit",
                "purpose": "Contact enrichment",
                "configured": any(
                    _is_configured(value)
                    for value in (
                        settings.apollo_api_key,
                        settings.hunter_api_key,
                        settings.clearbit_api_key,
                    )
                ),
                "env_var": "HUNTER_API_KEY",
            },
            {
                "key": "higgsfield",
                "label": "Higgsfield",
                "purpose": "Sample video generation",
                "configured": _is_configured(settings.higgsfield_api_key),
                "env_var": "HIGGSFIELD_API_KEY",
            },
            {
                "key": "smtp",
                "label": "SMTP sender",
                "purpose": "Outreach delivery",
                "configured": _is_configured(settings.smtp_host)
                and _is_configured(settings.sender_email),
                "env_var": "SMTP_HOST",
            },
        ]

    @staticmethod
    def config_summary() -> Dict[str, Any]:
        """Config values the dashboard displays. No secrets cross this boundary."""
        return {
            "market": {
                "niche": config.market.niche,
                "regions": list(config.market.regions),
                "seed_brands": list(config.market.seed_brands),
            },
            "prospect": {
                "max_leads": config.prospect.max_leads,
                "min_company_size": config.prospect.min_company_size,
                "max_company_size": config.prospect.max_company_size,
                "max_ad_spend_threshold": config.prospect.max_ad_spend_threshold,
                "qualification_weights": dict(config.prospect.qualification_weights),
            },
            "creative": {
                "video_duration": config.creative.video_duration,
                "video_resolution": config.creative.video_resolution,
                "default_style": config.creative.default_style,
            },
            "outreach": {
                "max_sends_per_day": config.outreach.max_sends_per_day,
                "min_seconds_between_sends": config.outreach.min_seconds_between_sends,
            },
            "approval": {
                "enabled": config.approval.enabled,
                "method": config.approval.method,
                "auto_approve": config.approval.auto_approve,
            },
            "contact_enrichment": {
                "target_roles": list(config.contact_enrichment.target_roles),
                "min_confidence": config.contact_enrichment.min_confidence,
            },
        }

    # -------------------------------------------------------------------- runs

    def run_index(self) -> List[Dict[str, Any]]:
        """Completed reports plus the live state, newest first."""
        entries: List[Dict[str, Any]] = []

        state = self.state()
        if state:
            entries.append(self._run_entry(CURRENT_RUN_ID, state, _mtime(self.state_path), True))

        for path in self.report_paths():
            report = _read_json(path)
            if report:
                entries.append(self._run_entry(path.stem, report, _mtime(path), False))

        return entries

    def _run_entry(
        self, run_id: str, context: Dict[str, Any], modified: float, is_live: bool
    ) -> Dict[str, Any]:
        send = context.get("send_summary") or {}
        qualification = context.get("qualification_summary") or {}
        decisions = self.decisions(run_id)
        return {
            "id": run_id,
            "is_live_state": is_live,
            "niche": context.get("niche"),
            "regions": context.get("regions") or [],
            "status": context.get("status"),
            "started_at": context.get("started_at"),
            "completed_at": context.get("completed_at"),
            "modified_at": _iso(modified),
            "completed_stages": context.get("completed_stages") or [],
            "leads": qualification.get("total_qualified") or len(self.merge_leads(context)),
            "average_score": qualification.get("average_score"),
            "drafted": send.get("total_drafted") or 0,
            "approved": sum(
                1 for decision in decisions.values() if decision.get("status") == "approved"
            )
            or send.get("approved_count")
            or 0,
            "sent": send.get("sent_count") or 0,
            "uses_mock_data": bool(context.get("is_mock_data")),
            "error": context.get("error"),
        }

    def log_tail(self, lines: int = 200) -> Dict[str, Any]:
        path = Path(config.orchestrator.log_file)
        if not path.is_absolute():
            candidates = [self.logs_dir / path.name, self.data_dir.parent / path]
        else:
            candidates = [path]

        for candidate in candidates:
            if candidate.exists():
                try:
                    content = candidate.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                tail = content.splitlines()[-lines:]
                return {"path": str(candidate), "lines": tail}

        return {"path": None, "lines": []}
