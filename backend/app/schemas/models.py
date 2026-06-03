from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


TaskStatus = Literal["created", "running", "qa_failed", "manual_review", "completed", "failed"]
TaskRunStatus = Literal["running", "completed", "qa_failed", "manual_review", "failed", "insufficient_evidence"]
AgentName = Literal[
    "PlannerAgent",
    "CollectorAgent",
    "PageFetcher",
    "Chunker",
    "Indexer",
    "Retriever",
    "AnalystAgent",
    "ReportWriterAgent",
    "QaAgent",
    "SurveyAgent",
    "QuestionnaireAgent",
    "EvidenceGate",
    "HumanReviewAgent",
    "FinalReport",
    "FinalReportAgent",
    "WorkflowEngine",
]


class CreateTaskRequest(BaseModel):
    product_name: str = Field(min_length=1)
    competitors: list[str] = Field(min_length=1)
    region: str = Field(min_length=1)
    industry: str = Field(min_length=1)


class Task(BaseModel):
    task_id: str
    product_name: str
    competitors: list[str]
    region: str
    industry: str
    status: TaskStatus
    rework_count: int = 0
    created_at: datetime
    updated_at: datetime


class TaskRun(BaseModel):
    run_id: str
    task_id: str
    workflow_engine: str
    collector_mode: str
    analyst_mode: str
    writer_mode: str
    content_mode: str | None = None
    demo_mode: str
    auto_rework: bool
    status: TaskRunStatus
    final_status: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    elapsed_time_ms: int | None = None
    error_message: str | None = None
    created_at: datetime


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"ev_{uuid4().hex[:10]}")
    run_id: str | None = None
    competitor: str | None = None
    source_type: Literal["web", "public_web", "document", "pricing_page", "review", "interview", "survey"]
    url: str | None = None
    local_ref: str | None = None
    collected_at: datetime = Field(default_factory=datetime.utcnow)
    snippet: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    source_domain: str | None = None
    source_quality: Literal["official", "documentation", "media", "review", "unknown", "low_quality"] = "unknown"
    relevance_score: float = Field(default=1.0, ge=0, le=1)
    relevance_level: Literal["high", "medium", "low", "unrelated"] = "high"
    relevance_reason: str = "Mock or legacy evidence is treated as relevant by default."
    entity_match_signals: dict[str, Any] = Field(default_factory=dict)
    content_mode: Literal["snippet", "page"] = "snippet"
    page_fetch_success: bool = False
    page_title: str | None = None
    content_excerpt: str | None = None
    content_chars: int | None = None
    fetch_status_code: int | None = None
    page_fetch_error: str | None = None
    fetched_at: datetime | None = None

    @model_validator(mode="after")
    def require_source_reference(self) -> "Evidence":
        if not self.url and not self.local_ref:
            raise ValueError("Evidence requires either url or local_ref")
        return self


class ProductProfile(BaseModel):
    product_name: str
    positioning: str
    target_segments: list[str]
    strengths: list[str]
    weaknesses: list[str]
    evidence_ids: list[str] = Field(min_length=1)
    custom_dimensions: dict[str, Any] = Field(default_factory=dict)


class FeatureTree(BaseModel):
    core_features: dict[str, list[str]]
    differentiators: list[str]
    evidence_ids: list[str] = Field(min_length=1)


class PricingModel(BaseModel):
    model: str
    tiers: list[str]
    pricing_notes: str
    evidence_ids: list[str] = Field(min_length=1)


class UserPersona(BaseModel):
    persona_name: str
    goals: list[str]
    pain_points: list[str]
    buying_triggers: list[str]
    evidence_ids: list[str] = Field(min_length=1)


class SwotItem(BaseModel):
    summary: str = Field(min_length=1)
    competitor: str | None = None
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class SwotAnalysis(BaseModel):
    strengths: list[SwotItem] = Field(default_factory=list)
    weaknesses: list[SwotItem] = Field(default_factory=list)
    opportunities: list[SwotItem] = Field(default_factory=list)
    threats: list[SwotItem] = Field(default_factory=list)


