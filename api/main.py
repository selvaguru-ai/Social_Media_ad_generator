"""FastAPI application serving the pipeline dashboard and its JSON API.

Start it with:

    python run_dashboard.py

Every endpoint is a read of the artifacts the pipeline already writes, except
the approval routes, which record the human decision the outreach stage waits
for.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.runs import RunManager
from api.store import CURRENT_RUN_ID, PipelineStore

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

app = FastAPI(
    title="Ad Generator Pipeline Dashboard",
    description=(
        "Read layer over the prospect-to-pitch pipeline's JSON artifacts, plus "
        "the human approval gate for drafted outreach."
    ),
    version="1.0.0",
)

store = PipelineStore()
runs = RunManager()


# --------------------------------------------------------------------- schemas


class DecisionRequest(BaseModel):
    status: str = Field(description="approved, rejected, or pending")
    note: str = Field(default="", max_length=2000)
    run_id: Optional[str] = None


class RunRequest(BaseModel):
    niche: Optional[str] = None
    regions: Optional[List[str]] = None
    resume: bool = False


# ----------------------------------------------------------------- API helpers


def _require_snapshot(run_id: Optional[str]) -> tuple[str, Dict[str, Any]]:
    resolved_id, context = store.snapshot(run_id)
    if context is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No pipeline data found. Run the pipeline first: "
                'python run_pipeline.py "sustainable fashion"'
            ),
        )
    return resolved_id, context


# ---------------------------------------------------------------------- routes


@app.get("/api/health", tags=["meta"])
def health() -> Dict[str, Any]:
    run_id, context = store.snapshot()
    integrations = store.integrations()
    return {
        "status": "ok",
        "has_data": context is not None,
        "run_id": run_id if context is not None else None,
        "integrations": integrations,
        "mock_mode": not any(item["configured"] for item in integrations),
        "run": runs.status(),
    }


@app.get("/api/config", tags=["meta"])
def read_config() -> Dict[str, Any]:
    return store.config_summary()


@app.get("/api/overview", tags=["pipeline"])
def overview(run_id: Optional[str] = None) -> Dict[str, Any]:
    resolved_id, context = _require_snapshot(run_id)
    leads = store.lead_views(context)
    messages = store.message_views(resolved_id, context)
    scores = [
        lead["qualification_score"]
        for lead in leads
        if isinstance(lead.get("qualification_score"), (int, float))
    ]

    return {
        "run_id": resolved_id,
        "niche": context.get("niche"),
        "regions": context.get("regions") or [],
        "status": context.get("status"),
        "started_at": context.get("started_at"),
        "completed_at": context.get("completed_at"),
        "error": context.get("error"),
        "uses_mock_data": bool(context.get("is_mock_data")),
        "funnel": store.funnel(context),
        "totals": {
            "brands_analyzed": context.get("total_brands_analyzed"),
            "active_ads": len(context.get("active_ads") or []),
            "advertisers": len(context.get("dominant_advertisers") or []),
            "leads": len(leads),
            "contactable": sum(1 for lead in leads if lead["has_contact"]),
            "pitch_ready": sum(1 for lead in leads if lead["stage"] == "pitch-ready"),
            "drafted": len(messages),
            "awaiting_decision": sum(1 for m in messages if m["status"] == "pending"),
            "approved": sum(1 for m in messages if m["status"] == "approved"),
            "rejected": sum(1 for m in messages if m["status"] == "rejected"),
            "sent": sum(1 for m in messages if m["status"] == "sent"),
        },
        "scores": {
            "average": round(sum(scores) / len(scores), 4) if scores else None,
            "top": max(scores) if scores else None,
            "low": min(scores) if scores else None,
            "count": len(scores),
        },
        "summaries": {
            "qualification": context.get("qualification_summary"),
            "enrichment": context.get("enrichment_summary"),
            "generation": context.get("generation_summary"),
            "send": context.get("send_summary"),
        },
    }


@app.get("/api/leads", tags=["pipeline"])
def list_leads(
    run_id: Optional[str] = None,
    region: Optional[str] = None,
    stage: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = Query(default="score", pattern="^(score|name|size|ads|confidence)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> Dict[str, Any]:
    resolved_id, context = _require_snapshot(run_id)
    leads = store.lead_views(context)

    if region:
        leads = [lead for lead in leads if lead.get("region") == region]
    if stage:
        leads = [lead for lead in leads if lead.get("stage") == stage]
    if search:
        needle = search.strip().lower()
        leads = [
            lead
            for lead in leads
            if needle in str(lead.get("brand_name", "")).lower()
            or needle in str(lead.get("domain", "")).lower()
            or needle in str((lead.get("contact") or {}).get("name", "")).lower()
        ]

    def sort_key(lead: Dict[str, Any]) -> Any:
        if sort == "name":
            return str(lead.get("brand_name") or "").lower()
        if sort == "size":
            return lead.get("company_size") or 0
        if sort == "ads":
            return (lead.get("ad_presence_summary") or {}).get("active_ads") or 0
        if sort == "confidence":
            return (lead.get("score_breakdown") or {}).get("match_confidence") or 0
        return lead.get("qualification_score") or 0

    leads.sort(key=sort_key, reverse=(order == "desc"))

    return {
        "run_id": resolved_id,
        "count": len(leads),
        "regions": sorted({lead["region"] for lead in store.lead_views(context) if lead.get("region")}),
        "weights": store.config_summary()["prospect"]["qualification_weights"],
        "leads": leads,
    }


@app.get("/api/leads/{lead_id}", tags=["pipeline"])
def read_lead(lead_id: str, run_id: Optional[str] = None) -> Dict[str, Any]:
    resolved_id, context = _require_snapshot(run_id)
    for lead in store.lead_views(context):
        if lead["id"] == lead_id:
            return {"run_id": resolved_id, "lead": lead}
    raise HTTPException(status_code=404, detail=f"No lead '{lead_id}' in run {resolved_id}.")


@app.get("/api/market", tags=["pipeline"])
def market(run_id: Optional[str] = None) -> Dict[str, Any]:
    resolved_id, context = _require_snapshot(run_id)
    advertisers = [a for a in (context.get("dominant_advertisers") or []) if isinstance(a, dict)]
    active_ads = [a for a in (context.get("active_ads") or []) if isinstance(a, dict)]

    per_region: Dict[str, int] = {}
    for ad in active_ads:
        key = ad.get("target_region") or ad.get("region") or "unknown"
        per_region[key] = per_region.get(key, 0) + 1

    return {
        "run_id": resolved_id,
        "niche": context.get("niche"),
        "discovered_at": context.get("discovered_at"),
        "uses_mock_data": bool(context.get("is_mock_data")),
        "advertisers": advertisers,
        "ads_per_region": per_region,
        "patterns": context.get("ad_patterns") or {},
        "active_ads": active_ads,
    }


@app.get("/api/messages", tags=["approval"])
def list_messages(run_id: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
    resolved_id, context = _require_snapshot(run_id)
    messages = store.message_views(resolved_id, context)
    if status:
        messages = [message for message in messages if message["status"] == status]

    approval = store.config_summary()["approval"]
    return {
        "run_id": resolved_id,
        "count": len(messages),
        "approval": approval,
        "gate_open": approval["enabled"] and not approval["auto_approve"],
        "messages": messages,
    }


@app.post("/api/messages/{message_id}/decision", tags=["approval"])
def decide(message_id: str, payload: DecisionRequest) -> Dict[str, Any]:
    resolved_id, context = _require_snapshot(payload.run_id)
    known = {message["id"] for message in store.message_views(resolved_id, context)}
    if message_id not in known:
        raise HTTPException(
            status_code=404, detail=f"No drafted message '{message_id}' in run {resolved_id}."
        )
    try:
        decision = store.record_decision(resolved_id, message_id, payload.status, payload.note)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {"run_id": resolved_id, "message_id": message_id, "decision": decision}


@app.get("/api/runs", tags=["runs"])
def list_runs() -> Dict[str, Any]:
    return {"runs": store.run_index(), "active": runs.status()}


@app.post("/api/runs", tags=["runs"])
def start_run(payload: RunRequest) -> Dict[str, Any]:
    try:
        return {"run": runs.start(payload.niche, payload.regions, payload.resume)}
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.delete("/api/runs/active", tags=["runs"])
def stop_run() -> Dict[str, Any]:
    try:
        return {"run": runs.stop()}
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/runs/active/log", tags=["runs"])
def run_log(lines: int = Query(default=200, ge=1, le=2000)) -> Dict[str, Any]:
    return {"run": runs.status(), **runs.log_tail(lines)}


@app.get("/api/logs", tags=["runs"])
def pipeline_log(lines: int = Query(default=200, ge=1, le=2000)) -> Dict[str, Any]:
    return store.log_tail(lines)


# ------------------------------------------------------------- static frontend


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    entry = DASHBOARD_DIR / "index.html"
    if not entry.exists():
        return JSONResponse(  # type: ignore[return-value]
            status_code=500, content={"detail": "dashboard/index.html is missing."}
        )
    return FileResponse(entry)


if DASHBOARD_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")


__all__ = ["app", "store", "runs", "CURRENT_RUN_ID"]
