from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.langgraph_runner import LangGraphWorkflowRunner
from app.agents.evidence_analyst import EvidenceAnalystAgent
from app.agents.planner import PlannerAgent
from app.agents.qa import QaAgent
from app.agents.report_agent import ReportAgent
from app.database import Base
from app.schemas import (
    CollectorConfig,
    CollectorConfigOverride,
    CreateTaskRequest,
    Evidence,
    EvidenceAnalystInput,
    EvidenceAnalystOutput,
    EvidenceAnalystReworkContext,
    EvidenceAnalystReworkTarget,
    EvidenceDimensionAnswerResult,
    EvidenceQuestionAnswer,
    EvidenceCoverageGap,
    EvidenceCoverageGapTarget,
    PlannerCollectionPlan,
    PlannerCollectionPlanItem,
    PlannerIncrementalInput,
    QaInput,
    ReportAgentInput,
    Task,
)
from app.services.planner_attempt_service import PlannerAttemptService
from app.services.evidence_content_fetcher import EvidenceContentFetcher
from app.services.llm_client import LlmClient, LlmResponse
from app.services.task_service import TaskService
from app.services.trace_service import TraceService


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_task() -> Task:
    now = datetime.utcnow()
    return Task(
        task_id="task_evidence_qa",
        product_name="测试产品",
        competitors=["竞品A"],
        region="中国",
        industry="测试行业",
        status="running",
        created_at=now,
        updated_at=now,
    )


def make_evidence(
    *,
    dimension: str = "feature",
    relevance_level: str = "high",
    source_quality: str = "official",
    snippet: str | None = None,
) -> Evidence:
    return Evidence(
        competitor="竞品A",
        source_type="public_web",
        url=f"https://example.com/{dimension}",
        source_domain="example.com",
        source_quality=source_quality,
        snippet=snippet or ("竞品A 官方产品资料详细介绍了功能参数、使用方式和公开能力。" * 3),
        confidence=0.85,
        relevance_level=relevance_level,
        relevance_score=0.9 if relevance_level == "high" else 0.4,
        entity_match_signals={
            "collector_query": f"竞品A {dimension}",
            "collector_dimension": dimension,
            "planner_dimension": dimension,
        },
    )


def collection_plan(competitor: str) -> PlannerCollectionPlan:
    return PlannerCollectionPlan(
        collector_search_plan={
            competitor: {
                "feature": PlannerCollectionPlanItem(
                    dimension_id="feature",
                    label="feature",
                    queries=[f"{competitor} feature official"],
                ),
                "pricing": PlannerCollectionPlanItem(
                    dimension_id="pricing",
                    label="pricing",
                    queries=[f"{competitor} pricing official"],
                ),
            }
        }
    )


def evidence_qa(db_session, *, evidence, selected_dimensions, diagnostics):
    task = make_task()
    return QaAgent(TraceService(db_session)).run(
        QaInput(
            task=task,
            run_id="run_evidence_qa",
            qa_stage="evidence",
            evidence=evidence,
            selected_dimensions=selected_dimensions,
            collection_plan=collection_plan(task.competitors[0]),
            collector_trace_summary=diagnostics,
        )
    ).qa_result


class FixedPlannerLlm:
    is_available = True
    provider = "test"
    model = "test-model"
    base_url = "https://llm.example"

    def __init__(self, content: str):
        self.content = content

    def chat_json(self, messages, timeout: float = 30.0):
        return LlmResponse(
            available=True,
            attempted=True,
            success=True,
            content=self.content,
            response_preview=self.content[:300],
        )


def test_evidence_qa_returns_passed_warning_and_failed(db_session):
    base_diagnostics = {
        "collection_plan_used": True,
        "collector_search_plan_missing": False,
        "collector_mode_requested": "web",
        "web_search_success": True,
        "failed_queries": [],
    }
    passed = evidence_qa(
        db_session,
        evidence=[make_evidence()],
        selected_dimensions=["feature"],
        diagnostics=base_diagnostics,
    )
    warning = evidence_qa(
        db_session,
        evidence=[
            make_evidence(
                source_quality="unknown",
                relevance_level="low",
                snippet="竞品A 简短摘要",
            )
        ],
        selected_dimensions=["feature", "pricing"],
        diagnostics={**base_diagnostics, "failed_queries": ["竞品A pricing"]},
    )
    failed = evidence_qa(
        db_session,
        evidence=[],
        selected_dimensions=["feature"],
        diagnostics=base_diagnostics,
    )

    assert passed.status == "passed"
    assert warning.status == "failed"
    assert warning.route_to == "PlannerAgent"
    assert warning.failed_dimensions == ["feature", "pricing"]
    coverage_gap = warning.metadata["coverage_gap"]
    assert coverage_gap["summary"]["target_count"] == 2
    assert {target["dimension_id"] for target in coverage_gap["targets"]} == {"feature", "pricing"}
    pricing_target = next(target for target in coverage_gap["targets"] if target["dimension_id"] == "pricing")
    assert pricing_target["original_queries"] == ["竞品A pricing official"]
    assert warning.failed_queries == ["竞品A pricing"]
    assert failed.status == "failed"
    assert failed.route_to == "PlannerAgent"
    assert failed.failed_competitors == ["竞品A"]


