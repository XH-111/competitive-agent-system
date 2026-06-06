from typing import Any

from app.agents.base import AgentExecutionError, AgentOutputValidationError, run_with_trace
from app.schemas import QaResult, Report, ReportWriterInput, ReportWriterOutput, SwotAnalysis, SwotItem
from app.services.llm_client import LlmClient, parse_llm_json
from app.services.trace_service import TraceService


class ReportWriterAgent:
    name = "ReportWriterAgent"

    def __init__(self, trace_service: TraceService, llm_client: LlmClient | None = None):
        self.trace_service = trace_service
        self.llm_client = llm_client or LlmClient()

    def run(self, input_data: ReportWriterInput) -> ReportWriterOutput:
        if input_data.writer_mode == "llm":
            try:
                return self._run_llm_with_trace(input_data)
            except AgentExecutionError as exc:
                if exc.fallback_to_mock:
                    return self._run_mock(input_data, fallback_reason=str(exc), previous_diagnostics=exc.output)
                return ReportWriterOutput(
                    draft_report={"markdown": "LLM ReportWriter output failed validation."},
                    writer_mode="llm",
                    diagnostics=exc.output.get("diagnostics", {}) if isinstance(exc.output, dict) else {},
                )
        return self._run_mock(input_data)

    def _base_diagnostics(self, input_data: ReportWriterInput) -> dict[str, Any]:
        return {
            "writer_mode_requested": input_data.writer_mode,
            "writer_mode_used": "mock",
            "llm_enabled": self.llm_client.is_available,
            "llm_provider": self.llm_client.provider,
            "llm_model": self.llm_client.model,
            "llm_base_url_configured": bool(self.llm_client.base_url),
            "has_api_key": self.llm_client.is_available,
            "llm_call_attempted": False,
            "llm_call_success": False,
            "llm_elapsed_time_ms": 0,
            "llm_error_type": None,
            "llm_error_message": None,
            "llm_response_preview": None,
            "llm_schema_validation_success": None,
            "llm_schema_validation_errors": [],
            "dimension_result_count": len(input_data.knowledge.dimension_results),
            "supported_dimension_result_count": sum(
                1 for item in input_data.knowledge.dimension_results if not item.insufficient_evidence
            ),
            "fallback_used": False,
            "llm_fallback_reason": None,
            "selected_dimensions": [item for item in input_data.selected_dimensions if item],
            "writer_guidance_count": len(input_data.writer_guidance),
            "intent_classification": input_data.intent_classification,
            "rework_context_applied": bool(input_data.rework_context),
            "claims_generated": False,
        }

    def _run_mock(
        self,
        input_data: ReportWriterInput,
        fallback_reason: str | None = None,
        previous_diagnostics: dict | None = None,
    ) -> ReportWriterOutput:
        task = input_data.task
        knowledge = input_data.knowledge

        def produce() -> ReportWriterOutput:
            diagnostics = previous_diagnostics or self._base_diagnostics(input_data)
            diagnostics.update(
                {
                    "writer_mode_used": "mock",
                    "fallback_used": bool(fallback_reason),
                    "llm_fallback_reason": fallback_reason,
                }
            )
            if input_data.simulate_missing_evidence:
                return ReportWriterOutput(
                    draft_report={"markdown": "# Draft\n\nStructured facts are missing evidence bindings."},
                    writer_mode="mock",
                    llm_fallback_reason=fallback_reason,
                    diagnostics=diagnostics,
                )

            markdown = self._mock_markdown(input_data)
            if input_data.force_bad_format:
                markdown = "Competitor report without a level-1 heading\n\nThis content demonstrates QA report-format routing."

            report = Report(
                task_id=task.task_id,
                markdown=markdown,
                json_report={
                    "knowledge": knowledge.model_dump(mode="json"),
                    "dimension_results": [item.model_dump(mode="json") for item in knowledge.dimension_results],
                    "swot": self._swot_payload(knowledge.swot),
                    "writer_mode": "mock",
                    "planner": self._planner_report_payload(input_data),
                    "llm_fallback_reason": fallback_reason,
                    "writer_diagnostics": diagnostics,
                },
                dimension_results=knowledge.dimension_results,
                qa_result=QaResult(task_id=task.task_id, status="passed"),
            )
            return ReportWriterOutput(
                report=report,
                writer_mode="mock",
                llm_fallback_reason=fallback_reason,
                diagnostics=diagnostics,
            )

        return run_with_trace(
            trace_service=self.trace_service,
            task_id=task.task_id,
            agent_name=self.name,
            to_agent="QaAgent",
            message_type="report",
            schema_name="ReportWriterOutput",
            input_summary=f"writer_mode_requested={input_data.writer_mode}; Generate report from validated DimensionResult facts",
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def _run_llm_with_trace(self, input_data: ReportWriterInput) -> ReportWriterOutput:
        task = input_data.task

        def produce() -> ReportWriterOutput:
            diagnostics = self._base_diagnostics(input_data)
            messages = self._messages(input_data)
            llm_response = self.llm_client.chat_json(messages)
            diagnostics.update(
                {
                    "llm_call_attempted": llm_response.attempted,
                    "llm_call_success": llm_response.success,
                    "llm_elapsed_time_ms": llm_response.elapsed_time_ms,
                    "llm_error_type": llm_response.error_type,
                    "llm_error_message": llm_response.error_message,
                    "llm_response_preview": llm_response.response_preview,
                }
            )

            if not llm_response.available:
                diagnostics.update(
                    {
                        "writer_mode_used": "mock",
                        "fallback_used": True,
                        "llm_fallback_reason": llm_response.fallback_reason,
                    }
                )
                return self._mock_output_without_trace(input_data, diagnostics, llm_response.fallback_reason)

            try:
                payload = parse_llm_json(llm_response.content or "")
            except Exception as exc:  # noqa: BLE001
                diagnostics.update(
                    {
                        "llm_error_type": exc.__class__.__name__,
                        "llm_error_message": str(exc),
                        "llm_schema_validation_success": False,
                        "llm_schema_validation_errors": [str(exc)],
                        "fallback_used": True,
                        "llm_fallback_reason": f"LLM returned invalid JSON: {exc}",
                    }
                )
                raise AgentOutputValidationError(
                    f"LLM returned invalid JSON: {exc}",
                    output=diagnostics,
                    fallback_to_mock=True,
                ) from exc

            markdown_report = payload.get("markdown_report")
            json_report = payload.get("json_report")
            if not isinstance(markdown_report, str) or not markdown_report.strip():
                diagnostics.update(
                    {
                        "llm_error_message": "LLM output missing markdown_report.",
                        "llm_schema_validation_success": False,
                        "llm_schema_validation_errors": ["LLM output missing markdown_report."],
                    }
                )
                raise AgentOutputValidationError(
                    "LLM output missing markdown_report.",
                    output={"markdown": "", "diagnostics": diagnostics},
                    fallback_to_mock=True,
                )
            if not isinstance(json_report, dict):
                diagnostics.update(
                    {
                        "llm_error_message": "LLM output missing json_report object.",
                        "llm_schema_validation_success": False,
                        "llm_schema_validation_errors": ["LLM output missing json_report object."],
                    }
                )
                raise AgentOutputValidationError(
                    "LLM output missing json_report object.",
                    output={"markdown": markdown_report, "diagnostics": diagnostics},
                    fallback_to_mock=True,
                )

            diagnostics.update(
                {
                    "writer_mode_used": "llm",
                    "llm_schema_validation_success": True,
                    "llm_schema_validation_errors": [],
                }
            )
            report = Report(
                task_id=task.task_id,
                markdown=markdown_report,
                json_report={
                    **json_report,
                    "knowledge": input_data.knowledge.model_dump(mode="json"),
                    "dimension_results": [
                        item.model_dump(mode="json") for item in input_data.knowledge.dimension_results
                    ],
                    "swot": self._swot_payload(input_data.knowledge.swot),
                    "writer_mode": "llm",
                    "planner": self._planner_report_payload(input_data),
                    "writer_diagnostics": diagnostics,
                },
                dimension_results=input_data.knowledge.dimension_results,
                qa_result=QaResult(task_id=task.task_id, status="passed"),
            )
            return ReportWriterOutput(report=report, writer_mode="llm", diagnostics=diagnostics)

        return run_with_trace(
            trace_service=self.trace_service,
            task_id=task.task_id,
            agent_name=self.name,
            to_agent="QaAgent",
            message_type="report",
            schema_name="ReportWriterOutput",
            input_summary=f"writer_mode_requested=llm; llm_provider={self.llm_client.provider}; llm_model={self.llm_client.model}; has_api_key={self.llm_client.is_available}",
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def _mock_output_without_trace(
        self,
        input_data: ReportWriterInput,
        diagnostics: dict[str, Any],
        fallback_reason: str | None,
    ) -> ReportWriterOutput:
        task = input_data.task
        knowledge = input_data.knowledge
        markdown = self._mock_markdown(input_data)
        report = Report(
            task_id=task.task_id,
            markdown=markdown,
            json_report={
                "knowledge": knowledge.model_dump(mode="json"),
                "dimension_results": [item.model_dump(mode="json") for item in knowledge.dimension_results],
                "swot": self._swot_payload(knowledge.swot),
                "writer_mode": "mock",
                "planner": self._planner_report_payload(input_data),
                "llm_fallback_reason": fallback_reason,
                "writer_diagnostics": diagnostics,
            },
            dimension_results=knowledge.dimension_results,
            qa_result=QaResult(task_id=task.task_id, status="passed"),
        )
        return ReportWriterOutput(report=report, writer_mode="mock", llm_fallback_reason=fallback_reason, diagnostics=diagnostics)

    def _messages(self, input_data: ReportWriterInput) -> list[dict[str, str]]:
        prompt_data: dict[str, Any] = {
            "task": input_data.task.model_dump(mode="json"),
            "knowledge": input_data.knowledge.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in input_data.evidence],
            "planner": self._planner_report_payload(input_data),
            "rework_context": input_data.rework_context.model_dump(mode="json") if input_data.rework_context else None,
        }
        system = (
            "你是 ReportWriterAgent，只能依据输入的 Evidence 和 Knowledge 撰写中文企业竞品分析报告。"
            "主要事实来源是 knowledge.dimension_results；ProductProfile、FeatureTree、PricingModel、"
            "UserPersona 和 SWOT 仅作为兼容摘要。"
            "不得编造事实或来源，不得生成 claims，不得修改或重新创建 dimension_results。"
            "使用 planner 的意图、selected_dimensions 和 writer_guidance 组织报告，并覆盖所有输入竞品与规划维度。"
            "具体结论只能来自有证据支持的 dimension_results；不得使用 unrelated Evidence。"
            "low relevance Evidence 只能用于保守的风险提示。"
            "不得把一个竞品的事实或 evidence_ids 转移给另一个竞品。"
            "证据不足时必须写“当前公开证据不足，暂不做强结论。”"
            "引用公开证据时使用“根据公开来源……”；仅有 snippet 时使用“公开摘要显示……”或“有公开报道提到……”。"
            "存在 rework_context 时，必须针对其中的问题修复报告。"
            "只返回合法 JSON，顶层 key 只能使用 markdown_report 和 json_report。"
            "不要输出 JSON 外文字，不要使用 Markdown 代码块包裹 JSON，不要翻译任何 JSON key。"
        )
        user = (
            "markdown_report 必须是完整中文 Markdown 报告，至少包含执行摘要、证据范围、按维度的竞品分析、"
            "SWOT、风险与不确定性、综合建议。"
            "报告必须围绕 planner.selected_dimensions 和 knowledge.dimension_results 展开。"
            "引用结构化事实时，在可读正文中保留 dimension_result_id 或 evidence_ids，确保可追溯。"
            "json_report 只做报告章节摘要，可以引用 dimension_result_id，但不能创建新的事实对象。"
            "输入：\n"
            f"{prompt_data}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _mock_markdown(self, input_data: ReportWriterInput) -> str:
        task = input_data.task
        dimensions = self._selected_dimensions(input_data)
        guidance = [item for item in input_data.writer_guidance if item]
        swot = input_data.knowledge.swot
        return "\n".join(
            [
                f"# 竞品分析报告：{task.product_name}",
                "",
                "## 执行摘要",
                self._executive_summary(input_data),
                "",
                "## 规划范围",
                f"任务意图：{input_data.intent_classification or 'competitive_analysis'}。",
                f"分析维度：{', '.join(dimensions) if dimensions else 'pricing, feature, persona'}。",
                *(f"- {line}" for line in guidance[:4]),
                "",
                "## 分维度竞品分析",
                *self._dimension_markdown(input_data),
                "",
                "## SWOT 分析",
                *self._swot_markdown(swot),
                "",
                "## 风险、不确定性与下一步",
                "当前结论仍受各竞品公开证据覆盖范围限制，重要建议应继续使用官方产品、定价和用户侧来源交叉验证。",
            ]
        )

    def _planner_report_payload(self, input_data: ReportWriterInput) -> dict[str, Any]:
        return {
            "intent_classification": input_data.intent_classification,
            "selected_dimensions": self._selected_dimensions(input_data),
            "writer_guidance": [item for item in input_data.writer_guidance if item],
        }

    @staticmethod
    def _dimension_markdown(input_data: ReportWriterInput) -> list[str]:
        results = input_data.knowledge.dimension_results
        if not results:
            return ["当前没有可用于报告的 dimension_results。"]
        lines: list[str] = []
        for result in results:
            status = "证据不足" if result.insufficient_evidence else "已有证据支持"
            evidence = ", ".join(result.evidence_ids) if result.evidence_ids else "insufficient_evidence"
            lines.append(
                f"- **{result.dimension_result_id} / {result.dimension_id} / {result.competitor or 'overall'} / {status}** "
                f"{result.summary} Evidence: {evidence}"
            )
        return lines

    @staticmethod
    def _swot_payload(swot: SwotAnalysis) -> dict[str, Any]:
        return swot.model_dump(mode="json")

    @staticmethod
    def _selected_dimensions(input_data: ReportWriterInput) -> list[str]:
        return [str(item).strip().lower() for item in input_data.selected_dimensions if str(item).strip()]

    def _executive_summary(self, input_data: ReportWriterInput) -> str:
        intent = input_data.intent_classification or "competitive_analysis"
        dimensions = self._selected_dimensions(input_data)
        dimension_label = "、".join(dimensions[:4]) if dimensions else "价格、功能和用户画像"
        return (
            f"本报告按 {intent} 任务理解，对 {dimension_label} 等规划维度进行比较，"
            "所有具体结论均以 AnalystAgent 输出的结构化事实和公开证据为边界。"
        )

    def _swot_markdown(self, swot: SwotAnalysis) -> list[str]:
        sections = [
            ("优势", swot.strengths),
            ("劣势", swot.weaknesses),
            ("机会", swot.opportunities),
            ("威胁", swot.threats),
        ]
        lines: list[str] = []
        for title, items in sections:
            lines.append(f"### {title}")
            lines.extend(self._swot_item_lines(items))
        return lines

    @staticmethod
    def _swot_item_lines(items: list[SwotItem]) -> list[str]:
        if not items:
            return ["- 当前公开证据不足，暂不做强结论。"]
        return [
            f"- **{item.competitor or 'overall'}** {item.summary} Evidence: {', '.join(item.evidence_ids)}"
            for item in items
        ]
