import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db_models  # noqa: F401
from app.database import Base
from app.db_models import KnowledgeItemRecordRow
from app.schemas import CreateTaskRequest, TraceRecord
from app.services.survey_service import SurveyService
from app.services.task_run_service import TaskRunService
from app.services.task_service import TaskService
from app.services.trace_service import TraceService


class UnavailableLlm:
    is_available = False
    provider = "test"
    model = "test-model"

    def chat_json(self, messages, timeout: float = 30.0):
        raise AssertionError("LLM should not be called in this test")


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with TestingSessionLocal() as db:
        yield db


def test_survey_generation_submission_and_kb_ingestion(db_session):
    task = TaskService(db_session).create_task(
        CreateTaskRequest(
            product_name="Test Product",
            competitors=["AcmeAI"],
            region="US",
            industry="AI",
        )
    )
    run = TaskRunService(db_session).create_run(
        task_id=task.task_id,
        workflow_engine="langgraph",
        collector_mode="mock",
        analyst_mode="llm",
        writer_mode="llm",
        content_mode=None,
        demo_mode="normal",
        auto_rework=False,
    )
    summary = {
        "run_id": run.run_id,
        "task_id": task.task_id,
        "selected_dimensions": ["pricing"],
        "evidence_analyst_output": {
            "question_results": [
                {
                    "competitor": "AcmeAI",
                    "dimension_id": "pricing",
                    "dimension_goal": "pricing",
                    "question_answers": [
                        {
                            "question_id": "q1",
                            "question": "What is enterprise pricing?",
                            "answer": "Current evidence is insufficient.",
                            "answer_status": "not_found",
                            "evidence_ids": [],
                        }
                    ],
                }
            ]
        },
    }
    TraceService(db_session).save(
        TraceRecord(
            trace_id="trace_workflow",
            task_id=task.task_id,
            run_id=run.run_id,
            agent_name="WorkflowEngine",
            input_summary="workflow",
            output_summary=json.dumps(summary),
            schema_validation_result="passed",
            elapsed_time_ms=1,
            created_at=datetime.utcnow(),
        )
    )

    service = SurveyService(db_session)
    service.response_qa_agent.llm_client = UnavailableLlm()
    survey = service.generate_for_run(task.task_id, run.run_id)

    assert survey["invite_code"]
    assert len(survey["questions"]) == 1
    question = survey["questions"][0]
    assert question["competitor"] == "AcmeAI"
    assert question["dimension_id"] == "pricing"
    assert question["source_gap_type"] == "not_found"

    response = service.submit_response(
        survey["invite_code"],
        answers=[
            {
                "question_id": question["question_id"],
                "answer": "Enterprise pricing required contacting sales during our evaluation.",
            }
        ],
    )

    assert response["kb_ingested"] is True
    assert response["qa_result"]["status"] == "accepted"
    rows = db_session.query(KnowledgeItemRecordRow).all()
    assert len(rows) == 1
    assert rows[0].source_type == "survey"
    assert rows[0].competitor == "AcmeAI"
    assert rows[0].source_url.startswith("survey://")


def test_low_quality_survey_response_is_not_ingested_to_kb(db_session):
    task = TaskService(db_session).create_task(
        CreateTaskRequest(
            product_name="Test Product",
            competitors=["AcmeAI"],
            region="US",
            industry="AI",
        )
    )
    run = TaskRunService(db_session).create_run(
        task_id=task.task_id,
        workflow_engine="langgraph",
        collector_mode="mock",
        analyst_mode="llm",
        writer_mode="llm",
        content_mode=None,
        demo_mode="normal",
        auto_rework=False,
    )
    summary = {
        "run_id": run.run_id,
        "task_id": task.task_id,
        "selected_dimensions": ["pricing"],
        "evidence_analyst_output": {
            "question_results": [
                {
                    "competitor": "AcmeAI",
                    "dimension_id": "pricing",
                    "dimension_goal": "pricing",
                    "question_answers": [
                        {
                            "question_id": "q1",
                            "question": "What is enterprise pricing?",
                            "answer": "Current evidence is insufficient.",
                            "answer_status": "not_found",
                            "evidence_ids": [],
                        }
                    ],
                }
            ]
        },
    }
    TraceService(db_session).save(
        TraceRecord(
            trace_id="trace_workflow_reject",
            task_id=task.task_id,
            run_id=run.run_id,
            agent_name="WorkflowEngine",
            input_summary="workflow",
            output_summary=json.dumps(summary),
            schema_validation_result="passed",
            elapsed_time_ms=1,
            created_at=datetime.utcnow(),
        )
    )

    service = SurveyService(db_session)
    service.response_qa_agent.llm_client = UnavailableLlm()
    survey = service.generate_for_run(task.task_id, run.run_id)
    question = survey["questions"][0]

    response = service.submit_response(
        survey["invite_code"],
        answers=[{"question_id": question["question_id"], "answer": "test"}],
    )

    assert response["kb_ingested"] is True
    assert response["qa_result"]["status"] == "rejected"
    assert db_session.query(KnowledgeItemRecordRow).count() == 0
