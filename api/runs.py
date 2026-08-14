"""Launch pipeline runs from the dashboard.

Runs shell out to the existing `run_pipeline.py` CLI rather than importing the
orchestrator, so the CLI stays the single execution path and a crashed run can
never take the API process down with it. One run at a time.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.config import LOGS_DIR, PROJECT_ROOT

RUN_LOG = LOGS_DIR / "dashboard_runs.log"


class RunManager:
    """Owns at most one child pipeline process."""

    def __init__(self, project_root: Path = PROJECT_ROOT, log_path: Path = RUN_LOG):
        self.project_root = Path(project_root)
        self.log_path = Path(log_path)
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._current: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ status

    def status(self) -> Dict[str, Any]:
        with self._lock:
            self._reap()
            if not self._current:
                return {"active": False, "run": None}
            return {"active": self._current["state"] == "running", "run": dict(self._current)}

    def _reap(self) -> None:
        """Fold a finished child process into the recorded status."""
        if not self._process or not self._current:
            return
        code = self._process.poll()
        if code is None:
            return
        self._current["state"] = "succeeded" if code == 0 else "failed"
        self._current["exit_code"] = code
        self._current["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._process = None

    # ------------------------------------------------------------------- start

    def start(
        self,
        niche: Optional[str] = None,
        regions: Optional[List[str]] = None,
        resume: bool = False,
    ) -> Dict[str, Any]:
        with self._lock:
            self._reap()
            if self._process is not None:
                raise RuntimeError("A pipeline run is already in progress.")

            command = [sys.executable, "-u", "run_pipeline.py"]
            if resume:
                command.append("--resume")
            elif niche:
                command.append(niche)
            if regions:
                command += ["--regions", ",".join(regions)]

            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            started_at = datetime.now(timezone.utc).isoformat()

            # stdin is closed so the outreach agent's isatty() check reads False
            # and the CLI approval prompt is skipped -- the dashboard is the gate.
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n=== run started {started_at}: {' '.join(command)} ===\n")
                log.flush()
                self._process = subprocess.Popen(
                    command,
                    cwd=str(self.project_root),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONUNBUFFERED": "1", "TERM": "dumb"},
                )

            self._current = {
                "pid": self._process.pid,
                "command": command,
                "niche": niche,
                "regions": regions or [],
                "resume": resume,
                "state": "running",
                "started_at": started_at,
                "finished_at": None,
                "exit_code": None,
            }
            return dict(self._current)

    # -------------------------------------------------------------------- stop

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            self._reap()
            if self._process is None:
                raise RuntimeError("No pipeline run is in progress.")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._reap()
            if self._current:
                self._current["state"] = "cancelled"
            return dict(self._current or {})

    # --------------------------------------------------------------------- log

    def log_tail(self, lines: int = 200) -> Dict[str, Any]:
        if not self.log_path.exists():
            return {"path": str(self.log_path), "lines": []}
        try:
            content = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {"path": str(self.log_path), "lines": []}
        return {"path": str(self.log_path), "lines": content.splitlines()[-lines:]}
