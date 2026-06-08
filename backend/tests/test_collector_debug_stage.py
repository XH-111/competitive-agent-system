from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.langgraph_runner import LangGraphWorkflowRunner
from app.agents.planner import PlannerAgent
from app.agents.qa import QaAgent
from app.database import Base
from app.schemas import (
    CollectorConfig,
    CollectorConfigOverride,
    CreateTaskRequest,
    Evidence,
    EvidenceCoverageGap,
    EvidenceCoverageGapTarget,
    PlannerCollectionPlan,
    PlannerCollectionPlanItem,
    PlannerIncrementalInput,
    QaInput,
    Task,
)
from app.services.planner_attempt_service import PlannerAttemptService
from app.services.llm_client import LlmResponse
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


def test_collector_only_runs_planner_collector_and_evidence_qa_only(db_session, monkeypatch):
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
                fallback_reason="disabled for collector_only test",
            )

    runner.planner.llm_client = UnavailablePlannerLlm()
    monkeypatch.setattr(
        runner,
        "evidence_gate_node",
        lambda *_args, **_kwargs: pytest.fail("collector_only executed EvidenceGate"),
    )
    for agent in (runner.analyst, runner.writer, runner.final_report):
        monkeypatch.setattr(
            agent,
            "run",
            lambda *_args, **_kwargs: pytest.fail("collector_only executed a frozen agent"),
        )

    result = runner.run(
        task.task_id,
        collector_mode="mock",
        workflow_engine_requested="langgraph",
        debug_stage="collector_only",
    )

    assert result["workflow_summary"]["debug_stage"] == "collector_only"
    assert result["workflow_summary"]["node_sequence"] == [
        "planner",
        "collector",
        "qa",
        "planner_incremental",
        "collector_incremental",
        "qa_incremental",
    ]
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
    assert result["report"] is None
    assert result["evidence"]
    statuses = {
        node["id"]: node["status"]
        for node in result["dag"]["nodes"]
    }
    assert statuses["EvidenceGate"] == "skipped"
    assert statuses["PageFetcher"] == "skipped"
    assert statuses["AnalystAgent"] == "skipped"
    assert statuses["ReportWriterAgent"] == "skipped"
    trace_agents = {
        trace.agent_name
        for trace in TraceService(db_session).list_for_task(
            task.task_id,
            run_id=result["run_id"],
        )
    }
    assert trace_agents == {"PlannerAgent", "CollectorAgent", "QaAgent", "WorkflowEngine"}
