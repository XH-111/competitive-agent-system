from collections import defaultdict
from typing import Any

from pydantic import ValidationError

from app.agents.base import AgentOutputValidationError
from app.agents.base import run_with_trace
from app.constants.analysis_dimensions import dimension_keyword_map, fixed_dimension_ids
from app.schemas import (
    AnalystInput,
    AnalystOutput,
    DimensionResult,
    Evidence,
    FeatureTree,
    PricingModel,
    ProductProfile,
    SwotAnalysis,
    SwotItem,
    UserPersona,
)
from app.services.evidence_relevance_service import is_relevant_evidence
from app.services.llm_client import LlmClient, parse_llm_json
from app.services.trace_service import TraceService


FEATURE_KEYWORDS = {
    "AI": ["ai", "artificial intelligence"],
    "automation": ["automation", "automated"],
    "collaboration": ["collaboration", "collaborative", "team"],
    "pricing": ["pricing", "price", "plan"],
    "integration": ["integration", "integrations"],
    "analytics": ["analytics", "analysis", "dashboard"],
    "security": ["security", "compliance"],
    "mobile": ["mobile", "app"],
    "API": ["api", "developer"],
    "workflow": ["workflow", "process"],
}

PRICING_KEYWORDS = ["free", "trial", "pricing", "subscription", "enterprise", "plan", "quote"]
PERSONA_KEYWORDS = {
    "enterprise teams": ["enterprise", "procurement"],
    "team operators": ["team", "operations"],
    "developers": ["developer", "engineering"],
    "marketing teams": ["marketer", "marketing"],
    "product teams": ["product team", "product manager"],
    "students": ["student", "education"],
}


