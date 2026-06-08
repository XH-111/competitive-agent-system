from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.agents.base import AgentExecutionError, run_with_trace
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
            collector_search_plan = self._collector_search_plan(input_data)
            has_search_plan = (
                self._has_incremental_search_plan(collector_search_plan)
                if self._collection_mode(input_data) == "incremental"
                else self._has_partial_search_plan(collector_search_plan)
                if input_data.partial_collection_plan_allowed
                else self._has_complete_search_plan(task.competitors, collector_search_plan)
            )
            if not has_search_plan:
                diagnostics.update(
                    {
                        "collection_plan_used": False,
                        "collector_search_plan_missing": True,
                        "fallback_used": False,
                        "fallback_reason": "collector_search_plan_missing",
                    }
                )
                raise AgentExecutionError(
                    "collector_search_plan_missing",
                    output={"diagnostics": diagnostics},
                )
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
        collection_mode = self._collection_mode(input_data)
        attempt_no = self._collector_attempt_no(input_data)
        return {
            "collector_mode_requested": input_data.collector_mode,
            "collector_mode_used": "mock",
            "collector_collection_mode": collection_mode,
            "collector_attempt_no": attempt_no,
            "incremental_collection_used": collection_mode == "incremental",
            "incremental_target_count": (
                len(input_data.incremental_collection_plan.targets)
                if input_data.incremental_collection_plan
                else 0
            ),
            "incremental_targets": self._incremental_targets_summary(input_data),
            "skip_evidence_ids": (
                input_data.incremental_collection_plan.skip_evidence_ids
                if input_data.incremental_collection_plan
                else []
            ),
            "collector_config_used": input_data.collector_config.model_dump(mode="json") if input_data.collector_config else None,
            "search_provider": self.web_search_client.provider,
            "search_base_url_configured": bool(self.web_search_client.base_url),
            "has_search_api_key": bool(self.web_search_client.api_key),
            "web_search_attempted": False,
            "web_search_success": False,
            "collection_plan_used": bool(self._collector_search_plan(input_data)),
            "collector_search_plan_source": "planner_collection_plan",
            "collector_search_plan_missing": not bool(self._collector_search_plan(input_data)),
            "collector_search_plan_used": bool(self._collector_search_plan(input_data)),
            "partial_collection_plan_allowed": input_data.partial_collection_plan_allowed,
            "entity_aliases_used": bool(input_data.competitor_aliases),
            "competitor_aliases_by_competitor": input_data.competitor_aliases,
            "alias_query_count_by_competitor": {},
            "alias_queries_preview_by_competitor": {},
            "targeted_recollection_used": False,
            "planned_query_count_by_competitor": {},
            "effective_query_count_by_competitor": {},
            "effective_queries_preview_by_competitor": {},
            "targeted_queries_preview_by_competitor": {},
            "skipped_queries_by_competitor": {},
            "query_policy": [],
            "query_dimensions_by_competitor": {},
            "query_count": 0,
            "query_count_by_competitor": {},
            "query_count_by_dimension": {},
            "failed_queries": [],
            "evidence_count": 0,
            "evidence_count_by_competitor": {},
            "evidence_count_by_dimension": {},
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

    @staticmethod
    def _collector_search_plan(input_data: CollectorInput) -> dict:
        if input_data.incremental_collection_plan is not None:
            return CollectorAgent._incremental_search_plan(input_data)
        if input_data.collection_plan is None:
            return {}
        return {
            competitor: {
                dimension_id: item.model_dump(mode="json")
                for dimension_id, item in dimensions.items()
            }
            for competitor, dimensions in input_data.collection_plan.collector_search_plan.items()
        }

    @staticmethod
    def _incremental_search_plan(input_data: CollectorInput) -> dict:
        plan = input_data.incremental_collection_plan
        if plan is None:
            return {}
        output: dict[str, dict[str, dict]] = {}
        for target in plan.targets:
            output.setdefault(target.competitor, {})[target.dimension_id] = {
                "dimension_id": target.dimension_id,
                "label": target.dimension_id,
                "queries": target.queries,
                "research_goals": [target.reason],
                "source": "planner_incremental_collection_plan",
            }
        return output

    @staticmethod
    def _has_complete_search_plan(competitors: list[str], collector_search_plan: dict) -> bool:
        if not collector_search_plan:
            return False
        for competitor in competitors:
            dimensions = collector_search_plan.get(competitor)
            if not isinstance(dimensions, dict) or not dimensions:
                return False
            if any(
                not isinstance(item, dict) or not item.get("queries")
                for item in dimensions.values()
            ):
                return False
        return True

    @staticmethod
    def _has_incremental_search_plan(collector_search_plan: dict) -> bool:
        if not collector_search_plan:
            return False
        for dimensions in collector_search_plan.values():
            if not isinstance(dimensions, dict) or not dimensions:
                return False
            if any(
                not isinstance(item, dict) or not item.get("queries")
                for item in dimensions.values()
            ):
                return False
        return True

    @staticmethod
    def _has_partial_search_plan(collector_search_plan: dict) -> bool:
        return CollectorAgent._has_incremental_search_plan(collector_search_plan)

    def _collect_web(self, input_data: CollectorInput, diagnostics: dict) -> CollectorOutput | None:
        task = input_data.task
        collector_search_plan = self._collector_search_plan(input_data)
        collection_competitors = self._collection_competitors(input_data, collector_search_plan)
        target_dimensions = self._target_dimensions_by_competitor(input_data, collector_search_plan)
        evidence: list[Evidence] = []
        raw_count = 0
        fallback_reason: str | None = None
        total_elapsed = 0
        seen_keys: set[tuple[str, str]] = set()
        buckets: dict[str, list[Evidence]] = {competitor: [] for competitor in collection_competitors}
        query_count_by_competitor: dict[str, int] = {competitor: 0 for competitor in collection_competitors}
        effective_query_count_by_competitor: dict[str, int] = {competitor: 0 for competitor in collection_competitors}
        effective_queries_preview_by_competitor: dict[str, list[str]] = {competitor: [] for competitor in collection_competitors}
        skipped_queries_by_competitor: dict[str, list[dict[str, str | None]]] = {competitor: [] for competitor in collection_competitors}
        query_dimensions_by_competitor: dict[str, list[dict[str, str | None]]] = {competitor: [] for competitor in collection_competitors}
        fallback_by_competitor: dict[str, str | None] = {competitor: None for competitor in collection_competitors}
        raw_search_result_count_by_competitor: dict[str, int] = {competitor: 0 for competitor in collection_competitors}
        unrelated_evidence_count_by_competitor: dict[str, int] = {competitor: 0 for competitor in collection_competitors}
        dimension_ids_by_competitor = {
            competitor: self._dimension_ids_for_competitor(collector_search_plan, competitor)
            for competitor in collection_competitors
        }
        query_count_by_dimension: dict[str, int] = {
            dimension_id: 0
            for dimensions in dimension_ids_by_competitor.values()
            for dimension_id in dimensions
        }
        failed_queries: list[str] = []
        evidence_count_by_dimension_by_competitor: dict[str, dict[str, int]] = {
            competitor: {dimension: 0 for dimension in dimensions}
            for competitor, dimensions in dimension_ids_by_competitor.items()
        }
        filtered_unrelated_count = 0

        for competitor in collection_competitors:
            aliases = input_data.competitor_aliases.get(competitor, [])
            query_plan = self._query_plan_for_competitor(
                competitor,
                collector_search_plan,
            )
            diagnostics["planned_query_count_by_competitor"][competitor] = len(query_plan["planned_queries"])
            effective_query_count_by_competitor[competitor] = len(query_plan["effective_queries"])
            effective_queries_preview_by_competitor[competitor] = query_plan["effective_queries"]
            all_candidate_queries = query_plan.get("all_candidate_queries", query_plan["effective_queries"])
            for skipped_query in all_candidate_queries[len(query_plan["effective_queries"]):]:
                skipped_queries_by_competitor[competitor].append(
                    {
                        "query": skipped_query,
                        "dimension_id": query_plan["query_metadata_by_query"].get(skipped_query, {}).get("dimension_id")
                        or "unknown",
                        "reason": "exceeded_max_query_count_per_competitor",
                    }
                )
            for query_index, query in enumerate(query_plan["effective_queries"]):
                query_metadata = query_plan["query_metadata_by_query"].get(query, {})
                query_dimension = query_metadata.get("dimension_id") or "unknown"
                dimension_config = self._dimension_config(input_data, competitor, query_dimension)
                if (
                    query_dimension in evidence_count_by_dimension_by_competitor[competitor]
                    and evidence_count_by_dimension_by_competitor[competitor][query_dimension]
                    >= dimension_config["max_evidence_per_dimension"]
                ):
                    skipped_queries_by_competitor[competitor].append(
                        {
                            "query": query,
                            "dimension_id": query_dimension,
                            "reason": "dimension_relevant_evidence_quota_reached",
                        }
                    )
                    continue
                query_dimensions_by_competitor[competitor].append(
                    {
                        "query": query,
                        "dimension_id": query_dimension,
                        "intent": query_metadata.get("intent"),
                        "source": query_metadata.get("source"),
                    }
                )
                query_count_by_competitor[competitor] += 1
                query_count_by_dimension[query_dimension] = query_count_by_dimension.get(query_dimension, 0) + 1
                response = self.web_search_client.search(query, limit=dimension_config["max_results_per_query"])
                diagnostics["web_search_attempted"] = diagnostics["web_search_attempted"] or response.attempted
                total_elapsed += response.elapsed_time_ms
                if not response.available:
                    failed_queries.append(query)
                    fallback_reason = response.fallback_reason or response.error_message or "Web search unavailable."
                    fallback_by_competitor[competitor] = fallback_reason
                    break

                added_for_query = 0
                for result in response.results:
                    raw_count += 1
                    raw_search_result_count_by_competitor[competitor] += 1
                    normalized_url = self.normalize_url(result.url)
                    if not self._domain_allowed(normalized_url, dimension_config):
                        continue
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
                            page_title=result.title or None,
                        ),
                        competitor,
                        title=result.title,
                        aliases=aliases,
                    )
                    signals = dict(candidate.entity_match_signals or {})
                    signals["collector_query"] = query
                    signals["collector_title"] = result.title
                    if query_dimension:
                        signals["collector_dimension"] = query_dimension
                        signals["planner_dimension"] = query_dimension
                    if query_metadata:
                        signals["collector_query_intent"] = query_metadata.get("intent")
                        signals["collector_query_source"] = query_metadata.get("source")
                        signals["collector_preferred_sources"] = query_metadata.get("preferred_sources", [])
                    self._apply_collection_attempt_signals(
                        signals,
                        input_data,
                        competitor=competitor,
                        dimension_id=query_dimension,
                    )
                    signals["confidence_breakdown"] = confidence_breakdown
                    candidate = candidate.model_copy(update={"entity_match_signals": signals})
                    if candidate.relevance_level == "unrelated":
                        unrelated_evidence_count_by_competitor[competitor] += 1
                        filtered_unrelated_count += 1
                    elif query_dimension in evidence_count_by_dimension_by_competitor[competitor]:
                        if evidence_count_by_dimension_by_competitor[competitor][query_dimension] >= dimension_config["max_evidence_per_dimension"]:
                            continue
                        evidence_count_by_dimension_by_competitor[competitor][query_dimension] += 1
                    buckets[competitor].append(candidate)
                    added_for_query += 1
                    if added_for_query >= dimension_config["max_evidence_per_query"]:
                        break
                if fallback_reason:
                    break
            if fallback_reason:
                break

        for competitor in collection_competitors:
            evidence.extend(buckets[competitor])

        evidence_count_by_competitor = {competitor: len(buckets[competitor]) for competitor in collection_competitors}
        relevant_evidence_count_by_competitor = {
            competitor: sum(1 for item in buckets[competitor] if item.relevance_level in {"high", "medium"})
            for competitor in collection_competitors
        }
        missing_competitors = [
            competitor for competitor, count in evidence_count_by_competitor.items() if count < MIN_EVIDENCE_PER_COMPETITOR
        ]
        missing_relevant = [
            competitor for competitor, count in relevant_evidence_count_by_competitor.items() if count < MIN_RELEVANT_EVIDENCE_PER_COMPETITOR
        ]
        evidence_count_by_dimension = {
            dimension_id: sum(
                counts.get(dimension_id, 0)
                for counts in evidence_count_by_dimension_by_competitor.values()
            )
            for dimension_id in query_count_by_dimension
        }
        diagnostics.update(
            {
                "collector_mode_used": "web",
                "collection_plan_used": True,
                "collector_search_plan_source": (
                    "planner_incremental_collection_plan"
                    if self._collection_mode(input_data) == "incremental"
                    else "planner_collection_plan"
                ),
                "collector_search_plan_missing": False,
                "collector_search_plan_used": True,
                "planned_query_count_by_competitor": diagnostics["planned_query_count_by_competitor"],
                "effective_query_count_by_competitor": effective_query_count_by_competitor,
                "effective_queries_preview_by_competitor": effective_queries_preview_by_competitor,
                "skipped_queries_by_competitor": skipped_queries_by_competitor,
                "query_policy": [
                    "planner_incremental_collection_plan"
                    if self._collection_mode(input_data) == "incremental"
                    else "planner_collection_plan"
                ],
                "query_dimensions_by_competitor": query_dimensions_by_competitor,
                "query_count": sum(query_count_by_competitor.values()),
                "query_count_by_competitor": query_count_by_competitor,
                    "query_count_by_dimension": query_count_by_dimension,
                    "failed_queries": self._dedupe_queries(failed_queries),
                "evidence_count": len(evidence),
                "evidence_count_by_competitor": evidence_count_by_competitor,
                "evidence_count_by_dimension": evidence_count_by_dimension,
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
                "target_dimensions_by_competitor": target_dimensions,
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
        collector_search_plan = self._collector_search_plan(input_data)
        diagnostics = diagnostics or self._base_diagnostics(input_data)
        collection_competitors = self._collection_competitors(input_data, collector_search_plan)
        evidence: list[Evidence] = []
        query_plans = {
            competitor: self._query_plan_for_competitor(
                competitor,
                collector_search_plan,
            )
            for competitor in collection_competitors
        }
        aliases_by_competitor = input_data.competitor_aliases
        for competitor in collection_competitors:
            aliases = aliases_by_competitor.get(competitor, [])
            if self._collection_mode(input_data) == "incremental":
                for index, query in enumerate(query_plans[competitor]["effective_queries"]):
                    metadata = query_plans[competitor]["query_metadata_by_query"].get(query, {})
                    dimension_id = metadata.get("dimension_id") or "unknown"
                    evidence.append(
                        apply_relevance(
                            Evidence(
                                competitor=competitor,
                                source_type="web",
                                url=f"https://example.com/{competitor.lower()}-{dimension_id}-incremental-{index}",
                                source_domain="example.com",
                                source_quality="official",
                                snippet=f"{competitor} {dimension_id} public source mentions {query}, official details, product capabilities, and current public information.",
                                confidence=0.86,
                            ),
                            competitor,
                            title=f"{competitor} {dimension_id}",
                            aliases=aliases,
                        )
                    )
                continue
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
        for competitor in collection_competitors:
            planned = [
                (
                    query,
                    query_plans[competitor]["query_metadata_by_query"].get(query, {}),
                )
                for query in query_plans[competitor]["effective_queries"]
            ]
            competitor_evidence = [item for item in evidence if item.competitor == competitor]
            for index, item in enumerate(competitor_evidence):
                if not planned:
                    break
                query, metadata = planned[index % len(planned)]
                dimension_id = metadata.get("dimension_id") or "unknown"
                signals = dict(item.entity_match_signals or {})
                signals.update(
                    {
                        "collector_query": query,
                        "collector_dimension": dimension_id,
                        "planner_dimension": dimension_id,
                        "collector_query_source": metadata.get("source") or "planner_collection_plan",
                    }
                )
                self._apply_collection_attempt_signals(
                    signals,
                    input_data,
                    competitor=competitor,
                    dimension_id=dimension_id,
                )
                item.entity_match_signals = signals
        evidence_count_by_competitor = {
            competitor: len([item for item in evidence if item.competitor == competitor])
            for competitor in collection_competitors
        }
        query_count_by_dimension: dict[str, int] = {}
        for plan in query_plans.values():
            for query in plan["effective_queries"]:
                dimension_id = plan["query_metadata_by_query"].get(query, {}).get("dimension_id") or "unknown"
                query_count_by_dimension[dimension_id] = query_count_by_dimension.get(dimension_id, 0) + 1
        evidence_count_by_dimension: dict[str, int] = {}
        for item in evidence:
            dimension_id = (item.entity_match_signals or {}).get("collector_dimension")
            if dimension_id:
                evidence_count_by_dimension[dimension_id] = evidence_count_by_dimension.get(dimension_id, 0) + 1
        diagnostics.update(
            {
                "collector_mode_used": "mock",
                "collection_plan_used": True,
                "collector_search_plan_source": (
                    "planner_incremental_collection_plan"
                    if self._collection_mode(input_data) == "incremental"
                    else "planner_collection_plan"
                ),
                "collector_search_plan_missing": False,
                "collector_search_plan_used": True,
                "entity_aliases_used": bool(input_data.competitor_aliases),
                "competitor_aliases_by_competitor": input_data.competitor_aliases,
                "alias_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["alias_queries"]) for competitor in collection_competitors
                },
                "alias_queries_preview_by_competitor": {
                    competitor: query_plans[competitor]["alias_queries"][:9] for competitor in collection_competitors
                },
                "targeted_recollection_used": self._collection_mode(input_data) == "incremental",
                "category_scope_hints_used": False,
                "planner_hint_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["planner_queries"]) for competitor in collection_competitors
                },
                "planned_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["planned_queries"]) for competitor in collection_competitors
                },
                "targeted_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["targeted_queries"]) for competitor in collection_competitors
                },
                "default_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["default_queries"]) for competitor in collection_competitors
                },
                "effective_query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["effective_queries"]) for competitor in collection_competitors
                },
                "effective_queries_preview_by_competitor": {
                    competitor: query_plans[competitor]["effective_queries"] for competitor in collection_competitors
                },
                "targeted_queries_preview_by_competitor": {
                    competitor: query_plans[competitor]["targeted_queries"][:9] for competitor in collection_competitors
                },
                "skipped_queries_by_competitor": {
                    competitor: [
                        {
                            "query": query,
                            "dimension_id": query_plans[competitor]["query_metadata_by_query"].get(query, {}).get("dimension_id")
                            or "unknown",
                            "reason": "exceeded_max_query_count_per_competitor",
                        }
                        for query in query_plans[competitor].get("all_candidate_queries", [])[len(query_plans[competitor]["effective_queries"]):]
                    ]
                    for competitor in collection_competitors
                },
                "query_policy": self._dedupe_queries(
                    [policy for competitor in collection_competitors for policy in query_plans[competitor]["query_policy"]]
                ),
                "query_dimensions_by_competitor": {
                    competitor: [
                        {
                            "query": query,
                            "dimension_id": query_plans[competitor]["query_metadata_by_query"].get(query, {}).get("dimension_id")
                            or "unknown",
                            "intent": query_plans[competitor]["query_metadata_by_query"].get(query, {}).get("intent"),
                            "source": query_plans[competitor]["query_metadata_by_query"].get(query, {}).get("source"),
                        }
                        for query in query_plans[competitor]["effective_queries"][:9]
                    ]
                    for competitor in collection_competitors
                },
                "query_count_by_competitor": {
                    competitor: len(query_plans[competitor]["effective_queries"])
                    for competitor in collection_competitors
                },
                "query_count": sum(
                    len(query_plans[competitor]["effective_queries"])
                    for competitor in collection_competitors
                ),
                "query_count_by_dimension": query_count_by_dimension,
                "evidence_count": len(evidence),
                "evidence_count_by_competitor": evidence_count_by_competitor,
                "evidence_count_by_dimension": evidence_count_by_dimension,
                "evidence_count_by_dimension_by_competitor": {
                    competitor: {dimension: 0 for dimension in self._dimension_ids_for_competitor(collector_search_plan, competitor)}
                    for competitor in collection_competitors
                },
                "raw_evidence_count": len(evidence),
                "deduplicated_evidence_count": len(evidence),
                "duplicate_removed_count": 0,
                "source_quality_summary": self._quality_summary(evidence),
                "low_confidence_count": sum(1 for item in evidence if item.confidence < 0.5),
                "competitor_coverage": evidence_count_by_competitor,
                "missing_competitors": [],
                "fallback_by_competitor": {competitor: None for competitor in collection_competitors},
                "raw_search_result_count_by_competitor": evidence_count_by_competitor,
                "relevant_evidence_count_by_competitor": evidence_count_by_competitor,
                "unrelated_evidence_count_by_competitor": {competitor: 0 for competitor in collection_competitors},
                "filtered_unrelated_count": 0,
                "missing_relevant_evidence_competitors": [],
                "target_dimensions_by_competitor": self._target_dimensions_by_competitor(input_data, collector_search_plan),
            }
        )
        return CollectorOutput(evidence=evidence, diagnostics=diagnostics)

    @staticmethod
    def _collection_mode(input_data: CollectorInput) -> str:
        return "incremental" if input_data.incremental_collection_plan is not None else "full"

    @staticmethod
    def _collector_attempt_no(input_data: CollectorInput) -> int:
        plan = input_data.incremental_collection_plan
        if plan is None:
            return 1
        return (plan.base_attempt_no or 1) + 1

    @staticmethod
    def _collection_competitors(input_data: CollectorInput, collector_search_plan: dict | None) -> list[str]:
        if input_data.incremental_collection_plan is None:
            if input_data.partial_collection_plan_allowed and isinstance(collector_search_plan, dict):
                return [
                    competitor
                    for competitor in input_data.task.competitors
                    if competitor in collector_search_plan
                ]
            return input_data.task.competitors
        if not isinstance(collector_search_plan, dict):
            return []
        return [
            competitor
            for competitor in input_data.task.competitors
            if competitor in collector_search_plan
        ]

    @staticmethod
    def _target_dimensions_by_competitor(input_data: CollectorInput, collector_search_plan: dict | None) -> dict[str, list[str]]:
        if not isinstance(collector_search_plan, dict):
            return {}
        return {
            competitor: list(dimensions.keys())
            for competitor, dimensions in collector_search_plan.items()
            if isinstance(dimensions, dict)
        }

    @staticmethod
    def _incremental_targets_summary(input_data: CollectorInput) -> list[dict[str, str | int]]:
        plan = input_data.incremental_collection_plan
        if plan is None:
            return []
        return [
            {
                "competitor": target.competitor,
                "dimension_id": target.dimension_id,
                "query_count": len(target.queries),
            }
            for target in plan.targets
        ]

    @staticmethod
    def _apply_collection_attempt_signals(
        signals: dict,
        input_data: CollectorInput,
        *,
        competitor: str,
        dimension_id: str,
    ) -> None:
        collection_mode = CollectorAgent._collection_mode(input_data)
        signals["collector_attempt_no"] = CollectorAgent._collector_attempt_no(input_data)
        signals["collector_mode"] = collection_mode
        if collection_mode == "incremental":
            signals["rework_source"] = "PlannerAgent"
            signals["target_competitor"] = competitor
            signals["target_dimension"] = dimension_id

    @staticmethod
    def _dimension_config(input_data: CollectorInput, competitor: str, dimension_id: str) -> dict:
        config = input_data.collector_config
        default = config.default if config else None
        matched = None
        if config:
            for override in config.overrides:
                if override.competitor == competitor and override.dimension_id == dimension_id:
                    matched = override
                    break

        def value(name: str, fallback: int) -> int:
            override_value = getattr(matched, name, None) if matched else None
            default_value = getattr(default, name, None) if default else None
            return int(override_value or default_value or fallback)

        include_domains = (
            matched.include_domains
            if matched and matched.include_domains
            else default.include_domains if default else []
        )
        exclude_domains = (
            matched.exclude_domains
            if matched and matched.exclude_domains
            else default.exclude_domains if default else []
        )
        return {
            "max_results_per_query": value("max_results_per_query", 8),
            "max_evidence_per_dimension": value("max_evidence_per_dimension", MAX_EVIDENCE_PER_DIMENSION),
            "min_valid_evidence_required": value("min_valid_evidence_required", MIN_RELEVANT_EVIDENCE_PER_COMPETITOR),
            "max_evidence_per_query": MAX_EVIDENCE_PER_QUERY,
            "include_domains": [item.lower().strip() for item in include_domains if item.strip()],
            "exclude_domains": [item.lower().strip() for item in exclude_domains if item.strip()],
        }

    @staticmethod
    def _domain_allowed(url: str, dimension_config: dict) -> bool:
        domain = CollectorAgent.extract_source_domain(url)
        host = urlparse(url).netloc.lower()
        include_domains = dimension_config.get("include_domains") or []
        exclude_domains = dimension_config.get("exclude_domains") or []
        if include_domains and not any(domain.endswith(item) or host.endswith(item) for item in include_domains):
            return False
        if exclude_domains and any(domain.endswith(item) or host.endswith(item) for item in exclude_domains):
            return False
        return True

    @classmethod
    def _query_plan_for_competitor(
        cls,
        competitor: str,
        collector_search_plan: dict | None,
    ) -> dict:
        planned_queries, query_metadata_by_query = cls._planned_queries_for_competitor(competitor, collector_search_plan)
        all_candidate_queries = cls._round_robin_queries_by_dimension(
            planned_queries,
            query_metadata_by_query,
        )
        effective_queries = all_candidate_queries[:MAX_QUERY_COUNT_PER_COMPETITOR]
        return {
            "targeted_queries": [],
            "alias_queries": [],
            "planner_queries": [],
            "planned_queries": planned_queries,
            "default_queries": [],
            "effective_queries": effective_queries,
            "all_candidate_queries": all_candidate_queries,
            "query_policy": cls._dedupe_queries(
                [
                    metadata.get("source") or "planner_collection_plan"
                    for metadata in query_metadata_by_query.values()
                ]
            ),
            "query_metadata_by_query": query_metadata_by_query,
        }

    @classmethod
    def _round_robin_queries_by_dimension(cls, queries: list[str], query_metadata_by_query: dict[str, dict]) -> list[str]:
        by_dimension: dict[str, list[str]] = {}
        dimension_order: list[str] = []
        for query in queries:
            metadata = query_metadata_by_query.get(query, {})
            dimension_id = metadata.get("dimension_id") or "unknown"
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
                    "source": (
                        "planner_incremental_collection_plan"
                        if item.get("source") == "planner_incremental_collection_plan"
                        else "planner_collection_plan"
                    ),
                    "dimension_id": item.get("dimension_id") or dimension_id,
                    "intent": (item.get("research_goals") or [None])[0],
                    "preferred_sources": [],
                    "query_strategy": None,
                }
        return cls._dedupe_queries(queries), query_metadata_by_query

    @staticmethod
    def _dimension_ids_for_competitor(collector_search_plan: dict | None, competitor: str) -> list[str]:
        if isinstance(collector_search_plan, dict):
            by_dimension = collector_search_plan.get(competitor)
            if isinstance(by_dimension, dict) and by_dimension:
                return list(by_dimension.keys())
        return []

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
