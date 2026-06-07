from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.base import AgentExecutionError
from app.agents.collector import CollectorAgent
from app.agents.langgraph_runner import LangGraphWorkflowRunner
from app.database import Base
from app.schemas import (
    AnalysisDimension,
    AnalysisDimensionPlan,
    CollectorInput,
    CollectorOutput,
    CreateTaskRequest,
    PlannerCollectionPlan,
    PlannerCollectionPlanItem,
    Task,
)
from app.services.trace_service import TraceService
from app.services.task_service import TaskService
from app.services.web_search_client import SearchResult, WebSearchResponse


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


@pytest.fixture()
def task():
    now = datetime.utcnow()
    return Task(
        task_id="task_collector_plan",
        product_name="测试产品",
        competitors=["竞品A"],
        region="中国",
        industry="测试行业",
        status="running",
        created_at=now,
        updated_at=now,
    )


def collection_plan() -> PlannerCollectionPlan:
    return PlannerCollectionPlan(
        collector_search_plan={
            "竞品A": {
                "feature": PlannerCollectionPlanItem(
                    dimension_id="feature",
                    label="功能",
                    queries=["竞品A 官方功能参数"],
                    research_goals=["确认公开功能"],
                    source="llm",
                ),
                "pricing": PlannerCollectionPlanItem(
                    dimension_id="pricing",
                    label="价格",
                    queries=["竞品A 官方价格"],
                    research_goals=["确认公开价格"],
                    source="llm",
                ),
            }
        }
    )


def dimension_plan() -> AnalysisDimensionPlan:
    return AnalysisDimensionPlan(
        selected_dimensions=["feature", "pricing"],
        dimension_plans=[
            AnalysisDimension(
                dimension_id="feature",
                label="功能",
                required=True,
                query_templates=["{competitor} 官方功能参数"],
            ),
            AnalysisDimension(
                dimension_id="pricing",
                label="价格",
                required=True,
                query_templates=["{competitor} 官方价格"],
            ),
        ],
    )


class RecordingSearchClient:
    provider = "fake"
    api_key = "fake"
    base_url = "https://search.example"

    def __init__(self):
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 8) -> WebSearchResponse:
        self.queries.append(query)
        return WebSearchResponse(
            available=True,
            attempted=True,
            success=True,
            results=[
                SearchResult(
                    title=f"{query} 公开资料",
                    url=f"https://competitor-a.example/{len(self.queries)}",
                    snippet=f"竞品A {query} 的公开资料和产品说明，包含足够的测试信息。",
                    score=0.9,
                )
            ],
        )


def test_collector_input_accepts_new_collection_plan(task):
    value = CollectorInput(
        task=task,
        collection_plan=collection_plan(),
        selected_dimensions=["feature", "pricing"],
        analysis_dimension_plan=dimension_plan(),
    )

    assert value.collection_plan.collector_search_plan["竞品A"]["feature"].queries == [
        "竞品A 官方功能参数"
    ]
    assert value.selected_dimensions == ["feature", "pricing"]


def test_collector_uses_only_planner_collection_plan_and_records_metadata(db_session, task):
    search_client = RecordingSearchClient()
    output = CollectorAgent(
        TraceService(db_session),
        web_search_client=search_client,
    ).run(
        CollectorInput(
            task=task,
            collector_mode="web",
            collection_plan=collection_plan(),
            selected_dimensions=["feature", "pricing"],
            analysis_dimension_plan=dimension_plan(),
        )
    )

    assert search_client.queries == ["竞品A 官方功能参数", "竞品A 官方价格"]
    assert output.diagnostics["collection_plan_used"] is True
    assert output.diagnostics["collector_search_plan_source"] == "planner_collection_plan"
    assert output.diagnostics["collector_search_plan_missing"] is False
    assert output.diagnostics["query_count_by_competitor"] == {"竞品A": 2}
    assert output.diagnostics["query_count_by_dimension"] == {"feature": 1, "pricing": 1}
    assert output.diagnostics["evidence_count_by_competitor"] == {"竞品A": 2}
    assert output.diagnostics["evidence_count_by_dimension"] == {"feature": 1, "pricing": 1}
    metadata = output.evidence[0].entity_match_signals
    assert metadata["collector_query"] == "竞品A 官方功能参数"
    assert metadata["collector_dimension"] == "feature"
    assert metadata["planner_dimension"] == "feature"
    assert metadata["collector_query_source"] == "planner_collection_plan"


def test_missing_collection_plan_fails_without_default_query_fallback(db_session, task):
    search_client = RecordingSearchClient()
    collector = CollectorAgent(TraceService(db_session), web_search_client=search_client)

    with pytest.raises(AgentExecutionError, match="collector_search_plan_missing"):
        collector.run(CollectorInput(task=task, collector_mode="web"))

    assert search_client.queries == []
    trace = TraceService(db_session).list_for_task(task.task_id)[0]
    assert trace.schema_validation_result == "failed"
    assert trace.error_message == "collector_search_plan_missing"
    assert "collector_search_plan_missing" in trace.output_summary


def test_analysis_plan_query_hints_and_metadata_are_not_used(db_session, task):
    legacy_plan = dimension_plan().model_copy(
        update={
            "query_hints": {"竞品A": ["旧 query_hints 搜索词"]},
            "metadata": {
                "collector_search_plan": {
                    "竞品A": {
                        "feature": {
                            "dimension_id": "feature",
                            "label": "功能",
                            "queries": ["旧 metadata 搜索词"],
                        }
                    }
                }
            },
        }
    )
    search_client = RecordingSearchClient()

    with pytest.raises(AgentExecutionError, match="collector_search_plan_missing"):
        CollectorAgent(TraceService(db_session), web_search_client=search_client).run(
            CollectorInput(
                task=task,
                collector_mode="web",
                analysis_dimension_plan=legacy_plan,
            )
        )

    assert search_client.queries == []


def test_langgraph_collector_node_passes_new_planner_fields(db_session):
    stored_task = TaskService(db_session).create_task(
        CreateTaskRequest(
            product_name="测试产品",
            competitors=["竞品A"],
            region="中国",
            industry="测试行业",
        )
    )
    runner = LangGraphWorkflowRunner(db_session)
    captured: dict[str, CollectorInput] = {}

    class CapturingCollector:
        def run(self, input_data: CollectorInput) -> CollectorOutput:
            captured["input"] = input_data
            return CollectorOutput(evidence=[], diagnostics={})

    runner.collector = CapturingCollector()
    plan = collection_plan()
    analysis_plan = dimension_plan()

    runner.collector_node(
        {
            "task_id": stored_task.task_id,
            "run_id": "run_planner_collector",
            "demo_mode": "normal",
            "rework_count": 0,
            "collector_mode": "web",
            "collection_plan": plan,
            "selected_dimensions": ["feature", "pricing"],
            "analysis_dimension_plan": analysis_plan,
            "rework_context": None,
            "competitor_aliases": {"竞品A": ["Competitor A"]},
            "node_sequence": ["planner"],
        }
    )

    input_data = captured["input"]
    assert input_data.collection_plan == plan
    assert input_data.selected_dimensions == ["feature", "pricing"]
    assert input_data.analysis_dimension_plan == analysis_plan
    assert input_data.rework_context is None
