from collections.abc import Iterable

from app.agents.base import run_with_trace
from app.schemas import QaInput, QaOutput, QaResult, ReworkInstruction
from app.services.trace_service import TraceService

MAX_REWORK = 3
SWOT_QUADRANTS = ("strengths", "weaknesses", "opportunities", "threats")
DIMENSION_KEYWORDS = {
    "positioning": ["positioning", "category", "differentiation", "segment"],
    "feature": ["feature", "capability", "workflow", "integration", "automation", "api"],
    "pricing": ["pricing", "price", "plan", "enterprise", "quote", "trial"],
    "persona": ["user", "buyer", "team", "persona", "segment"],
    "ux": ["ux", "usability", "workflow", "experience", "pain point"],
    "feedback": ["feedback", "review", "complaint", "pain point"],
    "prioritization": ["priority", "prioritize", "improve", "improvement"],
    "hypothesis": ["hypothesis", "assumption", "validate"],
}
SWOT_QUERY_FOCUS = {
    "strengths": ["official features", "product differentiation", "documentation"],
    "weaknesses": ["reviews complaints", "pain points", "usability issues"],
    "opportunities": ["feature gaps", "improvement opportunities", "user requests"],
    "threats": ["alternatives", "competitive pressure", "market comparison"],
}


class QaAgent:
    name = "QaAgent"

    def __init__(self, trace_service: TraceService):
        self.trace_service = trace_service

    def _result(
        self,
        task_id: str,
        rework_count: int,
        target_agent: str,
        error_type: str,
        reason: str,
        suggested_action: str,
        *,
        claim_id: str | None = None,
        failed_claim: str | None = None,
        failed_schema: str | None = None,
        instruction_metadata: dict | None = None,
        result_metadata: dict | None = None,
    ) -> QaResult:
        instruction = ReworkInstruction(
            target_agent=target_agent,
            error_type=error_type,
            reason=reason,
            suggested_action=suggested_action,
            claim_id=claim_id,
            failed_claim=failed_claim,
            failed_schema=failed_schema,
            metadata=instruction_metadata or {},
        )
        if rework_count >= MAX_REWORK:
            instruction.suggested_action = "已达到最大返工次数，请进入人工复核。"
            return QaResult(
                task_id=task_id,
                status="manual_review",
                hard_errors=[reason],
                route_to=None,
                rework_count=rework_count,
                rework_instructions=[instruction],
                metadata=result_metadata or {},
            )

        return QaResult(
            task_id=task_id,
            status="failed",
            hard_errors=[reason],
            rework_instructions=[instruction],
            route_to=target_agent,
            rework_count=rework_count + 1,
            metadata=result_metadata or {},
        )

    def _missing_relevant_evidence_result(self, input_data: QaInput, missing: list[str]) -> QaResult:
        return self._result(
            input_data.task.task_id,
            input_data.task.rework_count,
            "CollectorAgent",
            "missing_relevant_evidence",
            f"以下竞品缺少相关公开证据：{', '.join(missing)}。",
            "请重新运行 CollectorAgent，按竞品定向搜索；不要把无关搜索结果作为 Evidence 使用。",
            failed_schema="Evidence.relevance",
            instruction_metadata={
                "kind": "coverage_gap",
                "competitors": missing,
                "fix_type": "collect_more_evidence",
            },
            result_metadata={
                "swot_validation": {"status": "not_checked", "issues": []},
            },
        )

    def evaluate(self, input_data: QaInput) -> QaResult:
        task = input_data.task
        rework_count = task.rework_count

        if input_data.demo_mode == "qa_missing_evidence" or not input_data.evidence:
            return self._result(
                task.task_id,
                rework_count,
                "CollectorAgent",
                "missing_evidence",
                "当前没有可用于支撑后续分析或报告的 Evidence。",
                "请重新运行 CollectorAgent，并至少采集一条带来源引用的 Evidence。",
                failed_schema="Evidence",
                result_metadata={"swot_validation": {"status": "not_checked", "issues": []}},
            )

        evidence_coverage_issue = self._competitor_evidence_coverage_issue(input_data)
        if evidence_coverage_issue is not None:
            return evidence_coverage_issue

        relevance_coverage_issue = self._competitor_relevance_coverage_issue(input_data)
        if relevance_coverage_issue is not None:
            return relevance_coverage_issue

        if input_data.analysis is None:
            return self._result(
                task.task_id,
                rework_count,
                "AnalystAgent",
                "invalid_extraction",
                "AnalystAgent 输出缺失。",
                "请重新运行 AnalystAgent，生成 ProductProfile、FeatureTree、PricingModel、UserPersona 和 SWOT 输出。",
                failed_schema="AnalystOutput",
                result_metadata={"swot_validation": {"status": "not_checked", "issues": []}},
            )

        profile = input_data.analysis.product_profile
        if input_data.demo_mode == "qa_invalid_extraction" or not profile.positioning or not profile.target_segments:
            return self._result(
                task.task_id,
                rework_count,
                "AnalystAgent",
                "invalid_extraction",
                "ProductProfile 不完整或存在不一致。",
                "请重新运行 AnalystAgent，修复 ProductProfile 的 positioning 和 target_segments。",
                failed_schema="ProductProfile",
                result_metadata={"swot_validation": {"status": "not_checked", "issues": []}},
            )

        if self._has_unsupported_analysis(input_data):
            return self._result(
                task.task_id,
                rework_count,
                "AnalystAgent",
                "invalid_extraction",
                "Analyst 输出包含缺少证据支撑的结论。",
                "请重新运行 AnalystAgent，并保持结论与证据绑定、表达保守。",
                failed_schema="AnalystOutput",
                result_metadata={"swot_validation": {"status": "not_checked", "issues": []}},
            )

        if input_data.report_output is None:
            return self._result(
                task.task_id,
                rework_count,
                "ReportWriterAgent",
                "bad_report_format",
                "ReportWriterAgent 输出缺失。",
                "请重新运行 ReportWriterAgent，生成 Markdown 和 JSON 报告输出。",
                failed_schema="ReportWriterOutput",
            )

        if input_data.report_output.draft_report:
            for claim in input_data.report_output.draft_report.get("claims", []):
                if not claim.get("evidence_ids"):
                    return self._result(
                        task.task_id,
                        rework_count,
                        "ReportWriterAgent",
                        "bad_report_format",
                        "草稿报告中存在缺少 evidence_ids 的 Claim。",
                        "请重新运行 ReportWriterAgent，为每条 Claim 绑定 evidence_ids，或移除无证据支撑的 Claim。",
                        failed_claim=claim.get("text"),
                    )

        if input_data.report_output.report is None:
            return self._result(
                task.task_id,
                rework_count,
                "ReportWriterAgent",
                "bad_report_format",
                "Report 输出为空。",
                "请重新生成包含 markdown、json_report 和 claims 的 Report 对象。",
                failed_schema="Report",
            )

        report = input_data.report_output.report
        if input_data.demo_mode == "qa_bad_report" or not report.markdown.startswith("#"):
            return self._result(
                task.task_id,
                rework_count,
                "ReportWriterAgent",
                "bad_report_format",
                "Markdown 报告必须以一级标题开头。",
                "请重新运行 ReportWriterAgent，生成带有正确一级标题的 Markdown 报告。",
                failed_schema="Report.markdown",
            )

        for claim in report.claims:
            if not claim.evidence_ids:
                return self._result(
                    task.task_id,
                    rework_count,
                    "ReportWriterAgent",
                    "bad_report_format",
                    f"Claim {claim.claim_id} 缺少 evidence_ids。",
                    "请为每条有来源支撑的 Claim 绑定 evidence_ids。",
                    claim_id=claim.claim_id,
                    failed_claim=claim.text,
                )

        unrelated_claim_issue = self._unrelated_evidence_claim_issue(input_data)
        if unrelated_claim_issue is not None:
            return unrelated_claim_issue

        claim_coverage_issue = self._competitor_claim_coverage_issue(input_data)
        if claim_coverage_issue is not None:
            return claim_coverage_issue

        swot_issue = self._swot_issue_result(input_data)
        if swot_issue is not None:
            return swot_issue

        quality_suggestions, diagnostics = self._quality_suggestions(input_data)
        return QaResult(
            task_id=task.task_id,
            status="passed",
            soft_suggestions=[
                suggestion
                for suggestion in [
                    "正式生产使用前，请将 mock evidence 替换为真实采集证据。",
                    input_data.report_output.llm_fallback_reason if input_data.report_output else None,
                    *self._analysis_suggestions(input_data.analysis),
                    *quality_suggestions,
                ]
                if suggestion
            ],
            rework_count=rework_count,
            metadata={
                "swot_validation": diagnostics.get("swot_validation", {"status": "passed", "issues": []}),
            },
        )

    def _competitor_evidence_coverage_issue(self, input_data: QaInput) -> QaResult | None:
        if not any(item.competitor for item in input_data.evidence):
            return None
        evidence_count_by_competitor = {
            competitor: sum(1 for item in input_data.evidence if item.competitor == competitor)
            for competitor in input_data.task.competitors
        }
        missing = [competitor for competitor, count in evidence_count_by_competitor.items() if count == 0]
        if not missing:
            return None
        return self._result(
            input_data.task.task_id,
            input_data.task.rework_count,
            "CollectorAgent",
            "missing_evidence",
            f"以下竞品缺少 Evidence 覆盖：{', '.join(missing)}。",
            "请重新运行 CollectorAgent，按竞品分别搜索，并确保每个竞品都有 Evidence。",
            failed_schema="Evidence.competitor",
            instruction_metadata={"competitors": missing, "fix_type": "collect_more_evidence"},
            result_metadata={"swot_validation": {"status": "not_checked", "issues": []}},
        )

    def _competitor_relevance_coverage_issue(self, input_data: QaInput) -> QaResult | None:
        if not any(item.competitor for item in input_data.evidence):
            return None
        relevant_count_by_competitor = {
            competitor: sum(
                1
                for item in input_data.evidence
                if item.competitor == competitor and item.relevance_level in {"high", "medium"}
            )
            for competitor in input_data.task.competitors
        }
        missing = [competitor for competitor, count in relevant_count_by_competitor.items() if count == 0]
        if missing:
            return self._missing_relevant_evidence_result(input_data, missing)
        return None

    def _swot_issue_result(self, input_data: QaInput) -> QaResult | None:
        issues = self._collect_swot_issues(input_data)
        if not issues:
            return None
        primary = issues[0]
        metadata = {
            "swot_validation": {
                "status": "failed",
                "issues": issues,
                "issue_count": len(issues),
            }
        }
        instruction_metadata = {
            "kind": "swot_quality_issue",
            "competitor": primary.get("competitor"),
            "quadrant": primary.get("quadrant"),
            "fix_type": primary.get("fix_type"),
            "focus_dimensions": primary.get("focus_dimensions", []),
            "query_focus": primary.get("query_focus", []),
            "all_issue_types": [issue["error_type"] for issue in issues],
            "swot_issues": issues,
        }
        return self._result(
            input_data.task.task_id,
            input_data.task.rework_count,
            primary["target_agent"],
            primary["error_type"],
            primary["reason"],
            primary["suggested_action"],
            failed_schema="SwotAnalysis",
            instruction_metadata=instruction_metadata,
            result_metadata=metadata,
        )

    def _collect_swot_issues(self, input_data: QaInput) -> list[dict]:
        analysis = input_data.analysis
        if analysis is None:
            return []
        task = input_data.task
        swot = analysis.swot
        evidence_by_id = {item.evidence_id: item for item in input_data.evidence}
        relevant_count_by_competitor = {
            competitor: sum(
                1
                for item in input_data.evidence
                if item.competitor == competitor and item.relevance_level in {"high", "medium"}
            )
            for competitor in task.competitors
        }
        selected_dimensions = self._selected_dimensions(input_data)
        issues: list[dict] = []
        seen_issue_keys: set[tuple[str, str | None, str | None]] = set()

        for quadrant in SWOT_QUADRANTS:
            for item in getattr(swot, quadrant):
                key = (quadrant, item.competitor, item.summary)
                records = [evidence_by_id[evidence_id] for evidence_id in item.evidence_ids if evidence_id in evidence_by_id]
                strong_records = [record for record in records if record.relevance_level in {"high", "medium"}]
                mismatched = [
                    record
                    for record in records
                    if item.competitor and record.competitor and record.competitor != item.competitor
                ]
                if not strong_records and item.confidence >= 0.5:
                    issue = self._swot_issue(
                        error_type="swot_missing_support",
                        target_agent="CollectorAgent",
                        competitor=item.competitor,
                        quadrant=quadrant,
                        fix_type="collect_more_evidence",
                        reason=f"{item.competitor or '整体'} 的 SWOT {quadrant[:-1]} 缺少强证据支撑。",
                        suggested_action="保留该 SWOT 项前，请先补充更强的竞品专属证据。",
                        query_focus=self._query_focus(quadrant, selected_dimensions),
                        focus_dimensions=selected_dimensions,
                    )
                    if (issue["error_type"], issue.get("competitor"), issue.get("quadrant")) not in seen_issue_keys:
                        issues.append(issue)
                        seen_issue_keys.add((issue["error_type"], issue.get("competitor"), issue.get("quadrant")))
                    continue

                if mismatched:
                    issue = self._swot_issue(
                        error_type="swot_competitor_mismatch",
                        target_agent="AnalystAgent",
                        competitor=item.competitor,
                        quadrant=quadrant,
                        fix_type="recompute_swot",
                        reason=(
                            f"{item.competitor or '整体'} 的 SWOT {quadrant[:-1]} 引用了 "
                            f"{mismatched[0].competitor} 的证据。"
                        ),
                        suggested_action="请重新计算 SWOT，并确保每一项只绑定同一竞品的 Evidence。",
                        query_focus=self._query_focus(quadrant, selected_dimensions),
                        focus_dimensions=selected_dimensions,
                    )
                    if (issue["error_type"], issue.get("competitor"), issue.get("quadrant")) not in seen_issue_keys:
                        issues.append(issue)
                        seen_issue_keys.add((issue["error_type"], issue.get("competitor"), issue.get("quadrant")))

                if quadrant in {"opportunities", "threats"} and item.confidence >= 0.7 and len(strong_records) < 2:
                    issue = self._swot_issue(
                        error_type="swot_over_inference",
                        target_agent="AnalystAgent",
                        competitor=item.competitor,
                        quadrant=quadrant,
                        fix_type="soften_language",
                        reason=f"{item.competitor or '整体'} 的 SWOT {quadrant[:-1]} 基于稀疏证据存在过度推断风险。",
                        suggested_action="除非有更强证据，否则请降低 confidence、弱化表述或移除该项。",
                        query_focus=self._query_focus(quadrant, selected_dimensions),
                        focus_dimensions=selected_dimensions,
                    )
                    if (issue["error_type"], issue.get("competitor"), issue.get("quadrant")) not in seen_issue_keys:
                        issues.append(issue)
                        seen_issue_keys.add((issue["error_type"], issue.get("competitor"), issue.get("quadrant")))

        swot_competitors = {
            item.competitor
            for quadrant in SWOT_QUADRANTS
            for item in getattr(swot, quadrant)
            if item.competitor
        }
        for competitor in task.competitors:
            if competitor not in swot_competitors and relevant_count_by_competitor.get(competitor, 0) >= 2:
                target_agent = "CollectorAgent" if relevant_count_by_competitor.get(competitor, 0) < 2 else "AnalystAgent"
                issues.append(
                    self._swot_issue(
                        error_type="swot_sparse_competitor_coverage",
                        target_agent=target_agent,
                        competitor=competitor,
                        quadrant=None,
                        fix_type="collect_more_evidence" if target_agent == "CollectorAgent" else "recompute_swot",
                        reason=f"SWOT 缺少竞品 {competitor} 的覆盖。",
                        suggested_action=(
                            "重新计算 SWOT 前，请先为该竞品采集更多相关证据。"
                            if target_agent == "CollectorAgent"
                            else "请重新计算 SWOT，确保每个请求的竞品都有体现。"
                        ),
                        query_focus=self._query_focus("weaknesses", selected_dimensions),
                        focus_dimensions=selected_dimensions,
                    )
                )
                break

        dimension_gap_candidates = {"pricing", "feedback", "ux"}
        unsupported_dimensions = [
            dimension
            for dimension in selected_dimensions
            if dimension in dimension_gap_candidates
            and self._dimension_has_evidence_support(dimension, input_data.evidence)
            and not self._dimension_supported_in_swot(dimension, swot, evidence_by_id)
        ]
        if unsupported_dimensions:
            issues.append(
                self._swot_issue(
                    error_type="swot_dimension_gap",
                    target_agent="AnalystAgent",
                    competitor=None,
                    quadrant=None,
                    fix_type="recompute_swot",
                    reason=f"SWOT 未体现 Planner 选择的分析维度：{', '.join(unsupported_dimensions[:3])}。",
                    suggested_action="请重新计算 SWOT，并以保守方式体现 Planner 选择的维度。",
                    query_focus=[],
                    focus_dimensions=unsupported_dimensions,
                )
            )

        return issues[:5]

    def _swot_issue(
        self,
        *,
        error_type: str,
        target_agent: str,
        competitor: str | None,
        quadrant: str | None,
        fix_type: str,
        reason: str,
        suggested_action: str,
        query_focus: list[str],
        focus_dimensions: list[str],
    ) -> dict:
        return {
            "error_type": error_type,
            "target_agent": target_agent,
            "competitor": competitor,
            "quadrant": quadrant,
            "fix_type": fix_type,
            "reason": reason,
            "suggested_action": suggested_action,
            "query_focus": query_focus,
            "focus_dimensions": focus_dimensions,
        }

    def _dimension_supported_in_swot(self, dimension: str, swot, evidence_by_id: dict) -> bool:
        keywords = DIMENSION_KEYWORDS.get(dimension, [dimension])
        searchable_parts: list[str] = []
        for quadrant in SWOT_QUADRANTS:
            for item in getattr(swot, quadrant):
                searchable_parts.append(item.summary)
                searchable_parts.extend(
                    self._evidence_text(evidence_by_id[evidence_id])
                    for evidence_id in item.evidence_ids
                    if evidence_id in evidence_by_id
                )
        searchable_text = " ".join(searchable_parts).lower()
        return any(keyword.lower() in searchable_text for keyword in keywords)

    def _dimension_has_evidence_support(self, dimension: str, evidence: list) -> bool:
        keywords = DIMENSION_KEYWORDS.get(dimension, [dimension])
        searchable_text = " ".join(self._evidence_text(item) for item in evidence).lower()
        return any(keyword.lower() in searchable_text for keyword in keywords)

    def _query_focus(self, quadrant: str, selected_dimensions: list[str]) -> list[str]:
        dimension_queries = []
        for dimension in selected_dimensions[:3]:
            if dimension == "pricing":
                dimension_queries.extend(["pricing official", "plan comparison"])
            elif dimension == "feedback":
                dimension_queries.extend(["reviews", "complaints", "pain points"])
            elif dimension == "ux":
                dimension_queries.extend(["usability", "workflow pain points"])
            elif dimension == "feature":
                dimension_queries.extend(["features documentation", "product capabilities"])
        return self._dedupe([*SWOT_QUERY_FOCUS.get(quadrant, []), *dimension_queries])

    def _unrelated_evidence_claim_issue(self, input_data: QaInput) -> QaResult | None:
        if input_data.report_output is None or input_data.report_output.report is None:
            return None
        evidence_by_id = {item.evidence_id: item for item in input_data.evidence}
        for claim in input_data.report_output.report.claims:
            for evidence_id in claim.evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if evidence and evidence.relevance_level == "unrelated":
                    return self._result(
                        input_data.task.task_id,
                        input_data.task.rework_count,
                        "ReportWriterAgent",
                        "bad_report_format",
                        f"Claim {claim.claim_id} 使用了无关 Evidence {evidence_id}。",
                        "请重新运行 ReportWriterAgent，只使用同一竞品的 high/medium relevance Evidence 绑定 Claim。",
                        claim_id=claim.claim_id,
                        failed_claim=claim.text,
                        failed_schema="Claim.evidence_ids.relevance",
                    )
        return None

    def _competitor_claim_coverage_issue(self, input_data: QaInput) -> QaResult | None:
        if input_data.report_output is None or input_data.report_output.report is None:
            return None
        report = input_data.report_output.report
        evidence_by_id = {item.evidence_id: item for item in input_data.evidence}
        if not any(item.competitor for item in input_data.evidence) and not any(claim.competitor for claim in report.claims):
            return None

        claim_count_by_competitor = {
            competitor: sum(1 for claim in report.claims if claim.competitor == competitor)
            for competitor in input_data.task.competitors
        }
        missing_claims = [competitor for competitor, count in claim_count_by_competitor.items() if count == 0]
        if missing_claims:
            return self._result(
                input_data.task.task_id,
                input_data.task.rework_count,
                "ReportWriterAgent",
                "bad_report_format",
                f"以下竞品缺少 Claim 覆盖：{', '.join(missing_claims)}。",
                "请重新运行 ReportWriterAgent，为每个已有 Evidence 的竞品至少生成一条有来源支撑的 Claim。",
                failed_schema="Report.claims.competitor",
            )

        for claim in report.claims:
            if not claim.competitor:
                continue
            for evidence_id in claim.evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if evidence and evidence.competitor and evidence.competitor != claim.competitor:
                    return self._result(
                        input_data.task.task_id,
                        input_data.task.rework_count,
                        "ReportWriterAgent",
                        "bad_report_format",
                        f"Claim 与 Evidence 的竞品不匹配：{claim.claim_id} 使用了 {evidence_id}。",
                        "请重新运行 ReportWriterAgent，确保每条 Claim 只绑定同一竞品的 Evidence。",
                        claim_id=claim.claim_id,
                        failed_schema="Claim.evidence_ids",
                    )
        return None

    def _quality_suggestions(self, input_data: QaInput) -> tuple[list[str], dict]:
        suggestions: list[str] = []
        evidence_by_id = {item.evidence_id: item for item in input_data.evidence}
        low_confidence_claim_count = 0
        evidence_count_by_competitor = {
            competitor: sum(1 for item in input_data.evidence if item.competitor == competitor)
            for competitor in input_data.task.competitors
        }
        claims = input_data.report_output.report.claims if input_data.report_output and input_data.report_output.report else []
        claim_count_by_competitor = {
            competitor: sum(1 for claim in claims if claim.competitor == competitor)
            for competitor in input_data.task.competitors
        }
        missing_evidence_competitors = [competitor for competitor, count in evidence_count_by_competitor.items() if count == 0]
        missing_relevant_evidence_competitors = [
            competitor
            for competitor in input_data.task.competitors
            if not any(
                item.competitor == competitor and item.relevance_level in {"high", "medium"}
                for item in input_data.evidence
            )
        ]
        missing_claim_competitors = [competitor for competitor, count in claim_count_by_competitor.items() if count == 0]
        mismatched_evidence_claims = []
        unrelated_evidence_claims = []
        low_relevance_claims = []

        if len(input_data.evidence) < 3:
            suggestions.append("Evidence 数量少于 3 条，建议补充更多公开来源。")

        missing_domain_count = sum(1 for item in input_data.evidence if not item.source_domain)
        if missing_domain_count:
            suggestions.append("部分 Evidence 缺少 source_domain，建议检查来源解析。")

        if input_data.report_output and input_data.report_output.report:
            for claim in input_data.report_output.report.claims:
                related = [evidence_by_id[item] for item in claim.evidence_ids if item in evidence_by_id]
                if related and all(item.confidence < 0.5 for item in related):
                    low_confidence_claim_count += 1
                    suggestions.append("证据可信度较低，建议补充官方或高质量来源。")
                if related and all(item.relevance_level == "low" for item in related):
                    low_relevance_claims.append(claim.claim_id)
                    suggestions.append("部分 Claim 只由低相关 Evidence 支撑，建议补充更明确的竞品专属来源。")
                for evidence_id in claim.evidence_ids:
                    evidence = evidence_by_id.get(evidence_id)
                    if claim.competitor and evidence and evidence.competitor and claim.competitor != evidence.competitor:
                        mismatched_evidence_claims.append(
                            {
                                "claim_id": claim.claim_id,
                                "claim_competitor": claim.competitor,
                                "evidence_id": evidence_id,
                                "evidence_competitor": evidence.competitor,
                            }
                        )
                    if evidence and evidence.relevance_level == "unrelated":
                        unrelated_evidence_claims.append(
                            {
                                "claim_id": claim.claim_id,
                                "evidence_id": evidence_id,
                                "competitor": claim.competitor,
                                "relevance_level": evidence.relevance_level,
                            }
                        )

        swot_issues = self._collect_swot_issues(input_data) if input_data.analysis is not None else []
        if swot_issues:
            suggestions.append("SWOT 中存在支撑较弱的项目，建议补充更强证据或重新计算相关象限。")

        diagnostics = {
            "evidence_quality_checked": True,
            "low_confidence_claim_count": low_confidence_claim_count,
            "soft_suggestion_count": len(suggestions),
            "competitor_coverage_checked": True,
            "relevance_checked": True,
            "missing_evidence_competitors": missing_evidence_competitors,
            "missing_relevant_evidence_competitors": missing_relevant_evidence_competitors,
            "missing_claim_competitors": missing_claim_competitors,
            "mismatched_evidence_claims": mismatched_evidence_claims,
            "unrelated_evidence_claims": unrelated_evidence_claims,
            "low_relevance_claims": low_relevance_claims,
            "swot_validation": {
                "status": "failed" if swot_issues else "passed",
                "issues": swot_issues,
                "issue_count": len(swot_issues),
            },
            "competitor_coverage_result": {
                competitor: {
                    "evidence_count": evidence_count_by_competitor.get(competitor, 0),
                    "relevant_evidence_count": sum(
                        1
                        for item in input_data.evidence
                        if item.competitor == competitor and item.relevance_level in {"high", "medium"}
                    ),
                    "unrelated_evidence_count": sum(
                        1
                        for item in input_data.evidence
                        if item.competitor == competitor and item.relevance_level == "unrelated"
                    ),
                    "claim_count": claim_count_by_competitor.get(competitor, 0),
                }
                for competitor in input_data.task.competitors
            },
        }
        return suggestions, diagnostics

    @staticmethod
    def _analysis_suggestions(analysis) -> list[str]:
        suggestions = []
        objects = [analysis.product_profile, analysis.feature_tree, analysis.pricing_model, analysis.user_persona]
        if any(not getattr(item, "evidence_ids", []) for item in objects):
            suggestions.append("部分结构化分析字段缺少 evidence_ids。")
        capability_buckets = [
            bucket
            for buckets in analysis.capability_map.competitor_capabilities.values()
            for bucket in buckets
        ] if getattr(analysis, "capability_map", None) else []
        if capability_buckets and any(not bucket.evidence_ids and not bucket.insufficient_evidence for bucket in capability_buckets):
            suggestions.append("部分能力桶缺少 evidence_ids，应保持与证据绑定。")
        if "Evidence is insufficient" in analysis.product_profile.positioning or "当前公开证据不足" in analysis.product_profile.positioning:
            suggestions.append("结构化分析证据不足，建议补充更多来源。")
        if not analysis.feature_tree.core_features or not analysis.pricing_model.tiers or not analysis.user_persona.goals:
            suggestions.append("部分结构化分析章节仍较稀疏，可能需要更多证据。")
        return suggestions

    @staticmethod
    def _has_unsupported_analysis(input_data: QaInput) -> bool:
        text = " ".join(
            [
                input_data.analysis.product_profile.positioning if input_data.analysis else "",
                " ".join(input_data.analysis.product_profile.strengths) if input_data.analysis else "",
                input_data.analysis.pricing_model.pricing_notes if input_data.analysis else "",
            ]
        ).lower()
        return "unsupported conclusion" in text

    @staticmethod
    def _selected_dimensions(input_data: QaInput) -> list[str]:
        selected = []
        if input_data.analysis is not None:
            selected = input_data.analysis.product_profile.custom_dimensions.get("selected_dimensions", []) or []
        return [str(item).strip().lower() for item in selected if str(item).strip()]

    @staticmethod
    def _evidence_text(evidence) -> str:
        return evidence.content_excerpt or evidence.snippet

    @staticmethod
    def _dedupe(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            if not item or item in seen:
                continue
            seen.add(item)
            output.append(item)
        return output

    def run(self, input_data: QaInput) -> QaOutput:
        task = input_data.task

        def produce() -> QaOutput:
            result = self.evaluate(input_data)
            _, diagnostics = self._quality_suggestions(input_data)
            diagnostics["soft_suggestion_count"] = len(result.soft_suggestions)
            diagnostics["qa_status"] = result.status
            return QaOutput(qa_result=result, diagnostics=diagnostics)

        return run_with_trace(
            trace_service=self.trace_service,
            task_id=task.task_id,
            agent_name=self.name,
            to_agent="FinalReport",
            message_type="qa",
            schema_name="QaOutput",
            input_summary="校验 Schema、证据覆盖、报告格式、竞品覆盖和 SWOT 质量",
            retry_count=input_data.retry_count,
            fn=produce,
        )
