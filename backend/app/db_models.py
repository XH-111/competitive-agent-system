from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TaskRecord(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    competitors_json: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(String, nullable=False)
    industry: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="created", nullable=False)
    rework_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TaskRunRecord(Base):
    __tablename__ = "task_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    workflow_engine: Mapped[str] = mapped_column(String, nullable=False)
    collector_mode: Mapped[str] = mapped_column(String, nullable=False)
    analyst_mode: Mapped[str] = mapped_column(String, nullable=False)
    writer_mode: Mapped[str] = mapped_column(String, nullable=False)
    content_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    demo_mode: Mapped[str] = mapped_column(String, nullable=False)
    auto_rework: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String, default="running", nullable=False)
    final_status: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    elapsed_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TraceRecordRow(Base):
    __tablename__ = "traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    agent_name: Mapped[str] = mapped_column(String, index=True, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PlannerAttemptRecord(Base):
    __tablename__ = "planner_attempts"
    __table_args__ = (UniqueConstraint("run_id", "attempt_no", name="uq_planner_attempt_run_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    planner_output_json: Mapped[str] = mapped_column(Text, nullable=False)
    diagnostics_json: Mapped[str] = mapped_column(Text, nullable=False)
    rework_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_llm_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class EvidenceRecordRow(Base):
    __tablename__ = "evidence"

    evidence_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class ReportRecordRow(Base):
    __tablename__ = "reports"

    report_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class QaRecordRow(Base):
    __tablename__ = "qa_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class KnowledgeItemRecordRow(Base):
    __tablename__ = "knowledge_items"

    item_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_type: Mapped[str] = mapped_column(String, default="public_evidence", nullable=False)
    competitor: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    industry: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    region: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    source_quality: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    fact_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    task_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    content_hash: Mapped[str] = mapped_column(String, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class KnowledgeChunkRecordRow(Base):
    __tablename__ = "knowledge_chunks"

    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
