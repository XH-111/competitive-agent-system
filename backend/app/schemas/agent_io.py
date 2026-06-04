from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.models import (
    AnalysisDimensionPlan,
    Dag,
    DimensionResult,
    Evidence,
    FeatureTree,
    PlannerAmbiguityLevel,
    PlannerCompetitorCandidate,
    PlannerDownstreamGuidance,
    PlannerExtractedContext,
    PlannerScopeSnapshot,
    PlannerScopeSize,
    PlannerScopeType,
    PlannerStage,
    PlannerSurveyInput,
    PricingModel,
    ProductProfile,
    QaResult,
    ReworkContext,
    Report,
    RetrievedKnowledgeChunk,
    SwotAnalysis,
    Task,
    UserPersona,
)


DemoMode = Literal["normal", "qa_missing_evidence", "qa_invalid_extraction", "qa_bad_report"]


class PlannerInput(BaseModel):
    task: Task
    run_id: str | None = None
    retry_count: int = 0


class PlannerOutput(BaseModel):
    dag: Dag
    plan: list[str] = Field(min_length=1)
    intent_summary: str | None = None
    intent_classification: str = "competitive_analysis"
    ambiguity_level: PlannerAmbiguityLevel = "low"
    scope_type: PlannerScopeType = "specific_product_benchmark"
    scope_size: PlannerScopeSize = "narrow"
    extracted_context: PlannerExtractedContext | None = None
    selected_dimensions: list[str] = Field(default_factory=list)
    analysis_dimension_plan: AnalysisDimensionPlan | None = None
    survey_needed: bool = False
    survey_recommended: bool = False
    survey_objective: str | None = None
    survey_inputs: PlannerSurveyInput | None = None
    confirmed_scope: PlannerScopeSnapshot | None = None
    inferred_scope: PlannerScopeSnapshot | None = None
    suggested_scope: PlannerScopeSnapshot | None = None
    recommended_next_constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    candidate_competitors: list[PlannerCompetitorCandidate] = Field(default_factory=list)
    clarification_targets: list[str] = Field(default_factory=list)
    planning_stages: list[PlannerStage] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    planner_notes: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    downstream_guidance: PlannerDownstreamGuidance | None = None
    diagnostics: dict = Field(default_factory=dict)


class CollectorInput(BaseModel):
    task: Task
    run_id: str | None = None
    retry_count: int = 0
    collector_mode: Literal["mock", "web"] = "mock"
    planner_query_hints: dict[str, list[str]] = Field(default_factory=dict)
    gate_context: dict = Field(default_factory=dict)


class CollectorOutput(BaseModel):
    evidence: list[Evidence]
    diagnostics: dict = Field(default_factory=dict)


class AnalystInput(BaseModel):
    task: Task
    run_id: str | None = None
    evidence: list[Evidence]
    retry_count: int = 0
    force_invalid_extraction: bool = False
    analyst_mode: Literal["mock", "evidence", "llm"] = "evidence"
    selected_dimensions: list[str] = Field(default_factory=list)
    rework_context: ReworkContext | None = None
    retrieved_knowledge_chunks: list[RetrievedKnowledgeChunk] = Field(default_factory=list)


class AnalystOutput(BaseModel):
    dimension_results: list[DimensionResult] = Field(default_factory=list)
    product_profile: ProductProfile
    feature_tree: FeatureTree
    pricing_model: PricingModel
    user_persona: UserPersona
    swot: SwotAnalysis
    diagnostics: dict = Field(default_factory=dict)


class ReportWriterInput(BaseModel):
    task: Task
    run_id: str | None = None
    knowledge: AnalystOutput
    evidence: list[Evidence] = Field(default_factory=list)
    retry_count: int = 0
    simulate_missing_evidence: bool = False
    force_bad_format: bool = False
    writer_mode: Literal["mock", "llm"] = "mock"
    selected_dimensions: list[str] = Field(default_factory=list)
    writer_guidance: list[str] = Field(default_factory=list)
    intent_classification: str | None = None
    rework_context: ReworkContext | None = None


class ReportWriterOutput(BaseModel):
    report: Report | None = None
    draft_report: dict | None = None
    writer_mode: Literal["mock", "llm"] = "mock"
    llm_fallback_reason: str | None = None
    diagnostics: dict = Field(default_factory=dict)


class QaInput(BaseModel):
    task: Task
    run_id: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    analysis: AnalystOutput | None = None
    report_output: ReportWriterOutput | None = None
    retry_count: int = 0
    demo_mode: DemoMode = "normal"


class QaOutput(BaseModel):
    qa_result: QaResult
    diagnostics: dict = Field(default_factory=dict)


class FinalReportInput(BaseModel):
    task: Task
    run_id: str | None = None
    report: Report
    qa_result: QaResult
    evidence: list[Evidence]
    retry_count: int = 0


class FinalReportOutput(BaseModel):
    report: Report
    evidence_summary: list[str]