class Claim(BaseModel):
    claim_id: str = Field(default_factory=lambda: f"claim_{uuid4().hex[:10]}")
    competitor: str | None = None
    text: str = Field(min_length=1)
    category: Literal["positioning", "feature", "pricing", "persona", "risk", "recommendation"]
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class AnalysisDimension(BaseModel):
    dimension_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    required: bool = False
    priority: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisDimensionPlan(BaseModel):
    selected_dimensions: list[str] = Field(default_factory=list)
    dimension_plans: list[AnalysisDimension] = Field(default_factory=list)
    research_goals: list[str] = Field(default_factory=list)
    query_hints: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


PlannerAmbiguityLevel = Literal["low", "medium", "high"]
PlannerScopeType = Literal[
    "specific_product_benchmark",
    "semi_specific_benchmark",
    "category_scan",
    "broad_competitive_analysis",
    "mixed_intent",
    "strategic_ambiguous",
]
PlannerScopeSize = Literal["narrow", "medium", "broad"]


PlannerIntentLabel = Literal[
    "competitive_analysis",
    "product_positioning",
    "feature_comparison",
    "ux_review",
    "improvement_opportunity",
    "survey_design",
    "survey_analysis",
    "market_research",
    "unknown",
]


class PlannerExtractedContext(BaseModel):
    intent_classification: PlannerIntentLabel = "competitive_analysis"
    industry: str | None = None
    domain: str | None = None
    product_name: str | None = None
    product_type: str | None = None
    target_users: list[str] = Field(default_factory=list)
    region: str | None = None
    competitors_mentioned: list[str] = Field(default_factory=list)
    analysis_focus_points: list[str] = Field(default_factory=list)
    requested_outputs: list[str] = Field(default_factory=list)
    survey_needed: bool = False
    survey_reason: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)


class PlannerSurveyInput(BaseModel):
    objective: str | None = None
    respondent_type: str | None = None
    question_themes: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlannerCompetitorCandidate(BaseModel):
    name: str = Field(min_length=1)
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)
    priority: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlannerStage(BaseModel):
    stage_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    outputs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlannerDownstreamGuidance(BaseModel):
    collector: list[str] = Field(default_factory=list)
    analyst: list[str] = Field(default_factory=list)
    writer: list[str] = Field(default_factory=list)
    qa: list[str] = Field(default_factory=list)
    survey: list[str] = Field(default_factory=list)


class PlannerScopeSnapshot(BaseModel):
    competitors: list[str] = Field(default_factory=list)
    region: str | None = None
    industry: str | None = None
    product_name: str | None = None
    target_users: list[str] = Field(default_factory=list)
    selected_dimensions: list[str] = Field(default_factory=list)
    requested_outputs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DimensionResult(BaseModel):
    dimension_id: str = Field(min_length=1)
    competitor: str | None = None
    summary: str = Field(min_length=1)
    findings: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    insufficient_evidence: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_evidence_or_insufficient_flag(self) -> "DimensionResult":
        if not self.evidence_ids and not self.insufficient_evidence:
            raise ValueError("DimensionResult requires evidence_ids unless insufficient_evidence=true")
        return self


class Chunk(BaseModel):
    chunk_id: str = Field(default_factory=lambda: f"chunk_{uuid4().hex[:10]}")
    run_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    competitor: str = Field(min_length=1)
    source_url: str | None = None
    source_domain: str | None = None
    text: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    chunk_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    competitor: str = Field(min_length=1)
    text: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    citation_metadata: dict[str, Any]


