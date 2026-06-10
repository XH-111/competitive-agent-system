from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db_models  # noqa: F401
from app.database import Base
from app.db_models import KnowledgeItemRecordRow
from app.schemas import (
    CreateTaskRequest,
    EvidenceAnalystOutput,
    EvidenceDimensionAnswerResult,
    EvidenceQuestionAnswer,
)
from app.services.analyst_knowledge_service import AnalystKnowledgeService
from app.services.task_service import TaskService


def test_ingest_answered_questions_to_kb_and_skip_duplicates():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with TestingSessionLocal() as db:
        task = TaskService(db).create_task(
            CreateTaskRequest(
                product_name="Test Product",
                competitors=["AcmeAI"],
                region="US",
                industry="AI",
            )
        )
        output = EvidenceAnalystOutput(
            question_results=[
                EvidenceDimensionAnswerResult(
                    competitor="AcmeAI",
                    dimension_id="pricing",
                    dimension_goal="pricing benchmark",
                    research_questions=["What is enterprise pricing?"],
                    question_answers=[
                        EvidenceQuestionAnswer(
                            question_id="q1",
                            question="What is enterprise pricing?",
                            answer="AcmeAI publishes an enterprise contact-sales tier.",
                            evidence_ids=["ev_1"],
                            answer_status="answered",
                        ),
                        EvidenceQuestionAnswer(
                            question_id="q2",
                            question="What is implementation cost?",
                            answer="No evidence found.",
                            evidence_ids=[],
                            answer_status="not_found",
                        ),
                        EvidenceQuestionAnswer(
                            question_id="q3",
                            question="What is discount policy?",
                            answer="Discount policy appears available.",
                            evidence_ids=[],
                            answer_status="answered",
                        ),
                    ],
                    dimension_summary="summary",
                    warnings=[],
                )
            ],
            diagnostics={},
        )

        service = AnalystKnowledgeService(db)
        first = service.ingest_answered_questions(task=task, run_id="run_1", evidence_analyst_output=output)
        second = service.ingest_answered_questions(task=task, run_id="run_1", evidence_analyst_output=output)

        assert first["candidate_count"] == 2
        assert first["ingested"] == 1
        assert first["skipped_no_evidence"] == 1
        assert second["ingested"] == 0
        assert second["skipped_duplicate"] == 1
        rows = db.query(KnowledgeItemRecordRow).all()
        assert len(rows) == 1
        assert rows[0].source_type == "evidence_analyst_answer"
        assert rows[0].competitor == "AcmeAI"
