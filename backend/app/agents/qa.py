from collections import defaultdict
import re
from typing import Any

from app.agents.base import run_with_trace
from app.constants.analysis_dimensions import fixed_dimension_ids
from app.schemas import QaInput, QaOutput, QaResult, ReworkInstruction
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
            agent_name=self.name,
            to_agent="FinalReport",
            message_type="qa",
            schema_name="QaOutput",
            input_summary="Validate planner, Evidence, DimensionResult facts, and report fidelity",
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def evaluate(self, input_data: QaInput) -> QaResult:
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
        if next_count >= MAX_REWORK:
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
            "qa_contract": "planner_evidence_structured_fact_report",
            "claims_checked": False,
            "selected_dimensions": self._selected_dimensions(input_data),
            "dimension_coverage": self._dimension_coverage(input_data),
            "missing_dimension_evidence": self._dimension_evidence_gaps(input_data),
            "evidence_count": len(input_data.evidence),
            "dimension_result_count": len(input_data.analysis.dimension_results) if input_data.analysis else 0,
            "soft_suggestion_count": len(result.soft_suggestions),
        }

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        return list(dict.fromkeys(item for item in items if item))
