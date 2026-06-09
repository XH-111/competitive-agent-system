import json
import os
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db_models import PlannerAttemptRecord
from app.schemas import PlannerAttempt, PlannerOutput, ReworkContext


RAW_LLM_MAX_CHARS = 50_000


def _raw_llm_enabled() -> bool:
    return os.getenv("PLANNER_TRACE_RAW_LLM", "false").strip().lower() in {"1", "true", "yes", "on"}


def _to_schema(row: PlannerAttemptRecord) -> PlannerAttempt:
    return PlannerAttempt(
        run_id=row.run_id,
        attempt_no=row.attempt_no,
        status=row.status,
        planner_output=json.loads(row.planner_output_json),
        diagnostics=json.loads(row.diagnostics_json),
        rework_context=json.loads(row.rework_context_json) if row.rework_context_json else None,
        raw_llm_response=row.raw_llm_response,
        created_at=row.created_at,
    )


class PlannerAttemptService:
    def __init__(self, db: Session):
        self.db = db

    def save(
        self,
        *,
        run_id: str,
        status: str,
        planner_output: PlannerOutput | BaseModel | dict[str, Any] | None,
        diagnostics: dict[str, Any],
        rework_context: ReworkContext | dict[str, Any] | None = None,
        raw_llm_response: str | None = None,
    ) -> PlannerAttempt:
        current_max = (
            self.db.query(func.max(PlannerAttemptRecord.attempt_no))
            .filter_by(run_id=run_id)
            .scalar()
        )
        attempt_no = int(current_max or 0) + 1
        stored_raw = None
        if _raw_llm_enabled() and raw_llm_response:
            stored_raw = raw_llm_response[:RAW_LLM_MAX_CHARS]
        row = PlannerAttemptRecord(
            run_id=run_id,
            attempt_no=attempt_no,
            status=status,
            planner_output_json=json.dumps(
                _planner_payload(planner_output),
                ensure_ascii=False,
            ),
            diagnostics_json=json.dumps(diagnostics, ensure_ascii=False),
            rework_context_json=(
                _rework_context_json(rework_context) if rework_context is not None else None
            ),
            raw_llm_response=stored_raw,
            created_at=datetime.utcnow(),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _to_schema(row)

    def list_for_run(self, run_id: str) -> list[PlannerAttempt]:
        rows = (
            self.db.query(PlannerAttemptRecord)
            .filter_by(run_id=run_id)
            .order_by(PlannerAttemptRecord.attempt_no.asc())
            .all()
        )
        return [_to_schema(row) for row in rows]


def _planner_payload(planner_output: PlannerOutput | BaseModel | dict[str, Any] | None) -> dict[str, Any]:
    if planner_output is None:
        return {}
    if isinstance(planner_output, BaseModel):
        return planner_output.model_dump(mode="json")
    return planner_output


def _rework_context_json(rework_context: ReworkContext | dict[str, Any]) -> str:
    if isinstance(rework_context, BaseModel):
        return rework_context.model_dump_json()
    return json.dumps(rework_context, ensure_ascii=False)