def test_evidence_qa_uses_min_valid_evidence_required(db_session):
    task = make_task()
    result = QaAgent(TraceService(db_session)).run(
        QaInput(
            task=task,
            run_id="run_evidence_qa_policy",
            qa_stage="evidence",
            evidence=[make_evidence(dimension="pricing")],
            selected_dimensions=["pricing"],
            collection_plan=collection_plan(task.competitors[0]),
            collector_config=CollectorConfig(
                overrides=[
                    CollectorConfigOverride(
                        competitor=task.competitors[0],
                        dimension_id="pricing",
                        min_valid_evidence_required=2,
                    )
                ]
            ),
            collector_trace_summary={
                "collection_plan_used": True,
                "collector_search_plan_missing": False,
                "failed_queries": [],
            },
        )
    ).qa_result

    assert result.status == "failed"
    assert result.metadata["coverage_gap"]["summary"]["target_count"] == 1
    assert "requires 2" in result.metadata["coverage_gap"]["targets"][0]["reason"]


def test_evidence_qa_checks_only_collection_plan_targets(db_session):
    now = datetime.utcnow()
    task = Task(
        task_id="task_partial_evidence_qa",
        product_name="测试产品",
        competitors=["竞品A", "竞品B"],
        region="中国",
        industry="测试行业",
        status="running",
        created_at=now,
        updated_at=now,
    )
    plan = PlannerCollectionPlan(
        collector_search_plan={
            "竞品A": {
                "consumer_satisfaction": PlannerCollectionPlanItem(
                    dimension_id="consumer_satisfaction",
                    label="消费者满意度",
                    queries=["竞品A 消费者满意度"],
                )
            }
        }
    )

    result = QaAgent(TraceService(db_session)).run(
        QaInput(
            task=task,
            run_id="run_partial_evidence_qa",
            qa_stage="evidence",
            evidence=[],
            selected_dimensions=["consumer_satisfaction"],
            collection_plan=plan,
            collector_trace_summary={
                "collection_plan_used": True,
                "collector_search_plan_missing": False,
                "failed_queries": [],
            },
        )
    ).qa_result

    targets = result.metadata["coverage_gap"]["targets"]
    assert len(targets) == 1
    assert targets[0]["competitor"] == "竞品A"
    assert targets[0]["dimension_id"] == "consumer_satisfaction"


def test_planner_incremental_collection_uses_strict_llm_json(db_session):
    task = make_task()
    planner = PlannerAgent(
        TraceService(db_session),
        llm_client=FixedPlannerLlm(
            '{"mode":"incremental_collection_plan","targets":[{"competitor":"绔炲搧A","dimension_id":"pricing","queries":["绔炲搧A official pricing","pricing plans"],"reason":"补齐定价证据"}]}'
        ),
    )
    base_output = planner._deterministic_output(task, planner._base_diagnostics(), "test")
    gap = EvidenceCoverageGap(
        targets=[
            EvidenceCoverageGapTarget(
                competitor=task.competitors[0],
                dimension_id="pricing",
                reason_code="no_high_or_medium_evidence",
                reason="missing pricing evidence",
                original_queries=[f"{task.competitors[0]} pricing"],
            )
        ]
    )

    output = planner.plan_incremental_collection(
        PlannerIncrementalInput(
            task=task,
            run_id="run_incremental",
            base_planner_output=base_output,
            coverage_gap=gap,
            base_attempt_no=1,
        )
    )

    plan = output.incremental_collection_plan
    assert output.diagnostics["planner_mode_used"] == "llm"
    assert plan.mode == "incremental_collection_plan"
    assert plan.targets[0].competitor == task.competitors[0]
    assert plan.targets[0].dimension_id == "pricing"
    assert all(task.competitors[0] in query for query in plan.targets[0].queries)


