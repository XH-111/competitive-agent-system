from typing import Any, Literal, TypedDict

from app.schemas.agent_io import (
    AnalystOutput,
    CollectorOutput,
    FinalReportOutput,
    PlannerOutput,
    QaOutput,
    ReportWriterOutput,
)
from app.schemas.models import (
    AnalysisDimensionPlan,
    Chunk,
    ClaimSupportResult,
    DimensionResult,
    Evidence,
    PlannerAmbiguityLevel,
    PlannerCompetitorCandidate,
    PlannerExtractedContext,
    PlannerScopeSnapshot,
    PlannerScopeSize,
    PlannerScopeType,
    PlannerStage,
    PlannerSurveyInput,
    PlannerDownstreamGuidance,
    QaResult,
    Report,
    RetrievalResult,
    RetrievedKnowledgeChunk,
    ReworkContext,
    SwotAnalysis,
    SurveyEvidence,
    Task,
    TaskRun,
)


WorkflowEngine = Literal["custom", "langgraph"]


class ConditionalRoute(TypedDict, total=False):
    from_node: str
    to_node: str
    reason: str
    rework_count: int


class WorkflowState(TypedDict, total=False):
    task_id: str
    run_id: str
    trace_id: str | None
    task: Task
    task_run: TaskRun | None
    run_status: str | None
    workflow_engine_requested: str
    workflow_engine_used: str
    demo_mode: str
    collector_mode: str
    analyst_mode: str
    writer_mode: str
    content_mode: str | None
    auto_rework: bool
    rework_count: int
    max_rework: int
    planner_output: PlannerOutput | None
    planner_summary: dict[str, Any]
    collection_plan: dict[str, Any]
    collector_output: CollectorOutput | None
    analyst_output: AnalystOutput | None
    report_writer_output: ReportWriterOutput | None
    qa_output: QaOutput | None
    final_report_output: FinalReportOutput | None
    evidence_gate_output: dict[str, Any]
    page_fetch_output: dict[str, Any]
    entity_resolution: dict[str, Any]
    competitor_aliases: dict[str, list[str]]
    evidence: list[Evidence]
    intent_summary: str | None
    intent_classification: str | None
    ambiguity_level: PlannerAmbiguityLevel | None
    scope_type: PlannerScopeType | None
    scope_size: PlannerScopeSize | None
    extracted_context: PlannerExtractedContext | None
    selected_dimensions: list[str]
    analysis_dimension_plan: AnalysisDimensionPlan | None
    survey_needed: bool
    survey_recommended: bool
    survey_objective: str | None
    survey_inputs: PlannerSurveyInput | None
    downstream_guidance: PlannerDownstreamGuidance | None
    confirmed_scope: PlannerScopeSnapshot | None
    inferred_scope: PlannerScopeSnapshot | None
    suggested_scope: PlannerScopeSnapshot | None
    recommended_next_constraints: list[str]
    assumptions: list[str]
    candidate_competitors: list[PlannerCompetitorCandidate]
    clarification_targets: list[str]
    planning_stages: list[PlannerStage]
    planner_notes: list[str]
    planner_confidence: float | None
    dimension_results: list[DimensionResult]
    swot_analysis: SwotAnalysis | None
    survey_evidence: list[SurveyEvidence]
    chunks: list[Chunk]
    retrieval_results: list[RetrievalResult]
    retrieved_knowledge_chunks: list[RetrievedKnowledgeChunk]
    knowledge_hits: list[dict[str, Any]]
    claim_support_results: list[ClaimSupportResult]
    rework_context: ReworkContext | None
    report: Report | None
    qa_result: QaResult | None
    route_to: str | None
    final_status: str | None
    errors: list[str]
    node_sequence: list[str]
    conditional_routes_taken: list[ConditionalRoute]
    workflow_summary: dict[str, Any]
