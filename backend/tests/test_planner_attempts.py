import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.schemas import (
    AnalysisDimension,
    AnalysisDimensionPlan,
    PlannerCollectionPlanItem,
    PlannerCollectionPlan,
    PlannerDownstreamGuidance,
    PlannerOutput,
    PlannerSummary,
)
from app.services.planner_attempt_service import PlannerAttemptService, RAW_LLM_MAX_CHARS


BASE_DIMENSIONS = (
    "pricing",
    "feature",
    "persona",
    "strength",
    "weakness",
    "opportunity",
    "threat",
)


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


def make_planner_output(*, mode: str = "llm", fallback_used: bool = False) -> PlannerOutput:
    dimensions = [
        AnalysisDimension(
            dimension_id=dimension_id,
            label=dimension_id,
            required=True,
            priority=index,
            keywords=[dimension_id],
            query_templates=[f"{{competitor}} {dimension_id}"],
            research_goals=[f"research {dimension_id}"],
            source=mode,
        )
        for index, dimension_id in enumerate(BASE_DIMENSIONS, start=1)
    ]
    collection_plan = {
        "竞品A": {
            dimension_id: PlannerCollectionPlanItem(
                dimension_id=dimension_id,
                label=dimension_id,
                queries=[f"竞品A {dimension_id}"],
                research_goals=[f"research {dimension_id}"],
                source=mode,
            )
            for dimension_id in BASE_DIMENSIONS
        }
    }
    diagnostics = {
        "planner_mode_requested": "llm",
        "planner_mode_used": mode,
        "fallback_used": fallback_used,
        "selected_dimension_count": len(BASE_DIMENSIONS),
        "collection_plan_generated": True,
    }
    return PlannerOutput(
        planner_summary=PlannerSummary(
            product_name="测试产品",
            industry="测试行业",
            region="中国",
            competitors=["竞品A"],
            product_type="测试产品",
            task_goal="生成竞品调研计划",
        ),
        selected_dimensions=list(BASE_DIMENSIONS),
        analysis_dimension_plan=AnalysisDimensionPlan(
            selected_dimensions=list(BASE_DIMENSIONS),
            dimension_plans=dimensions,
        ),
        collection_plan=PlannerCollectionPlan(collector_search_plan=collection_plan),
        downstream_guidance=PlannerDownstreamGuidance(
            collector=["按计划采集"],
            analyst=["按维度分析"],
            writer=["按维度撰写"],
            qa=["校验覆盖率"],
        ),
        missing_information=[],
        planner_notes=["专项测试"],
        diagnostics=diagnostics,
    )


def test_attempt_numbers_increment_independently_per_run(db_session):
    service = PlannerAttemptService(db_session)
    output = make_planner_output()

    first_a = service.save(
        run_id="run_a",
        status="generated",
        planner_output=output,
        diagnostics=output.diagnostics,
    )
    first_b = service.save(
        run_id="run_b",
        status="generated",
        planner_output=output,
        diagnostics=output.diagnostics,
    )
    second_a = service.save(
        run_id="run_a",
        status="generated",
        planner_output=output,
        diagnostics=output.diagnostics,
    )

    assert (first_a.attempt_no, second_a.attempt_no) == (1, 2)
    assert first_b.attempt_no == 1


def test_all_statuses_and_normalized_payload_are_persisted(db_session):
    service = PlannerAttemptService(db_session)
    generated_output = make_planner_output()
    fallback_output = make_planner_output(mode="deterministic", fallback_used=True)

    generated = service.save(
        run_id="run_status",
        status="generated",
        planner_output=generated_output,
        diagnostics=generated_output.diagnostics,
    )
    fallback = service.save(
        run_id="run_status",
        status="fallback",
        planner_output=fallback_output,
        diagnostics=fallback_output.diagnostics,
    )
    failed = service.save(
        run_id="run_status",
        status="failed",
        planner_output=None,
        diagnostics={"planner_mode_used": "failed", "error_message": "planner failed"},
    )

    assert [generated.status, fallback.status, failed.status] == [
        "generated",
        "fallback",
        "failed",
    ]
    assert generated.planner_output["selected_dimensions"] == list(BASE_DIMENSIONS)
    assert generated.planner_output["collection_plan"]["collector_search_plan"]["竞品A"]["pricing"]["queries"] == [
        "竞品A pricing"
    ]
    assert generated.diagnostics == generated_output.diagnostics
    assert fallback.diagnostics["fallback_used"] is True
    assert failed.planner_output == {}
    assert failed.diagnostics["error_message"] == "planner failed"


def test_raw_llm_response_is_opt_in_and_truncated(db_session, monkeypatch):
    service = PlannerAttemptService(db_session)
    output = make_planner_output()
    raw_response = "x" * (RAW_LLM_MAX_CHARS + 20)

    monkeypatch.delenv("PLANNER_TRACE_RAW_LLM", raising=False)
    disabled = service.save(
        run_id="run_raw",
        status="generated",
        planner_output=output,
        diagnostics=output.diagnostics,
        raw_llm_response=raw_response,
    )

    monkeypatch.setenv("PLANNER_TRACE_RAW_LLM", "true")
    enabled = service.save(
        run_id="run_raw",
        status="generated",
        planner_output=output,
        diagnostics=output.diagnostics,
        raw_llm_response=raw_response,
    )

    assert disabled.raw_llm_response is None
    assert enabled.raw_llm_response == raw_response[:RAW_LLM_MAX_CHARS]


def test_list_for_run_is_scoped_and_ordered_by_attempt_number(db_session):
    service = PlannerAttemptService(db_session)
    output = make_planner_output()
    for status in ("generated", "fallback", "failed"):
        service.save(
            run_id="run_ordered",
            status=status,
            planner_output=output if status != "failed" else None,
            diagnostics={"planner_mode_used": status},
        )
    service.save(
        run_id="run_other",
        status="generated",
        planner_output=output,
        diagnostics=output.diagnostics,
    )

    attempts = service.list_for_run("run_ordered")

    assert [attempt.attempt_no for attempt in attempts] == [1, 2, 3]
    assert [attempt.status for attempt in attempts] == ["generated", "fallback", "failed"]
    assert all(attempt.run_id == "run_ordered" for attempt in attempts)
    assert [attempt.created_at for attempt in attempts] == sorted(
        attempt.created_at for attempt in attempts
    )
