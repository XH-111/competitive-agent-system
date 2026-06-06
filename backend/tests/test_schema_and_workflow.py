import json
import pytest
import time
import httpx
from datetime import datetime
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db_models  # noqa: F401
from app.agents.analyst import AnalystAgent
from app.agents.collector import CollectorAgent
from app.agents.final_report import FinalReportAgent
from app.agents.langgraph_runner import LangGraphWorkflowRunner
from app.agents.planner import PlannerAgent
from app.agents.qa import QaAgent
from app.agents.report_writer import ReportWriterAgent
from app.agents.runner import MockWorkflowRunner
from app.agents.runner_factory import resolve_workflow_engine
from app.constants.analysis_dimensions import apply_fixed_dimensions_to_plan, fixed_dimension_ids, fixed_query_hints_for_competitor
from app.database import Base
from app.schemas import (
    AgentMessage,
    AnalysisDimension,
    AnalysisDimensionPlan,
    AnalystInput,
    AnalystOutput,
    Claim,
    ClaimSupportResult,
    Chunk,
    CollectorInput,
    CollectorOutput,
    CreateTaskRequest,
    DimensionResult,
    Evidence,
    FinalReportInput,
    FinalReportOutput,
    PlannerInput,
    PlannerOutput,
    QaInput,
    QaResult,
    QaOutput,
    RetrievedKnowledgeChunk,
    RetrievalResult,
    Report,
    ReportWriterInput,
    ReportWriterOutput,
    ReworkInstruction,
    SurveyEvidence,
    WorkflowState,
)
from app.services.llm_client import LlmResponse
from app.services.page_fetcher import PageFetchResult, PageFetcher
from app.services.report_service import ReportService
from app.services.task_service import TaskService
from app.services.task_run_service import TaskRunService
from app.services.trace_service import TraceService
from app.services.evidence_relevance_service import apply_relevance, score_evidence_relevance
from app.services.entity_resolver_service import EntityResolverService
from app.services.evidence_service import EvidenceService
from app.services.web_search_client import SearchResult, WebSearchClient, WebSearchResponse


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def make_task(db_session):
    return TaskService(db_session).create_task(
        CreateTaskRequest(
            product_name="CIS Demo",
            competitors=["AlphaCI", "BetaIntel"],
            region="China",
            industry="B2B SaaS",
        )
    )


def make_custom_task(db_session, product_name="Random Demo", competitors=None, industry="Beauty Retail"):
    return TaskService(db_session).create_task(
        CreateTaskRequest(
            product_name=product_name,
            competitors=competitors or ["jskad", "sda", "dsja"],
            region="Global",
            industry=industry,
        )
    )


def build_agent_outputs(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    planner = PlannerAgent(trace_service)
    collector = CollectorAgent(trace_service)
    analyst = AnalystAgent(trace_service)
    writer = ReportWriterAgent(trace_service)
    qa = QaAgent(trace_service)

    plan = planner.run(PlannerInput(task=task))
    collected = collector.run(CollectorInput(task=task))
    analysis = analyst.run(AnalystInput(task=task, evidence=collected.evidence))
    report_output = writer.run(ReportWriterInput(task=task, knowledge=analysis))
    qa_output = qa.run(QaInput(task=task, evidence=collected.evidence, analysis=analysis, report_output=report_output))
    return task, trace_service, plan, collected, analysis, report_output, qa_output


def test_claim_requires_evidence_ids():
    with pytest.raises(ValidationError):
        Claim(text="Unsupported claim", category="feature", evidence_ids=[], confidence=0.5)


def test_evidence_requires_url_or_local_ref():
    with pytest.raises(ValidationError):
        Evidence(source_type="web", snippet="Missing source reference", confidence=0.7)


def test_agent_message_requires_trace_and_task_id():
    with pytest.raises(ValidationError):
        AgentMessage(
            trace_id="",
            task_id="",
            from_agent="PlannerAgent",
            to_agent="CollectorAgent",
            message_type="plan",
            schema_name="Dag",
            payload={},
        )


def test_public_contract_agent_names_are_reserved():
    message = AgentMessage(
        trace_id="trace_1",
        task_id="task_1",
        from_agent="Retriever",
        to_agent="SurveyAgent",
        message_type="evidence",
        schema_name="RetrievalResult",
        payload={},
    )
    assert message.from_agent == "Retriever"
    assert message.to_agent == "SurveyAgent"


def test_public_contract_analysis_dimension_validates():
    dimension = AnalysisDimension(
        dimension_id="pricing",
        label="Pricing",
        description="Pricing and packaging signals",
        keywords=["pricing", "plan"],
        required=True,
        priority=1,
        metadata={"owner": "planner"},
    )
    assert dimension.dimension_id == "pricing"
    assert dimension.metadata["owner"] == "planner"


def test_public_contract_analysis_dimension_plan_validates():
    plan = AnalysisDimensionPlan(
        selected_dimensions=["pricing"],
        dimension_plans=[
            AnalysisDimension(
                dimension_id="pricing",
                label="Pricing",
                keywords=["pricing"],
                required=True,
                priority=1,
            )
        ],
        research_goals=["Find pricing signals."],
        query_hints={"AlphaCI": ["AlphaCI pricing official"]},
    )
    assert plan.selected_dimensions == ["pricing"]
    assert plan.query_hints["AlphaCI"]


def test_fixed_competitive_dimensions_generate_collector_queries():
    queries = fixed_query_hints_for_competitor("苹果17pro", "智能手机")
    assert len(queries) == len(fixed_dimension_ids())
    joined = " ".join(queries)
    for keyword in ["价格", "功能", "用户画像", "优势", "劣势", "机会", "威胁"]:
        assert keyword in joined


def test_planner_dimension_plan_adds_industry_dynamic_dimensions(db_session):
    task = make_custom_task(
        db_session,
        product_name="\u65b0\u80fd\u6e90\u6c7d\u8f66\u7ade\u54c1\u5206\u6790",
        competitors=["\u5c0f\u7c73 SU7", "\u7279\u65af\u62c9 Model 3"],
        industry="\u65b0\u80fd\u6e90\u6c7d\u8f66",
    )
    plan = apply_fixed_dimensions_to_plan(None, task)

    for dimension_id in ["feature", "pricing", "persona", "strength", "weakness", "opportunity", "threat"]:
        assert dimension_id in plan.selected_dimensions
    for dimension_id in ["battery_range", "autonomous_driving", "charging_network", "vehicle_performance"]:
        assert dimension_id in plan.selected_dimensions

    search_plan = plan.metadata["collector_search_plan"]
    assert search_plan["\u5c0f\u7c73 SU7"]["battery_range"]["queries"] == ["\u5c0f\u7c73 SU7 \u7eed\u822a\u4e0e\u7535\u6c60"]
    assert plan.metadata["dimension_policy"] == "base_plus_dynamic_planner_search_plan"


def test_public_contract_dimension_result_requires_evidence_or_insufficient_flag():
    result = DimensionResult(
        dimension_id="pricing",
        competitor="AlphaCI",
        summary="Pricing evidence is insufficient.",
        findings=[],
        evidence_ids=[],
        confidence=0.3,
        insufficient_evidence=True,
    )
    assert result.insufficient_evidence is True
    with pytest.raises(ValidationError):
        DimensionResult(
            dimension_id="pricing",
            competitor="AlphaCI",
            summary="Unsupported pricing conclusion.",
            findings=[],
            evidence_ids=[],
            confidence=0.8,
            insufficient_evidence=False,
        )


def test_public_contract_chunk_requires_run_evidence_and_competitor():
    chunk = Chunk(
        run_id="run_1",
        evidence_id="ev_1",
        competitor="AlphaCI",
        source_url="https://alpha.example.com",
        source_domain="alpha.example.com",
        text="AlphaCI pricing content excerpt.",
    )
    assert chunk.run_id == "run_1"
    assert chunk.evidence_id == "ev_1"
    assert chunk.competitor == "AlphaCI"


def test_public_contract_retrieval_result_requires_citation_metadata():
    result = RetrievalResult(
        chunk_id="chunk_1",
        evidence_id="ev_1",
        run_id="run_1",
        competitor="AlphaCI",
        text="Retrieved pricing chunk.",
        score=0.82,
        citation_metadata={"source_domain": "alpha.example.com", "relevance_level": "high"},
    )
    assert result.citation_metadata["source_domain"] == "alpha.example.com"


def test_public_contract_survey_evidence_requires_run_sample_and_mock_flag():
    survey = SurveyEvidence(
        survey_id="survey_1",
        run_id="run_1",
        competitor="AlphaCI",
        question_ids=["q_1", "q_2"],
        sample_size=12,
        is_mock=True,
        snippet="Aggregated mock survey summary.",
        confidence=0.65,
    )
    assert survey.run_id == "run_1"
    assert survey.sample_size == 12
    assert survey.is_mock is True


def test_public_contract_workflow_state_accepts_reserved_fields():
    retrieval = RetrievalResult(
        chunk_id="chunk_1",
        evidence_id="ev_1",
        run_id="run_1",
        competitor="AlphaCI",
        text="Retrieved chunk.",
        score=0.8,
        citation_metadata={"source_domain": "alpha.example.com"},
    )
    state: WorkflowState = {
        "task_id": "task_1",
        "run_id": "run_1",
        "selected_dimensions": ["pricing"],
        "analysis_dimension_plan": AnalysisDimensionPlan(selected_dimensions=["pricing"]),
        "dimension_results": [
            DimensionResult(
                dimension_id="pricing",
                competitor="AlphaCI",
                summary="Pricing evidence exists.",
                findings=["Pricing page found."],
                evidence_ids=["ev_1"],
                confidence=0.8,
            )
        ],
        "survey_evidence": [
            SurveyEvidence(
                survey_id="survey_1",
                run_id="run_1",
                competitor="AlphaCI",
                question_ids=["q_1"],
                sample_size=1,
                is_mock=True,
                snippet="Mock survey summary.",
                confidence=0.5,
            )
        ],
        "chunks": [
            Chunk(
                chunk_id="chunk_1",
                run_id="run_1",
                evidence_id="ev_1",
                competitor="AlphaCI",
                text="Chunk text.",
            )
        ],
        "retrieval_results": [retrieval],
        "claim_support_results": [
            ClaimSupportResult(
                claim_id="claim_1",
                supported=True,
                support_score=0.8,
                retrieval_results=[retrieval],
                reason="Chunk supports claim.",
            )
        ],
        "rework_context": None,
    }
    assert state["selected_dimensions"] == ["pricing"]
    assert state["retrieval_results"][0].citation_metadata


def test_each_agent_run_uses_input_output_schema(db_session):
    task, _, plan, collected, analysis, report_output, qa_output = build_agent_outputs(db_session)
    assert isinstance(PlannerInput(task=task), PlannerInput)
    assert isinstance(plan, PlannerOutput)
    assert isinstance(collected, CollectorOutput)
    assert isinstance(analysis, AnalystOutput)
    assert isinstance(report_output, ReportWriterOutput)
    assert isinstance(qa_output, QaOutput)
    assert qa_output.qa_result.status == "passed"


def test_analyst_mode_mock_still_works(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = CollectorAgent(trace_service).run(CollectorInput(task=task)).evidence
    output = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence, analyst_mode="mock"))
    assert output.diagnostics["analyst_mode_used"] == "mock"
    assert output.product_profile.evidence_ids


def test_analyst_mode_evidence_extracts_features(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(source_type="public_web", url="https://example.com/features", source_domain="example.com", source_quality="official", snippet="The product supports AI automation, collaboration workflow, integration API, analytics and security features.", confidence=0.9),
        Evidence(source_type="public_web", url="https://example.com/pricing", source_domain="example.com", source_quality="official", snippet="Pricing includes free trial, subscription plan and enterprise quote.", confidence=0.9),
        Evidence(source_type="public_web", url="https://example.com/customers", source_domain="example.com", source_quality="unknown", snippet="Enterprise team, developer, marketer and product team users evaluate the product.", confidence=0.7),
    ]
    output = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence, analyst_mode="evidence"))
    assert output.diagnostics["analyst_mode_used"] == "evidence"
    assert "AI" in output.feature_tree.core_features
    assert output.feature_tree.evidence_ids


def test_analyst_evidence_extracts_pricing_and_persona(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(source_type="public_web", url="https://example.com/features", source_domain="example.com", source_quality="official", snippet="AI automation collaboration workflow and API integration for teams.", confidence=0.9),
        Evidence(source_type="public_web", url="https://example.com/pricing", source_domain="example.com", source_quality="official", snippet="Pricing page shows free trial, subscription plan and enterprise quote.", confidence=0.9),
        Evidence(source_type="public_web", url="https://example.com/users", source_domain="example.com", source_quality="unknown", snippet="Enterprise team, developer, marketer, product team and student users are mentioned.", confidence=0.7),
    ]
    output = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence, analyst_mode="evidence"))
    assert output.pricing_model.evidence_ids
    assert "pricing" in output.pricing_model.pricing_notes.lower()
    assert output.user_persona.evidence_ids
    assert output.user_persona.persona_name in {"企业团队", "团队用户", "开发者", "市场团队", "产品团队", "学生"}