class AnalystAgent:
    name = "AnalystAgent"

    def __init__(self, trace_service: TraceService, llm_client: LlmClient | None = None):
        self.trace_service = trace_service
        self.llm_client = llm_client or LlmClient()

    def run(self, input_data: AnalystInput) -> AnalystOutput:
        task = input_data.task

        def produce() -> AnalystOutput:
            if input_data.analyst_mode == "mock":
                return self._mock_output(input_data, fallback_reason=None)
            if input_data.analyst_mode == "llm":
                return self._llm_output(input_data)
            return self._evidence_output(input_data, fallback_reason=None)

        return run_with_trace(
            trace_service=self.trace_service,
            task_id=task.task_id,
            agent_name=self.name,
            to_agent="ReportWriterAgent",
            message_type="analysis",
            schema_name="AnalystOutput",
            input_summary=f"analyst_mode_requested={input_data.analyst_mode}; analyze {len(input_data.evidence)} evidence records",
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def _llm_output(self, input_data: AnalystInput) -> AnalystOutput:
        diagnostics = self._llm_diagnostics(input_data)
        messages = self._llm_messages(input_data)
        llm_response = self.llm_client.chat_json(messages)
        diagnostics.update(
            {
                "llm_call_attempted": llm_response.attempted,
                "llm_call_success": llm_response.success,
                "llm_elapsed_time_ms": llm_response.elapsed_time_ms,
                "llm_error_type": llm_response.error_type,
                "llm_error_message": llm_response.error_message,
                "llm_response_preview": llm_response.response_preview,
                "llm_response_text_preview": self._preview_text(llm_response.content, 5000),
                "llm_response_text_length": len(llm_response.content or ""),
            }
        )

        if not llm_response.available:
            return self._fallback_to_evidence(
                input_data,
                diagnostics,
                llm_response.fallback_reason or "LLM is unavailable; fallback to evidence mode.",
            )

        try:
            payload = parse_llm_json(llm_response.content or "")
            dimension_results = self._validate_llm_dimension_results(input_data, payload)
        except Exception as exc:  # noqa: BLE001 - analyst must keep workflow alive.
            diagnostics.update(
                {
                    "llm_schema_validation_success": False,
                    "llm_schema_validation_errors": [str(exc)],
                    "llm_fallback_reason": f"LLM Analyst output failed validation: {exc}",
                }
            )
            return self._fallback_to_evidence(input_data, diagnostics, diagnostics["llm_fallback_reason"])

        diagnostics.update(
            {
                "analyst_mode_used": "llm",
                "fallback_used": False,
                "llm_schema_validation_success": True,
                "llm_schema_validation_errors": [],
                "dimension_results_count": len(dimension_results),
                "insufficient_evidence_dimensions": [
                    item.dimension_id for item in dimension_results if item.insufficient_evidence
                ],
                "evidence_used_count": len({evidence_id for item in dimension_results for evidence_id in item.evidence_ids}),
            }
        )
        return self._output_from_dimension_results(input_data, dimension_results, diagnostics)

    def _fallback_to_evidence(self, input_data: AnalystInput, diagnostics: dict, fallback_reason: str) -> AnalystOutput:
        output = self._evidence_output(input_data, fallback_reason=fallback_reason)
        output.diagnostics.update(diagnostics)
        output.diagnostics.update(
            {
                "analyst_mode_used": "evidence",
                "fallback_used": True,
                "fallback_reason": fallback_reason,
                "analyst_fallback_reason": fallback_reason,
            }
        )
        return output

    def _mock_output(self, input_data: AnalystInput, fallback_reason: str | None) -> AnalystOutput:
        task = input_data.task
        ids = self._ids(input_data.evidence)
        selected_dimensions = self._selected_dimensions(input_data)
        diagnostics = self._diagnostics(
            input_data,
            "mock",
            ids,
            fallback_reason=fallback_reason,
            evidence_by_competitor=self._group_by_competitor(input_data.evidence, task.competitors),
            extracted_fields_by_competitor={
                competitor: {"profile": 1, "feature": 1, "pricing": 1, "persona": 1} for competitor in task.competitors
            },
        )
        competitor_analysis = {
            competitor: {
                "positioning": f"{competitor} is represented by mock competitor knowledge.",
                "features": ["collaboration", "workflow"],
                "pricing": ["paid/enterprise signal"],
                "persona": ["enterprise team"],
                "evidence_ids": [item.evidence_id for item in input_data.evidence if item.competitor == competitor] or ids[:1],
                "insufficient_evidence": False,
            }
            for competitor in task.competitors
        }
        profile = ProductProfile(
            product_name=task.product_name,
            positioning=""
            if input_data.force_invalid_extraction
            else f"{task.product_name} is a structured competitor analysis workspace.",
            target_segments=[]
            if input_data.force_invalid_extraction
            else ["product marketing team", "strategy team", "sales enablement team"],
            strengths=["traceable evidence", "structured schema", "QA feedback loop"],
            weaknesses=["current demo still uses simplified extraction rules"],
            evidence_ids=ids[:2],
            custom_dimensions={
                "region": task.region,
                "industry": task.industry,
                "analyst_mode": "mock",
                "selected_dimensions": selected_dimensions,
                "competitor_analysis": competitor_analysis,
            },
        )
        feature_tree = FeatureTree(
            core_features={competitor: ["collaboration", "workflow", "pricing"] for competitor in task.competitors},
            differentiators=["claim-to-evidence traceability", "manual review fallback"],
            evidence_ids=ids,
        )
        pricing = PricingModel(
            model="tiered SaaS benchmark",
            tiers=[f"{competitor}: starter/team/enterprise signals" for competitor in task.competitors],
            pricing_notes="Competitors usually package collaboration and integration capability into higher tiers.",
            evidence_ids=ids[1:3] or ids[:1],
        )
        persona = UserPersona(
            persona_name="competitor intelligence owner",
            goals=["reduce manual research time", "keep conclusions source-backed", "standardize report format"],
            pain_points=["sources are scattered", "evidence quality is opaque", "QA takes time"],
            buying_triggers=["new market entry", "quarterly planning", "sales battlecard refresh"],
            evidence_ids=ids[2:4] or ids[:1],
        )
        swot = self._build_mock_swot(task.competitors, ids, selected_dimensions)
        dimension_results = self._mock_dimension_results(task.competitors, selected_dimensions, competitor_analysis)
        diagnostics.update(
            {
                "selected_dimensions": selected_dimensions,
                "selected_dimension_count": len(selected_dimensions),
                "dimension_results_count": len(dimension_results),
                "swot_item_count": self._count_swot_items(swot),
                "rework_context_applied": bool(input_data.rework_context),
                "long_term_knowledge_chunk_count": len(input_data.retrieved_knowledge_chunks),
                "long_term_knowledge_used": False,
                "long_term_knowledge_policy": "current_run_evidence_has_priority",
            }
        )
        return AnalystOutput(
            dimension_results=dimension_results,
            product_profile=profile,
            feature_tree=feature_tree,
            pricing_model=pricing,
            user_persona=persona,
            swot=swot,
            diagnostics=diagnostics,
        )

    def _evidence_output(self, input_data: AnalystInput, fallback_reason: str | None) -> AnalystOutput:
        task = input_data.task
        selected_dimensions = self._selected_dimensions(input_data)
        usable_evidence = [item for item in input_data.evidence if item.relevance_level in {"high", "medium"}]
        weak_evidence = [item for item in input_data.evidence if item.relevance_level == "low"]
        evidence_by_competitor = self._group_by_competitor(usable_evidence, task.competitors)
        all_evidence_by_competitor = self._group_by_competitor(input_data.evidence, task.competitors)
        ids = self._ids(usable_evidence)
        competitor_analysis: dict[str, dict] = {}
        core_features: dict[str, list[str]] = {}
        aggregate_feature_hits: dict[str, list[Evidence]] = defaultdict(list)
        pricing_tiers: list[str] = []
        persona_goals: list[str] = []
        persona_pain_points: list[str] = []
        persona_triggers: list[str] = []
        aggregate_persona_labels: list[str] = []
        extracted_fields_by_competitor: dict[str, dict[str, int]] = {}
        evidence_used_by_competitor: dict[str, list[str]] = {}

        for competitor in task.competitors:
            competitor_evidence = sorted(evidence_by_competitor.get(competitor, []), key=lambda item: item.confidence, reverse=True)
            competitor_ids = self._ids(competitor_evidence)
            feature_hits = self._feature_hits(competitor_evidence)
            pricing_evidence = self._keyword_evidence(competitor_evidence, PRICING_KEYWORDS)
            persona_hits = self._persona_hits(competitor_evidence)
            insufficient = len(competitor_evidence) < 1 or (len(feature_hits) + len(pricing_evidence) + len(persona_hits)) < 1

            feature_names = list(feature_hits.keys()) or ["insufficient evidence"]
            pricing_labels = self._pricing_tiers(pricing_evidence)
            persona_labels = list(persona_hits.keys()) or ["Evidence-insufficient persona"]
            aggregate_persona_labels.extend([item for item in persona_labels if item != "Evidence-insufficient persona"])
            positioning = (
                "Evidence is insufficient for a confident conclusion."
                if insufficient
                else f"{competitor} positioning is inferred only from its own public evidence: {self._compact(self._evidence_text(competitor_evidence[0]))}"
            )

            competitor_analysis[competitor] = {
                "positioning": positioning,
                "features": feature_names,
                "pricing": pricing_labels,
                "persona": persona_labels,
                "evidence_ids": competitor_ids,
                "insufficient_evidence": insufficient,
            }
            evidence_used_by_competitor[competitor] = competitor_ids
            extracted_fields_by_competitor[competitor] = {
                "profile": 0 if insufficient else 1,
                "feature": len(feature_hits),
                "pricing": len(pricing_evidence),
                "persona": len(persona_hits),
            }
            for feature, values in feature_hits.items():
                aggregate_feature_hits[feature].extend(values)
                core_features[f"{competitor} / {feature}"] = self._feature_labels(feature, values)
            pricing_tiers.append(f"{competitor}: {', '.join(pricing_labels)}")
            persona_goals.append(f"{competitor}: evaluate fit for {', '.join(persona_labels[:2])}")
            persona_pain_points.append(f"{competitor}: needs more official, pricing, and user-feedback evidence cross-checks")
            persona_triggers.append(f"{competitor}: product selection and competitor replacement")

        for feature, values in aggregate_feature_hits.items():
            core_features.setdefault(feature, self._feature_labels(feature, values))

        missing_competitors = [competitor for competitor, records in evidence_by_competitor.items() if not records]
        insufficient = bool(missing_competitors) or any(item["insufficient_evidence"] for item in competitor_analysis.values())
        dimension_results = self._build_dimension_results(
            task.competitors,
            selected_dimensions,
            evidence_by_competitor=evidence_by_competitor,
            competitor_analysis=competitor_analysis,
        )
        diagnostics = self._diagnostics(
            input_data,
            "evidence",
            ids,
            fallback_reason=fallback_reason or ("Evidence is insufficient for one or more competitors." if insufficient else None),
            insufficient=insufficient,
            feature_count=sum(item["feature"] for item in extracted_fields_by_competitor.values()),
            pricing_count=sum(item["pricing"] for item in extracted_fields_by_competitor.values()),
            persona_count=sum(item["persona"] for item in extracted_fields_by_competitor.values()),
            evidence_by_competitor=evidence_by_competitor,
            extracted_fields_by_competitor=extracted_fields_by_competitor,
            evidence_used_by_competitor=evidence_used_by_competitor,
        )
        diagnostics.update(
            {
                "insufficient_relevant_evidence": insufficient,
                "relevant_evidence_count": len(usable_evidence),
                "low_relevance_evidence_count": len(weak_evidence),
                "unrelated_evidence_count": sum(1 for item in input_data.evidence if item.relevance_level == "unrelated"),
                "all_evidence_count_by_competitor": {
                    competitor: len(records) for competitor, records in all_evidence_by_competitor.items()
                },
                "content_source_used": self._content_source_summary(usable_evidence),
                "selected_dimensions": selected_dimensions,
                "selected_dimension_count": len(selected_dimensions),
                "dimension_results_count": len(dimension_results),
                "insufficient_evidence_dimensions": [
                    item.dimension_id for item in dimension_results if item.insufficient_evidence
                ],
                "long_term_knowledge_chunk_count": len(input_data.retrieved_knowledge_chunks),
                "long_term_knowledge_evidence_ids": [
                    item.evidence_id for item in input_data.retrieved_knowledge_chunks if item.evidence_id
                ],
                "long_term_knowledge_used": bool(input_data.retrieved_knowledge_chunks),
                "long_term_knowledge_policy": "current_run_evidence_has_priority",
                "long_term_knowledge_conflict_warnings": self._knowledge_conflict_warnings(input_data, usable_evidence),
            }
        )
        profile = ProductProfile(
            product_name=task.product_name,
            positioning=""
            if input_data.force_invalid_extraction
            else (
                "Evidence is insufficient for a confident conclusion."
                if insufficient
                else f"{task.product_name} compares {', '.join(task.competitors)} using competitor-specific public evidence."
            ),
            target_segments=[]
            if input_data.force_invalid_extraction
            else (["Evidence is insufficient for a confident conclusion."] if insufficient else ["enterprise team", "product team"]),
            strengths=self._strengths_from_features(dict(aggregate_feature_hits)) or ["Evidence is insufficient for a confident conclusion."],
            weaknesses=["Conclusions remain limited by available public evidence coverage per competitor."],
            evidence_ids=ids[: min(5, len(ids))],
            custom_dimensions={
                "region": task.region,
                "industry": task.industry,
                "analyst_mode": "evidence",
                "selected_dimensions": selected_dimensions,
                "insufficient_evidence": insufficient,
                "supporting_evidence_ids": ids,
                "competitor_analysis": competitor_analysis,
            },
        )
        feature_tree = FeatureTree(
            core_features=core_features or {"insufficient evidence": ["Evidence is insufficient for a confident conclusion."]},
            differentiators=self._strengths_from_features(dict(aggregate_feature_hits)) or ["Evidence is insufficient for a confident conclusion."],
            evidence_ids=ids,
        )
        pricing = PricingModel(
            model="Evidence-based competitor pricing summary" if not insufficient else "Evidence insufficient",
            tiers=pricing_tiers or ["Evidence is insufficient"],
            pricing_notes=self._pricing_notes([item for records in evidence_by_competitor.values() for item in records]),
            evidence_ids=[
                item.evidence_id
                for records in evidence_by_competitor.values()
                for item in self._keyword_evidence(records, PRICING_KEYWORDS)
            ]
            or ids[:1],
        )
        persona = UserPersona(
            persona_name=aggregate_persona_labels[0] if aggregate_persona_labels else "competitor evaluation team",
            goals=persona_goals or ["Evidence is insufficient for a confident conclusion."],
            pain_points=persona_pain_points or ["Need more competitor-specific evidence."],
            buying_triggers=persona_triggers or ["Evidence is insufficient for a confident conclusion."],
            evidence_ids=ids[: min(5, len(ids))],
        )
        swot = self._build_evidence_swot(
            task.competitors,
            selected_dimensions,
            evidence_by_competitor=evidence_by_competitor,
            competitor_analysis=competitor_analysis,
            aggregate_feature_hits=dict(aggregate_feature_hits),
        )
        swot, swot_refinement_summary = self._refine_swot_for_rework(
            swot,
            input_data=input_data,
            evidence_by_competitor=evidence_by_competitor,
        )
        diagnostics["swot_item_count"] = self._count_swot_items(swot)
        diagnostics["rework_context_applied"] = bool(input_data.rework_context)
        diagnostics["swot_refinement_summary"] = swot_refinement_summary
        return AnalystOutput(
            dimension_results=dimension_results,
            product_profile=profile,
            feature_tree=feature_tree,
            pricing_model=pricing,
            user_persona=persona,
            swot=swot,
            diagnostics=diagnostics,
        )

    def _llm_diagnostics(self, input_data: AnalystInput) -> dict[str, Any]:
        return {
            "analyst_mode_requested": input_data.analyst_mode,
            "analyst_mode_used": "llm",
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
            "llm_response_text_preview": None,
            "llm_response_text_length": 0,
            "llm_schema_validation_success": None,
            "llm_schema_validation_errors": [],
            "llm_fallback_reason": None,
            "fallback_used": False,
            "selected_dimensions": self._analysis_dimensions(self._selected_dimensions(input_data)),
            "long_term_knowledge_chunk_count": len(input_data.retrieved_knowledge_chunks),
            "rework_context_applied": bool(input_data.rework_context),
        }

    @staticmethod
    def _preview_text(content: str | None, limit: int) -> str | None:
        if content is None:
            return None
        return content[:limit]

    def _llm_messages(self, input_data: AnalystInput) -> list[dict[str, str]]:
        selected_dimensions = self._analysis_dimensions(self._selected_dimensions(input_data))
        usable_evidence = [
            item.model_dump(mode="json")
            for item in input_data.evidence
            if item.relevance_level in {"high", "medium"}
        ]
        prompt_data = {
            "task": input_data.task.model_dump(mode="json"),
            "selected_dimensions": selected_dimensions,
            "evidence": usable_evidence,
            "allowed_evidence_ids_by_competitor": self._allowed_evidence_ids_by_competitor(input_data.evidence),
            "long_term_knowledge": [item.model_dump(mode="json") for item in input_data.retrieved_knowledge_chunks],
            "rework_context": input_data.rework_context.model_dump(mode="json") if input_data.rework_context else None,
        }
        system = (
            "You are AnalystAgent. Extract dimension-level competitor facts from supplied Evidence only. "
            "Return strict JSON only. Do not wrap it in markdown code fences. "
            "JSON keys must stay English. Chinese explanatory text is allowed in values. "
            "The top-level JSON object must contain dimension_results. "
            "Each dimension result must include dimension_id, competitor, summary, findings, evidence_ids, confidence, insufficient_evidence, metadata. "
            "dimension_id must be one of selected_dimensions. competitor must be one of task.competitors. "
            "For insufficient evidence, set insufficient_evidence=true, evidence_ids=[], confidence<=0.4, and keep summary conservative. "
            "For supported findings, evidence_ids must be non-empty and must refer only to Evidence from the same competitor. "
            "Do not use unrelated or out-of-scope evidence. Long-term knowledge is secondary context; current Evidence has priority. "
            "When long-term knowledge is available, it is also injected into evidence as source_type=knowledge_base with a current-run evidence_id. "
            "Only cite evidence_id values that appear in the evidence list. Do not cite long_term_knowledge.evidence_id or chunk_id directly. "
            "If public evidence is weak, say 当前公开证据不足，暂不做强结论。"
        )
        user = (
            "Extract one dimension_result for each requested competitor and each selected dimension when possible. "
            "Output example: "
            '{"dimension_results":[{"dimension_id":"feature","competitor":"竞品A","summary":"中文摘要",'
            '"findings":["中文发现"],"evidence_ids":["ev_xxx"],"confidence":0.8,'
            '"insufficient_evidence":false,"metadata":{"reason":"based on high relevance public evidence"}}]}\n'
            "Input:\n"
            f"{prompt_data}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    @staticmethod
    def _allowed_evidence_ids_by_competitor(evidence: list[Evidence]) -> dict[str, list[str]]:
        output: dict[str, list[str]] = defaultdict(list)
        for item in evidence:
            if item.competitor and item.relevance_level in {"high", "medium"}:
                output[item.competitor].append(item.evidence_id)
        return dict(output)

    def _validate_llm_dimension_results(self, input_data: AnalystInput, payload: dict[str, Any]) -> list[DimensionResult]:
        if not isinstance(payload, dict):
            raise AgentOutputValidationError("LLM Analyst output must be a JSON object.")
        raw_results = payload.get("dimension_results")
        if not isinstance(raw_results, list):
            raise AgentOutputValidationError("LLM Analyst output missing dimension_results list.")

        allowed_dimensions = set(self._analysis_dimensions(self._selected_dimensions(input_data)))
        allowed_competitors = set(input_data.task.competitors)
        evidence_by_id = {item.evidence_id: item for item in input_data.evidence}
        results: list[DimensionResult] = []

        for index, item in enumerate(raw_results):
            if not isinstance(item, dict):
                raise AgentOutputValidationError(f"dimension_results[{index}] must be an object.")
            dimension_id = str(item.get("dimension_id", "")).strip().lower()
            competitor = item.get("competitor")
            if dimension_id not in allowed_dimensions:
                raise AgentOutputValidationError(f"dimension_id {dimension_id!r} is not in selected_dimensions.")
            if competitor not in allowed_competitors:
                raise AgentOutputValidationError(f"competitor {competitor!r} is not in task.competitors.")

            insufficient = bool(item.get("insufficient_evidence", False))
            evidence_ids = [str(evidence_id) for evidence_id in item.get("evidence_ids", []) if evidence_id]
            if insufficient:
                evidence_ids = []
            elif not evidence_ids:
                raise AgentOutputValidationError(f"DimensionResult {dimension_id}/{competitor} missing evidence_ids.")

            for evidence_id in evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None:
                    raise AgentOutputValidationError(f"DimensionResult cites unknown evidence_id {evidence_id}.")
                if evidence.relevance_level == "unrelated":
                    raise AgentOutputValidationError(f"DimensionResult cites unrelated evidence_id {evidence_id}.")
                if evidence.competitor and evidence.competitor != competitor:
                    raise AgentOutputValidationError(
                        f"DimensionResult {dimension_id}/{competitor} cites Evidence {evidence_id} from {evidence.competitor}."
                    )

            try:
                result = DimensionResult(
                    dimension_id=dimension_id,
                    competitor=competitor,
                    summary=str(item.get("summary") or "当前公开证据不足，暂不做强结论。"),
                    findings=[
                        str(finding)
                        for finding in item.get("findings", [])
                        if isinstance(finding, str) and finding.strip()
                    ],
                    evidence_ids=evidence_ids,
                    confidence=float(item.get("confidence", 0.35 if insufficient else 0.7)),
                    insufficient_evidence=insufficient,
                    metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                )
            except (TypeError, ValueError, ValidationError) as exc:
                raise AgentOutputValidationError(f"Invalid DimensionResult schema: {exc}") from exc
            results.append(result)

        self._ensure_dimension_coverage(input_data, results)
        return results

    def _ensure_dimension_coverage(self, input_data: AnalystInput, results: list[DimensionResult]) -> None:
        selected_dimensions = self._analysis_dimensions(self._selected_dimensions(input_data))
        existing = {(item.competitor, item.dimension_id) for item in results}
        for competitor in input_data.task.competitors:
            for dimension_id in selected_dimensions:
                if (competitor, dimension_id) not in existing:
                    results.append(
                        DimensionResult(
                            dimension_id=dimension_id,
                            competitor=competitor,
                            summary="当前公开证据不足，暂不做强结论。",
                            findings=[],
                            evidence_ids=[],
                            confidence=0.35,
                            insufficient_evidence=True,
                            metadata={"source": "llm_coverage_fallback"},
                        )
                    )

    def _output_from_dimension_results(
        self,
        input_data: AnalystInput,
        dimension_results: list[DimensionResult],
        diagnostics: dict[str, Any],
    ) -> AnalystOutput:
        task = input_data.task
        ids = self._dedupe([evidence_id for item in dimension_results for evidence_id in item.evidence_ids]) or ["insufficient_evidence"]
        by_competitor = {
            competitor: [item for item in dimension_results if item.competitor == competitor]
            for competitor in task.competitors
        }
        competitor_analysis = {
            competitor: self._competitor_analysis_from_dimensions(competitor, results)
            for competitor, results in by_competitor.items()
        }
        feature_results = [item for item in dimension_results if item.dimension_id in {"feature", "features"} and not item.insufficient_evidence]
        pricing_results = [item for item in dimension_results if item.dimension_id in {"pricing", "business_model"} and not item.insufficient_evidence]
        persona_results = [item for item in dimension_results if item.dimension_id in {"persona", "user_persona", "user"} and not item.insufficient_evidence]
        positioning_results = [item for item in dimension_results if item.dimension_id == "positioning" and not item.insufficient_evidence]
        insufficient = any(item.insufficient_evidence for item in dimension_results)

        profile = ProductProfile(
            product_name=task.product_name,
            positioning=positioning_results[0].summary if positioning_results else "当前公开证据不足，暂不做强结论。",
            target_segments=self._dedupe([finding for item in persona_results for finding in item.findings])[:4]
            or ["当前公开证据不足，暂不做强结论。"],
            strengths=self._dedupe([item.summary for item in feature_results])[:4]
            or ["当前公开证据不足，暂不做强结论。"],
            weaknesses=["Conclusions remain limited by available public evidence coverage per competitor."],
            evidence_ids=ids[: min(5, len(ids))],
            custom_dimensions={
                "region": task.region,
                "industry": task.industry,
                "analyst_mode": "llm",
                "selected_dimensions": self._analysis_dimensions(self._selected_dimensions(input_data)),
                "insufficient_evidence": insufficient,
                "supporting_evidence_ids": ids,
                "competitor_analysis": competitor_analysis,
            },
        )
        feature_tree = FeatureTree(
            core_features={
                f"{item.competitor} / {item.dimension_id}": item.findings or [item.summary]
                for item in feature_results
            }
            or {"insufficient evidence": ["当前公开证据不足，暂不做强结论。"]},
            differentiators=[item.summary for item in feature_results[:4]] or ["当前公开证据不足，暂不做强结论。"],
            evidence_ids=ids,
        )
        pricing = PricingModel(
            model="LLM dimension-based pricing summary" if pricing_results else "Evidence insufficient",
            tiers=[f"{item.competitor}: {item.summary}" for item in pricing_results] or ["当前公开证据不足，暂不做强结论。"],
            pricing_notes="; ".join(item.summary for item in pricing_results[:3])
            or "当前公开证据不足，暂不做强结论。",
            evidence_ids=self._dedupe([evidence_id for item in pricing_results for evidence_id in item.evidence_ids]) or ids[:1],
        )
        persona = UserPersona(
            persona_name=persona_results[0].competitor or "competitor evaluation team" if persona_results else "competitor evaluation team",
            goals=[item.summary for item in persona_results] or ["当前公开证据不足，暂不做强结论。"],
            pain_points=["Need more explicit public user-feedback evidence."],
            buying_triggers=["Product selection and competitor replacement"],
            evidence_ids=self._dedupe([evidence_id for item in persona_results for evidence_id in item.evidence_ids]) or ids[:1],
        )
        swot = self._build_dimension_swot(task.competitors, dimension_results)
        diagnostics.update(
            {
                "extracted_profile_count": len(positioning_results),
                "extracted_feature_count": len(feature_results),
                "extracted_pricing_count": len(pricing_results),
                "extracted_persona_count": len(persona_results),
                "insufficient_evidence": insufficient,
                "swot_item_count": self._count_swot_items(swot),
            }
        )
        return AnalystOutput(
            dimension_results=dimension_results,
            product_profile=profile,
            feature_tree=feature_tree,
            pricing_model=pricing,
            user_persona=persona,
            swot=swot,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _competitor_analysis_from_dimensions(competitor: str, results: list[DimensionResult]) -> dict:
        evidence_ids = [evidence_id for item in results for evidence_id in item.evidence_ids]
        return {
            "positioning": next((item.summary for item in results if item.dimension_id == "positioning"), "当前公开证据不足，暂不做强结论。"),
            "features": [finding for item in results if item.dimension_id in {"feature", "features"} for finding in (item.findings or [item.summary])],
            "pricing": [item.summary for item in results if item.dimension_id in {"pricing", "business_model"}],
            "persona": [item.summary for item in results if item.dimension_id in {"persona", "user_persona", "user"}],
            "evidence_ids": evidence_ids,
            "insufficient_evidence": any(item.insufficient_evidence for item in results),
            "dimension_result_count": len(results),
            "competitor": competitor,
        }

    def _build_dimension_swot(self, competitors: list[str], dimension_results: list[DimensionResult]) -> SwotAnalysis:
        supported = [item for item in dimension_results if not item.insufficient_evidence and item.evidence_ids]
        strengths = [
            SwotItem(
                summary=item.summary,
                competitor=item.competitor,
                evidence_ids=item.evidence_ids,
                confidence=min(0.9, item.confidence),
            )
            for item in supported
            if item.dimension_id in {"feature", "positioning"}
        ][:4]
        weaknesses = [
            SwotItem(
                summary=item.summary,
                competitor=item.competitor,
                evidence_ids=item.evidence_ids or ["insufficient_evidence"],
                confidence=min(0.45, item.confidence),
            )
            for item in dimension_results
            if item.insufficient_evidence
        ][:4]
        opportunities = [
            SwotItem(
                summary=item.summary,
                competitor=item.competitor,
                evidence_ids=item.evidence_ids,
                confidence=min(0.7, item.confidence),
            )
            for item in supported
            if item.dimension_id in {"pricing", "persona", "ux", "feedback"}
        ][:4]
        threats = [
            SwotItem(
                summary="Evidence coverage remains incomplete; keep cross-competitor conclusions conservative.",
                competitor=competitor,
                evidence_ids=["insufficient_evidence"],
                confidence=0.35,
            )
            for competitor in competitors
            if any(item.competitor == competitor and item.insufficient_evidence for item in dimension_results)
        ][:4]
        fallback_id = supported[0].evidence_ids[:1] if supported else ["insufficient_evidence"]
        return SwotAnalysis(
            strengths=strengths or [SwotItem(summary="当前公开证据不足，暂不做强结论。", evidence_ids=fallback_id, confidence=0.35)],
            weaknesses=weaknesses,
            opportunities=opportunities,
            threats=threats,
        )

    def _diagnostics(
        self,
        input_data: AnalystInput,
        used_mode: str,
        ids: list[str],
        *,
        fallback_reason: str | None,
        insufficient: bool = False,
        feature_count: int = 0,
        pricing_count: int = 0,
        persona_count: int = 0,
        evidence_by_competitor: dict[str, list[Evidence]] | None = None,
        extracted_fields_by_competitor: dict[str, dict[str, int]] | None = None,
        evidence_used_by_competitor: dict[str, list[str]] | None = None,
    ) -> dict:
        grouped = evidence_by_competitor or self._group_by_competitor(input_data.evidence, input_data.task.competitors)
        evidence_count_by_competitor = {competitor: len(records) for competitor, records in grouped.items()}
        competitors_covered = [competitor for competitor, count in evidence_count_by_competitor.items() if count > 0]
        missing_competitors = [
            competitor for competitor in input_data.task.competitors if evidence_count_by_competitor.get(competitor, 0) == 0
        ]
        return {
            "analyst_mode_requested": input_data.analyst_mode,
            "analyst_mode_used": used_mode,
            "evidence_count": len(input_data.evidence),
            "evidence_used_count": len(ids),
            "extracted_profile_count": 0 if insufficient else len(competitors_covered),
            "extracted_feature_count": feature_count,
            "extracted_pricing_count": pricing_count,
            "extracted_persona_count": persona_count,
            "insufficient_evidence": insufficient,
            "fallback_used": bool(fallback_reason),
            "fallback_reason": fallback_reason,
            "analyst_fallback_reason": fallback_reason,
            "competitors_requested": input_data.task.competitors,
            "competitors_covered": competitors_covered,
            "missing_competitors": missing_competitors,
            "evidence_count_by_competitor": evidence_count_by_competitor,
            "evidence_used_by_competitor": evidence_used_by_competitor
            or {competitor: [item.evidence_id for item in records] for competitor, records in grouped.items()},
            "extracted_fields_by_competitor": extracted_fields_by_competitor or {},
        }

    @staticmethod
    def _group_by_competitor(evidence: list[Evidence], competitors: list[str]) -> dict[str, list[Evidence]]:
        grouped: dict[str, list[Evidence]] = {competitor: [] for competitor in competitors}
        if evidence and not any(item.competitor for item in evidence) and all(is_relevant_evidence(item) for item in evidence):
            for competitor in competitors:
                grouped[competitor] = list(evidence)
            return grouped
        for item in evidence:
            if item.competitor in grouped:
                grouped[item.competitor].append(item)
            elif item.competitor is None and len(competitors) == 1:
                grouped[competitors[0]].append(item)
        return grouped

    @staticmethod
    def _ids(evidence: list[Evidence]) -> list[str]:
        return [item.evidence_id for item in evidence] or ["insufficient_evidence"]

    @staticmethod
    def _compact(text: str) -> str:
        return text[:120].replace("\n", " ")

    @staticmethod
    def _evidence_text(item: Evidence) -> str:
        return item.content_excerpt or item.snippet

    @staticmethod
    def _content_source_summary(evidence: list[Evidence]) -> dict[str, int]:
        return {
            "page_excerpt": sum(1 for item in evidence if item.content_excerpt),
            "snippet": sum(1 for item in evidence if not item.content_excerpt),
        }

    @staticmethod
    def _knowledge_conflict_warnings(input_data: AnalystInput, current_evidence: list[Evidence]) -> list[str]:
        current_competitors = {item.competitor for item in current_evidence if item.competitor}
        warnings: list[str] = []
        for chunk in input_data.retrieved_knowledge_chunks:
            competitor = chunk.metadata.get("competitor")
            if competitor and competitor not in input_data.task.competitors:
                warnings.append(f"Retrieved knowledge competitor {competitor} is outside current task scope.")
            if competitor and current_competitors and competitor not in current_competitors:
                warnings.append(f"Retrieved knowledge for {competitor} has no current-run evidence; keep it secondary.")
        return warnings

    def _feature_hits(self, evidence: list[Evidence]) -> dict[str, list[Evidence]]:
        hits: dict[str, list[Evidence]] = defaultdict(list)
        for item in evidence:
            text = self._evidence_text(item).lower()
            for feature, keywords in FEATURE_KEYWORDS.items():
                if any(keyword.lower() in text for keyword in keywords):
                    hits[feature].append(item)
        return dict(hits)

    def _keyword_evidence(self, evidence: list[Evidence], keywords: list[str]) -> list[Evidence]:
        matched = []
        for item in evidence:
            text = f"{self._evidence_text(item)} {item.url or ''}".lower()
            if any(keyword.lower() in text for keyword in keywords):
                matched.append(item)
        return matched

    def _persona_hits(self, evidence: list[Evidence]) -> dict[str, list[Evidence]]:
        hits: dict[str, list[Evidence]] = defaultdict(list)
        for item in evidence:
            text = self._evidence_text(item).lower()
            for persona, keywords in PERSONA_KEYWORDS.items():
                if any(keyword.lower() in text for keyword in keywords):
                    hits[persona].append(item)
        return dict(hits)

    @staticmethod
    def _feature_labels(feature: str, evidence: list[Evidence]) -> list[str]:
        ids = ", ".join(item.evidence_id for item in evidence[:3])
        return [f"{feature} related evidence: {ids}"]

    @staticmethod
    def _strengths_from_features(feature_hits: dict[str, list[Evidence]]) -> list[str]:
        return [f"Public evidence mentions {feature} capability" for feature in list(feature_hits.keys())[:4]]

    @staticmethod
    def _pricing_notes(evidence: list[Evidence]) -> str:
        if not evidence:
            return "Evidence is insufficient for a confident pricing conclusion."
        return "Public evidence includes pricing or packaging signals; official pages should be used for final validation."

    def _pricing_tiers(self, evidence: list[Evidence]) -> list[str]:
        if not evidence:
            return ["Evidence is insufficient"]
        return [
            "free/trial signal" if any(word in self._evidence_text(item).lower() for word in ["free", "trial"]) else "paid/enterprise signal"
            for item in evidence[:3]
        ]

    @staticmethod
    def _selected_dimensions(input_data: AnalystInput) -> list[str]:
        return [str(item).strip().lower() for item in input_data.selected_dimensions if str(item).strip()]

    @staticmethod
    def _count_swot_items(swot: SwotAnalysis) -> int:
        return len(swot.strengths) + len(swot.weaknesses) + len(swot.opportunities) + len(swot.threats)

    def _mock_dimension_results(
        self,
        competitors: list[str],
        selected_dimensions: list[str],
        competitor_analysis: dict[str, dict],
    ) -> list[DimensionResult]:
        results: list[DimensionResult] = []
        for competitor in competitors:
            details = competitor_analysis.get(competitor, {})
            evidence_ids = [str(item) for item in details.get("evidence_ids", []) if item]
            for dimension_id in self._analysis_dimensions(selected_dimensions):
                results.append(
                    DimensionResult(
                        dimension_id=dimension_id,
                        competitor=competitor,
                        summary=f"Mock {dimension_id} analysis for {competitor}.",
                        findings=self._dimension_findings_from_details(dimension_id, details),
                        evidence_ids=evidence_ids[:2],
                        confidence=0.55,
                        insufficient_evidence=False,
                        metadata={"source": "mock", "legacy_view": "compatibility_summary"},
                    )
                )
        return results

    def _build_dimension_results(
        self,
        competitors: list[str],
        selected_dimensions: list[str],
        *,
        evidence_by_competitor: dict[str, list[Evidence]],
        competitor_analysis: dict[str, dict],
    ) -> list[DimensionResult]:
        results: list[DimensionResult] = []
        for competitor in competitors:
            records = evidence_by_competitor.get(competitor, [])
            details = competitor_analysis.get(competitor, {})
            for dimension_id in self._analysis_dimensions(selected_dimensions):
                dimension_records = self._evidence_for_dimension(dimension_id, records)
                if not dimension_records and dimension_id in {"positioning", "swot", "risk"}:
                    dimension_records = records[:2]
                dimension_records = sorted(dimension_records, key=self._evidence_quality_key, reverse=True)
                evidence_ids = [item.evidence_id for item in dimension_records]
                insufficient = not evidence_ids or bool(details.get("insufficient_evidence")) and dimension_id in {"feature", "pricing", "persona"}
                results.append(
                    DimensionResult(
                        dimension_id=dimension_id,
                        competitor=competitor,
                        summary=self._dimension_summary(dimension_id, competitor, details, insufficient),
                        findings=[] if insufficient else self._dimension_findings(dimension_id, details, dimension_records),
                        evidence_ids=[] if insufficient else evidence_ids,
                        confidence=self._dimension_confidence(dimension_records, insufficient),
                        insufficient_evidence=insufficient,
                        metadata={
                            "source": "evidence",
                            "evidence_count": len(evidence_ids),
                            "source_domains": self._dedupe([item.source_domain or "" for item in dimension_records]),
                            "source_quality": self._dedupe([item.source_quality for item in dimension_records]),
                            "legacy_view": "primary_dimension_result",
                        },
                    )
                )
        return results

    @staticmethod
    def _analysis_dimensions(selected_dimensions: list[str]) -> list[str]:
        defaults = fixed_dimension_ids()
        candidates = selected_dimensions or defaults
        normalized = []
        for item in candidates:
            value = str(item).strip().lower()
            if value and value not in normalized:
                normalized.append(value)
        return normalized or defaults

    def _evidence_for_dimension(self, dimension_id: str, records: list[Evidence]) -> list[Evidence]:
        tagged_records = [
            item
            for item in records
            if (item.entity_match_signals or {}).get("collector_dimension") == dimension_id
        ]
        if tagged_records:
            return self._dedupe_evidence(tagged_records)
        if dimension_id in {"feature", "features"}:
            grouped = self._feature_hits(records)
            return self._dedupe_evidence([item for values in grouped.values() for item in values])
        if dimension_id in {"pricing", "business_model"}:
            return self._keyword_evidence(records, PRICING_KEYWORDS)
        if dimension_id in {"persona", "user_persona", "user"}:
            grouped = self._persona_hits(records)
            return self._dedupe_evidence([item for values in grouped.values() for item in values])
        keywords = {
            "positioning": ["positioning", "market", "product", "category", "solution", "定位", "产品", "解决方案"],
            "ux": ["ux", "user experience", "usability", "体验", "易用", "流程"],
            "feedback": ["feedback", "review", "complaint", "评价", "反馈", "痛点"],
            "risk": ["risk", "security", "compliance", "风险", "安全", "合规"],
            "swot": ["feature", "pricing", "enterprise", "workflow", "功能", "定价", "企业", "流程"],
            **dimension_keyword_map(),
        }.get(dimension_id, [dimension_id])
        return self._keyword_evidence(records, keywords)

    @staticmethod
    def _evidence_quality_key(evidence: Evidence) -> tuple[int, float, int, float]:
        relevance_rank = {"high": 3, "medium": 2, "low": 1, "unrelated": 0}.get(evidence.relevance_level, 0)
        quality_rank = {
            "official": 5,
            "documentation": 4,
            "media": 3,
            "review": 2,
            "unknown": 1,
            "low_quality": 0,
        }.get(evidence.source_quality, 0)
        return (relevance_rank, evidence.confidence, quality_rank, evidence.relevance_score)

    @staticmethod
    def _dedupe_evidence(records: list[Evidence]) -> list[Evidence]:
        seen: set[str] = set()
        output: list[Evidence] = []
        for item in records:
            if item.evidence_id in seen:
                continue
            seen.add(item.evidence_id)
            output.append(item)
        return output

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            if not item or item in seen:
                continue
            seen.add(item)
            output.append(item)
        return output

    def _dimension_summary(self, dimension_id: str, competitor: str, details: dict, insufficient: bool) -> str:
        if insufficient:
            return f"{competitor} has insufficient public evidence for {dimension_id}."
        if dimension_id == "positioning":
            return str(details.get("positioning") or f"{competitor} positioning is inferred from public evidence.")
        if dimension_id in {"feature", "features"}:
            return f"{competitor} has public feature signals: {', '.join((details.get('features') or [])[:4])}."
        if dimension_id in {"pricing", "business_model"}:
            return f"{competitor} has pricing or packaging signals: {', '.join((details.get('pricing') or [])[:3])}."
        if dimension_id in {"persona", "user_persona", "user"}:
            return f"{competitor} has persona signals: {', '.join((details.get('persona') or [])[:3])}."
        if dimension_id == "strength":
            return f"{competitor} has public evidence related to strengths and differentiators."
        if dimension_id == "weakness":
            return f"{competitor} has public evidence related to weaknesses, complaints, or pain points."
        if dimension_id == "opportunity":
            return f"{competitor} has public evidence related to market opportunities or growth signals."
        if dimension_id == "threat":
            return f"{competitor} has public evidence related to competitive threats or risks."
        if dimension_id == "swot":
            return f"{competitor} has evidence-backed signals that can feed SWOT analysis."
        return f"{competitor} has public evidence related to {dimension_id}."

    def _dimension_findings(self, dimension_id: str, details: dict, records: list[Evidence]) -> list[str]:
        base = self._dimension_findings_from_details(dimension_id, details)
        snippets = [self._compact(self._evidence_text(item)) for item in records[:2]]
        return self._dedupe([*base, *snippets])[:5]

    @staticmethod
    def _dimension_findings_from_details(dimension_id: str, details: dict) -> list[str]:
        if dimension_id in {"feature", "features"}:
            return [f"Feature signal: {item}" for item in details.get("features", []) if item and item != "insufficient evidence"]
        if dimension_id in {"pricing", "business_model"}:
            return [f"Pricing signal: {item}" for item in details.get("pricing", []) if item and "insufficient" not in item.lower()]
        if dimension_id in {"persona", "user_persona", "user"}:
            return [f"Persona signal: {item}" for item in details.get("persona", []) if item and "insufficient" not in item.lower()]
        if dimension_id == "positioning" and details.get("positioning"):
            return [str(details["positioning"])]
        if dimension_id in {"strength", "weakness", "opportunity", "threat"}:
            return [f"{dimension_id.title()} signal: {item}" for item in details.get("features", [])[:2] if item and item != "insufficient evidence"]
        return []

    @staticmethod
    def _dimension_confidence(records: list[Evidence], insufficient: bool) -> float:
        if insufficient or not records:
            return 0.35
        avg = sum(item.confidence for item in records) / len(records)
        relevance_bonus = 0.05 if any(item.relevance_level == "high" for item in records) else 0.0
        count_bonus = min(0.1, 0.03 * max(0, len(records) - 1))
        return min(0.9, round(avg + relevance_bonus + count_bonus, 2))

    def _build_mock_swot(self, competitors: list[str], ids: list[str], selected_dimensions: list[str]) -> SwotAnalysis:
        dimensions = ", ".join(selected_dimensions[:3]) if selected_dimensions else "feature, pricing, persona"
        competitor_label = competitors[0] if competitors else None
        return SwotAnalysis(
            strengths=[
                SwotItem(
                    summary=f"Mock analysis highlights evidence traceability across {dimensions}.",
                    competitor=competitor_label,
                    evidence_ids=ids[:1],
                    confidence=0.55,
                )
            ],
            weaknesses=[
                SwotItem(
                    summary="Mock extraction still relies on simplified rules and should be validated with richer evidence.",
                    competitor=competitor_label,
                    evidence_ids=ids[:1],
                    confidence=0.45,
                )
            ],
            opportunities=[
                SwotItem(
                    summary=f"Planner-selected dimensions suggest deeper comparison opportunities around {dimensions}.",
                    competitor=competitor_label,
                    evidence_ids=ids[:1],
                    confidence=0.5,
                )
            ],
            threats=[
                SwotItem(
                    summary="Public-evidence gaps can still limit confident competitor differentiation.",
                    competitor=competitor_label,
                    evidence_ids=ids[:1],
                    confidence=0.45,
                )
            ],
        )

    def _build_evidence_swot(
        self,
        competitors: list[str],
        selected_dimensions: list[str],
        *,
        evidence_by_competitor: dict[str, list[Evidence]],
        competitor_analysis: dict[str, dict],
        aggregate_feature_hits: dict[str, list[Evidence]],
    ) -> SwotAnalysis:
        strengths: list[SwotItem] = []
        weaknesses: list[SwotItem] = []
        opportunities: list[SwotItem] = []
        threats: list[SwotItem] = []
        dimension_focus = set(selected_dimensions)

        for feature, feature_evidence in sorted(aggregate_feature_hits.items(), key=lambda item: len(item[1]), reverse=True)[:3]:
            feature_competitors = {item.competitor for item in feature_evidence if item.competitor}
            competitor = next(iter(feature_competitors)) if len(feature_competitors) == 1 else None
            strengths.append(
                SwotItem(
                    summary=f"Public evidence repeatedly mentions {feature} capability, making it a visible competitive strength.",
                    competitor=competitor,
                    evidence_ids=[item.evidence_id for item in feature_evidence[:3]] or ["insufficient_evidence"],
                    confidence=min(0.9, 0.55 + 0.08 * len(feature_evidence)),
                )
            )

        for competitor in competitors:
            records = evidence_by_competitor.get(competitor, [])
            analysis = competitor_analysis.get(competitor, {})
            record_ids = [item.evidence_id for item in records[:3]] or ["insufficient_evidence"]
            if not records or analysis.get("insufficient_evidence"):
                weaknesses.append(
                    SwotItem(
                        summary="Relevant public evidence is still too thin for a strong competitor-specific conclusion.",
                        competitor=competitor,
                        evidence_ids=record_ids,
                        confidence=0.35,
                    )
                )
                threats.append(
                    SwotItem(
                        summary="Thin evidence coverage increases the risk of over-indexing on a small set of public signals.",
                        competitor=competitor,
                        evidence_ids=record_ids,
                        confidence=0.35,
                    )
                )
                continue

            pricing_records = self._keyword_evidence(records, PRICING_KEYWORDS)
            feature_labels = analysis.get("features") or []
            if pricing_records and {"pricing", "positioning"} & dimension_focus:
                opportunities.append(
                    SwotItem(
                        summary="Pricing and packaging signals are visible enough to support a sharper positioning comparison in the next step.",
                        competitor=competitor,
                        evidence_ids=[item.evidence_id for item in pricing_records[:3]],
                        confidence=0.65,
                    )
                )
            if feature_labels and feature_labels[0] != "insufficient evidence":
                opportunities.append(
                    SwotItem(
                        summary=f"Observed signals around {', '.join(feature_labels[:2])} create room for more targeted feature differentiation.",
                        competitor=competitor,
                        evidence_ids=record_ids,
                        confidence=0.6,
                    )
                )
            weaknesses.append(
                SwotItem(
                    summary=(
                        "UX and user-feedback conclusions should stay conservative until more explicit pain-point evidence is collected."
                        if {"ux", "feedback", "prioritization"} & dimension_focus
                        else "Current public evidence still leaves some workflow and buyer-fit uncertainty."
                    ),
                    competitor=competitor,
                    evidence_ids=record_ids,
                    confidence=0.5,
                )
            )
            threats.append(
                SwotItem(
                    summary="Cross-competitor conclusions should remain guarded because available evidence may not cover the full product surface.",
                    competitor=competitor,
                    evidence_ids=record_ids,
                    confidence=0.5,
                )
            )

        fallback_competitor = competitors[0] if competitors else None
        if not strengths:
            strengths.append(
                SwotItem(
                    summary="Some relevant evidence exists, but not enough to isolate a durable strength yet.",
                    competitor=fallback_competitor,
                    evidence_ids=["insufficient_evidence"],
                    confidence=0.35,
                )
            )
        if not opportunities:
            opportunities.append(
                SwotItem(
                    summary="Additional official documentation and pricing pages would improve opportunity mapping.",
                    competitor=fallback_competitor,
                    evidence_ids=["insufficient_evidence"],
                    confidence=0.35,
                )
            )

        return SwotAnalysis(
            strengths=strengths[:4],
            weaknesses=weaknesses[:4],
            opportunities=opportunities[:4],
            threats=threats[:4],
        )

    def _refine_swot_for_rework(
        self,
        swot: SwotAnalysis,
        *,
        input_data: AnalystInput,
        evidence_by_competitor: dict[str, list[Evidence]],
    ) -> tuple[SwotAnalysis, dict]:
        if input_data.rework_context is None:
            return swot, {"applied": False, "issues_seen": 0, "adjustments": []}

        metadata = input_data.rework_context.metadata or {}
        issues = metadata.get("swot_issues", []) if isinstance(metadata.get("swot_issues", []), list) else []
        if not issues:
            return swot, {"applied": False, "issues_seen": 0, "adjustments": []}

        refined = swot.model_copy(deep=True)
        evidence_by_id = {item.evidence_id: item for item in input_data.evidence}
        adjustments: list[str] = []

        for issue in issues:
            quadrant = issue.get("quadrant")
            competitor = issue.get("competitor")
            error_type = issue.get("error_type")
            if quadrant not in {"strengths", "weaknesses", "opportunities", "threats"}:
                continue
            items = getattr(refined, quadrant)
            for index, item in enumerate(items):
                if competitor and item.competitor not in {competitor, None}:
                    continue
                supporting_records = [
                    evidence_by_id[evidence_id]
                    for evidence_id in item.evidence_ids
                    if evidence_id in evidence_by_id and evidence_by_id[evidence_id].relevance_level in {"high", "medium"}
                ]
                competitor_records = evidence_by_competitor.get(item.competitor or competitor or "", [])
                if error_type == "swot_missing_support":
                    fallback_ids = [record.evidence_id for record in competitor_records[:2]] or ["insufficient_evidence"]
                    items[index] = item.model_copy(
                        update={
                            "summary": "Evidence remains too thin for a strong SWOT conclusion after recheck.",
                            "evidence_ids": fallback_ids,
                            "confidence": 0.35,
                        }
                    )
                    adjustments.append(f"{quadrant}:{item.competitor or 'overall'} softened due to missing support")
                elif error_type == "swot_over_inference":
                    items[index] = item.model_copy(
                        update={
                            "summary": f"Conservative follow-up: {item.summary}",
                            "confidence": min(item.confidence, 0.45),
                        }
                    )
                    adjustments.append(f"{quadrant}:{item.competitor or 'overall'} confidence lowered")
                elif error_type == "swot_competitor_mismatch":
                    same_competitor_ids = [record.evidence_id for record in competitor_records[:3]] or ["insufficient_evidence"]
                    items[index] = item.model_copy(
                        update={
                            "evidence_ids": same_competitor_ids,
                            "confidence": 0.4 if same_competitor_ids == ["insufficient_evidence"] else min(item.confidence, 0.5),
                        }
                    )
                    adjustments.append(f"{quadrant}:{item.competitor or 'overall'} evidence rebound to same competitor")
                elif error_type == "swot_dimension_gap" and supporting_records:
                    items[index] = item.model_copy(update={"confidence": min(item.confidence, 0.55)})
                    adjustments.append(f"{quadrant}:{item.competitor or 'overall'} kept conservative for planner dimension gap")

        return refined, {
            "applied": bool(adjustments),
            "issues_seen": len(issues),
            "adjustments": adjustments,
            "rework_error_type": input_data.rework_context.error_type,
        }