def test_planner_incremental_collection_falls_back_on_invalid_json(db_session):
    task = make_task()
    planner = PlannerAgent(
        TraceService(db_session),
        llm_client=FixedPlannerLlm("```json\n{\"mode\":\"incremental_collection_plan\",\"targets\":[]}\n```"),
    )
    base_output = planner._deterministic_output(task, planner._base_diagnostics(), "test")
    gap = EvidenceCoverageGap(
        targets=[
            EvidenceCoverageGapTarget(
                competitor=task.competitors[0],
                dimension_id="feature",
                reason_code="no_evidence_collected",
                reason="missing feature evidence",
                original_queries=[f"{task.competitors[0]} feature"],
            )
        ]
    )

    output = planner.plan_incremental_collection(
        PlannerIncrementalInput(
            task=task,
            run_id="run_incremental_bad_json",
            base_planner_output=base_output,
            coverage_gap=gap,
            base_attempt_no=1,
        )
    )

    assert output.diagnostics["planner_mode_used"] == "deterministic"
    assert output.diagnostics["fallback_used"] is True
    assert output.incremental_collection_plan.targets[0].queries


def test_planner_attempt_service_accepts_dict_rework_context(db_session):
    saved = PlannerAttemptService(db_session).save(
        run_id="run_dict_rework_context",
        status="generated",
        planner_output={"mode": "incremental_collection_plan"},
        diagnostics={"planner_mode_used": "llm"},
        rework_context={"source": "AnalystQA", "coverage_gap": {"targets": []}},
    )

    assert saved.rework_context == {"source": "AnalystQA", "coverage_gap": {"targets": []}}


