from __future__ import annotations

from datetime import datetime
from threading import Lock
from typing import Any


_LOCK = Lock()
_PROGRESS_BY_RUN_ID: dict[str, dict[str, Any]] = {}


def set_workflow_progress(
    run_id: str | None,
    *,
    current_agent: str,
    current_stage: str,
    message: str,
    current: int = 0,
    total: int = 0,
    unit: str = "",
    detail: str | None = None,
    status: str = "running",
    metadata: dict[str, Any] | None = None,
) -> None:
    if not run_id:
        return
    payload = {
        "run_id": run_id,
        "current_agent": current_agent,
        "current_stage": current_stage,
        "message": message,
        "current": current,
        "total": total,
        "unit": unit,
        "detail": detail,
        "status": status,
        "metadata": metadata or {},
        "updated_at": datetime.utcnow().isoformat(),
    }
    with _LOCK:
        _PROGRESS_BY_RUN_ID[run_id] = payload


def get_workflow_progress(run_id: str) -> dict[str, Any]:
    with _LOCK:
        return dict(_PROGRESS_BY_RUN_ID.get(run_id, {"run_id": run_id, "status": "unknown"}))

