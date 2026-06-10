from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

from app.schemas.models import (
    AnalysisDimensionPlan,
    CollectorConfig,
    Evidence,
    EvidenceAnalystReworkContext,
    EvidenceDimensionAnswerResult,
    EvidenceCoverageGap,
    PlannerIncrementalCollectionPlan,
    PlannerCollectionPlan,
    PlannerDownstreamGuidance,
    PlannerExtractedContext,
    PlannerSurveyInput,
    PlannerSummary,
    QaResult,
    ReworkContext,
    Report,
    Task,
)


DemoMode = Literal["normal", "qa_missing_evidence", "qa_invalid_extraction", "qa_bad_report"]


class PlannerInput(BaseModel):
    task: Task
    run_id: str | None = None
    retry_count: int = 0


class PlannerOutput(BaseModel):
    _raw_llm_response: str | None = PrivateAttr(default=None)

    planner_summary: PlannerSummary
    selected_dimensions: list[str] = Field(min_length=1)
    analysis_dimension_plan: AnalysisDimensionPlan
    collection_plan: PlannerCollectionPlan
    downstream_guidance: PlannerDownstreamGuidance
    missing_information: list[str] = Field(default_factory=list)
    planner_notes: list[str] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)


class PlannerIncrementalInput(BaseModel):
    task: Task
    run_id: str | None = None
    retry_count: int = 0
    base_planner_output: PlannerOutput
    coverage_gap: EvidenceCoverageGap
    base_attempt_no: int | None = None


class PlannerIncrementalOutput(BaseModel):
    _raw_llm_response: str | None = PrivateAttr(default=None)

    incremental_collection_plan: PlannerIncrementalCollectionPlan
    diagnostics: dict = Field(default_factory=dict)


class CollectorInput(BaseModel):
    task: Task
    run_id: str | None = None
    retry_count: int = 0
    collector_mode: Literal["mock", "web"] = "mock"
    collection_plan: PlannerCollectionPlan | None = None
    incremental_collection_plan: PlannerIncrementalCollectionPlan | None = None
    collector_config: CollectorConfig | None = None
    partial_collection_plan_allowed: bool = False
    selected_dimensions: list[str] = Field(default_factory=list)
    analysis_dimension_plan: AnalysisDimensionPlan | None = None
    rework_context: ReworkContext | None = None
    competitor_aliases: dict[str, list[str]] = Field(default_factory=dict)


class CollectorOutput(BaseModel):
    evidence: list[Evidence]
    diagnostics: dict = Field(default_factory=dict)


class EvidenceAnalystInput(BaseModel):
    task: Task
    run_id: str | None = None
    evidence: list[Evidence]
    retry_count: int = 0
    selected_dimensions: list[str] = Field(default_factory=list)
    collection_plan: PlannerCollectionPlan | None = None
    enabled: bool = True
    previous_output: "EvidenceAnalystOutput | None" = None
    rework_context: EvidenceAnalystReworkContext | None = None


class EvidenceAnalystOutput(BaseModel):
    question_results: list[EvidenceDimensionAnswerResult] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)


class ReportAgentInput(BaseModel):
    task: Task
    run_id: str | None = None
    evidence_analyst_output: EvidenceAnalystOutput
    evidence: list[Evidence] = Field(default_factory=list)
    selected_dimensions: list[str] = Field(default_factory=list)
    collection_plan: PlannerCollectionPlan | None = None
    retry_count: int = 0


class ReportSection(BaseModel):
    section_id: str
    section_no: str
    title: str
    summary: str
    competitor_analyses: list[dict] = Field(default_factory=list)
    comparison: str | None = None
    limitations: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"


class ReportEvidenceRef(BaseModel):
    evidence_id: str
    title: str | None = None
    url: str | None = None
    source_domain: str | None = None
    source_quality: str | None = None
    snippet: str | None = None


class ReportAgentOutput(BaseModel):
    report: Report
    report_title: str
    sections: list[ReportSection] = Field(default_factory=list)
    evidence_refs: dict[str, ReportEvidenceRef] = Field(default_factory=dict)
    markdown_report: str
    diagnostics: dict = Field(default_factory=dict)


class QaInput(BaseModel):
    task: Task
    run_id: str | None = None
    qa_stage: Literal["full", "evidence", "analyst"] = "full"
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_analyst_output: EvidenceAnalystOutput | None = None
    selected_dimensions: list[str] = Field(default_factory=list)
    analysis_dimension_plan: AnalysisDimensionPlan | None = None
    collection_plan: PlannerCollectionPlan | None = None
    collector_config: CollectorConfig | None = None
    collector_trace_summary: dict = Field(default_factory=dict)
    retry_count: int = 0
    demo_mode: DemoMode = "normal"


class QaOutput(BaseModel):
    qa_result: QaResult
    diagnostics: dict = Field(default_factory=dict)


class SurveyQuestionDraft(BaseModel):
    competitor: str | None = None
    dimension_id: str | None = None
    source_question_id: str | None = None
    source_gap_type: str = "unknown_gap"
    question_text: str = Field(min_length=1)
    question_type: Literal["short_text", "single_choice", "multiple_choice", "rating"] = "short_text"
    options: list[str] = Field(default_factory=list)
    required: bool = True
    reason: str = ""


class SurveyAgentInput(BaseModel):
    task: Task
    run_id: str | None = None
    evidence_analyst_output: EvidenceAnalystOutput | None = None
    qa_result: QaResult | None = None
    report_agent_output: ReportAgentOutput | None = None
    selected_dimensions: list[str] = Field(default_factory=list)
    collection_plan: PlannerCollectionPlan | None = None
    enabled: bool = True
    retry_count: int = 0


class SurveyAgentOutput(BaseModel):
    survey_title: str
    survey_description: str
    questions: list[SurveyQuestionDraft] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)
