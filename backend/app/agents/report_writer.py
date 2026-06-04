from typing import Any

from pydantic import ValidationError

from app.agents.base import AgentExecutionError, AgentOutputValidationError, run_with_trace
from app.schemas import Claim, QaResult, Report, ReportWriterInput, ReportWriterOutput, SwotAnalysis, SwotItem
from app.services.evidence_relevance_service import is_relevant_evidence
from app.services.llm_client import LlmClient, parse_llm_json
from app.services.trace_service import TraceService


OVERALL_COMPETITOR_LABELS = {
    "overall",
    "all",
    "all competitors",
    "all_competitors",
    "全竞品",
    "全部竞品",
    "全体竞品",
    "所有竞品",
    "整体",
    "综合",
}


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
                    draft_report={"claims": [], "markdown": "LLM ReportWriter output failed validation."},
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
            "rework_context_applied": bool(input_data.rework_context),
            "claim_competitor_normalization_count": 0,
            "claim_evidence_rebinding_count": 0,
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
                    draft_report={"claims": raw_claims, "markdown": "# Draft\n\nInvalid claim missing evidence."},
                    writer_mode="mock",
                    llm_fallback_reason=fallback_reason,
                    diagnostics=diagnostics,
                )

            claims = [Claim(**item) for item in raw_claims]
            markdown = self._mock_markdown(input_data, claims)
            if input_data.force_bad_format:
                markdown = "Competitor report without a level-1 heading\n\nThis content demonstrates QA report-format routing."

            report = Report(
                task_id=task.task_id,
                markdown=markdown,
                json_report={
                    "knowledge": knowledge.model_dump(mode="json"),
                    "swot": self._swot_payload(knowledge.swot),
                    "claims": [claim.model_dump(mode="json") for claim in claims],
                    "competitor_coverage": self._coverage_diagnostics(
                        task.competitors, [claim.model_dump(mode="json") for claim in claims]
                    ),
                    "writer_mode": "mock",
                    "planner": self._planner_report_payload(input_data),
                    "llm_fallback_reason": fallback_reason,
                    "writer_diagnostics": diagnostics,
                },
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

            claims_payload = payload.get("claims")
            if not isinstance(claims_payload, list):
                diagnostics.update(
                    {
                        "llm_error_message": "LLM output missing claims list.",
                        "llm_schema_validation_success": False,
                        "llm_schema_validation_errors": ["LLM output missing claims list."],
                    }
                )
                raise AgentOutputValidationError(
                    "LLM output missing claims list.",
                    output={"claims": [], "markdown": payload.get("markdown_report", ""), "diagnostics": diagnostics},
                )

            claims_payload, normalization_count, rebinding_count = self._normalize_claim_payloads(input_data, claims_payload)
            diagnostics.update(
                {
                    "claim_competitor_normalization_count": normalization_count,
                    "claim_evidence_rebinding_count": rebinding_count,
                }
            )

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
                json_report={
                    **(payload.get("json_report") if isinstance(payload.get("json_report"), dict) else {}),
                    "knowledge": input_data.knowledge.model_dump(mode="json"),
                    "swot": self._swot_payload(input_data.knowledge.swot),
                    "claims": [claim.model_dump(mode="json") for claim in claims],
                    "competitor_coverage": self._coverage_diagnostics(
                        task.competitors, [claim.model_dump(mode="json") for claim in claims]
                    ),
                    "writer_mode": "llm",
                    "planner": self._planner_report_payload(input_data),
                    "writer_diagnostics": diagnostics,
                },
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
            json_report={
                "knowledge": knowledge.model_dump(mode="json"),
                "swot": self._swot_payload(knowledge.swot),
                "claims": [claim.model_dump(mode="json") for claim in claims],
                "competitor_coverage": self._coverage_diagnostics(
                    task.competitors, [claim.model_dump(mode="json") for claim in claims]
                ),
                "writer_mode": "mock",
                "planner": self._planner_report_payload(input_data),
                "llm_fallback_reason": fallback_reason,
                "writer_diagnostics": diagnostics,
            },
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
                    "text": "Current public evidence is insufficient, so the report avoids strong competitor claims.",
                    "category": "risk",
                    "evidence_ids": [] if input_data.simulate_missing_evidence else ids,
                    "confidence": 0.55,
                }
            )
        return claims

    def _normalize_claim_payloads(
        self,
        input_data: ReportWriterInput,
        claims_payload: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int, int]:
        evidence_by_id = {item.evidence_id: item for item in input_data.evidence}
        evidence_ids_by_competitor = self._evidence_ids_by_competitor(input_data)
        normalized: list[dict[str, Any]] = []
        normalization_count = 0
        rebinding_count = 0

        for claim in claims_payload:
            next_claim = dict(claim)
            original_competitor = next_claim.get("competitor")
            normalized_competitor = self._normalize_claim_competitor(original_competitor, input_data.task.competitors)
            if normalized_competitor != original_competitor:
                normalization_count += 1
            next_claim["competitor"] = normalized_competitor

            evidence_ids = [str(item) for item in next_claim.get("evidence_ids", []) if item]
            if normalized_competitor in input_data.task.competitors:
                valid_ids = [
                    evidence_id
                    for evidence_id in evidence_ids
                    if self._evidence_matches_claim_competitor(evidence_by_id.get(evidence_id), normalized_competitor)
                ]
                if not valid_ids:
                    valid_ids = evidence_ids_by_competitor.get(normalized_competitor, [])[: max(1, min(2, len(evidence_ids) or 2))]
                if valid_ids != evidence_ids:
                    rebinding_count += 1
                next_claim["evidence_ids"] = valid_ids
            else:
                next_claim["evidence_ids"] = evidence_ids

            normalized.append(next_claim)

        return normalized, normalization_count, rebinding_count

    @staticmethod
    def _normalize_claim_competitor(value: Any, competitors: list[str]) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text in competitors:
            return text
        normalized = text.lower().replace("-", " ").replace("_", " ")
        if normalized in OVERALL_COMPETITOR_LABELS or text in OVERALL_COMPETITOR_LABELS:
            return None
        return text

    @staticmethod
    def _evidence_matches_claim_competitor(evidence: Any, competitor: str) -> bool:
        if evidence is None:
            return False
        return not evidence.competitor or evidence.competitor == competitor

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

    def _messages(self, input_data: ReportWriterInput) -> list[dict[str, str]]:
        prompt_data: dict[str, Any] = {
            "task": input_data.task.model_dump(mode="json"),
            "knowledge": input_data.knowledge.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in input_data.evidence],
            "planner": self._planner_report_payload(input_data),
            "rework_context": input_data.rework_context.model_dump(mode="json") if input_data.rework_context else None,
        }
        system = (
            "You are ReportWriterAgent. Write only from supplied Evidence and Knowledge. "
            "The primary structured facts are knowledge.dimension_results. "
            "Use ProductProfile, FeatureTree, PricingModel, UserPersona, and SWOT only as compatibility summaries. "
            "Do not invent sources. Every key claim must bind evidence_ids. "
            "Use planner intent, selected dimensions, writer guidance, and SWOT to frame the report. "
            "The report must include a SWOT section grounded in supplied evidence. "
            "Only use high or medium relevance evidence for concrete claims. "
            "Do not use unrelated Evidence. Low relevance Evidence may only support cautious risk notes. "
            "You must cover every input competitor. Each competitor needs its own subsection and at least one claim when its own evidence exists. "
            "Never use one competitor's evidence_ids to support another competitor's claim. "
            "If a claim is an overall cross-competitor conclusion, set claims[].competitor to null, not 全竞品, overall, or all competitors. "
            "If claims[].competitor names a specific competitor, all claims[].evidence_ids must come from Evidence with the same competitor. "
            "If a competitor lacks evidence, clearly state that public evidence is insufficient and avoid fabrication. "
            "When rework_context is present, directly fix the failed claim or evidence binding described there. "
            "Return strict JSON only with keys markdown_report, json_report, claims. "
            "For claims[].category, use only one of: positioning, feature, pricing, persona, risk, recommendation."
        )
        user = (
            "markdown_report must be a complete competitor analysis report with sections for executive summary, evidence scope, competitor analysis, SWOT, risks, and next steps. "
            "Organize the competitor analysis around planner.selected_dimensions and knowledge.dimension_results. "
            "Each claim must include claim_id, competitor, text, evidence_ids, category, confidence. "
            "Use 2-4 claims per competitor when evidence allows; otherwise keep claims cautious. "
            "Input:\n"
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
                f"# Competitor Analysis Report: {task.product_name}",
                "",
                "## Executive Summary",
                self._executive_summary(input_data),
                "",
                "## Planner Focus",
                f"Intent: {(input_data.intent_classification or 'competitive_analysis').replace('_', ' ')}.",
                f"Selected dimensions: {', '.join(dimensions) if dimensions else 'positioning, feature, pricing, persona'}.",
                *(f"- {line}" for line in guidance[:4]),
                "",
                "## Dimension Analysis",
                *self._dimension_markdown(input_data),
                "",
                "## Key Claims",
                *[
                    f"- **{claim.competitor or 'overall'} / {claim.claim_id}** {claim.text} Evidence: {', '.join(claim.evidence_ids)}"
                    for claim in claims
                ],
                "",
                "## SWOT Analysis",
                *self._swot_markdown(swot),
                "",
                "## Evidence Gaps And Next Steps",
                "Public conclusions remain constrained by competitor-specific evidence coverage, so any recommendation should be revalidated against official product, pricing, and customer-facing sources.",
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
            return ["No dimension_results were produced by AnalystAgent."]
        lines: list[str] = []
        for result in results[:12]:
            status = "insufficient" if result.insufficient_evidence else "supported"
            evidence = ", ".join(result.evidence_ids) if result.evidence_ids else "insufficient_evidence"
            lines.append(
                f"- **{result.dimension_id} / {result.competitor or 'overall'} / {status}** "
                f"{result.summary} Evidence: {evidence}"
            )
        return lines

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
        dimension_label = ", ".join(dimensions[:3]) if dimensions else "feature, positioning, and pricing"
        return (
            f"{competitor} is described conservatively using only its own relevant evidence, with report emphasis on {dimension_label}."
        )

    def _executive_summary(self, input_data: ReportWriterInput) -> str:
        intent = (input_data.intent_classification or "competitive_analysis").replace("_", " ")
        dimensions = self._selected_dimensions(input_data)
        dimension_label = ", ".join(dimensions[:4]) if dimensions else "positioning, feature, pricing, and persona"
        return (
            f"This report frames the comparison as {intent} and prioritizes {dimension_label}, so downstream conclusions stay aligned with the planner rather than defaulting to a generic summary."
        )

    def _swot_markdown(self, swot: SwotAnalysis) -> list[str]:
        sections = [
            ("Strengths", swot.strengths),
            ("Weaknesses", swot.weaknesses),
            ("Opportunities", swot.opportunities),
            ("Threats", swot.threats),
        ]
        lines: list[str] = []
        for title, items in sections:
            lines.append(f"### {title}")
            lines.extend(self._swot_item_lines(items))
        return lines

    @staticmethod
    def _swot_item_lines(items: list[SwotItem]) -> list[str]:
        if not items:
            return ["- No evidence-backed items available."]
        return [
            f"- **{item.competitor or 'overall'}** {item.summary} Evidence: {', '.join(item.evidence_ids)}"
            for item in items
        ]