def test_planner_scopes_research_goals_to_single_competitor(db_session):
    task = Task(
        task_id="task_single_competitor_questions",
        product_name="OurIDE",
        competitors=["AcmeAI", "BetaAI"],
        region="US",
        industry="AI coding",
        status="running",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    planner = PlannerAgent(
        TraceService(db_session),
        llm_client=FixedPlannerLlm(
            '{"planner_summary":{"intent_classification":"competitive_analysis","product_type":"AI coding","task_goal":"Compare products"},'
            '"selected_dimensions":["feature"],'
            '"dimension_suggestions":[{"dimension_id":"feature","label":"Code generation",'
            '"description":"Feature research","keywords":["code"],'
            '"query_templates":["{competitor} code generation"],'
            '"research_goals":["Compare AcmeAI and BetaAI code generation capability differences"]}],'
            '"missing_information":[],"planner_notes":[]}'
        ),
    )

    output = planner.run(type("Input", (), {"task": task, "run_id": "run_scope", "retry_count": 0})())
    acme_goals = output.collection_plan.collector_search_plan["AcmeAI"]["feature"].research_goals
    beta_goals = output.collection_plan.collector_search_plan["BetaAI"]["feature"].research_goals

    assert acme_goals
    assert beta_goals
    assert all("Compare AcmeAI and BetaAI" not in goal for goal in [*acme_goals, *beta_goals])
    assert all("不要要求与其他竞品直接对比" in goal for goal in [*acme_goals, *beta_goals])
    assert acme_goals[0].startswith("针对 AcmeAI")
    assert beta_goals[0].startswith("针对 BetaAI")


def test_main_flow_runs_planner_collector_qa_analyst_and_report(db_session, monkeypatch):
    task = TaskService(db_session).create_task(
        CreateTaskRequest(
            product_name="测试产品",
            competitors=["竞品A"],
            region="中国",
            industry="测试行业",
        )
    )
    runner = LangGraphWorkflowRunner(db_session)

    class UnavailablePlannerLlm:
        is_available = False
        provider = "test"
        model = "test-model"
        base_url = None

        def chat_json(self, messages, timeout: float = 30.0):
            return LlmResponse(
                available=False,
                attempted=False,
                success=False,
                fallback_reason="disabled for main_flow test",
            )

    runner.planner.llm_client = UnavailablePlannerLlm()
    runner.report_agent.llm_client = UnavailablePlannerLlm()
    assert not hasattr(runner, "evidence_gate_node")
    assert not hasattr(runner, "analyst")
    assert not hasattr(runner, "writer")
    assert not hasattr(runner, "final_report")

    result = runner.run(
        task.task_id,
        collector_mode="mock",
        workflow_engine_requested="langgraph",
        debug_stage="main_flow",
    )

    assert result["workflow_summary"]["debug_stage"] == "main_flow"
    node_sequence = result["workflow_summary"]["node_sequence"]
    assert node_sequence[:3] == ["planner", "collector", "qa"]
    assert "evidence_content_fetcher" in node_sequence
    assert "evidence_analyst" in node_sequence
    assert "analyst_qa" in node_sequence
    assert "report_agent" in node_sequence
    assert result["qa_result"].qa_stage == "evidence"
    assert result["qa_result"].status == "warning"
    assert result["qa_result"].metadata["coverage_gap"]["summary"]["target_count"] == 0
    assert result["workflow_summary"]["initial_qa_result"]["status"] == "failed"
    assert result["workflow_summary"]["initial_qa_result"]["metadata"]["coverage_gap"]["summary"]["target_count"] > 0
    assert result["workflow_summary"]["incremental_collection_plan"]["mode"] == "incremental_collection_plan"
    assert result["workflow_summary"]["incremental_collector_output"]["diagnostics"]["collector_collection_mode"] == "incremental"
    attempts = PlannerAttemptService(db_session).list_for_run(result["run_id"])
    assert attempts[-1].diagnostics["planner_output_type"] == "incremental_collection_plan"
    assert attempts[-1].planner_output["mode"] == "incremental_collection_plan"
    assert result["report"] is not None
    assert result["report"].markdown.startswith("#")
    assert result["workflow_summary"]["report_agent_output"]["diagnostics"]["fallback_used"] is True
    assert result["evidence"]
    statuses = {
        node["id"]: node["status"]
        for node in result["dag"]["nodes"]
    }
    assert set(statuses) == {
        "PlannerAgent",
        "CollectorAgent",
        "QaAgent",
        "EvidenceContentFetcher",
        "EvidenceAnalystAgent",
        "ReportAgent",
    }
    assert statuses["QaAgent"] == "completed"
    assert statuses["EvidenceContentFetcher"] == "completed"
    assert statuses["EvidenceAnalystAgent"] == "completed"
    assert statuses["ReportAgent"] == "completed"
    trace_agents = {
        trace.agent_name
        for trace in TraceService(db_session).list_for_task(
            task.task_id,
            run_id=result["run_id"],
        )
    }
    assert trace_agents == {
        "PlannerAgent",
        "CollectorAgent",
        "QaAgent",
        "EvidenceContentFetcher",
        "EvidenceAnalystAgent",
        "ReportAgent",
        "WorkflowEngine",
    }


def test_manual_evidence_selection_continues_same_run_without_evidence_gate(db_session):
    task = TaskService(db_session).create_task(
        CreateTaskRequest(
            product_name="测试产品",
            competitors=["竞品A"],
            region="中国",
            industry="测试行业",
        )
    )
    runner = LangGraphWorkflowRunner(db_session)
    collector_result = runner.run(
        task.task_id,
        collector_mode="mock",
        analyst_mode="mock",
        writer_mode="mock",
        workflow_engine_requested="langgraph",
        debug_stage="main_flow",
    )
    selected_id = collector_result["evidence"][0].evidence_id

    result = runner.run(
        task.task_id,
        collector_mode="mock",
        analyst_mode="mock",
        writer_mode="mock",
        workflow_engine_requested="langgraph",
        manual_evidence_selection_enabled=True,
        selected_evidence_ids=[selected_id],
        source_run_id=collector_result["run_id"],
    )

    summary = result["workflow_summary"]
    assert result["run_id"] == collector_result["run_id"]
    assert summary["manual_evidence_selection_used"] is True
    assert summary["manual_selected_evidence_ids"] == [selected_id]
    assert summary["node_sequence"][0] == "manual_evidence_selection"
    assert "evidence_content_fetcher" in summary["node_sequence"]
    assert "collector" not in summary["node_sequence"]
    assert "evidence_analyst" in summary["node_sequence"]
    assert "analyst_qa" in summary["node_sequence"]
    assert "report_agent" in summary["node_sequence"]
    assert "analyst" not in summary["node_sequence"]
    assert "report_writer" not in summary["node_sequence"]


def test_task_collection_strategy_sets_collector_and_content_fetch_defaults(db_session):
    task = TaskService(db_session).create_task(
        CreateTaskRequest(
            product_name="测试产品",
            competitors=["竞品A", "竞品B"],
            region="中国",
            industry="测试行业",
            collection_strategy_mode="expert",
        )
    )
    result = LangGraphWorkflowRunner(db_session).run(
        task.task_id,
        collector_mode="mock",
        workflow_engine_requested="langgraph",
        debug_stage="main_flow",
    )

    summary = result["workflow_summary"]
    assert summary["collection_strategy_mode"] == "expert"
    assert summary["collector_config_source"] == "task_strategy:expert"
    assert summary["collector_config"]["default"]["max_results_per_query"] == 8
    assert summary["collector_config"]["default"]["max_evidence_per_dimension"] == 20
    assert summary["collector_config"]["default"]["min_valid_evidence_required"] == 3
    assert summary["evidence_content_fetch_output"]["content_fetch_scope"] == "all_eligible_evidence"


def test_evidence_content_fetcher_limits_simple_mode_to_best_evidence_per_dimension():
    class StubContentFetcher(EvidenceContentFetcher):
        @property
        def is_available(self) -> bool:
            return True

        def fetch(self, url: str):
            from app.services.evidence_content_fetcher import EvidenceContentFetchResult

            return EvidenceContentFetchResult(
                success=True,
                content_excerpt=f"full text for {url}",
                content_chars=24,
            )

    evidence = [
            Evidence(
                evidence_id="ev_low",
                competitor="A",
                source_type="public_web",
                url="https://example.com/low",
                snippet="low",
            relevance_level="medium",
            source_quality="unknown",
            relevance_score=0.7,
            confidence=0.7,
            entity_match_signals={"collector_dimension": "pricing"},
        ),
            Evidence(
                evidence_id="ev_best",
                competitor="A",
                source_type="public_web",
                url="https://example.com/best",
                snippet="best",
            relevance_level="high",
            source_quality="official",
            relevance_score=0.9,
            confidence=0.9,
            entity_match_signals={"collector_dimension": "pricing"},
        ),
            Evidence(
                evidence_id="ev_other_dimension",
                competitor="A",
                source_type="public_web",
                url="https://example.com/other",
                snippet="other",
            relevance_level="high",
            source_quality="official",
            relevance_score=0.8,
            confidence=0.8,
            entity_match_signals={"collector_dimension": "feature"},
        ),
    ]

    enriched, diagnostics = StubContentFetcher(provider="tavily", api_key="test").enrich(
        evidence,
        enabled=True,
        max_per_competitor_dimension=1,
    )

    by_id = {item.evidence_id: item for item in enriched}
    assert by_id["ev_best"].content_mode == "page"
    assert by_id["ev_other_dimension"].content_mode == "page"
    assert by_id["ev_low"].content_mode == "snippet"
    assert by_id["ev_low"].page_fetch_error == "skipped:strategy_dimension_limit"
    assert diagnostics["content_fetch_attempt_count"] == 2
    assert diagnostics["content_fetch_limit_skipped_ids"] == ["ev_low"]


def test_evidence_content_fetcher_preserves_already_fetched_content():
    evidence = Evidence(
        evidence_id="ev_fetched",
        competitor="A",
        source_type="public_web",
        url="https://example.com/fetched",
        snippet="summary",
        confidence=0.9,
        relevance_level="high",
        source_quality="official",
        content_mode="page",
        page_fetch_success=True,
        content_excerpt="existing full text",
        content_chars=18,
        entity_match_signals={"collector_dimension": "pricing"},
    )

    enriched, diagnostics = EvidenceContentFetcher(
        provider="tavily",
        api_key="test",
    ).enrich([evidence], enabled=True)

    assert diagnostics["content_fetch_attempt_count"] == 0
    assert diagnostics["skipped_evidence_ids"] == ["ev_fetched"]
    assert enriched[0].content_mode == "page"
    assert enriched[0].page_fetch_success is True
    assert enriched[0].content_excerpt == "existing full text"


def test_llm_client_reads_standard_token_usage(monkeypatch):
    class StubResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 10,
                    "total_tokens": 50,
                },
            }

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.services.llm_client.httpx.post",
        lambda *args, **kwargs: StubResponse(),
    )

    response = LlmClient().chat_json([{"role": "user", "content": "test"}])

    assert response.prompt_tokens == 40
    assert response.completion_tokens == 10
    assert response.total_tokens == 50


