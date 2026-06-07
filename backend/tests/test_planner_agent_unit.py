import json
from datetime import datetime
from pathlib import Path

from app.agents.planner import PlannerAgent
from app.schemas import PlannerInput, PlannerOutput, Task
from app.services.llm_client import LlmResponse


class FakeTraceService:
    def __init__(self):
        self.saved = []
        self.current_run_id = None

    def set_run_context(self, run_id: str | None) -> None:
        self.current_run_id = run_id

    def save(self, trace):
        if trace.run_id is None and self.current_run_id:
            trace = trace.model_copy(update={"run_id": self.current_run_id})
        self.saved.append(trace)
        return trace


class FakeLlmClient:
    def __init__(self, *, content: str | None = None, available: bool = True, fallback_reason: str | None = None):
        self.content = content
        self.is_available = available
        self.fallback_reason = fallback_reason
        self.provider = "fake"
        self.model = "fake-model"
        self.base_url = "https://fake.local/v1"

    def chat_json(self, messages, timeout: float | None = None):
        if not self.is_available:
            return LlmResponse(
                available=False,
                attempted=False,
                success=False,
                fallback_reason=self.fallback_reason or "fake llm unavailable",
                provider=self.provider,
                model=self.model,
            )
        return LlmResponse(
            available=True,
            attempted=True,
            success=True,
            content=self.content,
            elapsed_time_ms=5,
            response_preview=(self.content or "")[:500],
            provider=self.provider,
            model=self.model,
        )


def make_task() -> Task:
    now = datetime.utcnow()
    return Task(
        task_id="task_planner_unit",
        product_name="充电器",
        competitors=["绿联140w智显充", "Anker140w充电器"],
        region="中国",
        industry="充电器",
        status="created",
        rework_count=0,
        created_at=now,
        updated_at=now,
    )


def run_planner(client: FakeLlmClient) -> PlannerOutput:
    agent = PlannerAgent(FakeTraceService(), llm_client=client)
    return agent.run(PlannerInput(task=make_task(), run_id="run_planner_unit"))


def llm_payload() -> dict:
    return {
        "planner_summary": {
            "intent_classification": "competitive_analysis",
            "product_type": "大功率氮化镓充电器",
            "task_goal": "比较两款 140W 充电器的产品能力和市场表现。",
        },
        "selected_dimensions": [
            "pricing",
            "feature",
            "persona",
            "strength",
            "weakness",
            "opportunity",
            "threat",
            "charging_protocol",
        ],
        "dimension_suggestions": [
            {
                "dimension_id": "charging_protocol",
                "label": "充电协议兼容性",
                "description": "比较支持的快充协议、功率分配和设备兼容性。",
                "keywords": ["PD 3.1", "快充协议", "兼容性"],
                "query_templates": [
                    "{competitor} PD 3.1 协议",
                    "{competitor} 兼容设备 功率分配",
                ],
                "research_goals": ["确认协议支持范围", "确认多口功率分配策略"],
            }
        ],
        "missing_information": [],
        "planner_notes": ["增加充电协议兼容性作为行业动态维度。"],
    }


def test_planner_output_has_only_new_top_level_fields():
    output = run_planner(FakeLlmClient(available=False))

    assert set(output.model_dump()) == {
        "planner_summary",
        "selected_dimensions",
        "analysis_dimension_plan",
        "collection_plan",
        "downstream_guidance",
        "missing_information",
        "planner_notes",
        "diagnostics",
    }
    assert set(PlannerOutput.model_fields) == set(output.model_dump())


def test_deterministic_fallback_generates_base_dimensions_and_collection_plan():
    output = run_planner(FakeLlmClient(available=False, fallback_reason="disabled"))

    assert output.selected_dimensions[:7] == list(PlannerAgent.BASE_DIMENSIONS)
    assert output.diagnostics["planner_mode_used"] == "deterministic"
    assert output.diagnostics["llm_schema_validation_success"] is False
    assert output.diagnostics["fallback_used"] is True
    assert output.diagnostics["collection_plan_generated"] is True
    for competitor in make_task().competitors:
        assert set(PlannerAgent.BASE_DIMENSIONS).issubset(output.collection_plan[competitor])


def test_llm_simple_payload_is_normalized_into_full_planner_output():
    output = run_planner(FakeLlmClient(content=json.dumps(llm_payload(), ensure_ascii=False)))

    assert output.diagnostics["planner_mode_used"] == "llm"
    assert output.diagnostics["llm_schema_validation_success"] is True
    assert "charging_protocol" in output.selected_dimensions
    dimension = next(
        item for item in output.analysis_dimension_plan.dimension_plans
        if item.dimension_id == "charging_protocol"
    )
    assert all("{competitor}" in template for template in dimension.query_templates)
    assert output.collection_plan["绿联140w智显充"]["charging_protocol"].queries[0].startswith("绿联140w智显充")


def test_parser_accepts_json_code_fence():
    content = f"```json\n{json.dumps(llm_payload(), ensure_ascii=False)}\n```"
    output = run_planner(FakeLlmClient(content=content))

    assert output.diagnostics["llm_schema_validation_success"] is True
    assert output.diagnostics["fallback_used"] is False


def test_parser_extracts_json_from_surrounding_text():
    content = f"以下是结果：\n{json.dumps(llm_payload(), ensure_ascii=False)}\n结束。"
    output = run_planner(FakeLlmClient(content=content))

    assert output.diagnostics["llm_schema_validation_success"] is True
    assert "charging_protocol" in output.selected_dimensions


def test_invalid_json_falls_back_and_keeps_debug_preview():
    content = '{"planner_summary": {"task_goal": "broken}'
    output = run_planner(FakeLlmClient(content=content))

    assert output.diagnostics["planner_mode_used"] == "deterministic"
    assert output.diagnostics["llm_schema_validation_success"] is False
    assert output.diagnostics["llm_schema_validation_errors"]
    assert output.diagnostics["llm_response_preview"] == content
    assert output.diagnostics["llm_fallback_reason"]


def test_prompt_requires_simple_strict_json_contract():
    agent = PlannerAgent(FakeTraceService(), llm_client=FakeLlmClient(available=False))
    prompt = "\n".join(message["content"] for message in agent._messages(make_task()))

    assert "只输出一个合法 JSON object" in prompt
    assert "不要使用 ```json 包裹" in prompt
    assert "JSON key 必须使用英文" in prompt
    assert "不允许 trailing comma" in prompt
    assert "dimension_suggestions" in prompt
    assert "不要生成 analysis_dimension_plan" in prompt


def test_output_contains_no_legacy_planner_or_claim_fields():
    output_json = output_text = json.dumps(
        run_planner(FakeLlmClient(content=json.dumps(llm_payload(), ensure_ascii=False))).model_dump(mode="json"),
        ensure_ascii=False,
    )

    for field_name in (
        '"dag"',
        '"plan"',
        '"ambiguity_level"',
        '"scope_type"',
        '"survey_needed"',
        '"candidate_competitors"',
        '"claims"',
    ):
        assert field_name not in output_json
    assert "traceable claims" not in output_text
    assert "source-bound claims" not in output_text


def test_documented_planner_output_example_is_valid_json_and_schema():
    example_path = Path(__file__).resolve().parents[2] / "docs" / "planner_output_example.json"
    payload = json.loads(example_path.read_text(encoding="utf-8"))

    output = PlannerOutput.model_validate(payload)
    assert output.planner_summary.product_name == "充电器"
    assert output.collection_plan["Anker140w充电器"]["feature"].queries
