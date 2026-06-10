from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db_models import TaskRunRecord
from app.schemas import TaskRun


def _to_schema(row: TaskRunRecord) -> TaskRun:
    return TaskRun(
        run_id=row.run_id,
        task_id=row.task_id,
        workflow_engine=row.workflow_engine,
        collector_mode=row.collector_mode,
        analyst_mode=row.analyst_mode,
        writer_mode=row.writer_mode,
        content_mode=row.content_mode,
        demo_mode=row.demo_mode,
        auto_rework=row.auto_rework,
        status=row.status,
        final_status=row.final_status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        elapsed_time_ms=row.elapsed_time_ms,
        error_message=row.error_message,
        created_at=row.created_at,
    )


class TaskRunService:
    def __init__(self, db: Session):
        self.db = db

    def create_run(
        self,
        *,
        task_id: str,
        workflow_engine: str,
        collector_mode: str,
        analyst_mode: str,
        writer_mode: str,
        content_mode: str | None,
        demo_mode: str,
        auto_rework: bool,
    ) -> TaskRun:
        now = datetime.utcnow()
        row = TaskRunRecord(
            run_id=f"run_{uuid4().hex[:10]}",
            task_id=task_id,
            workflow_engine=workflow_engine,
            collector_mode=collector_mode,
            analyst_mode=analyst_mode,
            writer_mode=writer_mode,
            content_mode=content_mode,
            demo_mode=demo_mode,
            auto_rework=auto_rework,
            status="running",
            started_at=now,
            created_at=now,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _to_schema(row)

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        final_status: str | None,
        elapsed_time_ms: int | None,
        error_message: str | None = None,
    ) -> TaskRun:
        row = self.db.get(TaskRunRecord, run_id)
        if row is None:
            raise KeyError(run_id)
        row.status = status
        row.final_status = final_status
        row.elapsed_time_ms = elapsed_time_ms
        row.error_message = error_message
        row.finished_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return _to_schema(row)

    def request_cancel(self, task_id: str, run_id: str) -> TaskRun:
        row = self.db.get(TaskRunRecord, run_id)
        if row is None or row.task_id != task_id:
            raise KeyError(run_id)
        if row.status == "running":
            row.status = "cancel_requested"
            self.db.commit()
            self.db.refresh(row)
        return _to_schema(row)

    def is_cancel_requested(self, run_id: str) -> bool:
        row = (
            self.db.query(TaskRunRecord)
            .filter_by(run_id=run_id)
            .populate_existing()
            .first()
        )
        return row is not None and row.status == "cancel_requested"

    def get_run(self, task_id: str, run_id: str) -> TaskRun:
        row = self.db.get(TaskRunRecord, run_id)
        if row is None or row.task_id != task_id:
            raise KeyError(run_id)
        return _to_schema(row)

    def list_for_task(self, task_id: str) -> list[TaskRun]:
        rows = (
            self.db.query(TaskRunRecord)
            .filter_by(task_id=task_id)
            .order_by(TaskRunRecord.started_at.desc(), TaskRunRecord.created_at.desc())
            .all()
        )
        return [_to_schema(row) for row in rows]

    def latest_for_task(self, task_id: str) -> TaskRun:
        row = (
            self.db.query(TaskRunRecord)
            .filter_by(task_id=task_id)
            .order_by(TaskRunRecord.started_at.desc(), TaskRunRecord.created_at.desc())
            .first()
        )
        if row is None:
            raise KeyError(task_id)
        return _to_schema(row)