def test_evidence_analyst_answers_planner_questions_with_evidence_ids(db_session):
    class StubLlmClient:
        is_available = True
        provider = "test"
        model = "test-model"

        def chat_json(self, messages, timeout=None):
            return LlmResponse(
                available=True,
                attempted=True,
                success=True,
                content=(
                    '{"question_answers":[{"question_id":"q1",'
                    '"question":"AcmeAI 的 Pro 套餐如何计费？",'
                    '"answer":"当前证据显示 AcmeAI Pro starts at $19 per user per month.",'
                    '"evidence_ids":["ev_001"],'
                    '"answer_status":"answered"}],'
                    '"dimension_summary":"当前证据回答了 Pro 套餐起价问题。",'
                    '"warnings":[]}'
                ),
                elapsed_time_ms=12,
                prompt_tokens=120,
                completion_tokens=30,
                total_tokens=150,
            )

    now = datetime.utcnow()
    task = Task(
        task_id="task_evidence_analyst",
        product_name="Test Product",
        competitors=["AcmeAI"],
        region="US",
        industry="AI",
        status="running",
        created_at=now,
        updated_at=now,
    )
    evidence = Evidence(
        evidence_id="ev_001",
        competitor="AcmeAI",
        source_type="public_web",
        url="https://acmeai.com/pricing",
        snippet="AcmeAI Pro starts at $19 per user per month.",
        confidence=0.9,
        source_domain="acmeai.com",
        source_quality="official",
        relevance_level="high",
        content_mode="page",
        content_excerpt="Free plan available. Pro starts at $19 per user per month. Enterprise requires sales.",
        entity_match_signals={"collector_dimension": "pricing"},
    )

    output = EvidenceAnalystAgent(TraceService(db_session), llm_client=StubLlmClient()).run(
        EvidenceAnalystInput(
            task=task,
            evidence=[evidence],
            selected_dimensions=["pricing"],
            collection_plan=PlannerCollectionPlan(
                collector_search_plan={
                    "AcmeAI": {
                        "pricing": PlannerCollectionPlanItem(
                            dimension_id="pricing",
                            label="定价与商业模式",
                            queries=["AcmeAI pricing"],
                            research_goals=["AcmeAI 的 Pro 套餐如何计费？"],
                        )
                    }
                }
            ),
        )
    )

    result = output.question_results[0]
    assert result.competitor == "AcmeAI"
    assert result.dimension_id == "pricing"
    assert result.question_answers[0].question_id == "q1"
    assert result.question_answers[0].evidence_ids == ["ev_001"]
    assert result.question_answers[0].answer_status == "answered"
    assert output.diagnostics["total_evidence"] == 1
    assert output.diagnostics["question_answer_count"] == 1
    assert output.diagnostics["llm_prompt_tokens"] == 120
    assert output.diagnostics["llm_completion_tokens"] == 30
    assert output.diagnostics["llm_total_tokens"] == 150
    trace = TraceService(db_session).list_for_task(task.task_id)[0]
    assert trace.model_name == "test-model"
    assert trace.token_usage == 150