def test_analyst_uses_collector_dimension_tags(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(
            source_type="public_web",
            url="https://alpha.example.com/commercial",
            source_domain="alpha.example.com",
            source_quality="official",
            competitor="AlphaCI",
            snippet="AlphaCI official commercial page with enterprise package information.",
            confidence=0.9,
            relevance_level="high",
            entity_match_signals={"collector_dimension": "pricing"},
        )
    ]
    output = AnalystAgent(trace_service).run(
        AnalystInput(task=task, evidence=evidence, analyst_mode="evidence", selected_dimensions=["pricing"])
    )
    pricing_result = next(item for item in output.dimension_results if item.dimension_id == "pricing" and item.competitor == "AlphaCI")
    assert pricing_result.evidence_ids == [evidence[0].evidence_id]
    assert pricing_result.insufficient_evidence is False


def test_analyst_insufficient_evidence_is_conservative_and_qa_suggests(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(competitor="AlphaCI", source_type="public_web", url="https://alpha.example/brief", source_domain="alpha.example", source_quality="unknown", snippet="AlphaCI brief public source.", confidence=0.6),
        Evidence(competitor="BetaIntel", source_type="public_web", url="https://beta.example/brief", source_domain="beta.example", source_quality="unknown", snippet="BetaIntel brief public source.", confidence=0.6),
    ]
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence, analyst_mode="evidence"))
    assert "Evidence is insufficient" in analysis.product_profile.positioning
    report_output = ReportWriterAgent(trace_service).run(
        ReportWriterInput(task=task, knowledge=analysis, evidence=evidence)
    )
    qa_result = QaAgent(trace_service).run(
        QaInput(task=task, evidence=evidence, analysis=analysis, report_output=report_output)
    ).qa_result
    assert qa_result.status == "passed"
    assert any("暂无直接 Evidence" in suggestion for suggestion in qa_result.soft_suggestions)


def test_analyst_trace_records_mode_and_counts(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(source_type="public_web", url="https://example.com/features", source_domain="example.com", source_quality="official", snippet="AI automation collaboration workflow, pricing plan and enterprise team.", confidence=0.9),
        Evidence(source_type="public_web", url="https://example.com/pricing", source_domain="example.com", source_quality="official", snippet="Free trial subscription pricing plan for developer team.", confidence=0.9),
        Evidence(source_type="public_web", url="https://example.com/security", source_domain="example.com", source_quality="documentation", snippet="Security analytics API integration documentation.", confidence=0.85),
    ]
    AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence, analyst_mode="evidence"))
    trace = next(item for item in trace_service.list_for_task(task.task_id) if item.agent_name == "AnalystAgent")
    diagnostics = json.loads(trace.output_summary)
    assert diagnostics["analyst_mode_requested"] == "evidence"
    assert diagnostics["analyst_mode_used"] == "evidence"
    assert diagnostics["extracted_feature_count"] > 0


def test_analyst_prefers_content_excerpt_over_snippet(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(
            source_type="public_web",
            url="https://alpha.example.com",
            competitor="AlphaCI",
            snippet="Brief source without target keywords.",
            content_mode="page",
            page_fetch_success=True,
            content_excerpt="AlphaCI product page describes AI automation pricing and enterprise team workflows.",
            confidence=0.9,
            relevance_level="high",
        )
    ]
    output = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence, analyst_mode="evidence"))
    assert "AI" in output.feature_tree.core_features
    assert output.diagnostics["content_source_used"]["page_excerpt"] == 1


def test_missing_evidence_routes_to_collector(db_session):
    task = make_task(db_session)
    qa = QaAgent(TraceService(db_session))
    result = qa.run(QaInput(task=task, evidence=[], demo_mode="qa_missing_evidence")).qa_result
    assert result.status == "failed"
    assert result.route_to == "CollectorAgent"
    assert result.rework_instructions[0].error_type == "missing_evidence"
    assert result.rework_instructions[0].failed_schema == "Evidence"


def test_invalid_planner_dimensions_route_to_planner(db_session):
    task = make_task(db_session)
    qa = QaAgent(TraceService(db_session))
    plan = AnalysisDimensionPlan(
        selected_dimensions=["feature"],
        metadata={"collector_search_plan": {}},
    )
    result = qa.run(
        QaInput(
            task=task,
            evidence=[],
            selected_dimensions=["feature"],
            analysis_dimension_plan=plan,
        )
    ).qa_result
    assert result.status == "failed"
    assert result.route_to == "PlannerAgent"
    assert result.rework_instructions[0].error_type == "invalid_planner_output"


def test_invalid_extraction_routes_to_analyst(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = CollectorAgent(trace_service).run(CollectorInput(task=task)).evidence
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence, force_invalid_extraction=True))
    report_output = ReportWriterAgent(trace_service).run(ReportWriterInput(task=task, knowledge=analysis))
    result = QaAgent(trace_service).run(
        QaInput(
            task=task,
            evidence=evidence,
            analysis=analysis,
            report_output=report_output,
            demo_mode="qa_invalid_extraction",
        )
    ).qa_result
    assert result.status == "failed"
    assert result.route_to == "AnalystAgent"
    assert result.rework_instructions[0].error_type == "invalid_extraction"
    assert result.rework_instructions[0].failed_schema == "AnalystOutput.dimension_results"


def test_bad_report_format_routes_to_report_writer(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = CollectorAgent(trace_service).run(CollectorInput(task=task)).evidence
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    report_output = ReportWriterAgent(trace_service).run(
        ReportWriterInput(task=task, knowledge=analysis, force_bad_format=True)
    )
    result = QaAgent(trace_service).run(
        QaInput(task=task, evidence=evidence, analysis=analysis, report_output=report_output, demo_mode="qa_bad_report")
    ).qa_result
    assert result.status == "failed"
    assert result.route_to == "ReportWriterAgent"
    assert result.rework_instructions[0].error_type == "bad_report_format"
    assert result.rework_instructions[0].failed_schema == "Report.markdown"


def test_max_rework_becomes_manual_review(db_session):
    task = make_task(db_session)
    task.rework_count = 3
    qa = QaAgent(TraceService(db_session))
    result = qa.run(QaInput(task=task, evidence=[], demo_mode="qa_missing_evidence")).qa_result
    assert result.status == "manual_review"
    assert result.route_to is None


def test_final_report_agent_creates_trace(db_session):
    task, trace_service, _, collected, _, report_output, qa_output = build_agent_outputs(db_session)
    assert report_output.report is not None
    output = FinalReportAgent(trace_service).run(
        FinalReportInput(task=task, report=report_output.report, qa_result=qa_output.qa_result, evidence=collected.evidence)
    )
    assert isinstance(output, FinalReportOutput)
    traces = trace_service.list_for_task(task.task_id)
    assert "FinalReport" in {trace.agent_name for trace in traces}


def test_normal_workflow_still_passes(db_session):
    task = make_task(db_session)
    result = MockWorkflowRunner(db_session).run(task.task_id, demo_mode="normal")
    assert result["qa_result"].status == "passed"
    assert result["report"] is not None
    traces = TraceService(db_session).list_for_task(task.task_id)
    assert {trace.agent_name for trace in traces} >= {
        "PlannerAgent",
        "CollectorAgent",
        "AnalystAgent",
        "ReportWriterAgent",
        "QaAgent",
        "FinalReport",
    }
    assert all(trace.schema_validation_result == "passed" for trace in traces)


def test_llm_writer_without_api_key_falls_back_to_mock(db_session, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    task = make_task(db_session)
    result = MockWorkflowRunner(db_session).run(task.task_id, writer_mode="llm")
    assert result["qa_result"].status == "passed"
    assert result["report"] is not None
    assert result["report"].json_report["writer_mode"] == "mock"
    diagnostics = result["report"].json_report["writer_diagnostics"]
    assert diagnostics["writer_mode_requested"] == "llm"
    assert diagnostics["writer_mode_used"] == "mock"
    assert diagnostics["fallback_used"] is True
    assert "LLM_API_KEY" in diagnostics["llm_fallback_reason"]


class FakeLlmClient:
    def __init__(self, content: str):
        self.content = content
        self.provider = "fake"
        self.model = "fake-model"
        self.base_url = "https://fake.local/v1"
        self.is_available = True

    def chat_json(self, messages, timeout: float = 30.0):
        time.sleep(0.02)
        return LlmResponse(
            available=True,
            content=self.content,
            provider="fake",
            model="fake-model",
            attempted=True,
            success=True,
            elapsed_time_ms=20,
            response_preview=self.content[:300],
        )


def test_llm_analyst_extracts_dimension_results(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(source_type="public_web", url="https://alpha.example.com", competitor="AlphaCI", snippet="AlphaCI supports AI automation workflow.", confidence=0.9, relevance_level="high"),
        Evidence(source_type="public_web", url="https://beta.example.com", competitor="BetaIntel", snippet="BetaIntel offers enterprise pricing plan.", confidence=0.85, relevance_level="high"),
    ]
    client = FakeLlmClient(
        '{"dimension_results":['
        '{"dimension_id":"feature","competitor":"AlphaCI","summary":"AlphaCI has AI workflow capability.",'
        '"findings":["AI automation workflow"],"evidence_ids":["'
        + evidence[0].evidence_id
        + '"],"confidence":0.82,"insufficient_evidence":false,"metadata":{"reason":"high relevance evidence"}},'
        '{"dimension_id":"feature","competitor":"BetaIntel","summary":"BetaIntel feature evidence is thin.",'
        '"findings":[],"evidence_ids":[],"confidence":0.35,"insufficient_evidence":true,"metadata":{}}]}'
    )
    output = AnalystAgent(trace_service, llm_client=client).run(
        AnalystInput(task=task, evidence=evidence, analyst_mode="llm", selected_dimensions=["feature"])
    )
    assert output.diagnostics["analyst_mode_used"] == "llm"
    assert output.diagnostics["llm_schema_validation_success"] is True
    assert len(output.dimension_results) == 2
    assert output.dimension_results[0].dimension_id == "feature"
    assert output.dimension_results[0].evidence_ids == [evidence[0].evidence_id]


def test_llm_analyst_invalid_json_falls_back_to_evidence(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(source_type="public_web", url="https://alpha.example.com", competitor="AlphaCI", snippet="AlphaCI AI automation workflow pricing enterprise team.", confidence=0.9, relevance_level="high"),
    ]
    output = AnalystAgent(trace_service, llm_client=FakeLlmClient("not json")).run(
        AnalystInput(task=task, evidence=evidence, analyst_mode="llm", selected_dimensions=["feature"])
    )
    assert output.diagnostics["analyst_mode_used"] == "evidence"
    assert output.diagnostics["fallback_used"] is True
    assert output.diagnostics["llm_schema_validation_success"] is False
    assert output.dimension_results


def test_llm_analyst_can_cite_current_run_knowledge_base_evidence(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(
            evidence_id="ev_kb_alpha001",
            source_type="knowledge_base",
            local_ref="kb_chunk_alpha001",
            competitor="AlphaCI",
            snippet="AlphaCI long-term knowledge says it supports AI workflow automation.",
            confidence=0.82,
            relevance_level="high",
            relevance_score=0.86,
        )
    ]
    client = FakeLlmClient(
        '{"dimension_results":[{"dimension_id":"feature","competitor":"AlphaCI",'
        '"summary":"AlphaCI 具备 AI 工作流自动化能力。","findings":["AI workflow automation"],'
        '"evidence_ids":["ev_kb_alpha001"],"confidence":0.82,"insufficient_evidence":false,'
        '"metadata":{"source":"knowledge_base_evidence"}}]}'
    )
    output = AnalystAgent(trace_service, llm_client=client).run(
        AnalystInput(task=task, evidence=evidence, analyst_mode="llm", selected_dimensions=["feature"])
    )

    assert output.diagnostics["analyst_mode_used"] == "llm"
    assert output.diagnostics["llm_schema_validation_success"] is True
    assert output.dimension_results[0].evidence_ids == ["ev_kb_alpha001"]


def test_llm_analyst_competitor_mismatch_falls_back_to_evidence(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(source_type="public_web", url="https://alpha.example.com", competitor="AlphaCI", snippet="AlphaCI AI automation workflow pricing enterprise team.", confidence=0.9, relevance_level="high"),
        Evidence(source_type="public_web", url="https://beta.example.com", competitor="BetaIntel", snippet="BetaIntel pricing plan.", confidence=0.85, relevance_level="high"),
    ]
    client = FakeLlmClient(
        '{"dimension_results":[{"dimension_id":"feature","competitor":"AlphaCI",'
        '"summary":"Wrong binding","findings":["wrong"],"evidence_ids":["'
        + evidence[1].evidence_id
        + '"],"confidence":0.8,"insufficient_evidence":false,"metadata":{}}]}'
    )
    output = AnalystAgent(trace_service, llm_client=client).run(
        AnalystInput(task=task, evidence=evidence, analyst_mode="llm", selected_dimensions=["feature"])
    )
    assert output.diagnostics["analyst_mode_used"] == "evidence"
    assert output.diagnostics["fallback_used"] is True
    assert output.diagnostics["llm_schema_validation_success"] is False


class FakeWebSearchClient:
    provider = "fake-search"
    api_key = "fake-key"
    base_url = "https://fake-search.local"

    def __init__(self, *, fail: bool = False):
        self.fail = fail

    def search(self, query: str, limit: int = 5):
        if self.fail:
            return WebSearchResponse(
                available=False,
                attempted=True,
                success=False,
                fallback_reason="Web search failed: timeout",
                error_type="TimeoutError",
                error_message="timeout",
            )
        competitor = query.split()[0]
        return WebSearchResponse(
            available=True,
            attempted=True,
            success=True,
            elapsed_time_ms=12,
            results=[
                SearchResult(title=f"{competitor} official pricing", url=f"https://{competitor.lower()}.example.com/pricing", snippet=f"{competitor} official pricing and features summary."),
                SearchResult(title=f"{competitor} docs", url=f"https://docs.{competitor.lower()}.example.com/features", snippet=f"{competitor} documentation describes collaboration features."),
            ],
        )


class FakeHttpResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.request = httpx.Request("POST", "https://api.tavily.com/search")

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("Client error", request=self.request, response=httpx.Response(self.status_code, text=self.text, request=self.request))


def test_page_fetcher_success_adds_content_excerpt():
    def handler(request):
        html = "<html><head><title>Alpha Pricing</title></head><body><nav>menu</nav><h1>Alpha pricing</h1><p>Alpha supports AI automation pricing for enterprise teams.</p></body></html>"
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=html)

    fetcher = PageFetcher(transport=httpx.MockTransport(handler), content_max_chars=3000, excerpt_max_chars=1200, respect_robots=False)
    evidence = [
        Evidence(
            source_type="public_web",
            url="https://alpha.example.com/pricing",
            source_domain="alpha.example.com",
            source_quality="official",
            competitor="AlphaCI",
            snippet="Alpha snippet",
            confidence=0.9,
            relevance_level="high",
        )
    ]
    enriched, diagnostics = fetcher.enrich(evidence, run_id="run_1")
    assert diagnostics["page_fetch_success_count"] == 1
    assert enriched[0].run_id == "run_1"
    assert enriched[0].content_mode == "page"
    assert enriched[0].page_fetch_success is True
    assert enriched[0].page_title == "Alpha Pricing"
    assert "AI automation" in (enriched[0].content_excerpt or "")


def test_page_fetcher_timeout_falls_back_to_snippet():
    def handler(request):
        raise httpx.TimeoutException("timeout")

    fetcher = PageFetcher(transport=httpx.MockTransport(handler), timeout=1, respect_robots=False)
    evidence = [
        Evidence(source_type="public_web", url="https://alpha.example.com", competitor="AlphaCI", snippet="Alpha snippet", confidence=0.9, relevance_level="high")
    ]
    enriched, diagnostics = fetcher.enrich(evidence, run_id="run_1")
    assert diagnostics["page_fetch_failed_count"] == 1
    assert enriched[0].content_mode == "snippet"
    assert enriched[0].page_fetch_success is False
    assert enriched[0].page_fetch_error == "timeout"


def test_page_fetcher_non_html_falls_back_to_snippet():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    fetcher = PageFetcher(transport=httpx.MockTransport(handler), respect_robots=False)
    evidence = [
        Evidence(source_type="public_web", url="https://alpha.example.com/file.pdf", competitor="AlphaCI", snippet="Alpha snippet", confidence=0.9, relevance_level="high")
    ]
    enriched, _ = fetcher.enrich(evidence, run_id="run_1")
    assert enriched[0].content_mode == "snippet"
    assert enriched[0].page_fetch_error == "non_html_content"


def test_page_fetcher_excerpt_limit_and_skips_unrelated():
    long_text = "Alpha pricing feature. " * 300

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=f"<html><body><p>{long_text}</p></body></html>")

    fetcher = PageFetcher(transport=httpx.MockTransport(handler), content_max_chars=500, excerpt_max_chars=120, respect_robots=False)
    evidence = [
        Evidence(source_type="public_web", url="https://alpha.example.com", competitor="AlphaCI", snippet="Alpha snippet", confidence=0.9, relevance_level="high"),
        Evidence(source_type="public_web", url="https://taxjar.com", competitor="AlphaCI", snippet="TaxJar unrelated", confidence=0.6, relevance_level="unrelated"),
    ]
    enriched, diagnostics = fetcher.enrich(evidence, run_id="run_1")
    assert len(enriched[0].content_excerpt or "") <= 120
    assert enriched[0].content_chars == 500
    assert enriched[1].page_fetch_error == "skipped:relevance_unrelated"
    assert diagnostics["page_fetch_skipped_count"] == 1


