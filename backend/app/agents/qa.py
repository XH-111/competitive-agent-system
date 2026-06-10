from collections import defaultdict
import re
from typing import Any

from app.agents.base import run_with_trace
from app.constants.analysis_dimensions import fixed_dimension_ids
from app.schemas import (
    EvidenceCoverageGap,
    EvidenceCoverageGapTarget,
    EvidenceCoverageSufficientItem,
    EvidenceCoverageSummary,
    QaInput,
    QaOutput,
    QaResult,
    ReworkInstruction,
)
from app.services.trace_service import TraceService


MAX_REWORK = 3


class QaAgent:
    name = "QaAgent"

    def __init__(self, trace_service: TraceService):
        self.trace_service = trace_service

    def run(self, input_data: QaInput) -> QaOutput:
        task = input_data.task

        def produce() -> QaOutput:
            result = self.evaluate(input_data)
            return QaOutput(qa_result=result, diagnostics=self._diagnostics(input_data, result))

        return run_with_trace(
            trace_service=self.trace_service,
            task_id=task.task_id,
            run_id=input_data.run_id,
            agent_name=self.name,
            to_agent="WorkflowEngine",
            message_type="qa",
            schema_name="QaOutput",
            input_summary=(
                "Validate Collector Evidence quality"
                if input_data.qa_stage == "evidence"
                else "Validate EvidenceAnalyst question answer coverage"
                if input_data.qa_stage == "analyst"
                else "Validate planner, Evidence, DimensionResult facts, and report fidelity"
            ),
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def evaluate(self, input_data: QaInput) -> QaResult:
        if input_data.qa_stage == "evidence":
            return self._evaluate_evidence_stage(input_data)
        if input_data.qa_stage == "analyst":
            return self._evaluate_analyst_stage(input_data)
        return QaResult(
            task_id=input_data.task.task_id,
            run_id=input_data.run_id,
            status="manual_review",
            hard_errors=["Legacy full QA stage is no longer supported by the current EvidenceAnalyst -> ReportAgent workflow."],
            soft_suggestions=["Use qa_stage='evidence' or qa_stage='analyst' in the current main workflow."],
            rework_count=input_data.retry_count,
            metadata={
                "qa_contract": "legacy_full_stage_removed",
                "current_contracts": ["evidence", "analyst"],
            },
        )

    def _evaluate_evidence_stage(self, input_data: QaInput) -> QaResult:
        diagnostics = input_data.collector_trace_summary or {}
        failed_queries = self._dedupe(
            [str(query) for query in diagnostics.get("failed_queries", []) if query]
        )
        collection_plan_used = diagnostics.get("collection_plan_used") is True
        collection_plan_missing = diagnostics.get("collector_search_plan_missing") is True
        hard_errors: list[str] = []

        if collection_plan_missing or not collection_plan_used:
            hard_errors.append("Collector did not execute PlannerOutput.collection_plan.collector_search_plan.")

        if (
            diagnostics.get("collector_mode_requested") == "web"
            and diagnostics.get("web_search_success") is False
            and not input_data.evidence
        ):
            hard_errors.append("Collector web search failed and returned no usable Evidence.")

        coverage_gap = self._evidence_coverage_gap(input_data, failed_queries)
        hard_errors.extend(target.reason for target in coverage_gap.targets)

        failed_dimensions, soft_suggestions = self._evidence_stage_warnings(input_data)
        if failed_queries:
            soft_suggestions.append(f"{len(failed_queries)} search queries failed or returned no results.")

        if hard_errors:
            route_to = "PlannerAgent"
            suggested_action = (
                "Fix Planner collection_plan so every competitor and dimension has executable queries."
                if collection_plan_missing
                else "Use metadata.coverage_gap to generate an incremental collection plan."
            )
            status = "failed"
        elif soft_suggestions:
            route_to = None
            suggested_action = "EvidenceQA found warnings; the result can continue for debugging but should be strengthened."
            status = "warning"
        else:
            route_to = None
            suggested_action = "EvidenceQA passed; no rework is required."
            status = "passed"

        return QaResult(
            task_id=input_data.task.task_id,
            run_id=input_data.run_id,
            qa_stage="evidence",
            status=status,
            hard_errors=self._dedupe(hard_errors),
            soft_suggestions=self._dedupe(soft_suggestions),
            route_to=route_to,
            failed_queries=failed_queries,
            failed_dimensions=self._dedupe([target.dimension_id for target in coverage_gap.targets] or failed_dimensions),
            failed_competitors=self._dedupe([target.competitor for target in coverage_gap.targets]),
            suggested_action=suggested_action,
            rework_count=input_data.retry_count,
            metadata={
                "qa_contract": "evidence_only",
                "auto_rework": False,
                "collection_plan_used": collection_plan_used,
                "evidence_count": len(input_data.evidence),
                "coverage_gap": coverage_gap.model_dump(mode="json"),
            },
        )

    def _evaluate_analyst_stage(self, input_data: QaInput) -> QaResult:
        output = input_data.evidence_analyst_output
        if output is None:
            return QaResult(
                task_id=input_data.task.task_id,
                run_id=input_data.run_id,
                qa_stage="analyst",
                status="warning",
                soft_suggestions=["EvidenceAnalyst output is missing; AnalystQA skipped."],
                suggested_action="Run EvidenceAnalyst before AnalystQA.",
                rework_count=input_data.retry_count,
                metadata={"qa_contract": "analyst_question_answer_coverage", "skip_reason": "missing_evidence_analyst_output"},
            )
        if output.diagnostics.get("evidence_analyst_enabled") is False:
            return QaResult(
                task_id=input_data.task.task_id,
                run_id=input_data.run_id,
                qa_stage="analyst",
                status="warning",
                soft_suggestions=["EvidenceAnalyst is disabled; AnalystQA did not trigger automatic rework."],
                suggested_action="Enable analyst_mode=llm to run AnalystQA coverage checks.",
                rework_count=input_data.retry_count,
                metadata={"qa_contract": "analyst_question_answer_coverage", "skip_reason": "evidence_analyst_disabled"},
            )
        not_found_targets: list[EvidenceCoverageGapTarget] = []
        if output:
            for result in output.question_results:
                competitor = result.competitor
                dimension_id = result.dimension_id
                if not competitor or not dimension_id:
                    continue
                for answer in result.question_answers:
                    if answer.answer_status != "not_found":
                        continue
                    suggestions = self._dedupe(answer.suggestions) or [
                        f"Collect one public source that directly answers: {answer.question}"
                    ]
                    not_found_targets.append(
                        EvidenceCoverageGapTarget(
                            competitor=competitor,
                            dimension_id=dimension_id,
                            reason_code="analyst_question_not_found",
                            reason=f"{competitor} / {dimension_id} question {answer.question_id} is not answered by current merged evidence.",
                            current_evidence_ids=answer.evidence_ids,
                            original_queries=self._original_queries(input_data, competitor, dimension_id),
                            question_id=answer.question_id,
                            question=answer.question,
                            suggestions=suggestions,
                            max_evidence=1,
                            content_fetch_priority="required",
                        )
                    )

        coverage_gap = EvidenceCoverageGap(
            targets=not_found_targets,
            summary={
                "qa_contract": "analyst_question_answer_coverage",
                "target_count": len(not_found_targets),
                "not_found_question_count": len(not_found_targets),
                "checked_group_count": len(output.question_results) if output else 0,
            },
        )
        if not_found_targets:
            return QaResult(
                task_id=input_data.task.task_id,
                run_id=input_data.run_id,
                qa_stage="analyst",
                status="failed",
                hard_errors=[f"{len(not_found_targets)} EvidenceAnalyst questions are not_found."],
                route_to="PlannerAgent",
                failed_dimensions=self._dedupe([target.dimension_id for target in not_found_targets]),
                failed_competitors=self._dedupe([target.competitor for target in not_found_targets]),
                suggested_action="Use metadata.coverage_gap to generate question-level incremental collection queries.",
                rework_count=input_data.retry_count,
                metadata={
                    "qa_contract": "analyst_question_answer_coverage",
                    "coverage_gap": coverage_gap.model_dump(mode="json"),
                    "auto_rework": True,
                },
            )

        return QaResult(
            task_id=input_data.task.task_id,
            run_id=input_data.run_id,
            qa_stage="analyst",
            status="passed",
            suggested_action="AnalystQA passed; no not_found question remains.",
            rework_count=input_data.retry_count,
            metadata={
                "qa_contract": "analyst_question_answer_coverage",
                "coverage_gap": coverage_gap.model_dump(mode="json"),
            },
        )

    def _evidence_coverage_gap(self, input_data: QaInput, failed_queries: list[str]) -> EvidenceCoverageGap:
        coverage_targets = self._coverage_targets(input_data)
        targets: list[EvidenceCoverageGapTarget] = []
        sufficient: list[EvidenceCoverageSufficientItem] = []
        for competitor, dimension_id in coverage_targets:
            records = self._evidence_for_competitor_dimension(input_data.evidence, competitor, dimension_id)
            valid = [item for item in records if self._is_valid_evidence_for_coverage(item)]
            required_valid_count = self._required_valid_evidence_count(input_data, competitor, dimension_id)
            if len(valid) >= required_valid_count:
                sufficient.append(
                    EvidenceCoverageSufficientItem(
                        competitor=competitor,
                        dimension_id=dimension_id,
                        valid_evidence_ids=[item.evidence_id for item in valid],
                        valid_evidence_count=len(valid),
                    )
                )
                continue
            target_failed_queries = self._failed_queries_for_dimension(
                failed_queries,
                competitor,
                dimension_id,
                input_data.collector_trace_summary,
            )
            reason_code = self._coverage_reason_code(records, target_failed_queries)
            targets.append(
                EvidenceCoverageGapTarget(
                    competitor=competitor,
                    dimension_id=dimension_id,
                    reason_code=reason_code,
                    reason=self._coverage_reason(competitor, dimension_id, reason_code, required_valid_count, len(valid)),
                    current_evidence_ids=[item.evidence_id for item in records],
                    current_evidence_summary=[self._coverage_summary(item) for item in records[:5]],
                    original_queries=self._original_queries(input_data, competitor, dimension_id),
                    failed_queries=target_failed_queries,
                )
            )
        return EvidenceCoverageGap(
            targets=targets,
            sufficient=sufficient,
            summary={
                "target_count": len(targets),
                "sufficient_count": len(sufficient),
                "checked_competitor_count": len({competitor for competitor, _ in coverage_targets}),
                "checked_dimension_count": len({dimension_id for _, dimension_id in coverage_targets}),
                "checked_target_count": len(coverage_targets),
                "collector_config_used": input_data.collector_config.model_dump(mode="json") if input_data.collector_config else None,
            },
        )

    def _coverage_targets(self, input_data: QaInput) -> list[tuple[str, str]]:
        if input_data.collection_plan is not None:
            selected = set(self._selected_dimensions(input_data))
            targets: list[tuple[str, str]] = []
            for competitor, dimensions in input_data.collection_plan.collector_search_plan.items():
                for dimension_id, item in dimensions.items():
                    value = item.dimension_id or dimension_id
                    if selected and value not in selected:
                        continue
                    targets.append((competitor, value))
            return list(dict.fromkeys(targets))
        return [
            (competitor, dimension_id)
            for competitor in input_data.task.competitors
            for dimension_id in self._selected_dimensions(input_data)
        ]

    @staticmethod
    def _evidence_for_competitor_dimension(evidence: list[Any], competitor: str, dimension_id: str) -> list[Any]:
        return [
            item
            for item in evidence
            if item.competitor == competitor
            and (item.entity_match_signals or {}).get("collector_dimension") == dimension_id
        ]

    @staticmethod
    def _is_valid_evidence_for_coverage(evidence: Any) -> bool:
        return evidence.relevance_level in {"high", "medium"} and evidence.source_quality != "low_quality"

    @staticmethod
    def _required_valid_evidence_count(input_data: QaInput, competitor: str, dimension_id: str) -> int:
        config = input_data.collector_config
        if not config:
            return 1
        matched = None
        for override in config.overrides:
            if override.competitor == competitor and override.dimension_id == dimension_id:
                matched = override
                break
        return (
            (matched.min_valid_evidence_required if matched else None)
            or config.default.min_valid_evidence_required
            or 1
        )

    @staticmethod
    def _coverage_reason_code(records: list[Any], failed_queries: list[str]) -> str:
        if not records:
            return "query_failed_without_valid_evidence" if failed_queries else "no_evidence_collected"
        if records and all(item.source_quality == "low_quality" for item in records):
            return "only_low_quality_evidence"
        if records and all(item.relevance_level == "unrelated" for item in records):
            return "dimension_not_collected"
        return "no_high_or_medium_evidence"

    @staticmethod
    def _coverage_reason(competitor: str, dimension_id: str, reason_code: str, required_valid_count: int = 1, valid_count: int = 0) -> str:
        if valid_count and valid_count < required_valid_count:
            return (
                f"{competitor} / {dimension_id} has {valid_count} valid Evidence, "
                f"but requires {required_valid_count}."
            )
        reasons = {
            "no_evidence_collected": f"{competitor} / {dimension_id} has no collected Evidence.",
            "no_high_or_medium_evidence": f"{competitor} / {dimension_id} has no high or medium relevance Evidence.",
            "only_low_quality_evidence": f"{competitor} / {dimension_id} only has low_quality Evidence.",
            "dimension_not_collected": f"{competitor} / {dimension_id} Evidence is all unrelated.",
            "query_failed_without_valid_evidence": f"{competitor} / {dimension_id} search queries failed and no valid Evidence exists.",
        }
        return reasons.get(reason_code, f"{competitor} / {dimension_id} has no valid Evidence.")

    @staticmethod
    def _coverage_summary(evidence: Any) -> EvidenceCoverageSummary:
        signals = evidence.entity_match_signals or {}
        return EvidenceCoverageSummary(
            evidence_id=evidence.evidence_id,
            source_domain=evidence.source_domain,
            source_quality=evidence.source_quality,
            relevance_level=evidence.relevance_level,
            relevance_score=evidence.relevance_score,
            confidence=evidence.confidence,
            collector_query=signals.get("collector_query"),
            relevance_reason=evidence.relevance_reason,
        )

    @staticmethod
    def _original_queries(input_data: QaInput, competitor: str, dimension_id: str) -> list[str]:
        if input_data.collection_plan is None:
            return []
        by_competitor = input_data.collection_plan.collector_search_plan.get(competitor)
        if not by_competitor:
            return []
        item = by_competitor.get(dimension_id)
        return list(item.queries) if item else []

    @staticmethod
    def _failed_queries_for_dimension(
        failed_queries: list[str],
        competitor: str,
        dimension_id: str,
        diagnostics: dict[str, Any],
    ) -> list[str]:
        if not failed_queries:
            return []
        query_dimensions = diagnostics.get("query_dimensions_by_competitor", {})
        by_competitor = query_dimensions.get(competitor, []) if isinstance(query_dimensions, dict) else []
        dimension_queries = {
            str(item.get("query"))
            for item in by_competitor
            if isinstance(item, dict) and item.get("dimension_id") == dimension_id and item.get("query")
        }
        if dimension_queries:
            return [query for query in failed_queries if query in dimension_queries]
        return [query for query in failed_queries if competitor in query]

    def _evidence_stage_warnings(self, input_data: QaInput) -> tuple[list[str], list[str]]:
        selected_dimensions = input_data.selected_dimensions
        seen_dimensions = {
            str((item.entity_match_signals or {}).get("collector_dimension"))
            for item in input_data.evidence
            if (item.entity_match_signals or {}).get("collector_dimension")
        }
        failed_dimensions = [
            dimension_id
            for dimension_id in selected_dimensions
            if dimension_id not in seen_dimensions
        ]
        suggestions: list[str] = []
        if failed_dimensions:
            suggestions.append(f"Insufficient Evidence for dimensions: {', '.join(failed_dimensions)}.")
        if any(len(item.snippet.strip()) < 80 for item in input_data.evidence):
            suggestions.append("Some Evidence snippets are short; prefer sources with richer content.")
        if any(item.source_quality in {"unknown", "low_quality"} for item in input_data.evidence):
            suggestions.append("Some Evidence sources have weak quality labels; prefer official, documentation, or reliable media sources.")
        if any(item.relevance_level == "low" for item in input_data.evidence):
            suggestions.append("Some Evidence is low relevance and should only be used as weak context.")
        return failed_dimensions, suggestions
    def _selected_dimensions(self, input_data: QaInput) -> list[str]:
        selected = input_data.selected_dimensions
        if not selected and input_data.analysis_dimension_plan is not None:
            selected = input_data.analysis_dimension_plan.selected_dimensions
        return self._dedupe([str(item).strip().lower() for item in selected if str(item).strip()])

    def _relevant_evidence(self, input_data: QaInput) -> list[Any]:
        return [
            item
            for item in input_data.evidence
            if item.relevance_level in {"high", "medium"} and item.source_quality != "low_quality"
        ]

    def _dimension_evidence_gaps(self, input_data: QaInput) -> list[dict[str, str]]:
        relevant = self._relevant_evidence(input_data)
        gaps: list[dict[str, str]] = []
        for competitor in input_data.task.competitors:
            for dimension_id in self._selected_dimensions(input_data):
                if not any(
                    item.competitor == competitor
                    and (item.entity_match_signals or {}).get("collector_dimension") == dimension_id
                    for item in relevant
                ):
                    gaps.append({"competitor": competitor, "dimension_id": dimension_id})
        return gaps

    def _dimension_coverage(self, input_data: QaInput) -> dict[str, dict[str, dict[str, int]]]:
        results = input_data.evidence_analyst_output.question_results if input_data.evidence_analyst_output else []
        return {
            competitor: {
                dimension_id: {
                    "question_group_count": sum(
                        1 for item in results if item.competitor == competitor and item.dimension_id == dimension_id
                    ),
                    "answered_question_count": sum(
                        1
                        for item in results
                        if item.competitor == competitor
                        and item.dimension_id == dimension_id
                        for answer in item.question_answers
                        if answer.answer_status == "answered"
                    ),
                }
                for dimension_id in self._selected_dimensions(input_data)
            }
            for competitor in input_data.task.competitors
        }

    def _soft_suggestions(self, input_data: QaInput) -> list[str]:
        suggestions: list[str] = []
        gaps = self._dimension_evidence_gaps(input_data)
        if gaps:
            suggestions.append(f"{len(gaps)} competitor/dimension pairs do not have direct high or medium Evidence yet.")
        if any(item.source_quality == "unknown" for item in input_data.evidence):
            suggestions.append("Some Evidence source_quality values are unknown; prefer official or documentation sources when possible.")
        if any(item.content_mode == "snippet" for item in input_data.evidence):
            suggestions.append("Some facts are based on snippets only; keep report wording conservative unless full content was fetched.")
        return suggestions

    def _diagnostics(self, input_data: QaInput, result: QaResult) -> dict[str, Any]:
        return {
            "qa_status": result.status,
            "qa_stage": input_data.qa_stage,
            "qa_contract": (
                "evidence_only"
                if input_data.qa_stage == "evidence"
                else "analyst_question_answer_coverage"
                if input_data.qa_stage == "analyst"
                else "planner_evidence_structured_fact_report"
            ),
            "claims_checked": False,
            "selected_dimensions": self._selected_dimensions(input_data),
            "dimension_coverage": self._dimension_coverage(input_data),
            "missing_dimension_evidence": self._dimension_evidence_gaps(input_data),
            "evidence_count": len(input_data.evidence),
            "question_group_count": (
                len(input_data.evidence_analyst_output.question_results)
                if input_data.evidence_analyst_output
                else 0
            ),
            "soft_suggestion_count": len(result.soft_suggestions),
            "failed_queries": result.failed_queries,
            "failed_dimensions": result.failed_dimensions,
            "failed_competitors": result.failed_competitors,
        }

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        return list(dict.fromkeys(item for item in items if item))