def test_evidence_analyst_keeps_unselected_competitor_questions(db_session):
    class StubLlmClient:
        is_available = False
        provider = "test"
        model = "test-model"

        def chat_json(self, messages, timeout=None):
            raise AssertionError("No LLM call is needed for fallback coverage.")

    now = datetime.utcnow()
    task = Task(
        task_id="task_evidence_analyst_missing_competitor",
        product_name="Test Product",
        competitors=["AcmeAI", "BetaAI"],
        region="US",
        industry="AI",
        status="running",
        created_at=now,
        updated_at=now,
    )
    evidence = Evidence(
        evidence_id="ev_acme",
        competitor="AcmeAI",
        source_type="public_web",
        url="https://acmeai.com/pricing",
        snippet="AcmeAI Pro starts at $19 per user per month.",
        confidence=0.9,
        source_domain="acmeai.com",
        source_quality="official",
        relevance_level="high",
        entity_match_signals={"collector_dimension": "pricing"},
    )
    plan_item = PlannerCollectionPlanItem(
        dimension_id="pricing",
        label="pricing",
        queries=["pricing"],
        research_goals=["Question 1", "Question 2"],
    )

    output = EvidenceAnalystAgent(TraceService(db_session), llm_client=StubLlmClient()).run(
        EvidenceAnalystInput(
            task=task,
            evidence=[evidence],
            selected_dimensions=["pricing"],
            collection_plan=PlannerCollectionPlan(
                collector_search_plan={
                    "AcmeAI": {"pricing": plan_item},
                    "BetaAI": {"pricing": plan_item},
                }
            ),
        )
    )

    by_competitor = {item.competitor: item for item in output.question_results}
    assert set(by_competitor) == {"AcmeAI", "BetaAI"}
    assert output.diagnostics["question_answer_count"] == 4
    assert all(answer.answer_status == "not_found" for answer in by_competitor["AcmeAI"].question_answers)
    assert all(answer.answer_status == "not_found" for answer in by_competitor["BetaAI"].question_answers)