def test_page_fetcher_prioritizes_high_quality_evidence():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><body><p>High quality official source.</p></body></html>")

    fetcher = PageFetcher(transport=httpx.MockTransport(handler), max_per_run=1, respect_robots=False)
    low_priority = Evidence(
        source_type="public_web",
        url="https://unknown.example.com",
        competitor="AlphaCI",
        snippet="Unknown medium confidence source.",
        confidence=0.61,
        source_quality="unknown",
        relevance_level="medium",
        relevance_score=0.6,
    )
    high_priority = Evidence(
        source_type="public_web",
        url="https://official.example.com",
        competitor="AlphaCI",
        snippet="Official high confidence source.",
        confidence=0.92,
        source_quality="official",
        relevance_level="high",
        relevance_score=0.9,
    )
    enriched, diagnostics = fetcher.enrich([low_priority, high_priority], run_id="run_1")
    assert diagnostics["page_fetch_success_count"] == 1
    assert diagnostics["fetched_evidence_ids"] == [high_priority.evidence_id]
    assert enriched[0].evidence_id == high_priority.evidence_id
    assert enriched[0].page_fetch_success is True


def test_page_fetcher_respects_competitor_and_run_limits():
    class CountingFetcher(PageFetcher):
        def __init__(self):
            super().__init__(max_per_competitor=1, max_per_run=2, respect_robots=False)
            self.calls = 0

        def fetch(self, url: str, *, run_id: str | None, evidence_id: str, competitor: str | None):
            self.calls += 1
            return PageFetchResult(
                success=True,
                status_code=200,
                page_title="ok",
                content_excerpt=f"{competitor} content",
                content_chars=10,
                fetched_at=datetime.utcnow(),
            )

    fetcher = CountingFetcher()
    evidence = [
        Evidence(source_type="public_web", url="https://alpha.example.com/1", competitor="AlphaCI", snippet="Alpha 1", confidence=0.9, relevance_level="high"),
        Evidence(source_type="public_web", url="https://alpha.example.com/2", competitor="AlphaCI", snippet="Alpha 2", confidence=0.9, relevance_level="high"),
        Evidence(source_type="public_web", url="https://beta.example.com/1", competitor="BetaIntel", snippet="Beta 1", confidence=0.9, relevance_level="high"),
        Evidence(source_type="public_web", url="https://gamma.example.com/1", competitor="Gamma", snippet="Gamma 1", confidence=0.9, relevance_level="high"),
    ]
    enriched, diagnostics = fetcher.enrich(evidence, run_id="run_1")
    assert fetcher.calls == 2
    assert diagnostics["page_fetch_success_count"] == 2
    assert any(item.page_fetch_error == "skipped:max_per_competitor" for item in enriched)
    assert any(item.page_fetch_error == "skipped:max_per_run" for item in enriched)


