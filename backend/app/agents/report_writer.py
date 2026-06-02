from typing import Any

from pydantic import ValidationError

from app.agents.base import AgentExecutionError, AgentOutputValidationError, run_with_trace
from app.schemas import (
    Claim,
    PlannerCoreSummary,
    QaResult,
    QuestionnaireFollowUpRecommendation,
    Report,
    ReportCorePayload,
    ReportDiagnosticsPayload,
    ReportExtensionPayload,
    ReportWriterInput,
    ReportWriterOutput,
    SurveyExtensionSummary,
    SwotAnalysis,
    SwotItem,
)
from app.services.evidence_relevance_service import is_relevant_evidence
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
                    draft_report={"claims": [], "markdown": "LLM ReportWriter 输出校验失败。"},
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
            "llm_category_normalization_count": 0,
            "claim_count_by_competitor": {},
            "missing_claim_competitors": [],
            "fallback_used": False,
            "llm_fallback_reason": None,
            "selected_dimensions": [item for item in input_data.selected_dimensions if item],
            "writer_guidance_count": len(input_data.writer_guidance),
            "intent_classification": input_data.intent_classification,
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
            raw_claims = self._mock_claim_payloads(input_data)
            diagnostics.update(self._coverage_diagnostics(task.competitors, raw_claims))
            if input_data.simulate_missing_evidence:
                return ReportWriterOutput(
                    draft_report={"claims": raw_claims, "markdown": "# 草稿\n\n存在缺少证据绑定的无效 Claim。"},
                    writer_mode="mock",
                    llm_fallback_reason=fallback_reason,
                    diagnostics=diagnostics,
                )

            claims = [Claim(**item) for item in raw_claims]
            markdown = self._mock_markdown(input_data, claims)
            if input_data.force_bad_format:
                markdown = "缺少一级标题的竞品分析报告\n\n这段内容用于演示 QA 对报告格式问题的路由。"

            report = Report(
                task_id=task.task_id,
                markdown=markdown,
                json_report=self._report_json_payload(
                    input_data=input_data,
                    claims=claims,
                    diagnostics=diagnostics,
                    writer_mode="mock",
                    llm_fallback_reason=fallback_reason,
                ),
                claims=claims,
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
            input_summary=f"writer_mode_requested={input_data.writer_mode}; Generate Markdown and JSON report with claim evidence_ids",
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

            contract_error = self._llm_output_contract_error(payload, input_data)
            if contract_error:
                diagnostics.update(
                    {
                        "llm_error_message": contract_error,
                        "llm_schema_validation_success": False,
                        "llm_schema_validation_errors": [contract_error],
                        "fallback_used": True,
                        "llm_fallback_reason": contract_error,
                    }
                )
                raise AgentOutputValidationError(
                    contract_error,
                    output=diagnostics,
                    fallback_to_mock=True,
                )
            claims_payload = payload["claims"]

            if any(not claim.get("evidence_ids") for claim in claims_payload):
                diagnostics.update(
                    {
                        "writer_mode_used": "llm",
                        "fallback_used": False,
                        "llm_error_message": "LLM claim missing evidence_ids.",
                        "llm_schema_validation_success": False,
                        "llm_schema_validation_errors": ["LLM claim missing evidence_ids."],
                    }
                )
                raise AgentOutputValidationError(
                    "LLM claim missing evidence_ids.",
                    output={"claims": claims_payload, "markdown": payload.get("markdown_report", ""), "diagnostics": diagnostics},
                )

            normalized_count = sum(1 for claim in claims_payload if not claim.get("category"))
            try:
                claims = [
                    Claim(
                        claim_id=claim["claim_id"],
                        competitor=claim.get("competitor"),
                        text=claim["text"],
                        evidence_ids=claim["evidence_ids"],
                        category=claim.get("category", "recommendation"),
                        confidence=claim.get("confidence", 0.7),
                    )
                    for claim in claims_payload
                ]
            except (KeyError, ValidationError) as exc:
                diagnostics.update(
                    {
                        "writer_mode_used": "llm",
                        "fallback_used": False,
                        "llm_error_type": exc.__class__.__name__,
                        "llm_error_message": str(exc),
                        "llm_schema_validation_success": False,
                        "llm_schema_validation_errors": [str(exc)],
                        "llm_category_normalization_count": normalized_count,
                    }
                )
                raise AgentOutputValidationError(
                    f"LLM output failed Claim schema validation: {exc}",
                    output={"claims": claims_payload, "markdown": payload.get("markdown_report", ""), "diagnostics": diagnostics},
                ) from exc

            diagnostics.update(
                {
                    "writer_mode_used": "llm",
                    "llm_schema_validation_success": True,
                    "llm_schema_validation_errors": [],
                    "llm_category_normalization_count": normalized_count,
                    **self._coverage_diagnostics(task.competitors, [claim.model_dump(mode="json") for claim in claims]),
                }
            )
            report = Report(
                task_id=task.task_id,
                markdown=payload.get("markdown_report", ""),
                json_report=self._report_json_payload(
                    input_data=input_data,
                    claims=claims,
                    diagnostics=diagnostics,
                    writer_mode="llm",
                    llm_fallback_reason=None,
                    base_payload=payload.get("json_report") if isinstance(payload.get("json_report"), dict) else None,
                ),
                claims=claims,
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
        claims = [Claim(**item) for item in self._mock_claim_payloads(input_data)]
        diagnostics.update(self._coverage_diagnostics(task.competitors, [claim.model_dump(mode="json") for claim in claims]))
        markdown = self._mock_markdown(input_data, claims)
        report = Report(
            task_id=task.task_id,
            markdown=markdown,
            json_report=self._report_json_payload(
                input_data=input_data,
                claims=claims,
                diagnostics=diagnostics,
                writer_mode="mock",
                llm_fallback_reason=fallback_reason,
            ),
            claims=claims,
            qa_result=QaResult(task_id=task.task_id, status="passed"),
        )
        return ReportWriterOutput(report=report, writer_mode="mock", llm_fallback_reason=fallback_reason, diagnostics=diagnostics)

    @staticmethod
    def _evidence_ids_by_competitor(input_data: ReportWriterInput) -> dict[str, list[str]]:
        grouped = {competitor: [] for competitor in input_data.task.competitors}
        for item in input_data.evidence:
            if item.competitor in grouped and is_relevant_evidence(item):
                grouped[item.competitor].append(item.evidence_id)
        if not any(grouped.values()) and not input_data.evidence:
            competitor_analysis = input_data.knowledge.product_profile.custom_dimensions.get("competitor_analysis", {})
            if isinstance(competitor_analysis, dict):
                for competitor, details in competitor_analysis.items():
                    if competitor in grouped and isinstance(details, dict):
                        ids = details.get("evidence_ids")
                        if isinstance(ids, list):
                            grouped[competitor] = [str(item) for item in ids if item]
        if not any(grouped.values()) and len(input_data.task.competitors) == 1:
            grouped[input_data.task.competitors[0]] = input_data.knowledge.product_profile.evidence_ids
        return grouped

    def _mock_claim_payloads(self, input_data: ReportWriterInput) -> list[dict[str, Any]]:
        grouped = self._evidence_ids_by_competitor(input_data)
        dimensions = self._selected_dimensions(input_data)
        preferred_category = self._preferred_claim_category(dimensions)
        claims: list[dict[str, Any]] = []
        for index, competitor in enumerate(input_data.task.competitors, start=1):
            ids = grouped.get(competitor, [])
            if not ids:
                continue
            claims.append(
                {
                    "claim_id": f"claim_{index:03d}",
                    "competitor": competitor,
                    "text": self._claim_text(competitor, dimensions),
                    "category": preferred_category,
                    "evidence_ids": [] if input_data.simulate_missing_evidence and index == 1 else ids[:2],
                    "confidence": 0.82,
                }
            )
        if not claims:
            ids = ["insufficient_evidence"] if input_data.evidence else input_data.knowledge.product_profile.evidence_ids
            claims.append(
                {
                    "claim_id": "claim_001",
                    "competitor": None,
                    "text": "当前公开证据不足，暂不做强结论。",
                    "category": "risk",
                    "evidence_ids": [] if input_data.simulate_missing_evidence else ids,
                    "confidence": 0.55,
                }
            )
        return claims

    @staticmethod
    def _coverage_diagnostics(competitors: list[str], claim_payloads: list[dict[str, Any]]) -> dict[str, Any]:
        claim_count_by_competitor = {competitor: 0 for competitor in competitors}
        for claim in claim_payloads:
            competitor = claim.get("competitor")
            if competitor in claim_count_by_competitor:
                claim_count_by_competitor[competitor] += 1
        return {
            "claim_count_by_competitor": claim_count_by_competitor,
            "missing_claim_competitors": [competitor for competitor, count in claim_count_by_competitor.items() if count == 0],
        }

    def _llm_output_contract_error(self, payload: dict[str, Any], input_data: ReportWriterInput) -> str | None:
        required_keys = ("markdown_report", "json_report", "claims")
        missing = [key for key in required_keys if key not in payload]
        if missing:
            return f"LLM output missing required top-level keys: {', '.join(missing)}."
        if not isinstance(payload.get("markdown_report"), str):
            return "LLM output markdown_report must be a string."
        if not isinstance(payload.get("json_report"), dict):
            return "LLM output json_report must be an object."
        json_report = payload["json_report"]
        if "claims" in json_report:
            return "LLM output must keep claims as a top-level array, not inside json_report."
        claims_payload = payload.get("claims")
        if not isinstance(claims_payload, list):
            return "LLM output claims must be a top-level array."
        if claims_payload and any(not isinstance(claim, dict) for claim in claims_payload):
            return "LLM output claims items must be objects."
        if not claims_payload and any(is_relevant_evidence(item) for item in input_data.evidence):
            return "LLM output has empty claims despite available relevant evidence."
        return None

    def _messages(self, input_data: ReportWriterInput) -> list[dict[str, str]]:
        prompt_data: dict[str, Any] = {
            "task": input_data.task.model_dump(mode="json"),
            "knowledge": input_data.knowledge.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in input_data.evidence],
            "planner": self._planner_report_payload(input_data),
        }
        system = (
            "你是 ReportWriterAgent，面向中文企业竞品分析场景撰写报告。只能基于输入的 Evidence 和 Knowledge 写作，不能编造来源。"
            "所有关键结论必须绑定 evidence_ids，JSON 字段名必须保持英文，不要翻译 markdown_report、json_report、claims、claim_id、competitor、text、evidence_ids、category、confidence 等 key。"
            "请结合 Planner intent、selected_dimensions、writer_guidance 和 SWOT 组织报告。报告正文、章节标题和解释性内容必须使用中文。"
            "你必须只返回合法 JSON object，不要输出任何 JSON 外的解释文字，不要使用 Markdown 代码块包裹 JSON。"
            "顶层字段必须至少包含 markdown_report、json_report、claims，且这三个 key 必须位于顶层。"
            "不要只返回 markdown_report；不要把 claims 放入 json_report；不要翻译 JSON key。"
            "markdown_report 是中文 Markdown 报告正文；json_report 是结构化报告摘要对象；claims 必须是顶层数组。"
            "如果证据不足，也必须返回 claims: []；如果 Evidence 足够，必须生成 claims，且每条 Claim 必须绑定 evidence_ids。"
            "报告必须包含基于证据的 SWOT 章节。具体强结论只能使用 high 或 medium relevance Evidence 支撑。"
            "不要使用 unrelated Evidence；low relevance Evidence 只能作为谨慎风险提示。"
            "必须覆盖每个输入 competitor。每个竞品在有自身证据时都需要独立小节和至少一条 Claim。"
            "严禁用一个竞品的 evidence_ids 支撑另一个竞品的 Claim。"
            "如果某个竞品证据不足，必须写“当前公开证据不足，暂不做强结论。”，不要补全或编造。"
            "引用证据时使用中文表达，例如“根据公开来源……”；如果只有 snippet-only evidence，应使用“公开摘要显示……”或“有公开报道提到……”，避免强断言。"
            "claims[].category 只能使用以下英文枚举之一：positioning, feature, pricing, persona, risk, recommendation。"
        )
        user = (
            "markdown_report 必须是一份完整中文竞品分析报告，章节建议包括：执行摘要、证据范围、竞品逐项分析、SWOT 分析、风险与不确定性、综合建议。"
            "不要输出英文 section title，除非是产品名、技术名或来源标题。"
            "每条 Claim 必须包含 claim_id、competitor、text、evidence_ids、category、confidence。"
            "证据允许时每个竞品生成 2-4 条 Claim；证据不足时保持保守表达，只写“当前公开证据不足，暂不做强结论。”"
            "输出示例必须保持如下 Schema 形状："
            '{"markdown_report":"# 竞品分析报告\\n\\n## 执行摘要\\n...","json_report":{"summary":"中文摘要","sections":[{"title":"产品定位","content":"中文内容","competitors":["竞品A"],"evidence_ids":["ev_xxx"]}]},"claims":[{"claim_id":"claim_001","competitor":"竞品A","category":"feature","text":"根据公开来源，竞品A具备某项能力。","evidence_ids":["ev_xxx"],"confidence":0.8}]}'
            "注意 claims 是顶层数组，不允许嵌套在 json_report 内部。"
            "输入如下：\n"
            f"{prompt_data}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _mock_markdown(self, input_data: ReportWriterInput, claims: list[Claim]) -> str:
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
                "## Planner 分析重点",
                f"意图 intent：{input_data.intent_classification or 'competitive_analysis'}。",
                f"已选分析维度 selected_dimensions：{', '.join(dimensions) if dimensions else 'positioning, feature, pricing, persona'}。",
                *(f"- {line}" for line in guidance[:4]),
                "",
                "## 关键结论",
                *[
                    f"- **{claim.competitor or '整体'} / {claim.claim_id}** {claim.text} 证据 evidence_ids：{', '.join(claim.evidence_ids)}"
                    for claim in claims
                ],
                "",
                "## SWOT 分析",
                *self._swot_markdown(swot),
                "",
                "## 证据缺口与下一步",
                "当前结论仍受各竞品公开证据覆盖度限制。任何正式建议都应继续用官网、定价页、产品文档和客户侧来源复核。",
            ]
        )

    def _planner_report_payload(self, input_data: ReportWriterInput) -> dict[str, Any]:
        return {
            "intent_classification": input_data.intent_classification,
            "selected_dimensions": self._selected_dimensions(input_data),
            "survey_needed": input_data.survey_needed,
            "survey_recommended": input_data.survey_recommended,
            "survey_objective": input_data.survey_objective,
            "survey_inputs": input_data.survey_inputs.model_dump(mode="json") if input_data.survey_inputs else None,
            "writer_guidance": [item for item in input_data.writer_guidance if item],
        }

    def _report_json_payload(
        self,
        *,
        input_data: ReportWriterInput,
        claims: list[Claim],
        diagnostics: dict[str, Any],
        writer_mode: str,
        llm_fallback_reason: str | None,
        base_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        claim_payloads = [claim.model_dump(mode="json") for claim in claims]
        coverage = self._coverage_diagnostics(input_data.task.competitors, claim_payloads)
        planner_payload = self._planner_report_payload(input_data)
        core = ReportCorePayload(
            knowledge=input_data.knowledge.model_dump(mode="json"),
            swot=self._swot_payload(input_data.knowledge.swot),
            claims=claim_payloads,
            competitor_coverage=coverage,
            planner=PlannerCoreSummary(
                intent_classification=planner_payload.get("intent_classification"),
                selected_dimensions=planner_payload.get("selected_dimensions") or [],
                writer_guidance=planner_payload.get("writer_guidance") or [],
                domain_pack=input_data.knowledge.product_profile.extensions.domain.domain_pack,
            ),
        )
        extensions = ReportExtensionPayload(
            survey=SurveyExtensionSummary(
                survey_needed=bool(planner_payload.get("survey_needed", False)),
                survey_recommended=bool(planner_payload.get("survey_recommended", False)),
                survey_objective=planner_payload.get("survey_objective"),
                survey_inputs=planner_payload.get("survey_inputs"),
                survey_guidance=[],
            ),
            questionnaire_follow_up=input_data.questionnaire_follow_up
            or QuestionnaireFollowUpRecommendation(
                recommended=bool(planner_payload.get("survey_recommended", False)),
                required=bool(planner_payload.get("survey_needed", False)),
                objective=planner_payload.get("survey_objective"),
                respondent_type=(planner_payload.get("survey_inputs") or {}).get("respondent_type"),
                question_themes=list((planner_payload.get("survey_inputs") or {}).get("question_themes") or []),
                hypotheses=list((planner_payload.get("survey_inputs") or {}).get("hypotheses") or []),
                guidance=[],
                selected_dimensions=planner_payload.get("selected_dimensions") or [],
                domain_pack=input_data.knowledge.product_profile.extensions.domain.domain_pack,
                metadata={"source": "report_writer_payload_fallback"},
            ),
            domain=input_data.knowledge.product_profile.extensions.domain,
            workflow=input_data.knowledge.product_profile.extensions.workflow,
        )
        diagnostics_payload = ReportDiagnosticsPayload(
            writer_mode=writer_mode,
            llm_fallback_reason=llm_fallback_reason,
            writer_diagnostics=diagnostics,
        )
        payload = dict(base_payload or {})
        payload.update(
            {
                "knowledge": core.knowledge,
                "swot": core.swot,
                "claims": core.claims,
                "competitor_coverage": core.competitor_coverage,
                "writer_mode": writer_mode,
                "planner": planner_payload,
                "llm_fallback_reason": llm_fallback_reason,
                "writer_diagnostics": diagnostics,
                "core": core.model_dump(mode="json", exclude_none=True),
                "extensions": extensions.model_dump(mode="json", exclude_none=True),
                "diagnostics": diagnostics_payload.model_dump(mode="json", exclude_none=True),
            }
        )
        return payload

    @staticmethod
    def _swot_payload(swot: SwotAnalysis) -> dict[str, Any]:
        return swot.model_dump(mode="json")

    @staticmethod
    def _selected_dimensions(input_data: ReportWriterInput) -> list[str]:
        return [str(item).strip().lower() for item in input_data.selected_dimensions if str(item).strip()]

    @staticmethod
    def _preferred_claim_category(dimensions: list[str]) -> str:
        if "pricing" in dimensions:
            return "pricing"
        if "persona" in dimensions:
            return "persona"
        if "positioning" in dimensions:
            return "positioning"
        return "feature"

    @staticmethod
    def _claim_text(competitor: str, dimensions: list[str]) -> str:
        dimension_label = ", ".join(dimensions[:3]) if dimensions else "feature, positioning, pricing"
        return (
            f"根据公开来源，{competitor} 仅基于其自身相关证据进行保守描述，报告重点关注 {dimension_label}。"
        )

    def _executive_summary(self, input_data: ReportWriterInput) -> str:
        intent = (input_data.intent_classification or "competitive_analysis").replace("_", " ")
        dimensions = self._selected_dimensions(input_data)
        dimension_label = ", ".join(dimensions[:4]) if dimensions else "positioning, feature, pricing, persona"
        return (
            f"本报告将分析意图识别为 {intent}，优先关注 {dimension_label}，以保证后续结论贴合 Planner 规划，而不是生成泛化摘要。"
        )

    def _swot_markdown(self, swot: SwotAnalysis) -> list[str]:
        sections = [
            ("优势 Strengths", swot.strengths),
            ("劣势 Weaknesses", swot.weaknesses),
            ("机会 Opportunities", swot.opportunities),
            ("威胁 Threats", swot.threats),
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
            f"- **{item.competitor or '整体'}** {item.summary} 证据 evidence_ids：{', '.join(item.evidence_ids)}"
            for item in items
        ]
