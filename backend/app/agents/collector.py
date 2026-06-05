from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.agents.base import run_with_trace
from app.constants.analysis_dimensions import FIXED_COMPETITIVE_DIMENSIONS, dimension_for_query, fixed_query_hints_for_competitor
from app.schemas import CollectorInput, CollectorOutput, Evidence
from app.services.evidence_relevance_service import apply_relevance
from app.services.trace_service import TraceService
from app.services.web_search_client import WebSearchClient

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "fbclid"}
QUALITY_CONFIDENCE = {
    "official": 0.9,
    "documentation": 0.85,
    "media": 0.75,
    "review": 0.65,
    "unknown": 0.6,
    "low_quality": 0.4,
}
MIN_EVIDENCE_PER_COMPETITOR = 2
MIN_RELEVANT_EVIDENCE_PER_COMPETITOR = 1
MAX_EVIDENCE_PER_COMPETITOR = 21
MAX_EVIDENCE_PER_DIMENSION = 3
MAX_EVIDENCE_PER_QUERY = 3
MAX_QUERY_COUNT_PER_COMPETITOR = 30


class CollectorAgent:
    name = "CollectorAgent"

    def __init__(self, trace_service: TraceService, web_search_client: WebSearchClient | None = None):
        self.trace_service = trace_service
        self.web_search_client = web_search_client or WebSearchClient()

    def run(self, input_data: CollectorInput) -> CollectorOutput:
        task = input_data.task

        def produce() -> CollectorOutput:
            diagnostics = self._base_diagnostics(input_data)
            if input_data.collector_mode == "web":
                web_output = self._collect_web(input_data, diagnostics)
                if web_output is not None:
                    return web_output
            return self._mock_output(input_data, diagnostics)

        return run_with_trace(
            trace_service=self.trace_service,
            task_id=task.task_id,
            agent_name=self.name,
            to_agent="AnalystAgent",
            message_type="evidence",
            schema_name="CollectorOutput",
            input_summary=f"collector_mode_requested={input_data.collector_mode}; collect evidence for {len(task.competitors)} competitors",
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def _base_diagnostics(self, input_data: CollectorInput) -> dict:
        return {
            "collector_mode_requested": input_data.collector_mode,
            "collector_mode_used": "mock",
            "search_provider": self.web_search_client.provider,
            "search_base_url_configured": bool(self.web_search_client.base_url),
            "has_search_api_key": bool(self.web_search_client.api_key),
            "web_search_attempted": False,
            "web_search_success": False,
            "planner_query_hints_used": bool(input_data.planner_query_hints),
            "collector_search_plan_used": bool(input_data.collector_search_plan),
            "entity_aliases_used": bool(input_data.competitor_aliases),
            "competitor_aliases_by_competitor": input_data.competitor_aliases,
            "alias_query_count_by_competitor": {},
            "alias_queries_preview_by_competitor": {},
            "targeted_recollection_used": bool(input_data.gate_context),
            "category_scope_hints_used": bool(input_data.planner_query_hints.get("category_scope")),
            "planner_hint_query_count_by_competitor": {},
            "planned_query_count_by_competitor": {},
            "targeted_query_count_by_competitor": {},
            "default_query_count_by_competitor": {},
            "effective_query_count_by_competitor": {},
            "effective_queries_preview_by_competitor": {},
            "targeted_queries_preview_by_competitor": {},
            "skipped_queries_by_competitor": {},
            "query_policy": [],
            "query_dimensions_by_competitor": {},
            "query_count": 0,
            "query_count_by_competitor": {},
            "evidence_count": 0,
            "evidence_count_by_competitor": {},
            "evidence_count_by_dimension_by_competitor": {},
            "raw_evidence_count": 0,
            "deduplicated_evidence_count": 0,
            "duplicate_removed_count": 0,
            "source_quality_summary": {},
            "low_confidence_count": 0,
            "competitor_coverage": {},
            "missing_competitors": [],
            "fallback_by_competitor": {},
            "raw_search_result_count_by_competitor": {},
            "relevant_evidence_count_by_competitor": {},
            "unrelated_evidence_count_by_competitor": {},
            "filtered_unrelated_count": 0,
            "missing_relevant_evidence_competitors": [],
            "fallback_used": False,
            "fallback_reason": None,
            "elapsed_time_ms": 0,
        }

    def _collect_web(self, input_data: CollectorInput, diagnostics: dict) -> CollectorOutput | None:
        task = input_data.task
        evidence: list[Evidence] = []
        raw_count = 0
        fallback_reason: str | None = None
        total_elapsed = 0
        seen_keys: set[tuple[str, str]] = set()
        buckets: dict[str, list[Evidence]] = {competitor: [] for competitor in task.competitors}
        query_count_by_competitor: dict[str, int] = {competitor: 0 for competitor in task.competitors}
        planner_hint_query_count_by_competitor: dict[str, int] = {competitor: 0 for competitor in task.competitors}
        default_query_count_by_competitor: dict[str, int] = {competitor: 0 for competitor in task.competitors}
        effective_query_count_by_competitor: dict[str, int] = {competitor: 0 for competitor in task.competitors}
        effective_queries_preview_by_competitor: dict[str, list[str]] = {competitor: [] for competitor in task.competitors}
        skipped_queries_by_competitor: dict[str, list[dict[str, str | None]]] = {competitor: [] for competitor in task.competitors}
        query_dimensions_by_competitor: dict[str, list[dict[str, str | None]]] = {competitor: [] for competitor in task.competitors}
        fallback_by_competitor: dict[str, str | None] = {competitor: None for competitor in task.competitors}
        raw_search_result_count_by_competitor: dict[str, int] = {competitor: 0 for competitor in task.competitors}
        unrelated_evidence_count_by_competitor: dict[str, int] = {competitor: 0 for competitor in task.competitors}
        dimension_ids_by_competitor = {
            competitor: self._dimension_ids_for_competitor(input_data.collector_search_plan, competitor)
            for competitor in task.competitors
        }
        max_evidence_by_competitor = {
            competitor: max(MAX_EVIDENCE_PER_COMPETITOR, len(dimensions) * MAX_EVIDENCE_PER_DIMENSION)
            for competitor, dimensions in dimension_ids_by_competitor.items()
        }
        evidence_count_by_dimension_by_competitor: dict[str, dict[str, int]] = {
            competitor: {dimension: 0 for dimension in dimensions}
            for competitor, dimensions in dimension_ids_by_competitor.items()
        }
        filtered_unrelated_count = 0

        for competitor in task.competitors:
            aliases = input_data.competitor_aliases.get(competitor, [])
            query_plan = self._query_plan_for_competitor(
                competitor,
                task.industry,
                input_data.planner_query_hints,
                input_data.collector_search_plan,
                input_data.gate_context,
                aliases,
            )
            planner_hint_query_count_by_competitor[competitor] = len(query_plan["planner_queries"])
            diagnostics["planned_query_count_by_competitor"][competitor] = len(query_plan["planned_queries"])
            diagnostics["alias_query_count_by_competitor"][competitor] = len(query_plan["alias_queries"])
            diagnostics["alias_queries_preview_by_competitor"][competitor] = query_plan["alias_queries"][:9]
            diagnostics["targeted_query_count_by_competitor"][competitor] = len(query_plan["targeted_queries"])
            default_query_count_by_competitor[competitor] = len(query_plan["default_queries"])
            effective_query_count_by_competitor[competitor] = len(query_plan["effective_queries"])
            effective_queries_preview_by_competitor[competitor] = query_plan["effective_queries"]
            diagnostics["targeted_queries_preview_by_competitor"][competitor] = query_plan["targeted_queries"][:9]
            all_candidate_queries = query_plan.get("all_candidate_queries", query_plan["effective_queries"])
            for skipped_query in all_candidate_queries[len(query_plan["effective_queries"]):]:
                skipped_queries_by_competitor[competitor].append(
                    {
                        "query": skipped_query,
                        "dimension_id": query_plan["query_metadata_by_query"].get(skipped_query, {}).get("dimension_id")
                        or dimension_for_query(skipped_query),
                        "reason": "exceeded_max_query_count_per_competitor",
                    }
                )
            for query_index, query in enumerate(query_plan["effective_queries"]):
                if len(buckets[competitor]) >= max_evidence_by_competitor[competitor]:
                    for skipped_query in query_plan["effective_queries"][query_index:]:
                        skipped_queries_by_competitor[competitor].append(
                            {
                                "query": skipped_query,
                                "dimension_id": query_plan["query_metadata_by_query"].get(skipped_query, {}).get("dimension_id")
                                or dimension_for_query(skipped_query),
                                "reason": "max_evidence_per_competitor_reached",
                            }
                        )
                    break
                query_metadata = query_plan["query_metadata_by_query"].get(query, {})
                query_dimension = query_metadata.get("dimension_id") or dimension_for_query(query)
                query_dimensions_by_competitor[competitor].append(
                    {
                        "query": query,
                        "dimension_id": query_dimension,
                        "intent": query_metadata.get("intent"),
                        "source": query_metadata.get("source"),
                    }
                )
                query_count_by_competitor[competitor] += 1
                response = self.web_search_client.search(query, limit=8)
                diagnostics["web_search_attempted"] = diagnostics["web_search_attempted"] or response.attempted
                total_elapsed += response.elapsed_time_ms
                if not response.available:
                    fallback_reason = response.fallback_reason or response.error_message or "Web search unavailable."
                    fallback_by_competitor[competitor] = fallback_reason
                    break

                added_for_query = 0
                for result in response.results:
                    raw_count += 1
                    raw_search_result_count_by_competitor[competitor] += 1
                    normalized_url = self.normalize_url(result.url)
                    dedupe_key = (competitor, normalized_url)
                    if dedupe_key in seen_keys:
                        continue
                    seen_keys.add(dedupe_key)
                    quality = self._source_quality(normalized_url, result.title, result.snippet, task.competitors)
                    confidence = self._confidence_for_result(normalized_url, result.snippet, quality, result.score)
                    confidence_breakdown = self._confidence_breakdown(quality, result.score)
                    candidate = apply_relevance(
                        Evidence(
                            competitor=competitor,
                            source_type="public_web",
                            url=normalized_url,
                            source_domain=self.extract_source_domain(normalized_url),
                            source_quality=quality,
                            snippet=self._snippet(result.title, result.snippet),
                            confidence=confidence,
                        ),
                        competitor,
                        title=result.title,
                        aliases=aliases,
                    )
                    signals = dict(candidate.entity_match_signals or {})
                    signals["collector_query"] = query
                    if query_dimension:
                        signals["collector_dimension"] = query_dimension
                    if query_metadata:
                        signals["collector_query_intent"] = query_metadata.get("intent")
                        signals["collector_query_source"] = query_metadata.get("source")
                        signals["collector_preferred_sources"] = query_metadata.get("preferred_sources", [])
                    signals["confidence_breakdown"] = confidence_breakdown
                    candidate = candidate.model_copy(update={"entity_match_signals": signals})
                    if candidate.relevance_level == "unrelated":
                        unrelated_evidence_count_by_competitor[competitor] += 1
                        filtered_unrelated_count += 1
                    elif query_dimension in evidence_count_by_dimension_by_competitor[competitor]:
                        if evidence_count_by_dimension_by_competitor[competitor][query_dimension] >= MAX_EVIDENCE_PER_DIMENSION:
                            continue
                        evidence_count_by_dimension_by_competitor[competitor][query_dimension] += 1
                    buckets[competitor].append(candidate)
                    added_for_query += 1
                    if added_for_query >= MAX_EVIDENCE_PER_QUERY or len(buckets[competitor]) >= max_evidence_by_competitor[competitor]:
                        break
                if fallback_reason:
                    break
            if fallback_reason:
                break

        for competitor in task.competitors:
            evidence.extend(buckets[competitor])

        evidence_count_by_competitor = {competitor: len(buckets[competitor]) for competitor in task.competitors}
        relevant_evidence_count_by_competitor = {
            competitor: sum(1 for item in buckets[competitor] if item.relevance_level in {"high", "medium"})
            for competitor in task.competitors
        }
        missing_competitors = [
            competitor for competitor, count in evidence_count_by_competitor.items() if count < MIN_EVIDENCE_PER_COMPETITOR
        ]
        missing_relevant = [
            competitor for competitor, count in relevant_evidence_count_by_competitor.items() if count < MIN_RELEVANT_EVIDENCE_PER_COMPETITOR
        ]
        diagnostics.update(
            {
                "collector_mode_used": "web",
                "planner_query_hints_used": bool(input_data.planner_query_hints),
                "collector_search_plan_used": bool(input_data.collector_search_plan),
                "targeted_recollection_used": any(
                    diagnostics["targeted_query_count_by_competitor"].get(competitor, 0) > 0 for competitor in task.competitors
                ),
                "category_scope_hints_used": bool(input_data.planner_query_hints.get("category_scope")),
                "planner_hint_query_count_by_competitor": planner_hint_query_count_by_competitor,
                "planned_query_count_by_competitor": diagnostics["planned_query_count_by_competitor"],
                "targeted_query_count_by_competitor": diagnostics["targeted_query_count_by_competitor"],
                "default_query_count_by_competitor": default_query_count_by_competitor,
                "effective_query_count_by_competitor": effective_query_count_by_competitor,
                "effective_queries_preview_by_competitor": effective_queries_preview_by_competitor,
                "targeted_queries_preview_by_competitor": diagnostics["targeted_queries_preview_by_competitor"],
                "skipped_queries_by_competitor": skipped_queries_by_competitor,
                "query_policy": self._dedupe_queries(
                    [
                        policy
                        for competitor in task.competitors
                        for policy in self._query_plan_for_competitor(
                            competitor,
                            task.industry,
                            input_data.planner_query_hints,
                            input_data.collector_search_plan,
                            input_data.gate_context,
                            input_data.competitor_aliases.get(competitor, []),
                        )["query_policy"]
                    ]
                ),
                "query_dimensions_by_competitor": query_dimensions_by_competitor,
                "query_count": sum(query_count_by_competitor.values()),
                "query_count_by_competitor": query_count_by_competitor,
                "evidence_count": len(evidence),
                "evidence_count_by_competitor": evidence_count_by_competitor,
                "evidence_count_by_dimension_by_competitor": evidence_count_by_dimension_by_competitor,
                "raw_evidence_count": raw_count,
                "deduplicated_evidence_count": len(evidence),
                "duplicate_removed_count": raw_count - len(evidence),
                "source_quality_summary": self._quality_summary(evidence),
                "low_confidence_count": sum(1 for item in evidence if item.confidence < 0.5),
                "competitor_coverage": evidence_count_by_competitor,
                "missing_competitors": missing_competitors,
                "fallback_by_competitor": fallback_by_competitor,
                "raw_search_result_count_by_competitor": raw_search_result_count_by_competitor,
                "relevant_evidence_count_by_competitor": relevant_evidence_count_by_competitor,
                "unrelated_evidence_count_by_competitor": unrelated_evidence_count_by_competitor,
                "filtered_unrelated_count": filtered_unrelated_count,
                "missing_relevant_evidence_competitors": missing_relevant,
                "elapsed_time_ms": total_elapsed,
            }
        )

        if fallback_reason or raw_count == 0:
            diagnostics.update(
                {
                    "collector_mode_used": "mock",
                    "web_search_success": False,
                    "fallback_used": True,
                    "fallback_reason": fallback_reason or "Web search returned no public results.",
                }
            )
            return None

        diagnostics.update(
            {
                "web_search_success": True,
                "fallback_used": False,
                "fallback_reason": None,
            }
        )
        return CollectorOutput(evidence=evidence, diagnostics=diagnostics)

    def _mock_output(self, input_data: CollectorInput, diagnostics: dict | None = None) -> CollectorOutput:
        task = input_data.task
        diagnostics = diagnostics or self._base_diagnostics(input_data)
        evidence: list[Evidence] = []
        query_plans = {
            competitor: self._query_plan_for_competitor(
                competitor,
                task.industry,
                input_data.planner_query_hints,
                input_data.collector_search_plan,
                input_data.gate_context,
                input_data.competitor_aliases.get(competitor, []),
            )
            for competitor in task.competitors
        }
        aliases_by_competitor = input_data.competitor_aliases
        for competitor in task.competitors:
            aliases = aliases_by_competitor.get(competitor, [])
            evidence.extend(
                [
                    apply_relevance(
                        Evidence(
                            competitor=competitor,
                            source_type="web",
                            url=f"https://example.com/{competitor.lower()}-product",
                            source_domain="example.com",
                            source_quality="unknown",
                            snippet=f"{competitor} public product page mentions {task.industry} features, collaboration workflow, and target users.",
                            confidence=0.78,
                        ),
                        competitor,
                        title=f"{competitor} product",
                        aliases=aliases,
                    ),
                    apply_relevance(
                        Evidence(
                            competitor=competitor,
                            source_type="pricing_page",
                            url=f"https://example.com/{competitor.lower()}-pricing",
                            source_domain="example.com",
                            source_quality="official",
                            snippet=f"{competitor} pricing page mentions paid plans, enterprise options, subscriptions, and product capabilities.",
                            confidence=0.86,
                        ),
                        competitor,
                        title=f"{competitor} pricing",
                        aliases=aliases,
                    ),
                ]
            )
        evidence_count_by_competitor = {competitor: 2 for competitor in task.competitors}
        diagnostics.update(
            {
                "collector_mode_used": "mock",
                "planner_query_hints_used": bool(input_data.planner_query_hints),
                "collector_search_plan_used": bool(input_data.collector_search_plan),
                "entity_aliases_used": bool(input_data.competitor_aliases),
                "competitor_aliases_by_competitor": input_data.competitor_aliases,
                "alias_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["alias_queries"]) for competitor in task.competitors
                },
                "alias_queries_preview_by_competitor": {
                    competitor: query_plans[competitor]["alias_queries"][:9] for competitor in task.competitors
                },
                "targeted_recollection_used": any(len(query_plans[competitor]["targeted_queries"]) > 0 for competitor in task.competitors),
                "category_scope_hints_used": bool(input_data.planner_query_hints.get("category_scope")),
                "planner_hint_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["planner_queries"]) for competitor in task.competitors
                },
                "planned_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["planned_queries"]) for competitor in task.competitors
                },
                "targeted_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["targeted_queries"]) for competitor in task.competitors
                },
                "default_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["default_queries"]) for competitor in task.competitors
                },
                "effective_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["effective_queries"]) for competitor in task.competitors
                },
                "effective_queries_preview_by_competitor": {
                    competitor: query_plans[competitor]["effective_queries"] for competitor in task.competitors
                },
                "targeted_queries_preview_by_competitor": {
                    competitor: query_plans[competitor]["targeted_queries"][:9] for competitor in task.competitors
                },
                "skipped_queries_by_competitor": {
                    competitor: [
                        {
                            "query": query,
                            "dimension_id": query_plans[competitor]["query_metadata_by_query"].get(query, {}).get("dimension_id")
                            or dimension_for_query(query),
                            "reason": "exceeded_max_query_count_per_competitor",
                        }
                        for query in query_plans[competitor].get("all_candidate_queries", [])[len(query_plans[competitor]["effective_queries"]):]
                    ]
                    for competitor in task.competitors
                },
                "query_policy": self._dedupe_queries(
                    [policy for competitor in task.competitors for policy in query_plans[competitor]["query_policy"]]
                ),
                "query_dimensions_by_competitor": {
                    competitor: [
                        {
                            "query": query,
                            "dimension_id": query_plans[competitor]["query_metadata_by_query"].get(query, {}).get("dimension_id")
                            or dimension_for_query(query),
                            "intent": query_plans[competitor]["query_metadata_by_query"].get(query, {}).get("intent"),
                            "source": query_plans[competitor]["query_metadata_by_query"].get(query, {}).get("source"),
                        }
                        for query in query_plans[competitor]["effective_queries"][:9]
                    ]
                    for competitor in task.competitors
                },
                "evidence_count": len(evidence),
                "evidence_count_by_competitor": evidence_count_by_competitor,
                "evidence_count_by_dimension_by_competitor": {
                    competitor: {dimension: 0 for dimension in self._dimension_ids_for_competitor(input_data.collector_search_plan, competitor)}
                    for competitor in task.competitors
                },
                "raw_evidence_count": len(evidence),
                "deduplicated_evidence_count": len(evidence),
                "duplicate_removed_count": 0,
                "source_quality_summary": self._quality_summary(evidence),
                "low_confidence_count": sum(1 for item in evidence if item.confidence < 0.5),
                "competitor_coverage": evidence_count_by_competitor,
                "missing_competitors": [],
                "fallback_by_competitor": {competitor: None for competitor in task.competitors},
                "raw_search_result_count_by_competitor": evidence_count_by_competitor,
                "relevant_evidence_count_by_competitor": evidence_count_by_competitor,
                "unrelated_evidence_count_by_competitor": {competitor: 0 for competitor in task.competitors},
                "filtered_unrelated_count": 0,
                "missing_relevant_evidence_competitors": [],
            }
        )
        return CollectorOutput(evidence=evidence, diagnostics=diagnostics)

    @staticmethod
    def _default_queries_for_competitor(competitor: str, industry: str) -> list[str]:
        has_chinese = any("\u4e00" <= char <= "\u9fff" for char in competitor)
        if has_chinese:
            return [
                f"{competitor} 官网 功能 定价 企业版",
                f"{competitor} 产品介绍 协作 办公",
                f"{competitor} pricing features official",
                f"{competitor} strengths weaknesses opportunities threats review",
                f"{competitor} official pricing features product",
            ]
        return [
            f"{competitor} official pricing features product",
            f"{competitor} {industry} pricing features official",
            f"{competitor} product documentation pricing",
            f"{competitor} strengths weaknesses opportunities threats review",
        ]

    @classmethod
    def _query_plan_for_competitor(
        cls,
        competitor: str,
        industry: str,
        planner_query_hints: dict[str, list[str]] | None,
        collector_search_plan: dict | None,
        gate_context: dict | None,
        aliases: list[str] | None = None,
    ) -> dict:
        hints = planner_query_hints or {}
        competitor_hints = cls._normalize_queries(hints.get(competitor, []))
        category_scope = [
            cls._combine_competitor_with_category_hint(competitor, hint)
            for hint in cls._normalize_queries(hints.get("category_scope", []))
        ]
        targeted_queries = cls._targeted_queries_for_competitor(competitor, gate_context)
        alias_queries = cls._alias_queries_for_competitor(competitor, industry, aliases or [])
        planner_queries = cls._dedupe_queries([*competitor_hints, *category_scope])
        planned_queries, query_metadata_by_query = cls._planned_queries_for_competitor(competitor, collector_search_plan)
        default_queries = fixed_query_hints_for_competitor(competitor, industry)
        query_policy = ["targeted_recollection_queries"]
        if planned_queries:
            query_policy.append("planner_collector_search_plan")
            base_queries = planned_queries
        elif planner_queries:
            query_policy.append("planner_query_hints")
            base_queries = planner_queries
        else:
            query_policy.append("fixed_competitor_dimension_queries")
            base_queries = default_queries
        ordered_base_queries = cls._round_robin_queries_by_dimension(base_queries, query_metadata_by_query)
        all_candidate_queries = cls._dedupe_queries([*targeted_queries, *ordered_base_queries])
        effective_queries = all_candidate_queries[:MAX_QUERY_COUNT_PER_COMPETITOR]
        for query in targeted_queries:
            query_metadata_by_query.setdefault(
                query,
                {
                    "source": "targeted_recollection",
                    "dimension_id": dimension_for_query(query),
                    "intent": "Re-collect evidence for the failed QA/EvidenceGate focus.",
                    "preferred_sources": ["official", "documentation", "media", "review"],
                },
            )
        return {
            "targeted_queries": targeted_queries,
            "alias_queries": alias_queries,
            "planner_queries": planner_queries,
            "planned_queries": planned_queries,
            "default_queries": default_queries,
            "effective_queries": effective_queries,
            "all_candidate_queries": all_candidate_queries,
            "query_policy": query_policy,
            "query_metadata_by_query": query_metadata_by_query,
        }

    @classmethod
    def _round_robin_queries_by_dimension(cls, queries: list[str], query_metadata_by_query: dict[str, dict]) -> list[str]:
        by_dimension: dict[str, list[str]] = {}
        dimension_order: list[str] = []
        for query in queries:
            metadata = query_metadata_by_query.get(query, {})
            dimension_id = metadata.get("dimension_id") or dimension_for_query(query) or "unknown"
            if dimension_id not in by_dimension:
                by_dimension[dimension_id] = []
                dimension_order.append(dimension_id)
            by_dimension[dimension_id].append(query)

        ordered: list[str] = []
        max_depth = max((len(items) for items in by_dimension.values()), default=0)
        for index in range(max_depth):
            for dimension_id in dimension_order:
                dimension_queries = by_dimension[dimension_id]
                if index < len(dimension_queries):
                    ordered.append(dimension_queries[index])
        return cls._dedupe_queries(ordered)

    @classmethod
    def _planned_queries_for_competitor(cls, competitor: str, collector_search_plan: dict | None) -> tuple[list[str], dict[str, dict]]:
        if not isinstance(collector_search_plan, dict):
            return [], {}
        by_dimension = collector_search_plan.get(competitor)
        if not isinstance(by_dimension, dict):
            return [], {}
        queries: list[str] = []
        query_metadata_by_query: dict[str, dict] = {}
        for dimension_id, item in by_dimension.items():
            if not isinstance(item, dict):
                continue
            item_queries = cls._normalize_queries(item.get("queries", []))
            for query in item_queries:
                queries.append(query)
                query_metadata_by_query[query] = {
                    "source": "planner_search_plan",
                    "dimension_id": item.get("dimension_id") or dimension_id,
                    "intent": item.get("intent"),
                    "preferred_sources": item.get("preferred_sources", []),
                    "query_strategy": item.get("query_strategy"),
                }
        return cls._dedupe_queries(queries), query_metadata_by_query

    @staticmethod
    def _dimension_ids_for_competitor(collector_search_plan: dict | None, competitor: str) -> list[str]:
        if isinstance(collector_search_plan, dict):
            by_dimension = collector_search_plan.get(competitor)
            if isinstance(by_dimension, dict) and by_dimension:
                return list(by_dimension.keys())
        return list(FIXED_COMPETITIVE_DIMENSIONS)

    @classmethod
    def _alias_queries_for_competitor(cls, competitor: str, industry: str, aliases: list[str]) -> list[str]:
        queries: list[str] = []
        competitor_key = competitor.replace(" ", "").lower()
        for alias in aliases[:4]:
            alias_key = alias.replace(" ", "").lower()
            if not alias_key or alias_key == competitor_key:
                continue
            queries.extend(
                [
                    f"{alias} official {industry}",
                    f"{alias} features specs official",
                    f"{alias} pricing price",
                ]
            )
        return cls._dedupe_queries(queries)

    @classmethod
    def _targeted_queries_for_competitor(cls, competitor: str, gate_context: dict | None) -> list[str]:
        context = gate_context or {}
        targeted = context.get("targeted_recollection") or {}
        competitor_targets = []
        if isinstance(targeted.get("by_competitor"), dict):
            competitor_targets = targeted["by_competitor"].get(competitor, []) or []
        if not competitor_targets and isinstance(context.get("rework_context"), dict):
            rework_context = context["rework_context"]
            if rework_context.get("related_competitor") == competitor:
                competitor_targets = [rework_context]

        queries: list[str] = []
        for item in competitor_targets:
            metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
            query_focus = metadata.get("query_focus", []) if isinstance(metadata, dict) else []
            fix_type = metadata.get("fix_type") if isinstance(metadata, dict) else None
            quadrant = metadata.get("quadrant") if isinstance(metadata, dict) else None
            focus_dimensions = metadata.get("focus_dimensions", []) if isinstance(metadata, dict) else []
            for focus in query_focus[:4]:
                queries.append(f"{competitor} {focus}")
            for dimension in focus_dimensions[:2]:
                queries.append(f"{competitor} {dimension} official")
            if fix_type == "collect_more_evidence":
                queries.append(f"{competitor} official product pricing features")
            if quadrant == "weaknesses":
                queries.append(f"{competitor} reviews complaints pain points")
            elif quadrant == "opportunities":
                queries.append(f"{competitor} feature gaps improvement opportunities")
            elif quadrant == "threats":
                queries.append(f"{competitor} alternatives competitive pressure")
            elif quadrant == "strengths":
                queries.append(f"{competitor} official differentiators product")
        return cls._dedupe_queries(queries)

    @staticmethod
    def _normalize_queries(values: list[str]) -> list[str]:
        return [str(value).strip() for value in values if isinstance(value, str) and value.strip()]

    @staticmethod
    def _combine_competitor_with_category_hint(competitor: str, hint: str) -> str:
        normalized_hint = hint.strip()
        if not normalized_hint:
            return ""
        if competitor.lower() in normalized_hint.lower():
            return normalized_hint
        return f"{competitor} {normalized_hint}"

    @staticmethod
    def _dedupe_queries(queries: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for query in queries:
            normalized = " ".join(query.split()).strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(normalized)
        return output

    @staticmethod
    def _snippet(title: str, snippet: str) -> str:
        text = f"{title}: {snippet}" if title and title not in snippet else snippet
        return text[:500]

    @staticmethod
    def _confidence_for_result(url: str, snippet: str, quality: str, score: float | None = None) -> float:
        base = QUALITY_CONFIDENCE[quality]
        if score is None or quality in {"official", "documentation", "low_quality"}:
            return base
        score_confidence = max(0.6, min(0.9, 0.6 + float(score) * 0.3))
        return round((base * 0.7) + (score_confidence * 0.3), 2)

    @staticmethod
    def _confidence_breakdown(quality: str, score: float | None = None) -> dict:
        base = QUALITY_CONFIDENCE[quality]
        if score is None or quality in {"official", "documentation", "low_quality"}:
            return {
                "source_quality": quality,
                "source_quality_base": base,
                "search_score": score,
                "search_score_confidence": None,
                "formula": "source_quality_base",
                "final_confidence": base,
                "reason": "Official/documentation/low_quality sources use the source-quality confidence directly.",
            }
        score_confidence = round(max(0.6, min(0.9, 0.6 + float(score) * 0.3)), 2)
        final_confidence = round((base * 0.7) + (score_confidence * 0.3), 2)
        return {
            "source_quality": quality,
            "source_quality_base": base,
            "search_score": score,
            "search_score_confidence": score_confidence,
            "formula": "source_quality_base * 0.7 + search_score_confidence * 0.3",
            "final_confidence": final_confidence,
            "reason": "Non-official sources blend source quality with search-provider score.",
        }

    @staticmethod
    def _source_quality(url: str, title: str, snippet: str, competitors: list[str]) -> str:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        text = f"{title} {snippet} {url}".lower()
        if not host or any(token in host for token in ("localhost", "spam", "click")):
            return "low_quality"
        if any(token in text for token in ("review", "compare", "alternative", "alternatives")):
            return "review"
        if any(token in host for token in ("techcrunch.", "theverge.", "36kr.", "infoq.", "gartner.", "forrester.", "wikipedia.org")):
            return "media"
        if any(token in path for token in ("docs", "document", "developer", "help", "support")) or any(
            token in host for token in ("docs.", "developer.", "help.", "support.")
        ):
            return "documentation"
        competitor_tokens = [
            self_token
            for competitor in competitors
            for self_token in [competitor.lower().replace(" ", "")]
            if self_token
        ]
        competitor_tokens.extend(CollectorAgent._known_domain_aliases(competitors))
        host_competitor_match = any(token in host.replace("-", "").replace(".", "") for token in competitor_tokens)
        title_or_snippet_competitor_match = any(token in f"{title} {snippet}".lower().replace(" ", "") for token in competitor_tokens)
        path_competitor_match = any(token in path.replace("-", "").replace("_", "") for token in competitor_tokens)
        if host_competitor_match or (
            title_or_snippet_competitor_match and path_competitor_match and any(token in path for token in ("official", "pricing", "product", "features"))
        ):
            return "official"
        if len(snippet.strip()) < 30:
            return "low_quality"
        return "unknown"

    @staticmethod
    def normalize_url(url: str) -> str:
        parsed = urlparse(url.strip())
        query = urlencode([(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key not in TRACKING_PARAMS])
        path = parsed.path.rstrip("/") or parsed.path
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))

    @staticmethod
    def extract_source_domain(url: str) -> str:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        parts = host.split(".")
        if len(parts) >= 3 and parts[-2] in {"com", "com.cn", "co"}:
            return ".".join(parts[-3:])
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return host or "unknown"

    @staticmethod
    def _known_domain_aliases(competitors: list[str]) -> list[str]:
        aliases = {
            "飞书": ["feishu", "lark"],
            "钉钉": ["dingtalk"],
            "企业微信": ["wecom", "wechatwork"],
        }
        output: list[str] = []
        for competitor in competitors:
            output.extend(aliases.get(competitor, []))
        return output

    @staticmethod
    def _quality_summary(evidence: list[Evidence]) -> dict:
        summary: dict[str, int] = {}
        for item in evidence:
            summary[item.source_quality] = summary.get(item.source_quality, 0) + 1
        return summary
