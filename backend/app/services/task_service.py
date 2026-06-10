import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db_models import TaskRecord
from app.schemas import CreateTaskRequest, Task
from app.constants.collection_strategy import normalize_collection_strategy_mode


def _to_schema(row: TaskRecord) -> Task:
    return Task(
        task_id=row.task_id,
        product_name=row.product_name,
        competitors=json.loads(row.competitors_json),
        region=row.region,
        industry=row.industry,
        collection_strategy_mode=normalize_collection_strategy_mode(getattr(row, "collection_strategy_mode", None)),
        is_highlighted=bool(getattr(row, "is_highlighted", False)),
        status=row.status,
        rework_count=row.rework_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class TaskService:
    def __init__(self, db: Session):
        self.db = db

    def create_task(self, request: CreateTaskRequest) -> Task:
        row = TaskRecord(
            task_id=f"task_{uuid4().hex[:10]}",
            product_name=request.product_name,
            competitors_json=json.dumps(request.competitors, ensure_ascii=False),
            region=request.region,
            industry=request.industry,
            collection_strategy_mode=request.collection_strategy_mode,
            status="created",
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _to_schema(row)

    def list_tasks(self) -> list[Task]:
        rows = self.db.query(TaskRecord).order_by(TaskRecord.created_at.desc()).all()
        return [_to_schema(row) for row in rows]

    def get_task(self, task_id: str) -> Task:
        row = self.db.get(TaskRecord, task_id)
        if row is None:
            raise KeyError(task_id)
        return _to_schema(row)

    def update_status(self, task_id: str, status: str, rework_count: int | None = None) -> Task:
        row = self.db.get(TaskRecord, task_id)
        if row is None:
            raise KeyError(task_id)
        row.status = status
        if rework_count is not None:
            row.rework_count = rework_count
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return _to_schema(row)

    def update_highlight(self, task_id: str, is_highlighted: bool) -> Task:
        row = self.db.get(TaskRecord, task_id)
        if row is None:
            raise KeyError(task_id)
        row.is_highlighted = is_highlighted
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return _to_schema(row)
