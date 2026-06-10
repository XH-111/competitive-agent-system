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
    node_statuses: dict[str, str] | None = None,
) -> None:
    if not run_id:
        return
    with _LOCK:
        previous = _PROGRESS_BY_RUN_ID.get(run_id, {})
        merged_node_statuses = dict(previous.get("node_statuses") or {})
        previous_agent = previous.get("current_agent")
        if (
            previous_agent
            and previous_agent != current_agent
            and merged_node_statuses.get(previous_agent) == "running"
        ):
            merged_node_statuses[previous_agent] = "completed"
        if node_statuses:
            merged_node_statuses.update(node_statuses)
        if status == "running":
            merged_node_statuses[current_agent] = "running"
        elif current_agent:
            merged_node_statuses[current_agent] = status
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
            "node_statuses": merged_node_statuses,
            "updated_at": datetime.utcnow().isoformat(),
        }
        _PROGRESS_BY_RUN_ID[run_id] = payload


def get_workflow_progress(run_id: str) -> dict[str, Any]:
    with _LOCK:
        return dict(_PROGRESS_BY_RUN_ID.get(run_id, {"run_id": run_id, "status": "unknown"}))
