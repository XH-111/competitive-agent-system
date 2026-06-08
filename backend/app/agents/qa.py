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
            to_agent="FinalReport",
            message_type="qa",
            schema_name="QaOutput",
            input_summary=(
                "Validate Collector Evidence quality"
                if input_data.qa_stage == "evidence"
                else "Validate planner, Evidence, DimensionResult facts, and report fidelity"
            ),
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def evaluate(self, input_data: QaInput) -> QaResult:
        if input_data.qa_stage == "evidence":
            return self._evaluate_evidence_stage(input_data)
        for check in (
            self._planner_issue,
            self._evidence_issue,
            self._structured_fact_issue,
            self._report_issue,
        ):
            issue = check(input_data)
            if issue is not None:
                return issue

        return QaResult(
            task_id=input_data.task.task_id,
            run_id=input_data.run_id,
            status="passed",
            soft_suggestions=self._soft_suggestions(input_data),
            rework_count=input_data.retry_count,
            metadata={
                "qa_contract": "planner_evidence_structured_fact_report",
                "claims_checked": False,
                "dimension_coverage": self._dimension_coverage(input_data),
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
            suggestions.append(
                f"以下维度 Evidence 不足：{', '.join(failed_dimensions)}。"
            )
        if any(len(item.snippet.strip()) < 80 for item in input_data.evidence):
            suggestions.append("部分 Evidence snippet 较短，建议补充信息量更高的来源。")
        if any(item.source_quality in {"unknown", "low_quality"} for item in input_data.evidence):
            suggestions.append("部分 Evidence 来源质量偏低，建议补充官方、文档或可靠媒体来源。")
        if any(item.relevance_level == "low" for item in input_data.evidence):
            suggestions.append("部分 Evidence 相关性为 low，仅建议作为弱提示。")
        return failed_dimensions, suggestions

    def _planner_issue(self, input_data: QaInput) -> QaResult | None:
        if input_data.analysis_dimension_plan is None:
            return None

        selected = self._selected_dimensions(input_data)
        missing_base = [dimension_id for dimension_id in fixed_dimension_ids() if dimension_id not in selected]
        if missing_base:
            return self._failure(
                input_data,
                target_agent="PlannerAgent",
                error_type="invalid_planner_output",
                reason=f"Planner 缺少固定基础维度：{', '.join(missing_base)}。",
                suggested_action="重新运行 PlannerAgent，保留全部固定基础维度后再生成动态维度和采集计划。",
                failed_schema="AnalysisDimensionPlan.selected_dimensions",
                metadata={"missing_dimensions": missing_base, "fix_type": "restore_required_dimensions"},
            )

        plan = input_data.analysis_dimension_plan
        search_plan = (plan.metadata or {}).get("collector_search_plan", {}) if plan else {}
        missing: list[dict[str, str]] = []
        for competitor in input_data.task.competitors:
            by_dimension = search_plan.get(competitor, {}) if isinstance(search_plan, dict) else {}
            for dimension_id in selected:
                item = by_dimension.get(dimension_id, {}) if isinstance(by_dimension, dict) else {}
                queries = item.get("queries", []) if isinstance(item, dict) else []
                if not queries:
                    missing.append({"competitor": competitor, "dimension_id": dimension_id})
        if missing:
            first = missing[0]
            return self._failure(
                input_data,
                target_agent="PlannerAgent",
                error_type="missing_dimension_search_plan",
                reason=(
                    f"Planner 未为 {first['competitor']} 的 {first['dimension_id']} 维度生成搜索词，"
                    f"共缺少 {len(missing)} 个竞品维度组合。"
                ),
                suggested_action="为每个竞品和每个 selected_dimension 生成至少一个明确、可执行的搜索词。",
                failed_schema="AnalysisDimensionPlan.metadata.collector_search_plan",
                metadata={"missing_search_plan": missing, "fix_type": "rebuild_collector_search_plan"},
            )
        return None

    def _evidence_issue(self, input_data: QaInput) -> QaResult | None:
        if input_data.demo_mode == "qa_missing_evidence" or not input_data.evidence:
            return self._failure(
                input_data,
                target_agent="CollectorAgent",
                error_type="missing_evidence",
                reason="当前没有可用于结构化事实抽取的 Evidence。",
                suggested_action="按照 Planner 的竞品与维度采集计划补充公开证据。",
                failed_schema="Evidence",
            )

        relevant = self._relevant_evidence(input_data)
        missing_competitors = [
            competitor
            for competitor in input_data.task.competitors
            if not any(item.competitor == competitor for item in relevant)
        ]
        if missing_competitors:
            return self._failure(
                input_data,
                target_agent="CollectorAgent",
                error_type="missing_relevant_evidence",
                reason=f"以下竞品缺少 high/medium 相关 Evidence：{', '.join(missing_competitors)}。",
                suggested_action="针对缺失竞品重新采集官方页面、文档、可靠媒体或明确包含竞品实体的公开来源。",
                failed_schema="Evidence.relevance",
                metadata={
                    "missing_competitors": missing_competitors,
                    "fix_type": "collect_more_relevant_evidence",
                },
            )
        return None

    def _structured_fact_issue(self, input_data: QaInput) -> QaResult | None:
        analysis = input_data.analysis
        if input_data.demo_mode == "qa_invalid_extraction":
            return self._failure(
                input_data,
                target_agent="AnalystAgent",
                error_type="invalid_extraction",
                reason="DimensionResult 结构化抽取未通过校验。",
                suggested_action="重新运行 AnalystAgent，并按竞品和维度生成可校验的结构化事实。",
                failed_schema="AnalystOutput.dimension_results",
            )
        if analysis is None or not analysis.dimension_results:
            return self._failure(
                input_data,
                target_agent="AnalystAgent",
                error_type="invalid_extraction",
                reason="AnalystAgent 未生成 DimensionResult 结构化事实。",
                suggested_action="按竞品和 selected_dimensions 重新输出 DimensionResult。",
                failed_schema="AnalystOutput.dimension_results",
            )

        evidence_by_id = {item.evidence_id: item for item in input_data.evidence}
        results_by_key: dict[tuple[str | None, str], list[Any]] = defaultdict(list)
        for result in analysis.dimension_results:
            results_by_key[(result.competitor, result.dimension_id)].append(result)

        for competitor in input_data.task.competitors:
            for dimension_id in self._selected_dimensions(input_data):
                results = results_by_key.get((competitor, dimension_id), [])
                if not results:
                    return self._fact_failure(
                        input_data,
                        "dimension_coverage_gap",
                        competitor,
                        dimension_id,
                        f"缺少 {competitor} / {dimension_id} 的 DimensionResult。",
                        "补齐该竞品维度的结构化事实；证据不足时也必须输出 insufficient_evidence=true。",
                    )

                result = results[0]
                if result.insufficient_evidence:
                    if result.evidence_ids or result.confidence > 0.4:
                        return self._fact_failure(
                            input_data,
                            "invalid_insufficient_evidence_state",
                            competitor,
                            dimension_id,
                            f"{result.dimension_result_id} 标记证据不足，但仍包含证据或置信度高于 0.4。",
                            "清空 evidence_ids、将 confidence 降至 0.4 以下，并使用保守表述。",
                            result.dimension_result_id,
                        )
                    continue

                if not result.evidence_ids:
                    return self._fact_failure(
                        input_data,
                        "fact_missing_evidence",
                        competitor,
                        dimension_id,
                        f"{result.dimension_result_id} 没有绑定 evidence_ids。",
                        "重新抽取该事实，并绑定同竞品、同维度的 Evidence。",
                        result.dimension_result_id,
                    )

                for evidence_id in result.evidence_ids:
                    evidence = evidence_by_id.get(evidence_id)
                    if evidence is None:
                        return self._fact_failure(
                            input_data,
                            "fact_evidence_not_found",
                            competitor,
                            dimension_id,
                            f"{result.dimension_result_id} 引用了当前 run 中不存在的 Evidence {evidence_id}。",
                            "只能从当前 run 的 Evidence 白名单中选择 evidence_ids。",
                            result.dimension_result_id,
                            evidence_id=evidence_id,
                        )
                    if evidence.competitor and evidence.competitor != competitor:
                        return self._fact_failure(
                            input_data,
                            "fact_competitor_mismatch",
                            competitor,
                            dimension_id,
                            f"{result.dimension_result_id} 属于 {competitor}，但引用了 {evidence.competitor} 的 {evidence_id}。",
                            "重新运行 AnalystAgent，只绑定同一竞品的 Evidence。",
                            result.dimension_result_id,
                            evidence_id=evidence_id,
                        )
                    if evidence.relevance_level not in {"high", "medium"}:
                        return self._fact_failure(
                            input_data,
                            "fact_evidence_not_found",
                            competitor,
                            dimension_id,
                            f"{result.dimension_result_id} 引用了非 high/medium Evidence {evidence_id}。",
                            "移除低相关或无关证据；没有有效证据时标记 insufficient_evidence。",
                            result.dimension_result_id,
                            evidence_id=evidence_id,
                        )
                    evidence_dimension = (evidence.entity_match_signals or {}).get("collector_dimension")
                    if evidence_dimension and evidence_dimension != dimension_id:
                        return self._fact_failure(
                            input_data,
                            "fact_dimension_mismatch",
                            competitor,
                            dimension_id,
                            f"{result.dimension_result_id} 属于 {dimension_id}，但 {evidence_id} 的采集维度是 {evidence_dimension}。",
                            "优先绑定同维度 Evidence；无法支撑时标记 insufficient_evidence。",
                            result.dimension_result_id,
                            evidence_id=evidence_id,
                        )
        return None

    def _report_issue(self, input_data: QaInput) -> QaResult | None:
        output = input_data.report_output
        if output is None or output.report is None:
            return self._failure(
                input_data,
                target_agent="ReportWriterAgent",
                error_type="bad_report_format",
                reason="ReportWriterAgent 没有生成 Report。",
                suggested_action="基于已校验的 DimensionResult 重新生成 markdown 和 json_report。",
                failed_schema="ReportWriterOutput.report",
            )

        report = output.report
        if input_data.demo_mode == "qa_bad_report" or not report.markdown.strip().startswith("#"):
            return self._failure(
                input_data,
                target_agent="ReportWriterAgent",
                error_type="bad_report_format",
                reason="Markdown 报告缺少一级标题或正文格式不完整。",
                suggested_action="重新生成以一级标题开头的完整 Markdown 报告。",
                failed_schema="Report.markdown",
            )

        analysis_results = input_data.analysis.dimension_results if input_data.analysis else []
        expected_ids = {item.dimension_result_id for item in analysis_results}
        report_ids = {item.dimension_result_id for item in report.dimension_results}
        if expected_ids != report_ids:
            return self._failure(
                input_data,
                target_agent="ReportWriterAgent",
                error_type="report_fact_mismatch",
                reason="Report 携带的 DimensionResult 与 AnalystAgent 输出不一致。",
                suggested_action="不要重新生成或修改结构化事实，原样引用 AnalystAgent 的 dimension_results。",
                failed_schema="Report.dimension_results",
                metadata={
                    "missing_fact_ids": sorted(expected_ids - report_ids),
                    "unexpected_fact_ids": sorted(report_ids - expected_ids),
                    "fix_type": "restore_validated_dimension_results",
                },
            )

        markdown_lower = report.markdown.lower()
        missing_competitors = [
            competitor for competitor in input_data.task.competitors if competitor.lower() not in markdown_lower
        ]
        if missing_competitors:
            return self._failure(
                input_data,
                target_agent="ReportWriterAgent",
                error_type="report_competitor_gap",
                reason=f"报告未覆盖竞品：{', '.join(missing_competitors)}。",
                suggested_action="为每个输入竞品生成独立内容；证据不足时明确说明不足。",
                failed_schema="Report.markdown.competitor_coverage",
                metadata={"missing_competitors": missing_competitors},
            )

        unsupported_statements = self._unsupported_report_statements(report.markdown, report.dimension_results)
        if unsupported_statements:
            first = unsupported_statements[0]
            return self._failure(
                input_data,
                target_agent="ReportWriterAgent",
                error_type="report_unsupported_statement",
                reason=(
                    f"报告在证据不足的 {first['competitor']} / {first['dimension_id']} 维度中"
                    f"生成了强结论：{first['statement']}"
                ),
                suggested_action=(
                    "删除证据不足维度中的推导性结论，只保留“当前公开证据不足，暂不做强结论。”，"
                    "或先补充对应维度 Evidence。"
                ),
                failed_schema="Report.markdown.fact_fidelity",
                metadata={
                    "unsupported_statements": unsupported_statements[:10],
                    "fix_type": "remove_unsupported_report_inference",
                },
            )

        supported = [item for item in report.dimension_results if not item.insufficient_evidence]
        missing_citations = [
            item.dimension_result_id
            for item in supported
            if item.dimension_result_id not in report.markdown
            and not any(evidence_id in report.markdown for evidence_id in item.evidence_ids)
        ]
        if missing_citations:
            return self._failure(
                input_data,
                target_agent="ReportWriterAgent",
                error_type="report_missing_citations",
                reason=f"报告中有 {len(missing_citations)} 条结构化事实缺少 fact/evidence 引用。",
                suggested_action="在对应报告段落中保留 dimension_result_id 或 evidence_ids，确保可追溯。",
                failed_schema="Report.markdown.citations",
                metadata={"missing_fact_ids": missing_citations},
            )
        return None

    @staticmethod
    def _unsupported_report_statements(markdown: str, dimension_results: list[Any]) -> list[dict[str, str]]:
        insufficient_pairs = {
            (item.competitor, item.dimension_id)
            for item in dimension_results
            if item.insufficient_evidence and item.competitor
        }
        if not insufficient_pairs:
            return []

        heading_keywords = {
            "pricing": ["定价", "价格", "商业模式"],
            "feature": ["产品特性", "功能能力", "功能"],
            "persona": ["用户画像", "目标场景"],
            "strength": ["产品优势", "优势", "strength"],
            "weakness": ["产品劣势", "劣势", "weakness"],
            "opportunity": ["市场机会", "机会", "opportunit"],
            "threat": ["竞争威胁", "威胁", "threat"],
            "gpu_performance": ["gpu性能", "gpu 性能", "核心性能"],
            "cooling_design": ["散热设计", "散热"],
            "power_consumption": ["功耗", "能耗"],
            "gaming_performance": ["游戏性能", "游戏帧率"],
            "after_sales_policy": ["售后政策", "售后", "质保"],
        }
        conservative_markers = (
            "证据不足",
            "暂不做强结论",
            "无法形成",
            "无法判断",
            "尚未",
            "未采集",
            "缺少",
            "不足以",
        )
        current_dimension: str | None = None
        issues: list[dict[str, str]] = []
        for raw_line in markdown.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                normalized_heading = re.sub(r"[\s#*（）()：:、.0-9一二三四五六七八九十]+", "", line).lower()
                current_dimension = next(
                    (
                        dimension_id
                        for dimension_id, keywords in heading_keywords.items()
                        if any(keyword in normalized_heading for keyword in keywords)
                    ),
                    None,
                )
                continue
            if current_dimension is None or any(marker in line for marker in conservative_markers):
                continue
            for competitor, dimension_id in insufficient_pairs:
                if dimension_id == current_dimension and competitor in line:
                    issues.append(
                        {
                            "competitor": competitor,
                            "dimension_id": dimension_id,
                            "statement": line[:240],
                        }
                    )
        return issues

    def _failure(
        self,
        input_data: QaInput,
        *,
        target_agent: str,
        error_type: str,
        reason: str,
        suggested_action: str,
        failed_schema: str,
        metadata: dict[str, Any] | None = None,
    ) -> QaResult:
        instruction = ReworkInstruction(
            target_agent=target_agent,
            error_type=error_type,
            reason=reason,
            suggested_action=suggested_action,
            failed_schema=failed_schema,
            metadata=metadata or {},
        )
        next_count = max(input_data.retry_count, input_data.task.rework_count) + 1
        if next_count > MAX_REWORK:
            instruction.suggested_action = "已达到最大返工次数，请转人工复核。"
            return QaResult(
                task_id=input_data.task.task_id,
                run_id=input_data.run_id,
                status="manual_review",
                hard_errors=[reason],
                rework_instructions=[instruction],
                rework_count=next_count,
                metadata={"qa_contract": "planner_evidence_structured_fact_report", **(metadata or {})},
            )
        return QaResult(
            task_id=input_data.task.task_id,
            run_id=input_data.run_id,
            status="failed",
            hard_errors=[reason],
            rework_instructions=[instruction],
            route_to=target_agent,
            rework_count=next_count,
            metadata={"qa_contract": "planner_evidence_structured_fact_report", **(metadata or {})},
        )

    def _fact_failure(
        self,
        input_data: QaInput,
        error_type: str,
        competitor: str,
        dimension_id: str,
        reason: str,
        suggested_action: str,
        dimension_result_id: str | None = None,
        *,
        evidence_id: str | None = None,
    ) -> QaResult:
        return self._failure(
            input_data,
            target_agent="AnalystAgent",
            error_type=error_type,
            reason=reason,
            suggested_action=suggested_action,
            failed_schema="DimensionResult",
            metadata={
                "kind": "structured_fact_issue",
                "competitor": competitor,
                "dimension_id": dimension_id,
                "dimension_result_id": dimension_result_id,
                "evidence_id": evidence_id,
                "fix_type": "reextract_dimension_fact",
            },
        )

    def _selected_dimensions(self, input_data: QaInput) -> list[str]:
        selected = input_data.selected_dimensions
        if not selected and input_data.analysis_dimension_plan is not None:
            selected = input_data.analysis_dimension_plan.selected_dimensions
        if not selected and input_data.analysis is not None:
            selected = [item.dimension_id for item in input_data.analysis.dimension_results]
        if not selected and input_data.analysis is not None:
            selected = input_data.analysis.product_profile.custom_dimensions.get("selected_dimensions", []) or []
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
        results = input_data.analysis.dimension_results if input_data.analysis else []
        return {
            competitor: {
                dimension_id: {
                    "result_count": sum(
                        1 for item in results if item.competitor == competitor and item.dimension_id == dimension_id
                    ),
                    "supported_count": sum(
                        1
                        for item in results
                        if item.competitor == competitor
                        and item.dimension_id == dimension_id
                        and not item.insufficient_evidence
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
            suggestions.append(f"{len(gaps)} 个竞品维度暂无直接 Evidence，报告应保留证据不足说明。")
        if any(item.source_quality == "unknown" for item in input_data.evidence):
            suggestions.append("部分 Evidence 的 source_quality 为 unknown，建议补充官方或文档来源交叉验证。")
        if any(item.content_mode == "snippet" for item in input_data.evidence):
            suggestions.append("部分事实仅基于搜索摘要，报告中应使用保守措辞。")
        high_confidence_single_source = [
            item
            for item in (input_data.analysis.dimension_results if input_data.analysis else [])
            if not item.insufficient_evidence and item.confidence >= 0.85 and len(item.evidence_ids) == 1
        ]
        if high_confidence_single_source:
            suggestions.append(
                f"{len(high_confidence_single_source)} 条结构化事实仅由单条 Evidence 支撑但置信度较高，建议补充交叉来源。"
            )
        if input_data.report_output and input_data.report_output.llm_fallback_reason:
            suggestions.append(input_data.report_output.llm_fallback_reason)
        return suggestions

    def _diagnostics(self, input_data: QaInput, result: QaResult) -> dict[str, Any]:
        return {
            "qa_status": result.status,
            "qa_stage": input_data.qa_stage,
            "qa_contract": (
                "evidence_only"
                if input_data.qa_stage == "evidence"
                else "planner_evidence_structured_fact_report"
            ),
            "claims_checked": False,
            "selected_dimensions": self._selected_dimensions(input_data),
            "dimension_coverage": self._dimension_coverage(input_data),
            "missing_dimension_evidence": self._dimension_evidence_gaps(input_data),
            "evidence_count": len(input_data.evidence),
            "dimension_result_count": len(input_data.analysis.dimension_results) if input_data.analysis else 0,
            "soft_suggestion_count": len(result.soft_suggestions),
            "failed_queries": result.failed_queries,
            "failed_dimensions": result.failed_dimensions,
            "failed_competitors": result.failed_competitors,
        }

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        return list(dict.fromkeys(item for item in items if item))