class KnowledgeItem(BaseModel):
    item_id: str = Field(default_factory=lambda: f"kb_item_{uuid4().hex[:10]}")
    source_type: str = "public_evidence"
    competitor: str | None = None
    industry: str | None = None
    region: str | None = None
    title: str | None = None
    text: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_domain: str | None = None
    source_quality: str | None = None
    confidence: float = Field(ge=0, le=1)
    relevance_score: float = Field(default=0.0, ge=0, le=1)
    evidence_id: str = Field(min_length=1)
    fact_id: str | None = None
    task_id: str = Field(min_length=1)
    run_id: str | None = None
    content_hash: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class KnowledgeChunk(BaseModel):
    chunk_id: str = Field(default_factory=lambda: f"kb_chunk_{uuid4().hex[:10]}")
    item_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    embedding: list[float] = Field(default_factory=list)
    token_count: int = Field(default=0, ge=0)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RetrievedKnowledgeChunk(BaseModel):
    chunk_id: str
    text: str
    score: float = Field(ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_id: str | None = None
    text_preview: str = ""
    source_url: str | None = None
    source_domain: str | None = None
    source_quality: str | None = None
    updated_at: datetime | None = None


class ClaimSupportResult(BaseModel):
    claim_id: str = Field(min_length=1)
    supported: bool
    support_score: float = Field(ge=0, le=1)
    retrieval_results: list[RetrievalResult] = Field(default_factory=list)
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SurveyEvidence(BaseModel):
    survey_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    competitor: str | None = None
    question_ids: list[str] = Field(default_factory=list)
    sample_size: int = Field(ge=0)
    is_mock: bool
    snippet: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouteInstruction(BaseModel):
    route_to: AgentName | None = None
    error_type: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    target_agent: AgentName | None = None
    related_competitor: str | None = None
    related_claim_id: str | None = None
    related_evidence_id: str | None = None
    suggested_action: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReworkContext(BaseModel):
    route_to: AgentName | None = None
    error_type: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    target_agent: AgentName | None = None
    related_competitor: str | None = None
    related_claim_id: str | None = None
    related_evidence_id: str | None = None
    suggested_action: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentMessage(BaseModel):
    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    from_agent: AgentName
    to_agent: AgentName
    message_type: Literal["plan", "evidence", "analysis", "report", "qa", "final", "error"]
    schema_name: str = Field(min_length=1)
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReworkInstruction(BaseModel):
    target_agent: AgentName
    error_type: Literal[
        "missing_evidence",
        "missing_relevant_evidence",
        "invalid_extraction",
        "contradiction",
        "bad_report_format",
        "swot_missing_support",
        "swot_over_inference",
        "swot_competitor_mismatch",
        "swot_dimension_gap",
        "swot_sparse_competitor_coverage",
    ]
    reason: str
    suggested_action: str
    claim_id: str | None = None
    failed_claim: str | None = None
    failed_schema: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReworkHistoryItem(BaseModel):
    round: int
    from_status: Literal["failed", "manual_review"]
    error_type: str
    route_to: AgentName | None = None
    action: str
    result_status: Literal["passed", "failed", "manual_review"] | None = None
    reason: str | None = None
    failed_schema: str | None = None
    claim_id: str | None = None
    failed_claim: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class QaResult(BaseModel):
    task_id: str
    run_id: str | None = None
    status: Literal["passed", "failed", "manual_review"]
    hard_errors: list[str] = Field(default_factory=list)
    soft_suggestions: list[str] = Field(default_factory=list)
    rework_instructions: list[ReworkInstruction] = Field(default_factory=list)
    rework_history: list[ReworkHistoryItem] = Field(default_factory=list)
    route_to: AgentName | None = None
    rework_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class TraceRecord(BaseModel):
    trace_id: str
    task_id: str
    run_id: str | None = None
    agent_name: AgentName
    input_summary: str
    output_summary: str
    schema_validation_result: Literal["passed", "failed"]
    model_name: str = "mock-runner-v0"
    token_usage: int | None = None
    elapsed_time_ms: int
    retry_count: int = 0
    error_message: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Report(BaseModel):
    report_id: str = Field(default_factory=lambda: f"report_{uuid4().hex[:10]}")
    task_id: str
    run_id: str | None = None
    markdown: str
    json_report: dict[str, Any]
    claims: list[Claim]
    qa_result: QaResult | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DagNode(BaseModel):
    id: AgentName
    label: str
    status: Literal["pending", "running", "completed", "failed", "manual_review"]


class DagEdge(BaseModel):
    source: AgentName
    target: AgentName
    label: str = ""


class Dag(BaseModel):
    nodes: list[DagNode]
    edges: list[DagEdge]


class AgentRunResult(BaseModel):
    message: AgentMessage
    output: dict[str, Any]

    model_config = ConfigDict(arbitrary_types_allowed=True)