def test_llm_writer_success_records_diagnostics_and_elapsed_time(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = CollectorAgent(trace_service).run(CollectorInput(task=task)).evidence
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    writer = ReportWriterAgent(
        trace_service,
        llm_client=FakeLlmClient(
            '{"markdown_report":"# LLM Report","json_report":{}}'
        ),
    )
    writer_output = writer.run(ReportWriterInput(task=task, knowledge=analysis, evidence=evidence, writer_mode="llm"))
    assert writer_output.report is not None
    diagnostics = writer_output.report.json_report["writer_diagnostics"]
    assert diagnostics["writer_mode_requested"] == "llm"
    assert diagnostics["writer_mode_used"] == "llm"
    assert diagnostics["llm_call_attempted"] is True
    assert diagnostics["llm_call_success"] is True
    assert diagnostics["llm_schema_validation_success"] is True
    assert diagnostics["llm_schema_validation_errors"] == []
    assert diagnostics["claims_generated"] is False
    assert writer_output.report.claims == []
    assert writer_output.report.dimension_results == analysis.dimension_results
    traces = [trace for trace in trace_service.list_for_task(task.task_id) if trace.agent_name == "ReportWriterAgent"]
    assert traces[-1].elapsed_time_ms > 0
    trace_diagnostics = json.loads(traces[-1].output_summary)
    assert trace_diagnostics["llm_schema_validation_success"] is True


def test_report_writer_prompt_uses_dimension_results_and_forbids_claims(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = CollectorAgent(trace_service).run(CollectorInput(task=task)).evidence
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    writer = ReportWriterAgent(trace_service)

    messages = writer._messages(
        ReportWriterInput(
            task=task,
            knowledge=analysis,
            evidence=evidence,
            selected_dimensions=["feature"],
        )
    )
    prompt = "\n".join(item["content"] for item in messages)

    assert "knowledge.dimension_results" in prompt
    assert "不得生成 claims" in prompt
    assert "markdown_report 和 json_report" in prompt
    assert "dimension_result_id" in prompt
    assert "evidence_ids" in prompt


def test_llm_writer_ignores_legacy_claim_payload(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(source_type="public_web", url="https://alpha.example.com", competitor="AlphaCI", snippet="AlphaCI AI automation workflow.", confidence=0.9),
        Evidence(source_type="public_web", url="https://beta.example.com", competitor="BetaIntel", snippet="BetaIntel pricing plan for enterprise team.", confidence=0.9),
    ]
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    writer = ReportWriterAgent(
        trace_service,
        llm_client=FakeLlmClient(
            '{"markdown_report":"# LLM Report","json_report":{},"claims":['
            '{"claim_id":"c_overall","competitor":"全竞品","text":"整体结论","category":"risk","evidence_ids":["'
            + evidence[0].evidence_id
            + '"],"confidence":0.6},'
            '{"claim_id":"c_alpha","competitor":"AlphaCI","text":"AlphaCI claim","category":"feature","evidence_ids":["'
            + evidence[0].evidence_id
            + '"],"confidence":0.7},'
            '{"claim_id":"c_beta","competitor":"BetaIntel","text":"BetaIntel claim","category":"pricing","evidence_ids":["'
            + evidence[1].evidence_id
            + '"],"confidence":0.7}]}'
        ),
    )
    output = writer.run(ReportWriterInput(task=task, knowledge=analysis, evidence=evidence, writer_mode="llm"))
    assert output.report is not None
    assert output.report.claims == []
    assert output.report.dimension_results == analysis.dimension_results
    assert "claims" not in output.report.json_report


def test_llm_writer_preserves_analyst_fact_evidence_bindings(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(source_type="public_web", url="https://alpha.example.com", competitor="AlphaCI", snippet="AlphaCI AI automation workflow.", confidence=0.9),
        Evidence(source_type="public_web", url="https://beta.example.com", competitor="BetaIntel", snippet="BetaIntel pricing plan for enterprise team.", confidence=0.9),
    ]
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    writer = ReportWriterAgent(
        trace_service,
        llm_client=FakeLlmClient(
            '{"markdown_report":"# LLM Report","json_report":{},"claims":['
            '{"claim_id":"c_alpha","competitor":"AlphaCI","text":"AlphaCI claim","category":"feature","evidence_ids":["'
            + evidence[1].evidence_id
            + '"],"confidence":0.7},'
            '{"claim_id":"c_beta","competitor":"BetaIntel","text":"BetaIntel claim","category":"pricing","evidence_ids":["'
            + evidence[1].evidence_id
            + '"],"confidence":0.7}]}'
        ),
    )
    output = writer.run(ReportWriterInput(task=task, knowledge=analysis, evidence=evidence, writer_mode="llm"))
    assert output.report is not None
    assert output.report.claims == []
    assert output.report.dimension_results == analysis.dimension_results


def test_collector_mode_mock_workflow_still_passes(db_session):
    task = make_task(db_session)
    result = MockWorkflowRunner(db_session).run(task.task_id, collector_mode="mock")
    assert result["qa_result"].status == "passed"
    collector_trace = next(trace for trace in TraceService(db_session).list_for_task(task.task_id) if trace.agent_name == "CollectorAgent")
    diagnostics = json.loads(collector_trace.output_summary)
    assert diagnostics["collector_mode_requested"] == "mock"
    assert diagnostics["collector_mode_used"] == "mock"


def test_collector_web_without_api_key_falls_back_to_mock(db_session, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    output = CollectorAgent(trace_service).run(CollectorInput(task=task, collector_mode="web"))
    assert output.evidence
    assert output.diagnostics["collector_mode_requested"] == "web"
    assert output.diagnostics["collector_mode_used"] == "mock"
    assert output.diagnostics["fallback_used"] is True
    assert output.diagnostics["fallback_reason"]


def test_tavily_results_convert_to_evidence(db_session, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("SEARCH_API_KEY", "Bearer fake-search-key")
    monkeypatch.setenv("SEARCH_BASE_URL", "https://api.tavily.com")
    monkeypatch.setenv("SEARCH_MAX_RESULTS", "5")

    def fake_post(url, headers, json, timeout):
        assert url == "https://api.tavily.com/search"
        assert headers["Authorization"] == "Bearer fake-search-key"
        assert json["include_raw_content"] is False
        return FakeHttpResponse(
            {
                "results": [
                    {
                            "title": "AlphaCI official pricing",
                            "url": "https://www.alphaci.example/pricing",
                            "content": "AlphaCI official pricing and feature summary for enterprise collaboration.",
                        "score": 0.92,
                    }
                ]
            }
        )

    monkeypatch.setattr("app.services.web_search_client.httpx.post", fake_post)
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    output = CollectorAgent(trace_service, web_search_client=WebSearchClient()).run(
        CollectorInput(task=task, collector_mode="web")
    )
    assert output.diagnostics["collector_mode_used"] == "web"
    assert output.evidence[0].source_type == "public_web"
    assert output.evidence[0].url == "https://www.alphaci.example/pricing"
    assert output.evidence[0].source_domain == "alphaci.example"
    assert output.evidence[0].source_quality == "official"
    assert output.evidence[0].confidence >= 0.85


def test_tavily_empty_results_fallback_to_mock(db_session, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("SEARCH_API_KEY", "fake-search-key")
    monkeypatch.setenv("SEARCH_BASE_URL", "https://api.tavily.com")

    def fake_post(url, headers, json, timeout):
        return FakeHttpResponse({"results": []})

    monkeypatch.setattr("app.services.web_search_client.httpx.post", fake_post)
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    output = CollectorAgent(trace_service, web_search_client=WebSearchClient()).run(
        CollectorInput(task=task, collector_mode="web")
    )
    assert output.diagnostics["collector_mode_used"] == "mock"
    assert output.diagnostics["fallback_used"] is True
    assert "no public results" in output.diagnostics["fallback_reason"]


def test_tavily_401_fallback_to_mock_and_records_reason(db_session, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("SEARCH_API_KEY", "fake-search-key")
    monkeypatch.setenv("SEARCH_BASE_URL", "https://api.tavily.com")

    def fake_post(url, headers, json, timeout):
        return FakeHttpResponse({"detail": "Unauthorized"}, status_code=401)

    monkeypatch.setattr("app.services.web_search_client.httpx.post", fake_post)
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    output = CollectorAgent(trace_service, web_search_client=WebSearchClient()).run(
        CollectorInput(task=task, collector_mode="web")
    )
    assert output.diagnostics["collector_mode_used"] == "mock"
    assert output.diagnostics["fallback_used"] is True
    assert "401" in output.diagnostics["fallback_reason"] or "Client error" in output.diagnostics["fallback_reason"]


def test_search_status_does_not_return_api_key(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("SEARCH_API_KEY", "secret-search-key")
    monkeypatch.setenv("SEARCH_BASE_URL", "https://api.tavily.com")
    status = WebSearchClient().status()
    assert status["search_provider"] == "tavily"
    assert status["api_key_configured"] is True
    assert "secret-search-key" not in json.dumps(status)
    assert status["max_results"] == 5


def test_collector_web_results_convert_to_evidence(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    output = CollectorAgent(trace_service, web_search_client=FakeWebSearchClient()).run(
        CollectorInput(task=task, collector_mode="web")
    )
    assert output.evidence
    assert output.evidence[0].source_type == "public_web"
    assert output.evidence[0].url == "https://alphaci.example.com/pricing"
    assert output.evidence[0].confidence == 0.9
    assert output.evidence[0].entity_match_signals["collector_query"]
    assert output.diagnostics["collector_mode_used"] == "web"
    assert output.diagnostics["web_search_success"] is True


def test_collector_runs_dimension_queries_before_stopping(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)

    class DimensionSearchClient(FakeWebSearchClient):
        def __init__(self):
            super().__init__()
            self.calls_by_competitor = {competitor: 0 for competitor in task.competitors}

        def search(self, query: str, limit: int = 5):
            competitor = next(item for item in task.competitors if item in query)
            self.calls_by_competitor[competitor] += 1
            call_index = self.calls_by_competitor[competitor]
            return WebSearchResponse(
                available=True,
                attempted=True,
                success=True,
                elapsed_time_ms=1,
                results=[
                    SearchResult(
                        title=f"{competitor} dimension source {call_index}-{index}",
                        url=f"https://{competitor.lower()}.example.com/source-{call_index}-{index}",
                        snippet=f"{competitor} public evidence for dimension query {call_index}: pricing feature persona strengths weaknesses opportunities threats.",
                    )
                    for index in range(1, 4)
                ],
            )

    client = DimensionSearchClient()
    output = CollectorAgent(trace_service, web_search_client=client).run(
        CollectorInput(
            task=task,
            collector_mode="web",
            planner_query_hints={competitor: fixed_query_hints_for_competitor(competitor, task.industry) for competitor in task.competitors},
        )
    )
    assert output.diagnostics["web_search_success"] is True
    for competitor in task.competitors:
        assert output.diagnostics["query_count_by_competitor"][competitor] >= len(fixed_dimension_ids())
        assert output.diagnostics["evidence_count_by_competitor"][competitor] == len(fixed_dimension_ids()) * 3
        assert output.diagnostics["evidence_count_by_dimension_by_competitor"][competitor] == {
            dimension_id: 3 for dimension_id in fixed_dimension_ids()
        }


def test_collector_deduplicates_normalized_urls(db_session):
    class DuplicateSearchClient(FakeWebSearchClient):
        def __init__(self):
            super().__init__()
            self.called = False

        def search(self, query: str, limit: int = 5):
            if self.called:
                return WebSearchResponse(available=True, attempted=True, success=True, elapsed_time_ms=1, results=[])
            self.called = True
            return WebSearchResponse(
                available=True,
                attempted=True,
                success=True,
                elapsed_time_ms=1,
                results=[
                    SearchResult(title="AlphaCI pricing", url="https://www.alphaci.example/pricing/?utm_source=x&fbclid=y", snippet="AlphaCI pricing product features official page."),
                    SearchResult(title="AlphaCI pricing copy", url="https://www.alphaci.example/pricing", snippet="AlphaCI pricing product features official page duplicated."),
                ],
            )

    task = make_task(db_session)
    trace_service = TraceService(db_session)
    output = CollectorAgent(trace_service, web_search_client=DuplicateSearchClient()).run(
        CollectorInput(task=task, collector_mode="web")
    )
    assert len(output.evidence) == 1
    assert output.evidence[0].url == "https://www.alphaci.example/pricing"
    assert output.diagnostics["raw_evidence_count"] == 2
    assert output.diagnostics["deduplicated_evidence_count"] == 1
    assert output.diagnostics["duplicate_removed_count"] == 1


def test_collector_records_competitor_coverage(db_session):
    class CoverageSearchClient(FakeWebSearchClient):
        def search(self, query: str, limit: int = 5):
            if "AlphaCI" in query:
                return WebSearchResponse(
                    available=True,
                    attempted=True,
                    success=True,
                    results=[
                        SearchResult(title="AlphaCI official", url="https://alphaci.example/pricing", snippet="AlphaCI pricing product features for enterprise team."),
                        SearchResult(title="AlphaCI docs", url="https://docs.alphaci.example/features", snippet="AlphaCI documentation for AI automation workflow."),
                    ],
                )
            return WebSearchResponse(
                available=True,
                attempted=True,
                success=True,
                results=[
                    SearchResult(title="BetaIntel official", url="https://betaintel.example/pricing", snippet="BetaIntel pricing product features for enterprise team."),
                    SearchResult(title="BetaIntel docs", url="https://docs.betaintel.example/features", snippet="BetaIntel documentation for analytics workflow."),
                ],
            )

    task = make_task(db_session)
    trace_service = TraceService(db_session)
    output = CollectorAgent(trace_service, web_search_client=CoverageSearchClient()).run(
        CollectorInput(task=task, collector_mode="web")
    )
    assert output.diagnostics["competitor_coverage"]["AlphaCI"] >= 2
    assert output.diagnostics["competitor_coverage"]["BetaIntel"] >= 2
    assert output.diagnostics["missing_competitors"] == []


def test_web_collector_generates_queries_and_evidence_per_competitor(db_session):
    class RecordingSearchClient(FakeWebSearchClient):
        def __init__(self):
            super().__init__()
            self.queries = []

        def search(self, query: str, limit: int = 5):
            self.queries.append(query)
            competitor = "AlphaCI" if "AlphaCI" in query else "BetaIntel"
            return WebSearchResponse(
                available=True,
                attempted=True,
                success=True,
                results=[
                    SearchResult(title=f"{competitor} official", url=f"https://{competitor.lower()}.example/pricing", snippet=f"{competitor} official pricing product features enterprise plan."),
                    SearchResult(title=f"{competitor} docs", url=f"https://docs.{competitor.lower()}.example/features", snippet=f"{competitor} documentation shows collaboration workflow API."),
                ],
            )

    task = make_task(db_session)
    client = RecordingSearchClient()
    output = CollectorAgent(TraceService(db_session), web_search_client=client).run(
        CollectorInput(task=task, collector_mode="web")
    )
    assert any("AlphaCI" in query for query in client.queries)
    assert any("BetaIntel" in query for query in client.queries)
    assert {item.competitor for item in output.evidence} == {"AlphaCI", "BetaIntel"}
    assert output.diagnostics["evidence_count_by_competitor"]["AlphaCI"] >= 2
    assert output.diagnostics["evidence_count_by_competitor"]["BetaIntel"] >= 2


def test_analyst_groups_structured_knowledge_by_competitor(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(competitor="AlphaCI", source_type="public_web", url="https://alphaci.example/pricing", source_domain="alphaci.example", source_quality="official", snippet="AlphaCI AI automation pricing enterprise plan for product team.", confidence=0.9),
        Evidence(competitor="AlphaCI", source_type="public_web", url="https://alphaci.example/features", source_domain="alphaci.example", source_quality="official", snippet="AlphaCI collaboration workflow API integration for teams.", confidence=0.9),
        Evidence(competitor="BetaIntel", source_type="public_web", url="https://betaintel.example/pricing", source_domain="betaintel.example", source_quality="official", snippet="BetaIntel pricing subscription enterprise quote.", confidence=0.9),
        Evidence(competitor="BetaIntel", source_type="public_web", url="https://betaintel.example/features", source_domain="betaintel.example", source_quality="official", snippet="BetaIntel analytics security workflow for enterprise team.", confidence=0.9),
    ]
    output = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence, analyst_mode="evidence"))
    competitor_analysis = output.product_profile.custom_dimensions["competitor_analysis"]
    assert set(competitor_analysis) == {"AlphaCI", "BetaIntel"}
    assert all(competitor_analysis[item]["evidence_ids"] for item in task.competitors)
    assert output.diagnostics["evidence_count_by_competitor"] == {"AlphaCI": 2, "BetaIntel": 2}


def test_report_writer_mock_preserves_structured_facts(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = CollectorAgent(trace_service).run(CollectorInput(task=task)).evidence
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    output = ReportWriterAgent(trace_service).run(ReportWriterInput(task=task, knowledge=analysis, evidence=evidence))
    assert output.report is not None
    assert output.report.claims == []
    assert output.report.dimension_results == analysis.dimension_results
    assert output.diagnostics["claims_generated"] is False


def test_qa_detects_missing_competitor_evidence(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(competitor="AlphaCI", source_type="public_web", url="https://alphaci.example/pricing", source_domain="alphaci.example", source_quality="official", snippet="AlphaCI pricing product features.", confidence=0.9)
    ]
    result = QaAgent(trace_service).run(QaInput(task=task, evidence=evidence)).qa_result
    assert result.status == "failed"
    assert result.route_to == "CollectorAgent"
    assert result.rework_instructions[0].failed_schema == "Evidence.relevance"


def test_qa_detects_report_missing_competitor(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(competitor="AlphaCI", source_type="public_web", url="https://alphaci.example/features", source_domain="alphaci.example", source_quality="official", snippet="AlphaCI product features.", confidence=0.9, entity_match_signals={"collector_dimension": "feature"}),
        Evidence(competitor="BetaIntel", source_type="public_web", url="https://betaintel.example/features", source_domain="betaintel.example", source_quality="official", snippet="BetaIntel product features.", confidence=0.9, entity_match_signals={"collector_dimension": "feature"}),
    ]
    base_analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    facts = [
        DimensionResult(dimension_id="feature", competitor="AlphaCI", summary="AlphaCI feature.", evidence_ids=[evidence[0].evidence_id], confidence=0.8),
        DimensionResult(dimension_id="feature", competitor="BetaIntel", summary="BetaIntel feature.", evidence_ids=[evidence[1].evidence_id], confidence=0.8),
    ]
    analysis = base_analysis.model_copy(update={"dimension_results": facts})
    report = Report(
        task_id=task.task_id,
        markdown=f"# Report\nAlphaCI\n{facts[0].dimension_result_id}\n{facts[1].dimension_result_id}",
        json_report={},
        dimension_results=facts,
    )
    result = QaAgent(trace_service).run(
        QaInput(
            task=task,
            evidence=evidence,
            analysis=analysis,
            report_output=ReportWriterOutput(report=report),
            selected_dimensions=["feature"],
        )
    ).qa_result
    assert result.status == "failed"
    assert result.route_to == "ReportWriterAgent"
    assert result.rework_instructions[0].error_type == "report_competitor_gap"


def test_qa_detects_fact_using_other_competitor_evidence(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(competitor="AlphaCI", source_type="public_web", url="https://alphaci.example/features", source_domain="alphaci.example", source_quality="official", snippet="AlphaCI product features.", confidence=0.9, entity_match_signals={"collector_dimension": "feature"}),
        Evidence(competitor="BetaIntel", source_type="public_web", url="https://betaintel.example/features", source_domain="betaintel.example", source_quality="official", snippet="BetaIntel product features.", confidence=0.9, entity_match_signals={"collector_dimension": "feature"}),
    ]
    base_analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    facts = [
        DimensionResult(dimension_id="feature", competitor="AlphaCI", summary="AlphaCI feature.", evidence_ids=[evidence[1].evidence_id], confidence=0.8),
        DimensionResult(dimension_id="feature", competitor="BetaIntel", summary="BetaIntel feature.", evidence_ids=[evidence[1].evidence_id], confidence=0.8),
    ]
    analysis = base_analysis.model_copy(update={"dimension_results": facts})
    result = QaAgent(trace_service).run(
        QaInput(task=task, evidence=evidence, analysis=analysis, selected_dimensions=["feature"])
    ).qa_result
    assert result.status == "failed"
    assert result.route_to == "AnalystAgent"
    assert result.rework_instructions[0].error_type == "fact_competitor_mismatch"


def test_url_tracking_params_are_ignored():
    assert CollectorAgent.normalize_url("https://example.com/path/?utm_source=a&spm=b&x=1") == "https://example.com/path?x=1"


def test_source_domain_extraction():
    assert CollectorAgent.extract_source_domain("https://www.feishu.cn/pricing") == "feishu.cn"
    assert CollectorAgent.extract_source_domain("https://open.dingtalk.com/document") == "dingtalk.com"


def test_source_quality_confidence_rules():
    official_quality = CollectorAgent._source_quality("https://www.feishu.cn/pricing", "pricing", "Official pricing product features content.", ["飞书"])
    unknown_quality = CollectorAgent._source_quality("https://example.net/blog", "blog", "General market content with enough words to evaluate quality.", ["飞书"])
    low_quality = CollectorAgent._source_quality("https://spam.example/click", "x", "short", ["飞书"])
    assert CollectorAgent._confidence_for_result("https://www.feishu.cn/pricing", "Official pricing product features content.", official_quality) > CollectorAgent._confidence_for_result("https://example.net/blog", "General market content with enough words to evaluate quality.", unknown_quality)
    assert CollectorAgent._confidence_for_result("https://spam.example/click", "short", low_quality) < 0.5


def test_relevance_score_high_when_competitor_appears_in_title_or_snippet():
    evidence = Evidence(
        competitor="Feishu",
        source_type="public_web",
        url="https://www.feishu.cn/pricing",
        source_domain="feishu.cn",
        source_quality="official",
        snippet="Feishu pricing and collaboration features for enterprise teams.",
        confidence=0.9,
    )
    result = score_evidence_relevance(evidence, "Feishu", title="Feishu official pricing")
    assert result.relevance_score >= 0.75
    assert result.relevance_level == "high"
    assert result.entity_match_signals["competitor_in_title"] is True


def test_relevance_score_low_when_competitor_never_appears():
    evidence = Evidence(
        competitor="jskad",
        source_type="public_web",
        url="https://www.taxjar.com/pricing",
        source_domain="taxjar.com",
        source_quality="official",
        snippet="TaxJar pricing and tax compliance automation for businesses.",
        confidence=0.9,
    )
    result = score_evidence_relevance(evidence, "jskad", title="TaxJar pricing")
    assert result.relevance_score < 0.25
    assert result.relevance_level == "unrelated"


def test_snippet_only_random_competitor_match_cannot_be_high_relevance():
    evidence = Evidence(
        competitor="djkhaseda",
        source_type="public_web",
        url="https://www.openai.com/business/chatgpt-pricing",
        source_domain="openai.com",
        source_quality="official",
        snippet="djkhaseda is mentioned in a generated search snippet about ChatGPT pricing.",
        confidence=0.9,
    )
    result = score_evidence_relevance(evidence, "djkhaseda", title="ChatGPT Enterprise pricing")
    assert result.relevance_level == "low"
    assert result.relevance_score <= 0.35
    assert result.entity_match_signals["strong_entity_match"] is False


def test_pricing_path_without_competitor_entity_match_is_not_official():
    quality = CollectorAgent._source_quality(
        "https://www.openai.com/business/chatgpt-pricing",
        "ChatGPT Enterprise pricing",
        "djkhaseda appears in a search snippet but the page belongs to OpenAI.",
        ["djkhaseda"],
    )
    assert quality != "official"


def test_known_chinese_competitor_alias_keeps_normal_competitors_relevant():
    evidence = Evidence(
        competitor="飞书",
        source_type="public_web",
        url="https://www.larksuite.com/pricing",
        source_domain="larksuite.com",
        source_quality="official",
        snippet="Lark pricing and collaboration features for business teams.",
        confidence=0.9,
    )
    result = score_evidence_relevance(evidence, "飞书", title="Lark official pricing")
    assert result.relevance_level in {"high", "medium"}
    assert result.entity_match_signals["competitor_alias_matched"] is True


def test_collector_filters_unrelated_evidence(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)

    class UnrelatedSearchClient(FakeWebSearchClient):
        def search(self, query: str, limit: int = 5):
            return WebSearchResponse(
                available=True,
                attempted=True,
                success=True,
                elapsed_time_ms=10,
                results=[
                    SearchResult(title="TaxJar pricing", url="https://www.taxjar.com/pricing", snippet="TaxJar sales tax pricing and automation."),
                    SearchResult(title="Random docs", url="https://docs.other.example/product", snippet="Other product documentation."),
                ],
            )

    output = CollectorAgent(trace_service, web_search_client=UnrelatedSearchClient()).run(
        CollectorInput(task=task, collector_mode="web")
    )
    assert output.evidence
    assert all(item.relevance_level == "unrelated" for item in output.evidence)
    assert output.diagnostics["filtered_unrelated_count"] > 0
    assert set(output.diagnostics["missing_relevant_evidence_competitors"]) == set(task.competitors)


def test_analyst_does_not_use_unrelated_evidence(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    unrelated = apply_relevance(
        Evidence(
            competitor="AlphaCI",
            source_type="public_web",
            url="https://www.taxjar.com/pricing",
            source_domain="taxjar.com",
            source_quality="official",
            snippet="TaxJar pricing and tax automation.",
            confidence=0.9,
        ),
        "AlphaCI",
        title="TaxJar pricing",
    )
    output = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=[unrelated], analyst_mode="evidence"))
    assert output.diagnostics["insufficient_relevant_evidence"] is True
    assert "Evidence is insufficient" in output.product_profile.positioning


def test_qa_detects_unrelated_evidence_used_by_structured_fact(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    unrelated = apply_relevance(
        Evidence(
            competitor="AlphaCI",
            source_type="public_web",
            url="https://www.taxjar.com/pricing",
            source_domain="taxjar.com",
            source_quality="official",
            snippet="TaxJar pricing and tax automation.",
            confidence=0.9,
        ),
        "AlphaCI",
        title="TaxJar pricing",
    )
    # Add one relevant evidence per competitor so structured-fact validation is reached.
    relevant_alpha = apply_relevance(
        Evidence(
            competitor="AlphaCI",
            source_type="public_web",
            url="https://www.alphaci.example/pricing",
            source_domain="alphaci.example",
            source_quality="official",
            snippet="AlphaCI pricing product features.",
            confidence=0.9,
        ),
        "AlphaCI",
        title="AlphaCI pricing",
    )
    relevant_beta = apply_relevance(
        Evidence(
            competitor="BetaIntel",
            source_type="public_web",
            url="https://www.betaintel.example/pricing",
            source_domain="betaintel.example",
            source_quality="official",
            snippet="BetaIntel pricing product features.",
            confidence=0.9,
        ),
        "BetaIntel",
        title="BetaIntel pricing",
    )
    evidence = [unrelated, relevant_alpha, relevant_beta]
    base_analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence, analyst_mode="mock"))
    facts = [
        DimensionResult(dimension_id="feature", competitor="AlphaCI", summary="Unsupported AlphaCI fact.", evidence_ids=[unrelated.evidence_id], confidence=0.8),
        DimensionResult(dimension_id="feature", competitor="BetaIntel", summary="BetaIntel fact.", evidence_ids=[relevant_beta.evidence_id], confidence=0.8),
    ]
    analysis = base_analysis.model_copy(update={"dimension_results": facts})
    result = QaAgent(trace_service).run(
        QaInput(task=task, evidence=evidence, analysis=analysis, selected_dimensions=["feature"])
    ).qa_result
    assert result.status == "failed"
    assert result.route_to == "AnalystAgent"
    assert result.rework_instructions[0].error_type == "fact_evidence_not_found"


def test_report_writer_preserves_analyst_insufficient_fact_for_unrelated_evidence(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    unrelated = apply_relevance(
        Evidence(
            competitor="AlphaCI",
            source_type="public_web",
            url="https://www.taxjar.com/pricing",
            source_domain="taxjar.com",
            source_quality="official",
            snippet="TaxJar pricing and tax automation.",
            confidence=0.9,
        ),
        "AlphaCI",
        title="TaxJar pricing",
    )
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=[unrelated], analyst_mode="evidence"))
    output = ReportWriterAgent(trace_service).run(ReportWriterInput(task=task, knowledge=analysis, evidence=[unrelated]))
    assert output.report is not None
    assert output.report.claims == []
    assert output.report.dimension_results == analysis.dimension_results
    assert all(unrelated.evidence_id not in fact.evidence_ids for fact in output.report.dimension_results)


def test_random_competitors_do_not_generate_strong_conclusions(db_session):
    task = make_custom_task(db_session)
    trace_service = TraceService(db_session)

    class RandomUnrelatedSearchClient(FakeWebSearchClient):
        def search(self, query: str, limit: int = 5):
            return WebSearchResponse(
                available=True,
                attempted=True,
                success=True,
                elapsed_time_ms=10,
                results=[
                    SearchResult(title="TaxJar pricing", url="https://www.taxjar.com/pricing", snippet="TaxJar sales tax pricing and automation for small businesses."),
                    SearchResult(title="Beauty retail guide", url="https://media.example.com/beauty", snippet="General beauty retail market overview."),
                ],
            )

    collected = CollectorAgent(trace_service, web_search_client=RandomUnrelatedSearchClient()).run(
        CollectorInput(task=task, collector_mode="web")
    )
    assert collected.evidence
    assert all(item.relevance_level == "unrelated" for item in collected.evidence)
    assert set(collected.diagnostics["missing_relevant_evidence_competitors"]) == {"jskad", "sda", "dsja"}

    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=collected.evidence, analyst_mode="evidence"))
    assert "Evidence is insufficient" in analysis.product_profile.positioning

    report_output = ReportWriterAgent(trace_service).run(ReportWriterInput(task=task, knowledge=analysis, evidence=collected.evidence))
    qa_result = QaAgent(trace_service).run(
        QaInput(task=task, evidence=collected.evidence, analysis=analysis, report_output=report_output)
    ).qa_result
    assert qa_result.status == "failed"
    assert qa_result.route_to == "CollectorAgent"
    assert qa_result.rework_instructions[0].error_type == "missing_relevant_evidence"


def test_snippet_evidence_generates_soft_suggestion(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = [
        Evidence(competitor="AlphaCI", source_type="public_web", url="https://alpha.example/features", source_domain="alpha.example", source_quality="official", snippet="AlphaCI features.", confidence=0.8, entity_match_signals={"collector_dimension": "feature"}),
        Evidence(competitor="BetaIntel", source_type="public_web", url="https://beta.example/features", source_domain="beta.example", source_quality="official", snippet="BetaIntel features.", confidence=0.8, entity_match_signals={"collector_dimension": "feature"}),
    ]
    base_analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    facts = [
        DimensionResult(dimension_id="feature", competitor="AlphaCI", summary="AlphaCI feature.", evidence_ids=[evidence[0].evidence_id], confidence=0.8),
        DimensionResult(dimension_id="feature", competitor="BetaIntel", summary="BetaIntel feature.", evidence_ids=[evidence[1].evidence_id], confidence=0.8),
    ]
    analysis = base_analysis.model_copy(update={"dimension_results": facts})
    report = Report(
        task_id=task.task_id,
        markdown=f"# Report\nAlphaCI {facts[0].dimension_result_id}\nBetaIntel {facts[1].dimension_result_id}",
        json_report={},
        dimension_results=facts,
    )
    qa_result = QaAgent(trace_service).run(
        QaInput(
            task=task,
            evidence=evidence,
            analysis=analysis,
            report_output=ReportWriterOutput(report=report),
            selected_dimensions=["feature"],
        )
    ).qa_result
    assert qa_result.status == "passed"
    assert any("搜索摘要" in item for item in qa_result.soft_suggestions)


def test_collector_web_timeout_does_not_crash_workflow(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    output = CollectorAgent(trace_service, web_search_client=FakeWebSearchClient(fail=True)).run(
        CollectorInput(task=task, collector_mode="web")
    )
    assert output.evidence
    assert output.diagnostics["collector_mode_used"] == "mock"
    assert output.diagnostics["fallback_used"] is True
    assert "timeout" in output.diagnostics["fallback_reason"]


def test_collector_trace_records_mode_and_fallback_reason(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    CollectorAgent(trace_service, web_search_client=FakeWebSearchClient(fail=True)).run(
        CollectorInput(task=task, collector_mode="web")
    )
    collector_trace = next(trace for trace in trace_service.list_for_task(task.task_id) if trace.agent_name == "CollectorAgent")
    diagnostics = json.loads(collector_trace.output_summary)
    assert diagnostics["collector_mode_requested"] == "web"
    assert diagnostics["collector_mode_used"] == "mock"
    assert diagnostics["fallback_reason"]


def test_collector_mode_web_and_writer_mode_llm_parameters_both_pass(db_session, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    task = make_task(db_session)
    runner = MockWorkflowRunner(db_session)
    runner.collector = CollectorAgent(runner.trace_service, web_search_client=FakeWebSearchClient())
    result = runner.run(task.task_id, collector_mode="web", writer_mode="llm")
    assert result["qa_result"].status == "passed"
    traces = TraceService(db_session).list_for_task(task.task_id)
    collector_trace = next(trace for trace in traces if trace.agent_name == "CollectorAgent")
    writer_trace = next(trace for trace in traces if trace.agent_name == "ReportWriterAgent")
    collector_diagnostics = json.loads(collector_trace.output_summary)
    writer_diagnostics = json.loads(writer_trace.output_summary)
    assert collector_diagnostics["collector_mode_requested"] == "web"
    assert writer_diagnostics["writer_mode_requested"] == "llm"


def test_llm_report_missing_competitor_routes_to_report_writer(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = CollectorAgent(trace_service).run(CollectorInput(task=task)).evidence
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    writer = ReportWriterAgent(
        trace_service,
        llm_client=FakeLlmClient(
            '{"markdown_report":"# Bad\\nOnly AlphaCI is covered.","json_report":{}}'
        ),
    )
    writer_output = writer.run(ReportWriterInput(task=task, knowledge=analysis, evidence=evidence, writer_mode="llm"))
    qa_result = QaAgent(trace_service).run(
        QaInput(task=task, evidence=evidence, analysis=analysis, report_output=writer_output)
    ).qa_result
    assert qa_result.status == "failed"
    assert qa_result.route_to == "ReportWriterAgent"
    assert qa_result.rework_instructions[0].error_type == "report_competitor_gap"


def test_llm_report_schema_failure_falls_back_to_mock(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = CollectorAgent(trace_service).run(CollectorInput(task=task)).evidence
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    writer = ReportWriterAgent(
        trace_service,
        llm_client=FakeLlmClient(
            '{"markdown_report":"# Bad"}'
        ),
    )
    writer_output = writer.run(ReportWriterInput(task=task, knowledge=analysis, evidence=evidence, writer_mode="llm"))
    assert writer_output.report is not None
    assert writer_output.writer_mode == "mock"
    assert writer_output.report.claims == []
    traces = trace_service.list_for_task(task.task_id)
    failed_trace = next(trace for trace in traces if trace.agent_name == "ReportWriterAgent" and trace.schema_validation_result == "failed")
    trace_diagnostics = json.loads(failed_trace.output_summary)
    assert trace_diagnostics["llm_schema_validation_success"] is False
    assert trace_diagnostics["llm_schema_validation_errors"]


def test_mock_writer_schema_diagnostics_are_null(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = CollectorAgent(trace_service).run(CollectorInput(task=task)).evidence
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    writer_output = ReportWriterAgent(trace_service).run(ReportWriterInput(task=task, knowledge=analysis, evidence=evidence))
    assert writer_output.report is not None
    diagnostics = writer_output.report.json_report["writer_diagnostics"]
    assert diagnostics["writer_mode_used"] == "mock"
    assert diagnostics["llm_schema_validation_success"] is None


def test_llm_invalid_json_falls_back_to_mock_and_records_trace_error(db_session):
    task = make_task(db_session)
    trace_service = TraceService(db_session)
    evidence = CollectorAgent(trace_service).run(CollectorInput(task=task)).evidence
    analysis = AnalystAgent(trace_service).run(AnalystInput(task=task, evidence=evidence))
    writer = ReportWriterAgent(trace_service, llm_client=FakeLlmClient("not valid json"))
    writer_output = writer.run(ReportWriterInput(task=task, knowledge=analysis, evidence=evidence, writer_mode="llm"))
    assert writer_output.report is not None
    assert writer_output.report.json_report["writer_mode"] == "mock"
    traces = trace_service.list_for_task(task.task_id)
    assert any(trace.schema_validation_result == "failed" and trace.error_message for trace in traces)
    assert any("LLM returned invalid JSON" in (trace.error_message or "") for trace in traces)


def test_latest_report_for_task_uses_latest_saved_row(db_session):
    task = make_task(db_session)
    service = ReportService(db_session)
    claim = Claim(text="supported", category="feature", evidence_ids=["ev_1"], confidence=0.9)
    old_report = Report(task_id=task.task_id, markdown="# Old", json_report={"version": "old"}, claims=[claim], qa_result=QaResult(task_id=task.task_id, status="passed"))
    new_report = Report(task_id=task.task_id, markdown="# New", json_report={"version": "new"}, claims=[claim], qa_result=QaResult(task_id=task.task_id, status="passed"))
    service.save_report(old_report)
    service.save_report(new_report)
    latest = service.get_latest_for_task(task.task_id)
    assert latest.report_id == new_report.report_id
    assert latest.json_report["version"] == "new"


@pytest.mark.parametrize(
    ("demo_mode", "expected_route"),
    [
        ("qa_missing_evidence", "CollectorAgent"),
        ("qa_invalid_extraction", "AnalystAgent"),
        ("qa_bad_report", "ReportWriterAgent"),
    ],
)
def test_auto_rework_routes_and_then_passes(db_session, demo_mode, expected_route):
    task = make_task(db_session)
    result = MockWorkflowRunner(db_session).run(task.task_id, demo_mode=demo_mode, auto_rework=True)
    qa_result = result["qa_result"]
    assert qa_result.status == "passed"
    assert qa_result.rework_count == 1
    assert qa_result.rework_history
    assert qa_result.rework_history[0].route_to == expected_route
    assert qa_result.rework_history[0].result_status == "passed"
    assert qa_result.rework_history[0].reason
    assert qa_result.rework_history[0].failed_schema
    assert result["report"] is not None

    traces = TraceService(db_session).list_for_task(task.task_id)
    agent_names = [trace.agent_name for trace in traces]
    assert "QaAgent" in agent_names
    assert agent_names.count("QaAgent") >= 2
    assert expected_route in agent_names


def test_auto_rework_respects_manual_review_limit(db_session):
    task = make_task(db_session)
    TaskService(db_session).update_status(task.task_id, "running", rework_count=3)
    task = TaskService(db_session).get_task(task.task_id)
    qa = QaAgent(TraceService(db_session))
    result = qa.run(QaInput(task=task, evidence=[], demo_mode="qa_missing_evidence")).qa_result
    assert result.status == "manual_review"
    assert result.route_to is None


def test_workflow_engine_resolve_priority(monkeypatch):
    monkeypatch.delenv("WORKFLOW_ENGINE", raising=False)
    assert resolve_workflow_engine(None) == "custom"
    monkeypatch.setenv("WORKFLOW_ENGINE", "langgraph")
    assert resolve_workflow_engine(None) == "langgraph"
    assert resolve_workflow_engine("custom") == "custom"
    assert resolve_workflow_engine("langgraph") == "langgraph"


def test_langgraph_normal_workflow_passes_and_finalizes(db_session):
    task = make_task(db_session)
    result = LangGraphWorkflowRunner(db_session).run(task.task_id, workflow_engine_requested="langgraph")
    assert result["qa_result"].status == "passed"
    assert result["report"] is not None
    summary = result["workflow_summary"]
    assert summary["workflow_engine_used"] == "langgraph"
    assert set(fixed_dimension_ids()).issubset(summary["selected_dimensions"])
    for competitor in task.competitors:
        assert result["plan"].analysis_dimension_plan.query_hints[competitor]
    assert "evidence_gate" in summary["node_sequence"]
    assert summary["evidence_gate_output"]["evidence_gate_passed"] is True
    assert summary["node_sequence"][-1] == "final_report"
    traces = TraceService(db_session).list_for_task(task.task_id)
    assert {trace.agent_name for trace in traces} >= {"PlannerAgent", "CollectorAgent", "EvidenceGate", "PageFetcher", "AnalystAgent", "ReportWriterAgent", "QaAgent", "FinalReport", "WorkflowEngine"}


def test_langgraph_page_fetcher_trace_sequence_and_run_id(db_session):
    task = make_task(db_session)

    class FakePageFetcher:
        def enrich(self, evidence, *, run_id=None, enabled=True):
            enriched = [
                item.model_copy(
                    update={
                        "run_id": run_id,
                        "content_mode": "page",
                        "page_fetch_success": True,
                        "content_excerpt": f"{item.competitor} AI automation pricing enterprise team content.",
                        "content_chars": 64,
                    }
                )
                for item in evidence
            ]
            return enriched, {
                "page_fetch_attempted": True,
                "page_fetch_success_count": len(enriched),
                "page_fetch_failed_count": 0,
                "page_fetch_skipped_count": 0,
                "page_fetch_fallback_count": 0,
                "fetched_evidence_ids": [item.evidence_id for item in enriched],
                "skipped_evidence_ids": [],
                "run_id": run_id,
            }

    runner = LangGraphWorkflowRunner(db_session)
    runner.page_fetcher = FakePageFetcher()
    result = runner.run(task.task_id, workflow_engine_requested="langgraph", content_mode="page")
    assert "page_fetcher" in result["workflow_summary"]["node_sequence"]
    assert result["workflow_summary"]["page_fetch_output"]["page_fetch_success_count"] > 0
    evidence = EvidenceService(db_session).list_for_task(task.task_id, run_id=result["run_id"])
    assert evidence and all(item.run_id == result["run_id"] for item in evidence)
    assert any(item.content_mode == "page" for item in evidence)
    trace = next(item for item in TraceService(db_session).list_for_task(task.task_id, run_id=result["run_id"]) if item.agent_name == "PageFetcher")
    assert json.loads(trace.output_summary)["run_id"] == result["run_id"]


def test_langgraph_run_cleanup_prevents_old_evidence_and_report_mix(db_session):
    task = make_task(db_session)
    first = LangGraphWorkflowRunner(db_session).run(task.task_id, workflow_engine_requested="langgraph")
    assert first["report"] is not None
    assert EvidenceService(db_session).list_for_task(task.task_id)

    class UnrelatedSearchClient(FakeWebSearchClient):
        def search(self, query: str, limit: int = 5):
            return WebSearchResponse(
                available=True,
                attempted=True,
                success=True,
                elapsed_time_ms=1,
                results=[
                    SearchResult(title="TaxJar pricing", url="https://www.taxjar.com/pricing", snippet="TaxJar sales tax pricing."),
                ],
            )

    runner = LangGraphWorkflowRunner(db_session)
    runner.collector = CollectorAgent(runner.trace_service, web_search_client=UnrelatedSearchClient())
    second = runner.run(task.task_id, collector_mode="web", workflow_engine_requested="langgraph")
    assert second["report"] is None
    latest_evidence = EvidenceService(db_session).list_for_task(task.task_id)
    assert latest_evidence
    assert all(item.relevance_level == "unrelated" for item in latest_evidence)
    with pytest.raises(KeyError):
        ReportService(db_session).get_latest_for_task(task.task_id)


def test_langgraph_evidence_gate_blocks_random_competitors_before_report_writer(db_session):
    task = make_custom_task(db_session, competitors=["xqzvra", "lmptuo"])

    class UnrelatedSearchClient(FakeWebSearchClient):
        def search(self, query: str, limit: int = 5):
            return WebSearchResponse(
                available=True,
                attempted=True,
                success=True,
                elapsed_time_ms=1,
                results=[
                    SearchResult(title="Unrelated pricing", url="https://www.taxjar.com/pricing", snippet="TaxJar pricing page."),
                ],
            )

    runner = LangGraphWorkflowRunner(db_session)
    runner.collector = CollectorAgent(runner.trace_service, web_search_client=UnrelatedSearchClient())
    result = runner.run(task.task_id, collector_mode="web", workflow_engine_requested="langgraph")
    assert result["report"] is None
    assert result["qa_result"].status == "failed"
    assert result["qa_result"].rework_instructions[0].error_type == "missing_relevant_evidence"
    assert result["workflow_summary"]["final_status"] == "insufficient_evidence"
    assert result["workflow_summary"]["node_sequence"] == ["planner", "collector", "evidence_gate", "final_report"]
    assert result["workflow_summary"]["evidence_gate_output"]["evidence_gate_passed"] is False
    gate_output = result["workflow_summary"]["evidence_gate_output"]
    assert gate_output["failure_explanation"]
    assert gate_output["evidence_diagnostics_by_competitor"]["xqzvra"]["total_evidence_count"] > 0
    assert gate_output["evidence_diagnostics_by_competitor"]["xqzvra"]["reason_code"] in {
        "alias_or_entity_not_matched",
        "only_low_or_unrelated_evidence",
    }
    assert result["qa_result"].rework_instructions[0].metadata["evidence_gate_details"]["by_competitor"]["xqzvra"]["top_evidence"]
    assert "report_writer" not in result["workflow_summary"]["node_sequence"]


def test_langgraph_evidence_gate_auto_rework_routes_to_collector(db_session):
    task = make_custom_task(db_session, competitors=["xqzvra", "lmptuo"])

    class UnrelatedSearchClient(FakeWebSearchClient):
        def search(self, query: str, limit: int = 5):
            return WebSearchResponse(
                available=True,
                attempted=True,
                success=True,
                elapsed_time_ms=1,
                results=[
                    SearchResult(title="Unrelated pricing", url="https://www.taxjar.com/pricing", snippet="TaxJar pricing page."),
                ],
            )

    runner = LangGraphWorkflowRunner(db_session)
    runner.collector = CollectorAgent(runner.trace_service, web_search_client=UnrelatedSearchClient())
    result = runner.run(task.task_id, collector_mode="web", auto_rework=True, workflow_engine_requested="langgraph")
    routes = result["workflow_summary"]["conditional_routes_taken"]
    assert any(route["from_node"] == "evidence_gate" and route["to_node"] == "collector" for route in routes)
    assert result["workflow_summary"]["final_status"] == "manual_review"


@pytest.mark.parametrize(
    ("demo_mode", "expected_node"),
    [
        ("qa_missing_evidence", "collector"),
        ("qa_invalid_extraction", "analyst"),
        ("qa_bad_report", "report_writer"),
    ],
)
def test_langgraph_auto_rework_conditional_routes(db_session, demo_mode, expected_node):
    task = make_task(db_session)
    result = LangGraphWorkflowRunner(db_session).run(task.task_id, demo_mode=demo_mode, auto_rework=True)
    assert result["qa_result"].status == "passed"
    routes = result["workflow_summary"]["conditional_routes_taken"]
    assert routes
    assert routes[0]["to_node"] == expected_node
    assert result["workflow_summary"]["rework_count"] == 1


def test_langgraph_without_auto_rework_stops_on_qa_failure(db_session):
    task = make_task(db_session)
    result = LangGraphWorkflowRunner(db_session).run(task.task_id, demo_mode="qa_bad_report", auto_rework=False)
    assert result["qa_result"].status == "failed"
    assert result["report"] is None
    assert result["workflow_summary"]["conditional_routes_taken"] == []
    assert result["workflow_summary"]["final_status"] == "qa_failed"


def test_langgraph_max_rework_enters_manual_review_without_loop(db_session):
    task = make_task(db_session)
    TaskService(db_session).update_status(task.task_id, "running", rework_count=3)
    task = TaskService(db_session).get_task(task.task_id)
    runner = LangGraphWorkflowRunner(db_session)
    state = {
        "task_id": task.task_id,
        "task": task,
        "workflow_engine_requested": "langgraph",
        "workflow_engine_used": "langgraph",
        "demo_mode": "qa_missing_evidence",
        "collector_mode": "mock",
        "analyst_mode": "evidence",
        "writer_mode": "mock",
        "content_mode": None,
        "auto_rework": True,
        "rework_count": 3,
        "max_rework": 3,
        "evidence": [],
        "report": None,
        "qa_result": None,
        "route_to": None,
        "final_status": None,
        "errors": [],
        "node_sequence": [],
        "conditional_routes_taken": [],
        "workflow_summary": {},
    }
    output = runner.qa_node(state)
    assert output["qa_result"].status == "manual_review"
    assert runner.route_after_qa(output) == "final_report"


def test_langgraph_unknown_route_enters_manual_review_and_records_route(db_session):
    task = make_task(db_session)
    runner = LangGraphWorkflowRunner(db_session)

    class UnknownRouteQa:
        def run(self, input_data):
            result = QaResult(
                task_id=input_data.task.task_id,
                status="failed",
                hard_errors=["Unknown route demo"],
                route_to="CollectorAgent",
                rework_count=1,
                rework_instructions=[
                    ReworkInstruction(
                        target_agent="CollectorAgent",
                        error_type="missing_evidence",
                        reason="Unknown route demo",
                        suggested_action="Manual review required.",
                    )
                ],
            )
            object.__setattr__(result, "route_to", "UnknownAgent")
            return QaOutput(
                qa_result=result
            )

    runner.qa = UnknownRouteQa()
    state = {
        "task_id": task.task_id,
        "task": task,
        "workflow_engine_requested": "langgraph",
        "workflow_engine_used": "langgraph",
        "demo_mode": "normal",
        "collector_mode": "mock",
        "analyst_mode": "evidence",
        "writer_mode": "mock",
        "content_mode": None,
        "auto_rework": True,
        "rework_count": 0,
        "max_rework": 3,
        "evidence": [],
        "report": None,
        "qa_result": None,
        "route_to": None,
        "final_status": None,
        "errors": [],
        "node_sequence": [],
        "conditional_routes_taken": [],
        "workflow_summary": {},
    }
    output = runner.qa_node(state)
    assert output["final_status"] == "manual_review"
    assert output["conditional_routes_taken"][0]["reason"] == "unknown_route"
    assert output["conditional_routes_taken"][0]["to_node"] == "final_report"
    assert output["conditional_routes_taken"][0]["final_status"] == "manual_review"
    assert runner.route_after_qa(output) == "final_report"
    final_state = runner.final_report_node(output)
    assert final_state["final_status"] == "manual_review"


def test_langgraph_workflow_trace_contains_recoverable_summary_fields(db_session):
    task = make_task(db_session)
    result = LangGraphWorkflowRunner(db_session).run(task.task_id, workflow_engine_requested="langgraph")
    trace = next(trace for trace in TraceService(db_session).list_for_task(task.task_id) if trace.agent_name == "WorkflowEngine")
    summary = json.loads(trace.output_summary)
    for key in [
        "workflow_engine_requested",
        "workflow_engine_used",
        "node_sequence",
        "conditional_routes_taken",
        "rework_count",
        "final_status",
        "elapsed_time_ms",
    ]:
        assert key in summary
    assert summary["workflow_engine_used"] == result["workflow_summary"]["workflow_engine_used"]


def test_langgraph_conditional_routes_support_frontend_rework_history(db_session):
    task = make_task(db_session)
    result = LangGraphWorkflowRunner(db_session).run(task.task_id, demo_mode="qa_bad_report", auto_rework=True)
    routes = result["workflow_summary"]["conditional_routes_taken"]
    assert routes
    assert routes[0]["from_node"] == "qa"
    assert routes[0]["to_node"] == "report_writer"
    assert routes[0]["reason"] == "bad_report_format"
    assert result["qa_result"].rework_history
    assert result["qa_result"].rework_history[0].reason
    assert result["qa_result"].rework_history[0].failed_schema


def test_langgraph_modes_and_competitor_coverage_still_work(db_session, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    task = make_task(db_session)
    result = LangGraphWorkflowRunner(db_session).run(
        task.task_id,
        collector_mode="mock",
        analyst_mode="evidence",
        writer_mode="llm",
        workflow_engine_requested="langgraph",
    )
    assert result["qa_result"].status == "passed"
    assert result["report"] is not None
    assert result["report"].claims == []
    assert {fact.competitor for fact in result["report"].dimension_results} == set(task.competitors)
    writer_trace = next(trace for trace in TraceService(db_session).list_for_task(task.task_id) if trace.agent_name == "ReportWriterAgent")
    writer_diagnostics = json.loads(writer_trace.output_summary)
    assert writer_diagnostics["writer_mode_requested"] == "llm"


def test_langgraph_executes_structured_fact_qa(db_session, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    task = make_task(db_session)
    result = LangGraphWorkflowRunner(db_session).run(task.task_id, workflow_engine_requested="langgraph")

    assert result["report"] is not None, {
        "qa_status": result["qa_result"].status,
        "route_to": result["qa_result"].route_to,
        "hard_errors": result["qa_result"].hard_errors,
        "rework_history": [item.model_dump(mode="json") for item in result["qa_result"].rework_history],
        "workflow_summary": result["workflow_summary"],
    }
    assert result["qa_result"].status == "passed"
    assert result["qa_result"].metadata["qa_contract"] == "planner_evidence_structured_fact_report"
    assert result["qa_result"].metadata["claims_checked"] is False
    assert "qa" in result["workflow_summary"]["node_sequence"]
    assert result["workflow_summary"]["node_sequence"][-1] == "final_report"


def test_langgraph_injects_retrieved_knowledge_as_current_run_evidence(db_session):
    task = make_task(db_session)
    runner = LangGraphWorkflowRunner(db_session)
    current_evidence = [
        Evidence(
            evidence_id="ev_current_alpha",
            run_id="run_test",
            source_type="public_web",
            url="https://alpha.example.com/current",
            competitor="AlphaCI",
            snippet="AlphaCI current public evidence.",
            confidence=0.9,
            relevance_level="high",
        )
    ]
    chunk = RetrievedKnowledgeChunk(
        chunk_id="kb_chunk_alpha001",
        text="AlphaCI long-term knowledge supports AI workflow automation.",
        text_preview="AlphaCI long-term knowledge supports AI workflow automation.",
        score=0.86,
        evidence_id="ev_old_alpha",
        source_url="https://alpha.example.com/old",
        source_domain="alpha.example.com",
        source_quality="official",
        metadata={
            "competitor": "AlphaCI",
            "evidence_id": "ev_old_alpha",
            "confidence": 0.8,
            "source_url": "https://alpha.example.com/old",
        },
    )

    output = runner._inject_knowledge_evidence(task, "run_test", current_evidence, [chunk])
    knowledge_evidence = [item for item in output if item.source_type == "knowledge_base"]

    assert len(knowledge_evidence) == 1
    assert knowledge_evidence[0].evidence_id.startswith("ev_kb_")
    assert knowledge_evidence[0].run_id == "run_test"
    assert knowledge_evidence[0].entity_match_signals["original_evidence_id"] == "ev_old_alpha"
    saved = EvidenceService(db_session).list_for_task(task.task_id, run_id="run_test")
    assert {item.evidence_id for item in saved} == {knowledge_evidence[0].evidence_id}


def test_task_run_created_for_each_langgraph_run(db_session):
    task = make_task(db_session)
    first = LangGraphWorkflowRunner(db_session).run(task.task_id, workflow_engine_requested="langgraph")
    second = LangGraphWorkflowRunner(db_session).run(task.task_id, workflow_engine_requested="langgraph")
    runs = TaskRunService(db_session).list_for_task(task.task_id)
    assert len(runs) == 2
    assert first["run_id"] != second["run_id"]
    assert {run.run_id for run in runs} == {first["run_id"], second["run_id"]}


def test_run_id_binds_evidence_report_qa_and_trace(db_session):
    task = make_task(db_session)
    result = LangGraphWorkflowRunner(db_session).run(task.task_id, workflow_engine_requested="langgraph")
    run_id = result["run_id"]
    evidence = EvidenceService(db_session).list_for_task(task.task_id, run_id=run_id)
    report = ReportService(db_session).get_for_task_run(task.task_id, run_id)
    qa_result = ReportService(db_session).qa_for_task_run(task.task_id, run_id)
    traces = TraceService(db_session).list_for_task(task.task_id, run_id=run_id)
    assert evidence and all(item.run_id == run_id for item in evidence)
    assert report.run_id == run_id
    assert qa_result.run_id == run_id
    assert traces and all(trace.run_id == run_id for trace in traces)
    workflow_trace = next(trace for trace in traces if trace.agent_name == "WorkflowEngine")
    assert json.loads(workflow_trace.output_summary)["run_id"] == run_id


def test_multiple_runs_do_not_mix_evidence_or_latest_report(db_session):
    task = make_task(db_session)
    first = LangGraphWorkflowRunner(db_session).run(task.task_id, workflow_engine_requested="langgraph")
    first_run_id = first["run_id"]
    first_evidence_ids = {item.evidence_id for item in EvidenceService(db_session).list_for_task(task.task_id, run_id=first_run_id)}

    second = LangGraphWorkflowRunner(db_session).run(task.task_id, workflow_engine_requested="langgraph")
    second_run_id = second["run_id"]
    second_evidence = EvidenceService(db_session).list_for_task(task.task_id, run_id=second_run_id)
    assert second_run_id != first_run_id
    assert second_evidence and all(item.run_id == second_run_id for item in second_evidence)
    assert not first_evidence_ids.intersection({item.evidence_id for item in second_evidence})
    assert ReportService(db_session).get_latest_for_task(task.task_id).run_id == second_run_id
    assert EvidenceService(db_session).list_for_task(task.task_id)[0].run_id == second_run_id


def test_custom_runner_creates_run_and_old_chain_still_runs(db_session):
    task = make_task(db_session)
    run = TaskRunService(db_session).create_run(
        task_id=task.task_id,
        workflow_engine="custom",
        collector_mode="mock",
        analyst_mode="evidence",
        writer_mode="mock",
        content_mode=None,
        demo_mode="normal",
        auto_rework=False,
    )
    result = MockWorkflowRunner(db_session).run(task.task_id, run_id=run.run_id)
    finished = TaskRunService(db_session).finish_run(
        run.run_id,
        status="completed",
        final_status=result["qa_result"].status,
        elapsed_time_ms=0,
    )
    assert result["qa_result"].status == "passed"
    assert finished.run_id == run.run_id
    assert EvidenceService(db_session).list_for_task(task.task_id, run_id=run.run_id)


def test_entity_resolver_rule_aliases_for_chinese_apple_product(db_session):
    task = make_custom_task(
        db_session,
        product_name="\u667a\u80fd\u624b\u673a\u7ade\u54c1\u5206\u6790",
        competitors=["\u82f9\u679c17pro"],
        industry="\u667a\u80fd\u624b\u673a",
    )

    resolved = EntityResolverService().resolve_for_task(task)
    aliases = {alias.lower().replace(" ", "") for alias in resolved["\u82f9\u679c17pro"]["aliases"]}

    assert "iphone17pro" in aliases
    assert "appleiphone17pro" in aliases


def test_relevance_scoring_uses_resolved_aliases_for_chinese_product_name():
    competitor = "\u82f9\u679c17pro"
    aliases = EntityResolverService()._rule_result(competitor).aliases
    evidence = Evidence(
        competitor=competitor,
        source_type="public_web",
        url="https://www.apple.com/iphone-17-pro/",
        source_domain="apple.com",
        source_quality="official",
        snippet="Apple iPhone 17 Pro official page introduces camera, display, and performance capabilities.",
        confidence=0.9,
    )

    scored = apply_relevance(evidence, competitor, title="Apple iPhone 17 Pro", aliases=aliases)

    assert scored.relevance_level in {"high", "medium"}
    assert scored.entity_match_signals["competitor_alias_matched"] is True
    assert "iphone 17 pro" in scored.entity_match_signals["aliases_used"]


def test_collector_web_uses_entity_aliases_for_query_and_relevance(db_session):
    task = make_custom_task(
        db_session,
        product_name="\u667a\u80fd\u624b\u673a\u7ade\u54c1\u5206\u6790",
        competitors=["\u82f9\u679c17pro"],
        industry="\u667a\u80fd\u624b\u673a",
    )
    aliases = EntityResolverService().aliases_for_task(task)

    class AliasSearchClient:
        provider = "fake"
        api_key = "fake"
        base_url = "https://fake-search.example"

        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str, limit: int = 8) -> WebSearchResponse:
            self.queries.append(query)
            return WebSearchResponse(
                available=True,
                attempted=True,
                success=True,
                results=[
                    SearchResult(
                        title="Apple iPhone 17 Pro official specs",
                        url="https://www.apple.com/iphone-17-pro/",
                        snippet="Apple iPhone 17 Pro official page introduces pricing, camera, display, and performance capabilities.",
                        score=0.9,
                    )
                ],
            )

    trace_service = TraceService(db_session)
    search_client = AliasSearchClient()
    output = CollectorAgent(trace_service, web_search_client=search_client).run(
        CollectorInput(
            task=task,
            collector_mode="web",
            competitor_aliases=aliases,
        )
    )

    assert output.diagnostics["entity_aliases_used"] is True
    assert output.diagnostics["alias_query_count_by_competitor"]["\u82f9\u679c17pro"] > 0
    assert not any("iPhone" in query or "Apple" in query for query in search_client.queries)
    assert search_client.queries == [
        "\u82f9\u679c17pro \u4ef7\u683c",
        "\u82f9\u679c17pro \u529f\u80fd",
        "\u82f9\u679c17pro \u7528\u6237\u753b\u50cf",
        "\u82f9\u679c17pro \u4f18\u52bf",
        "\u82f9\u679c17pro \u52a3\u52bf",
        "\u82f9\u679c17pro \u673a\u4f1a",
        "\u82f9\u679c17pro \u5a01\u80c1",
    ]
    assert output.diagnostics["missing_relevant_evidence_competitors"] == []
    assert output.evidence[0].relevance_level in {"high", "medium"}


def test_collector_web_uses_planner_collector_search_plan(db_session):
    task = make_custom_task(
        db_session,
        product_name="\u65b0\u80fd\u6e90\u6c7d\u8f66\u7ade\u54c1\u5206\u6790",
        competitors=["\u5c0f\u7c73 SU7"],
        industry="\u65b0\u80fd\u6e90\u6c7d\u8f66",
    )
    plan = apply_fixed_dimensions_to_plan(None, task)
    collector_search_plan = plan.metadata["collector_search_plan"]

    class PlannedSearchClient:
        provider = "fake"
        api_key = "fake"
        base_url = "https://fake-search.example"

        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str, limit: int = 8) -> WebSearchResponse:
            self.queries.append(query)
            index = len(self.queries)
            return WebSearchResponse(
                available=True,
                attempted=True,
                success=True,
                results=[
                    SearchResult(
                        title="\u5c0f\u7c73 SU7 \u5b98\u65b9\u4ea7\u54c1\u4fe1\u606f",
                        url=f"https://www.mi.com/su7/{index}",
                        snippet="\u5c0f\u7c73 SU7 \u5b98\u65b9\u9875\u9762\u4ecb\u7ecd\u7eed\u822a\u3001\u667a\u9a7e\u548c\u52a8\u529b\u80fd\u529b\u3002",
                        score=0.9,
                    )
                ],
            )

    search_client = PlannedSearchClient()
    output = CollectorAgent(TraceService(db_session), web_search_client=search_client).run(
        CollectorInput(
            task=task,
            collector_mode="web",
            collector_search_plan=collector_search_plan,
        )
    )

    assert output.diagnostics["collector_search_plan_used"] is True
    assert "planner_collector_search_plan" in output.diagnostics["query_policy"]
    assert "\u5c0f\u7c73 SU7 \u7eed\u822a\u4e0e\u7535\u6c60" in search_client.queries
    assert not any("features specs official" in query for query in search_client.queries)
    battery_evidence = next(
        item for item in output.evidence if (item.entity_match_signals or {}).get("collector_dimension") == "battery_range"
    )
    assert battery_evidence.entity_match_signals["collector_query_source"] == "planner_search_plan"


def test_collector_records_skipped_queries_when_plan_exceeds_limit(db_session):
    task = make_custom_task(
        db_session,
        product_name="\u641c\u7d22\u4e0a\u9650\u6d4b\u8bd5",
        competitors=["AlphaCI"],
        industry="B2B SaaS",
    )
    collector_search_plan = {
        "AlphaCI": {
            f"dimension_{index}": {
                "dimension_id": f"dimension_{index}",
                "queries": [f"AlphaCI dimension {index} official"],
                "intent": "test",
                "preferred_sources": ["official"],
            }
            for index in range(35)
        }
    }

    class EmptySearchClient:
        provider = "fake"
        api_key = "fake"
        base_url = "https://fake-search.example"

        def search(self, query: str, limit: int = 8) -> WebSearchResponse:
            return WebSearchResponse(available=True, attempted=True, success=True, results=[])

    output = CollectorAgent(TraceService(db_session), web_search_client=EmptySearchClient()).run(
        CollectorInput(task=task, collector_mode="web", collector_search_plan=collector_search_plan)
    )

    diagnostics = output.diagnostics
    assert diagnostics["effective_query_count_by_competitor"]["AlphaCI"] == 30
    assert len(diagnostics["effective_queries_preview_by_competitor"]["AlphaCI"]) == 30
    skipped = diagnostics["skipped_queries_by_competitor"]["AlphaCI"]
    assert len(skipped) == 5
    assert skipped[0]["reason"] == "exceeded_max_query_count_per_competitor"


def test_collector_query_budget_round_robins_dimensions_before_extra_queries():
    collector_search_plan = {
        "AlphaCI": {
            f"dimension_{index}": {
                "dimension_id": f"dimension_{index}",
                "queries": [
                    f"AlphaCI dimension {index} query 1",
                    f"AlphaCI dimension {index} query 2",
                    f"AlphaCI dimension {index} query 3",
                ],
                "intent": "test",
                "preferred_sources": ["official"],
            }
            for index in range(12)
        }
    }

    query_plan = CollectorAgent._query_plan_for_competitor(
        "AlphaCI",
        "B2B SaaS",
        planner_query_hints={},
        collector_search_plan=collector_search_plan,
        gate_context={},
        aliases=[],
    )

    first_round = query_plan["effective_queries"][:12]
    assert first_round == [f"AlphaCI dimension {index} query 1" for index in range(12)]
    assert "AlphaCI dimension 0 query 2" in query_plan["effective_queries"][:24]
    assert len(query_plan["effective_queries"]) == 30
    assert len(query_plan["all_candidate_queries"]) == 36


def test_planner_llm_dynamic_dimensions_and_search_plan_are_normalized(db_session):
    task = make_custom_task(
        db_session,
        product_name="\u667a\u80fd\u624b\u673a\u7ade\u54c1\u5206\u6790",
        competitors=["iPhone", "\u5c0f\u7c73\u65d7\u8230\u673a"],
        industry="\u667a\u80fd\u624b\u673a",
    )
    payload = {
        "intent_summary": "\u667a\u80fd\u624b\u673a\u7ade\u54c1\u5206\u6790",
        "intent_classification": "competitive_analysis",
        "industry": "\u667a\u80fd\u624b\u673a",
        "domain": "\u667a\u80fd\u624b\u673a",
        "product_name": "\u667a\u80fd\u624b\u673a\u7ade\u54c1\u5206\u6790",
        "product_type": "\u65d7\u8230\u624b\u673a",
        "target_users": ["\u6d88\u8d39\u8005"],
        "region": "\u4e2d\u56fd",
        "competitors_mentioned": ["iPhone", "\u5c0f\u7c73\u65d7\u8230\u673a"],
        "analysis_focus_points": ["\u5f71\u50cf", "\u82af\u7247", "AI"],
        "requested_outputs": ["\u7ade\u54c1\u5206\u6790\u62a5\u544a"],
        "survey_needed": False,
        "survey_reason": "",
        "missing_information": [],
        "confidence": 0.9,
        "ambiguity_level": "low",
        "scope_type": "specific_product_benchmark",
        "scope_size": "narrow",
        "selected_dimensions": ["feature", "pricing", "persona", "strength", "weakness", "opportunity", "threat", "camera_capability"],
        "dynamic_dimensions": [
            {
                "dimension_id": "camera_capability",
                "label": "\u5f71\u50cf\u80fd\u529b",
                "description": "\u6bd4\u8f83\u6444\u50cf\u5934\u548c\u5f71\u50cf\u7b97\u6cd5\u3002",
                "keywords": ["\u5f71\u50cf", "\u6444\u50cf\u5934", "camera"],
                "reason": "\u624b\u673a\u7ade\u54c1\u9700\u8981\u6bd4\u8f83\u5f71\u50cf\u80fd\u529b\u3002",
                "preferred_sources": ["official", "review"],
            },
            {
                "dimension_id": "\u4e2d\u6587\u5b57\u6bb5",
                "label": "\u5e94\u88ab\u4e22\u5f03",
                "keywords": ["bad"],
            },
        ],
        "collector_search_plan": {
            "iPhone": {
                "camera_capability": {
                    "queries": ["iPhone \u5f71\u50cf\u80fd\u529b \u8bc4\u6d4b", "iPhone \u6444\u50cf\u5934 \u5b98\u65b9"],
                    "intent": "\u91c7\u96c6 iPhone \u5f71\u50cf\u8bc1\u636e\u3002",
                    "preferred_sources": ["official", "review"],
                },
                "\u4e2d\u6587\u7ef4\u5ea6": {
                    "queries": ["iPhone bad"],
                    "intent": "bad",
                    "preferred_sources": ["official"],
                },
            },
            "NotInTask": {
                "camera_capability": {
                    "queries": ["NotInTask camera"],
                    "intent": "bad",
                    "preferred_sources": ["official"],
                }
            },
        },
        "survey_objective": "",
        "survey_inputs": {"objective": "", "respondent_type": "", "question_themes": [], "hypotheses": []},
        "recommended_next_constraints": [],
        "assumptions": [],
        "candidate_competitors": [],
        "clarification_targets": [],
        "planning_stages": [],
        "planner_notes": [],
        "downstream_guidance": {"collector": [], "analyst": [], "writer": [], "qa": [], "survey": []},
    }
    planner_output = PlannerAgent(TraceService(db_session), llm_client=FakeLlmClient(json.dumps(payload, ensure_ascii=False))).run(
        PlannerInput(task=task)
    )
    normalized = apply_fixed_dimensions_to_plan(planner_output.analysis_dimension_plan, task)

    assert "camera_capability" in normalized.selected_dimensions
    assert "\u4e2d\u6587\u5b57\u6bb5" not in normalized.selected_dimensions
    assert normalized.metadata["llm_collector_search_plan_used"] is True
    assert normalized.metadata["collector_search_plan"]["iPhone"]["camera_capability"]["queries"][0] == "iPhone \u5f71\u50cf\u80fd\u529b \u8bc4\u6d4b"
    assert "NotInTask" not in normalized.metadata["collector_search_plan"]
