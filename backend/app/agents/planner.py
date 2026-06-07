from __future__ import annotations

import json
import re
from typing import Any

from app.agents.base import run_with_trace
from app.schemas import (
    AnalysisDimension,
    AnalysisDimensionPlan,
    PlannerCollectionPlanItem,
    PlannerDownstreamGuidance,
    PlannerInput,
    PlannerOutput,
    PlannerSummary,
    Task,
)
from app.services.llm_client import LlmClient
from app.services.trace_service import TraceService


class PlannerAgent:
    name = "PlannerAgent"
    BASE_DIMENSIONS = (
        "pricing",
        "feature",
        "persona",
        "strength",
        "weakness",
        "opportunity",
        "threat",
    )
    BASE_DIMENSION_DEFINITIONS = {
        "pricing": {
            "label": "定价与商业模式",
            "description": "调研价格、套餐、计费方式、商业模式和购买门槛。",
            "keywords": ["价格", "定价", "套餐", "商业模式"],
            "query_templates": ["{competitor} 价格", "{competitor} 官方 价格", "{competitor} 套餐 商业模式"],
            "research_goals": ["确认公开价格与套餐结构", "识别计费方式和商业模式"],
        },
        "feature": {
            "label": "功能能力",
            "description": "调研核心功能、产品能力、参数和差异化能力。",
            "keywords": ["功能", "能力", "参数", "产品介绍"],
            "query_templates": ["{competitor} 功能", "{competitor} 官方 功能 参数", "{competitor} 产品能力 公开文档"],
            "research_goals": ["确认核心功能与关键参数", "识别差异化产品能力"],
        },
        "persona": {
            "label": "用户画像与目标场景",
            "description": "调研目标用户、典型场景、使用需求和购买动机。",
            "keywords": ["目标用户", "用户画像", "使用场景", "客户案例"],
            "query_templates": ["{competitor} 目标用户", "{competitor} 用户画像", "{competitor} 使用场景 客户案例"],
            "research_goals": ["识别目标用户群体", "确认典型使用场景和需求"],
        },
        "strength": {
            "label": "优势",
            "description": "调研产品相对竞品的核心优势、口碑亮点和竞争壁垒。",
            "keywords": ["优势", "亮点", "口碑", "竞争力"],
            "query_templates": ["{competitor} 优势", "{competitor} 核心优势 评测", "{competitor} 用户好评 亮点"],
            "research_goals": ["识别有证据支持的竞争优势", "区分产品宣传与外部评价"],
        },
        "weakness": {
            "label": "劣势",
            "description": "调研产品缺点、限制、投诉、风险和用户痛点。",
            "keywords": ["缺点", "不足", "投诉", "痛点"],
            "query_templates": ["{competitor} 缺点", "{competitor} 不足 评测", "{competitor} 用户投诉 痛点"],
            "research_goals": ["识别公开可验证的产品短板", "收集用户反馈中的主要痛点"],
        },
        "opportunity": {
            "label": "机会",
            "description": "调研市场增长、需求变化、技术趋势和潜在扩展空间。",
            "keywords": ["市场机会", "增长", "趋势", "潜在场景"],
            "query_templates": ["{competitor} 市场机会", "{competitor} 增长趋势", "{competitor} 潜在应用场景"],
            "research_goals": ["识别市场与技术带来的增长机会", "发现潜在用户和应用场景"],
        },
        "threat": {
            "label": "威胁",
            "description": "调研竞争压力、替代品、监管变化和外部风险。",
            "keywords": ["竞争威胁", "替代品", "监管风险", "市场风险"],
            "query_templates": ["{competitor} 竞争威胁", "{competitor} 替代产品", "{competitor} 市场风险 监管"],
            "research_goals": ["识别主要竞争与替代威胁", "确认监管和市场外部风险"],
        },
    }

    def __init__(self, trace_service: TraceService, llm_client: LlmClient | None = None):
        self.trace_service = trace_service
        self.llm_client = llm_client or LlmClient()

    def run(self, input_data: PlannerInput) -> PlannerOutput:
        task = input_data.task
        return run_with_trace(
            trace_service=self.trace_service,
            task_id=task.task_id,
            run_id=input_data.run_id,
            agent_name=self.name,
            to_agent="CollectorAgent",
            message_type="plan",
            schema_name="PlannerOutput",
            input_summary=(
                f"{task.product_name} vs {', '.join(task.competitors)}; "
                f"region={task.region}; industry={task.industry}"
            ),
            retry_count=input_data.retry_count,
            fn=lambda: self._plan(task),
        )

    def _plan(self, task: Task) -> PlannerOutput:
        diagnostics = self._base_diagnostics()
        response = self.llm_client.chat_json(self._messages(task))
        diagnostics.update(
            {
                "llm_call_attempted": response.attempted,
                "llm_call_success": response.success,
                "llm_elapsed_time_ms": response.elapsed_time_ms,
                "llm_error_type": response.error_type,
                "llm_error_message": response.error_message,
                "llm_response_preview": response.response_preview,
            }
        )

        if not response.available:
            reason = response.fallback_reason or "Planner LLM is unavailable"
            diagnostics.update(
                {
                    "planner_mode_used": "deterministic",
                    "llm_schema_validation_success": False,
                    "llm_schema_validation_errors": [reason],
                    "fallback_used": True,
                    "llm_fallback_reason": reason,
                }
            )
            return self._deterministic_output(task, diagnostics, reason)

        try:
            payload = self._parse_planner_json(response.content or "")
            diagnostics.update(
                {
                    "planner_mode_used": "llm",
                    "llm_schema_validation_success": True,
                    "llm_schema_validation_errors": [],
                }
            )
            return self._build_planner_output(task, payload, diagnostics, source="llm")
        except Exception as exc:  # noqa: BLE001 - planner must preserve the workflow fallback.
            reason = f"Planner LLM output validation failed: {exc}"
            diagnostics.update(
                {
                    "planner_mode_used": "deterministic",
                    "llm_schema_validation_success": False,
                    "llm_schema_validation_errors": [str(exc)],
                    "fallback_used": True,
                    "llm_fallback_reason": reason,
                }
            )
            return self._deterministic_output(task, diagnostics, reason)

    def _deterministic_output(
        self,
        task: Task,
        diagnostics: dict[str, Any],
        fallback_reason: str,
    ) -> PlannerOutput:
        payload = {
            "planner_summary": {
                "intent_classification": "competitive_analysis",
                "product_type": task.industry,
                "task_goal": f"围绕 {task.product_name} 对 {', '.join(task.competitors)} 开展结构化竞品调研。",
            },
            "selected_dimensions": list(self.BASE_DIMENSIONS),
            "dimension_suggestions": [],
            "missing_information": [],
            "planner_notes": [
                "Planner 使用确定性回退生成基础维度与采集计划。",
                fallback_reason,
            ],
        }
        return self._build_planner_output(task, payload, diagnostics, source="deterministic")

    def _build_planner_output(
        self,
        task: Task,
        payload: dict[str, Any],
        diagnostics: dict[str, Any],
        *,
        source: str,
    ) -> PlannerOutput:
        raw_summary = payload.get("planner_summary")
        raw_summary = raw_summary if isinstance(raw_summary, dict) else {}
        planner_summary = PlannerSummary(
            intent_classification=self._safe_text(
                raw_summary.get("intent_classification"),
                fallback="competitive_analysis",
            ),
            product_name=task.product_name,
            industry=task.industry,
            region=task.region,
            competitors=self._valid_competitors(task.competitors),
            product_type=self._safe_text(raw_summary.get("product_type"), fallback=task.industry),
            task_goal=self._safe_text(
                raw_summary.get("task_goal"),
                fallback=f"围绕 {task.product_name} 对 {', '.join(task.competitors)} 开展结构化竞品调研。",
            ),
        )
        suggestions = self._normalize_dimension_suggestions(payload.get("dimension_suggestions"))
        selected_dimensions = self._normalize_selected_dimensions(
            payload.get("selected_dimensions"),
            suggestions,
        )
        dimensions = [
            self._build_dimension_definition(
                dimension_id,
                suggestions.get(dimension_id),
                source=source,
                priority=index + 1,
            )
            for index, dimension_id in enumerate(selected_dimensions)
        ]
        collection_plan = self._build_collection_plan(task.competitors, dimensions)
        serialized_collection_plan = {
            competitor: {
                dimension_id: item.model_dump(mode="json")
                for dimension_id, item in by_dimension.items()
            }
            for competitor, by_dimension in collection_plan.items()
        }
        analysis_dimension_plan = AnalysisDimensionPlan(
            selected_dimensions=selected_dimensions,
            dimension_plans=dimensions,
            research_goals=[
                goal
                for dimension in dimensions
                for goal in dimension.research_goals
            ],
            query_hints={
                competitor: [
                    query
                    for item in by_dimension.values()
                    for query in item.queries
                ]
                for competitor, by_dimension in collection_plan.items()
            },
            metadata={
                "collector_search_plan": serialized_collection_plan,
                "collection_plan_source": "PlannerOutput.collection_plan",
            },
        )
        diagnostics.update(
            {
                "selected_dimension_count": len(selected_dimensions),
                "selected_dimensions": selected_dimensions,
                "collection_plan_generated": bool(collection_plan),
                "fallback_used": source == "deterministic",
            }
        )
        notes = self._normalize_string_list(payload.get("planner_notes"))
        notes.append(
            "LLM 仅提供任务理解和维度建议；完整维度计划、搜索计划与下游指导由后端规范化生成。"
        )
        return PlannerOutput(
            planner_summary=planner_summary,
            selected_dimensions=selected_dimensions,
            analysis_dimension_plan=analysis_dimension_plan,
            collection_plan=collection_plan,
            downstream_guidance=self._build_downstream_guidance(selected_dimensions),
            missing_information=self._normalize_string_list(payload.get("missing_information")),
            planner_notes=self._dedupe(notes),
            diagnostics=diagnostics,
        )

    def _parse_planner_json(self, content: str) -> dict[str, Any]:
        text = content.strip()
        fenced = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fenced:
            text = fenced.group(1).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("Planner LLM output does not contain a JSON object")
            payload = json.loads(text[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("Planner LLM output must be a JSON object")
        allowed = {
            "planner_summary",
            "selected_dimensions",
            "dimension_suggestions",
            "missing_information",
            "planner_notes",
        }
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise ValueError(
                f"Planner LLM output contains unsupported fields: {', '.join(unexpected)}"
            )
        return payload

    def _normalize_dimension_suggestions(self, raw: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, list):
            return {}
        suggestions: dict[str, dict[str, Any]] = {}
        for item in raw[:12]:
            if not isinstance(item, dict):
                continue
            dimension_id = self._normalize_dimension_id(item.get("dimension_id"))
            if dimension_id:
                suggestions[dimension_id] = item
        return suggestions

    def _normalize_selected_dimensions(
        self,
        raw: Any,
        suggestions: dict[str, dict[str, Any]],
    ) -> list[str]:
        selected = list(self.BASE_DIMENSIONS)
        candidates = raw if isinstance(raw, list) else []
        for value in [*candidates, *suggestions.keys()]:
            dimension_id = self._normalize_dimension_id(value)
            if dimension_id and dimension_id not in selected:
                selected.append(dimension_id)
        return selected[:15]

    @staticmethod
    def _normalize_dimension_id(value: Any) -> str | None:
        dimension_id = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", dimension_id):
            return None
        return dimension_id

    def _build_dimension_definition(
        self,
        dimension_id: str,
        suggestion: dict[str, Any] | None,
        *,
        source: str,
        priority: int,
    ) -> AnalysisDimension:
        base = self.BASE_DIMENSION_DEFINITIONS.get(dimension_id, {})
        suggestion = suggestion or {}
        label = self._safe_text(
            suggestion.get("label"),
            fallback=base.get("label") or dimension_id,
        )
        description = self._safe_text(
            suggestion.get("description"),
            fallback=base.get("description") or f"调研 {label} 相关的公开信息。",
        )
        keywords = self._normalize_string_list(suggestion.get("keywords"))
        if not keywords:
            keywords = list(base.get("keywords", []))
        templates = self._normalize_query_templates(
            suggestion.get("query_templates"),
            fallback=base.get("query_templates") or [f"{{competitor}} {label}"],
        )
        research_goals = self._normalize_string_list(suggestion.get("research_goals"))
        if not research_goals:
            research_goals = list(
                base.get("research_goals", [f"确认 {label} 的公开事实和证据"])
            )
        return AnalysisDimension(
            dimension_id=dimension_id,
            label=label,
            description=description,
            required=dimension_id in self.BASE_DIMENSIONS,
            priority=priority,
            keywords=keywords,
            query_templates=templates,
            research_goals=research_goals,
            source="base" if dimension_id in self.BASE_DIMENSIONS else source,
        )

    def _normalize_query_templates(self, raw: Any, *, fallback: list[str]) -> list[str]:
        templates = self._normalize_string_list(raw) or fallback
        normalized: list[str] = []
        for template in templates[:5]:
            value = template.strip()
            if "{competitor}" not in value:
                value = f"{{competitor}} {value}"
            if value not in normalized:
                normalized.append(value)
        return normalized or ["{competitor} 公开信息"]

    def _build_collection_plan(
        self,
        competitors: list[str],
        dimensions: list[AnalysisDimension],
    ) -> dict[str, dict[str, PlannerCollectionPlanItem]]:
        plan: dict[str, dict[str, PlannerCollectionPlanItem]] = {}
        for competitor in self._valid_competitors(competitors):
            plan[competitor] = {}
            for dimension in dimensions:
                plan[competitor][dimension.dimension_id] = PlannerCollectionPlanItem(
                    dimension_id=dimension.dimension_id,
                    label=dimension.label,
                    queries=[
                        template.replace("{competitor}", competitor)
                        for template in dimension.query_templates
                    ],
                    research_goals=dimension.research_goals,
                    source=dimension.source,
                )
        return plan

    @staticmethod
    def _build_downstream_guidance(
        selected_dimensions: list[str],
    ) -> PlannerDownstreamGuidance:
        dimension_text = ", ".join(selected_dimensions)
        return PlannerDownstreamGuidance(
            collector=[
                "严格按 collection_plan 中的 competitor × dimension 搜索计划采集公开信息。",
                "保留每条搜索结果对应的 competitor、dimension_id、query 和来源元数据。",
            ],
            analyst=[
                f"按选定维度抽取结构化事实：{dimension_text}。",
                "当前运行证据优先；证据不足时明确标记，不补写未经证实的事实。",
            ],
            writer=[
                "按 selected_dimensions 组织中文竞品分析报告。",
                "仅使用结构化分析结果和可追溯证据撰写结论。",
            ],
            qa=[
                "检查基础维度是否齐全，以及每个竞品是否都有对应采集计划。",
                "检查 query_templates 是否保留 {competitor} 占位符，collection_plan 是否正确展开。",
            ],
            survey=[],
        )

    def _base_diagnostics(self) -> dict[str, Any]:
        return {
            "planner_mode_requested": "llm",
            "planner_mode_used": "deterministic",
            "llm_call_attempted": False,
            "llm_call_success": False,
            "llm_schema_validation_success": None,
            "llm_schema_validation_errors": [],
            "fallback_used": False,
            "llm_fallback_reason": None,
            "selected_dimension_count": 0,
            "collection_plan_generated": False,
            "llm_enabled": self.llm_client.is_available,
            "llm_provider": self.llm_client.provider,
            "llm_model": self.llm_client.model,
            "llm_base_url_configured": bool(self.llm_client.base_url),
            "has_api_key": self.llm_client.is_available,
            "llm_elapsed_time_ms": 0,
            "llm_error_type": None,
            "llm_error_message": None,
            "llm_response_preview": None,
        }

    def _messages(self, task: Task) -> list[dict[str, str]]:
        system = (
            "你是企业竞品分析系统的 PlannerAgent。你的职责仅限于理解任务和建议分析维度。\n"
            "固定基础维度 pricing、feature、persona、strength、weakness、opportunity、threat 必须全部保留。\n"
            "可以根据行业和竞品增加动态维度，但不要生成 DAG、报告、结论、问卷或证据。\n\n"
            "JSON 输出规则：\n"
            "1. 只输出一个合法 JSON object，不要输出解释文字。\n"
            "2. 不要使用 Markdown 代码块，不要使用 ```json 包裹。\n"
            "3. JSON key 必须使用英文。\n"
            "4. 所有字符串必须使用双引号，不允许单引号。\n"
            "5. 不允许 trailing comma，不允许中文冒号，不允许注释。\n"
            "6. 不允许省略字段名，不允许在 JSON 外输出任何自然语言。\n"
            "7. 只生成 planner_summary、selected_dimensions、dimension_suggestions、missing_information、planner_notes 五个顶层字段。\n"
            "8. 不要生成 analysis_dimension_plan、collection_plan、downstream_guidance 或 diagnostics；这些字段由后端规范化生成。"
        )
        user = (
            "请根据以下任务返回简单 JSON：\n"
            f"product_name: {task.product_name}\n"
            f"industry: {task.industry}\n"
            f"region: {task.region}\n"
            f"competitors: {json.dumps(task.competitors, ensure_ascii=False)}\n\n"
            "输出结构：\n"
            "{\n"
            '  "planner_summary": {\n'
            '    "intent_classification": "competitive_analysis",\n'
            '    "product_type": "产品类型",\n'
            '    "task_goal": "中文任务目标"\n'
            "  },\n"
            '  "selected_dimensions": ["pricing", "feature", "persona", "strength", "weakness", "opportunity", "threat"],\n'
            '  "dimension_suggestions": [\n'
            "    {\n"
            '      "dimension_id": "dynamic_dimension_id",\n'
            '      "label": "中文名称",\n'
            '      "description": "中文说明",\n'
            '      "keywords": ["关键词"],\n'
            '      "query_templates": ["{competitor} 中文搜索主题", "{competitor} 官方 中文搜索主题"],\n'
            '      "research_goals": ["中文研究目标"]\n'
            "    }\n"
            "  ],\n"
            '  "missing_information": [],\n'
            '  "planner_notes": ["中文规划说明"]\n'
            "}\n\n"
            'dimension_suggestions 只放动态扩展维度；所有 query_templates 必须包含原样的 "{competitor}" 占位符。'
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    @staticmethod
    def _valid_competitors(values: list[str]) -> list[str]:
        return [value.strip() for value in values if isinstance(value, str) and value.strip()]

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [
            str(item).strip()
            for item in value
            if isinstance(item, (str, int, float)) and str(item).strip()
        ]

    @staticmethod
    def _safe_text(value: Any, *, fallback: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))