def test_evidence_analyst_incremental_only_reanswers_not_found_targets(db_session):
    class StubLlmClient:
        is_available = True
        provider = "test"
        model = "test-model"

        def __init__(self):
            self.calls = 0

        def chat_json(self, messages, timeout=None):
            self.calls += 1
            return LlmResponse(
                available=True,
                attempted=True,
                success=True,
                content=(
                    '{"question_answers":[{"question_id":"q2",'
                    '"question":"What is AcmeAI enterprise pricing?",'
                    '"answer":"The new evidence says Enterprise pricing requires contacting sales.",'
                    '"evidence_ids":["ev_new"],'
                    '"answer_status":"answered",'
                    '"suggestions":[]}],'
                    '"dimension_summary":"Enterprise pricing is now answered.",'
                    '"warnings":[]}'
                ),
                elapsed_time_ms=10,
            )

    now = datetime.utcnow()
    task = Task(
        task_id="task_incremental_evidence_analyst",
        product_name="Test Product",
        competitors=["AcmeAI"],
        region="US",
        industry="AI",
        status="running",
        created_at=now,
        updated_at=now,
    )
    previous_output = EvidenceAnalystOutput(
        question_results=[
            EvidenceDimensionAnswerResult(
                competitor="AcmeAI",
                dimension_id="pricing",
                dimension_goal="pricing",
                research_questions=[
                    "What is AcmeAI Pro pricing?",
                    "What is AcmeAI enterprise pricing?",
                ],
                question_answers=[
                    EvidenceQuestionAnswer(
                        question_id="q1",
                        question="What is AcmeAI Pro pricing?",
                        answer="Pro starts at $19 per user per month.",
                        evidence_ids=["ev_old"],
                        answer_status="answered",
                    ),
                    EvidenceQuestionAnswer(
                        question_id="q2",
                        question="What is AcmeAI enterprise pricing?",
                        answer="Current evidence is insufficient.",
                        evidence_ids=[],
                        answer_status="not_found",
                    ),
                ],
                dimension_summary="Pro pricing is answered; enterprise pricing is missing.",
            )
        ],
        diagnostics={"evidence_analyst_mode": "question_answer_by_competitor_dimension"},
    )
    evidence = [
        Evidence(
            evidence_id="ev_old",
            competitor="AcmeAI",
            source_type="public_web",
            url="https://acmeai.com/pricing",
            snippet="Pro starts at $19 per user per month.",
            confidence=0.9,
            source_domain="acmeai.com",
            source_quality="official",
            relevance_level="high",
            entity_match_signals={"collector_dimension": "pricing"},
        ),
        Evidence(
            evidence_id="ev_new",
            competitor="AcmeAI",
            source_type="public_web",
            url="https://acmeai.com/enterprise",
            snippet="Enterprise pricing requires contacting sales.",
            confidence=0.9,
            source_domain="acmeai.com",
            source_quality="official",
            relevance_level="high",
            content_mode="page",
            content_excerpt="Enterprise pricing requires contacting sales.",
            entity_match_signals={"collector_dimension": "pricing"},
        ),
    ]
    llm_client = StubLlmClient()

    output = EvidenceAnalystAgent(TraceService(db_session), llm_client=llm_client).run(
        EvidenceAnalystInput(
            task=task,
            evidence=evidence,
            selected_dimensions=["pricing"],
            previous_output=previous_output,
            rework_context=EvidenceAnalystReworkContext(
                targets=[
                    EvidenceAnalystReworkTarget(
                        competitor="AcmeAI",
                        dimension_id="pricing",
                        question_id="q2",
                        question="What is AcmeAI enterprise pricing?",
                        new_evidence_ids=["ev_new"],
                    )
                ]
            ),
            collection_plan=PlannerCollectionPlan(
                collector_search_plan={
                    "AcmeAI": {
                        "pricing": PlannerCollectionPlanItem(
                            dimension_id="pricing",
                            label="pricing",
                            queries=["AcmeAI pricing"],
                            research_goals=[
                                "What is AcmeAI Pro pricing?",
                                "What is AcmeAI enterprise pricing?",
                            ],
                        )
                    }
                }
            ),
        )
    )

    answers = output.question_results[0].question_answers
    assert llm_client.calls == 1
    assert answers[0].question_id == "q1"
    assert answers[0].answer == "Pro starts at $19 per user per month."
    assert answers[0].answer_status == "answered"
    assert answers[1].question_id == "q2"
    assert answers[1].answer_status == "answered"
    assert answers[1].evidence_ids == ["ev_new"]
    assert output.diagnostics["evidence_analyst_mode"] == "incremental_answer_not_found_only"
    assert output.diagnostics["incremental_reanswer_target_count"] == 1


