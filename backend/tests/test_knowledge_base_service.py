from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.schemas import CreateTaskRequest, Evidence
from app.services.knowledge_base_service import KbIngestionService, KbRetrieverService
from app.services.task_service import TaskService


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _task(db):
    return TaskService(db).create_task(
        CreateTaskRequest(
            product_name="AI 协作工具竞品分析",
            competitors=["AlphaCI"],
            region="中国",
            industry="B2B SaaS",
        )
    )


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="ev_alpha_001",
        competitor="AlphaCI",
        source_type="public_web",
        url="https://alpha.example.com/product",
        source_domain="alpha.example.com",
        source_quality="official",
        snippet="AlphaCI official page describes AI collaboration, workflow automation, enterprise plans, and team productivity features.",
        confidence=0.86,
        relevance_score=0.9,
        relevance_level="high",
        content_mode="page",
        page_fetch_success=True,
        page_title="AlphaCI Product",
        content_excerpt="AlphaCI official page describes AI collaboration, workflow automation, enterprise plans, and team productivity features for B2B teams.",
        collected_at=datetime.utcnow(),
    )


def test_kb_ingestion_skips_low_quality_evidence():
    db = _session()
    task = _task(db)
    low_quality = _evidence().model_copy(update={"source_quality": "low_quality"})

    result = KbIngestionService(db).ingest_evidence([low_quality], task=task, run_id="run_1")

    assert result["ingested"] == 0
    assert result["skipped_low_quality"] == 1


def test_kb_ingestion_is_idempotent_and_retrievable():
    db = _session()
    task = _task(db)
    evidence = _evidence()
    service = KbIngestionService(db)

    first = service.ingest_evidence([evidence], task=task, run_id="run_1")
    second = service.ingest_evidence([evidence], task=task, run_id="run_1")
    hits = KbRetrieverService(db).retrieve_for_task(task, selected_dimensions=["feature"], top_k=3)

    assert first["ingested"] == 1
    assert second["skipped_duplicate"] == 1
    assert hits
    assert hits[0].evidence_id == evidence.evidence_id
    assert hits[0].metadata["source_url"] == evidence.url
