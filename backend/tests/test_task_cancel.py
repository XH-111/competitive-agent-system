from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db_models  # noqa: F401
from app.database import Base
from app.schemas import CreateTaskRequest
from app.services.task_run_service import TaskRunService
from app.services.task_service import TaskService


def test_task_run_can_request_cancel():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        task = TaskService(db).create_task(
            CreateTaskRequest(
                product_name="Cancel Demo",
                competitors=["AlphaCI"],
                region="China",
                industry="B2B SaaS",
            )
        )
        run_service = TaskRunService(db)
        run = run_service.create_run(
            task_id=task.task_id,
            workflow_engine="langgraph",
            collector_mode="mock",
            analyst_mode="evidence",
            writer_mode="mock",
            content_mode=None,
            demo_mode="normal",
            auto_rework=False,
        )

        cancelled = run_service.request_cancel(task.task_id, run.run_id)

        assert cancelled.status == "cancel_requested"
        assert run_service.is_cancel_requested(run.run_id) is True
    finally:
        db.close()