def test_report_agent_fallback_generates_markdown_from_evidence_analyst_output(db_session):
    class UnavailableLlm:
        is_available = False
        provider = "test"
        model = "test-model"

        def chat_json(self, messages, timeout=None):
            raise AssertionError("ReportAgent should not call unavailable LLM")

    now = datetime.utcnow()
    task = Task(
        task_id="task_report_agent",
        product_name="Test Product",
        competitors=["AcmeAI"],
        region="US",
        industry="AI",
        status="running",
        created_at=now,
        updated_at=now,
    )
    evidence_analyst_output = EvidenceAnalystOutput(
        question_results=[
            EvidenceDimensionAnswerResult(
                competitor="AcmeAI",
                dimension_id="pricing",
                dimension_goal="pricing",
                research_questions=["What is AcmeAI Pro pricing?"],
                question_answers=[
                    EvidenceQuestionAnswer(
                        question_id="q1",
                        question="What is AcmeAI Pro pricing?",
                        answer="Pro starts at $19 per user per month.",
                        evidence_ids=["ev_001"],
                        answer_status="answered",
                    )
                ],
                dimension_summary="AcmeAI Pro pricing is answered.",
            )
        ]
    )

    output = ReportAgent(TraceService(db_session), llm_client=UnavailableLlm()).run(
        ReportAgentInput(
            task=task,
            run_id="run_report_agent",
            evidence_analyst_output=evidence_analyst_output,
        )
    )

    assert output.report.markdown.startswith("# 竞品分析报告")
    assert "Pro starts at $19 per user per month." in output.report.markdown
    assert output.report.json_report["report_agent"]["sections"][0]["section_id"] == "pricing"
    assert output.report.json_report["report_agent"]["evidence_refs"]["ev_001"]["source_domain"] is None
    assert output.sections[0].section_no == "1.1"
    assert output.sections[0].competitor_analyses
    assert output.diagnostics["fallback_used"] is True


def test_report_agent_renumbers_llm_sections_from_one_one(db_session):
    class StubLlm:
        is_available = True
        provider = "test"
        model = "test-model"

        def chat_json(self, messages, timeout=None):
            return LlmResponse(
                available=True,
                content=(
                    '{"report_title":"Test Report","sections":[{'
                    '"section_id":"pricing",'
                    '"section_no":"2.1",'
                    '"title":"Pricing",'
                    '"summary":"Pricing is covered.",'
                    '"competitor_analyses":[{"competitor":"AcmeAI","analysis":"Pro starts at $19.","strengths":["Clear pricing"],"weaknesses":[],"evidence_ids":["ev_001"]}],'
                    '"comparison":"Only one competitor is covered.",'
                    '"limitations":[],'
                    '"evidence_ids":["ev_001"],'
                    '"confidence":"high"'
                    '}]}'
                ),
                model="test-model",
                attempted=True,
                success=True,
                elapsed_time_ms=1,
            )

    now = datetime.utcnow()
    task = Task(
        task_id="task_report_agent_renumber",
        product_name="Test Product",
        competitors=["AcmeAI"],
        region="US",
        industry="AI",
        status="running",
        created_at=now,
        updated_at=now,
    )
    evidence_analyst_output = EvidenceAnalystOutput(
        question_results=[
            EvidenceDimensionAnswerResult(
                competitor="AcmeAI",
                dimension_id="pricing",
                dimension_goal="pricing",
                research_questions=["What is AcmeAI Pro pricing?"],
                question_answers=[
                    EvidenceQuestionAnswer(
                        question_id="q1",
                        question="What is AcmeAI Pro pricing?",
                        answer="Pro starts at $19.",
                        evidence_ids=["ev_001"],
                        answer_status="answered",
                    )
                ],
                dimension_summary="Pricing is answered.",
            )
        ]
    )

    output = ReportAgent(TraceService(db_session), llm_client=StubLlm()).run(
        ReportAgentInput(
            task=task,
            run_id="run_report_agent_renumber",
            evidence_analyst_output=evidence_analyst_output,
        )
    )

    assert output.sections[0].section_no == "1.1"
    assert output.report.json_report["report_agent"]["sections"][0]["section_no"] == "1.1"
