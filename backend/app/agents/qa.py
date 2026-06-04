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
            instruction.suggested_action = "Max rework reached. Escalate to manual review."
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
            f"Missing relevant public evidence for competitors: {', '.join(missing)}.",
            "Re-run CollectorAgent with precise per-competitor search and do not use unrelated search results as Evidence.",
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
                "No usable Evidence is available to support downstream analysis or reporting.",
                "Re-run CollectorAgent and collect at least one Evidence item with a source reference.",
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
                "AnalystAgent output is missing.",
                "Re-run AnalystAgent to produce ProductProfile, FeatureTree, PricingModel, UserPersona, and SWOT output.",
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
                "ProductProfile is incomplete or inconsistent.",
                "Re-run AnalystAgent and repair ProductProfile positioning and target_segments.",
                failed_schema="ProductProfile",
                result_metadata={"swot_validation": {"status": "not_checked", "issues": []}},
            )

        if self._has_unsupported_analysis(input_data):
            return self._result(
                task.task_id,
                rework_count,
                "AnalystAgent",
                "invalid_extraction",
                "Analyst output contains unsupported conclusions.",
                "Re-run AnalystAgent and keep conclusions evidence-bound and conservative.",
                failed_schema="AnalystOutput",
                result_metadata={"swot_validation": {"status": "not_checked", "issues": []}},
            )

        dimension_issue = self._dimension_result_issue(input_data)
        if dimension_issue is not None:
            return dimension_issue

        if input_data.report_output is None:
            return self._result(
                task.task_id,
                rework_count,
                "ReportWriterAgent",
                "bad_report_format",
                "ReportWriterAgent output is missing.",
                "Re-run ReportWriterAgent to generate Markdown and JSON report outputs.",
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
                        "Draft report contains a claim without evidence_ids.",
                        "Re-run ReportWriterAgent and bind every claim to evidence_ids or remove unsupported claims.",
                        failed_claim=claim.get("text"),
                    )

        if input_data.report_output.report is None:
            return self._result(
                task.task_id,
                rework_count,
                "ReportWriterAgent",
                "bad_report_format",
                "Report output is empty.",
                "Re-generate a report object with markdown, json_report, and claims.",
                failed_schema="Report",
            )

        report = input_data.report_output.report
        if input_data.demo_mode == "qa_bad_report" or not report.markdown.startswith("#"):
            return self._result(
                task.task_id,
                rework_count,
                "ReportWriterAgent",
                "bad_report_format",
                "Markdown report must start with a level-1 heading.",
                "Re-run ReportWriterAgent and generate a Markdown report with a proper top-level heading.",
                failed_schema="Report.markdown",
            )

        for claim in report.claims:
            if not claim.evidence_ids:
                return self._result(
                    task.task_id,
                    rework_count,
                    "ReportWriterAgent",
                    "bad_report_format",
                    f"Claim {claim.claim_id} is missing evidence_ids.",
                    "Bind evidence_ids for every source-backed claim.",
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
                    "Before production use, replace mock evidence with real collected evidence.",
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
            f"Missing competitor evidence coverage: {', '.join(missing)}.",
            "Re-run CollectorAgent with per-competitor search and ensure every competitor has Evidence.",
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
                        reason=f"SWOT {quadrant[:-1]} for {item.competitor or 'overall'} lacks strong evidence support.",
                        suggested_action="Collect stronger competitor-specific evidence before keeping this SWOT item.",
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
                            f"SWOT {quadrant[:-1]} for {item.competitor or 'overall'} cites evidence from "
                            f"{mismatched[0].competitor}."
                        ),
                        suggested_action="Recompute SWOT and bind each item only to evidence from the same competitor.",
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
                        reason=f"SWOT {quadrant[:-1]} for {item.competitor or 'overall'} looks over-inferred from sparse evidence.",
                        suggested_action="Lower confidence, soften language, or remove the item unless stronger evidence exists.",
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
                        reason=f"SWOT coverage is missing for competitor {competitor}.",
                        suggested_action=(
                            "Collect more relevant evidence for this competitor before recomputing SWOT."
                            if target_agent == "CollectorAgent"
                            else "Recompute SWOT so each requested competitor is represented."
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
                    reason=f"SWOT does not reflect planner-selected dimensions: {', '.join(unsupported_dimensions[:3])}.",
                    suggested_action="Recompute SWOT so planner-selected dimensions are reflected conservatively in the items.",
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
                        f"Claim {claim.claim_id} uses unrelated evidence {evidence_id}.",
                        "Re-run ReportWriterAgent and bind claims only to high/medium relevance Evidence from the same competitor.",
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
                f"Missing competitor claim coverage: {', '.join(missing_claims)}.",
                "Re-run ReportWriterAgent and generate at least one source-backed claim for each competitor with available Evidence.",
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
                        (
                            f"Claim evidence competitor mismatch: {claim.claim_id} belongs to "
                            f"{claim.competitor} but uses Evidence {evidence_id} from {evidence.competitor}."
                        ),
                        "Re-run ReportWriterAgent and bind each claim only to Evidence from the same competitor.",
                        claim_id=claim.claim_id,
                        failed_claim=claim.text,
                        failed_schema="Claim.evidence_ids",
                        instruction_metadata={
                            "kind": "claim_evidence_competitor_mismatch",
                            "claim_id": claim.claim_id,
                            "claim_competitor": claim.competitor,
                            "failed_claim": claim.text,
                            "evidence_id": evidence_id,
                            "evidence_competitor": evidence.competitor,
                            "evidence_source_domain": evidence.source_domain,
                            "evidence_relevance_level": evidence.relevance_level,
                            "evidence_source_quality": evidence.source_quality,
                            "fix_type": "rebind_claim_evidence",
                        },
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
            suggestions.append("Evidence count is below 3; collect more public sources.")

        missing_domain_count = sum(1 for item in input_data.evidence if not item.source_domain)
        if missing_domain_count:
            suggestions.append("Some Evidence items are missing source_domain; review source parsing.")

        if input_data.report_output and input_data.report_output.report:
            for claim in input_data.report_output.report.claims:
                related = [evidence_by_id[item] for item in claim.evidence_ids if item in evidence_by_id]
                if related and all(item.confidence < 0.5 for item in related):
                    low_confidence_claim_count += 1
                    suggestions.append("证据可信度较低，建议补充官方或高质量来源。")
                if related and all(item.relevance_level == "low" for item in related):
                    low_relevance_claims.append(claim.claim_id)
                    suggestions.append("Some claims are only backed by low-relevance evidence; add clearer competitor-specific sources.")
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
            suggestions.append("SWOT contains weakly supported items; collect stronger evidence or recompute the affected quadrants.")

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
            "dimension_coverage_result": self._dimension_coverage_result(input_data),
        }
        return suggestions, diagnostics

    def _dimension_result_issue(self, input_data: QaInput) -> QaResult | None:
        if input_data.analysis is None:
            return None
        selected_dimensions = self._selected_dimensions(input_data)
        if not selected_dimensions:
            return None
        results = input_data.analysis.dimension_results
        if not results:
            return self._result(
                input_data.task.task_id,
                input_data.task.rework_count,
                "AnalystAgent",
                "invalid_extraction",
                "AnalystAgent did not produce dimension_results for planner-selected dimensions.",
                "Re-run AnalystAgent and extract DimensionResult records for each selected dimension and competitor.",
                failed_schema="AnalystOutput.dimension_results",
            )
        evidence_by_id = {item.evidence_id: item for item in input_data.evidence}
        for competitor in input_data.task.competitors:
            for dimension_id in selected_dimensions:
                candidates = [
                    item
                    for item in results
                    if item.competitor == competitor and item.dimension_id == dimension_id
                ]
                if not candidates:
                    return self._result(
                        input_data.task.task_id,
                        input_data.task.rework_count,
                        "AnalystAgent",
                        "invalid_extraction",
                        f"Missing DimensionResult for competitor {competitor} and dimension {dimension_id}.",
                        "Re-run AnalystAgent and produce dimension-level findings for every requested competitor and dimension.",
                        failed_schema="DimensionResult",
                        instruction_metadata={
                            "competitor": competitor,
                            "dimension_id": dimension_id,
                            "fix_type": "extract_dimension_result",
                        },
                    )
                for result in candidates:
                    if result.insufficient_evidence:
                        continue
                    for evidence_id in result.evidence_ids:
                        evidence = evidence_by_id.get(evidence_id)
                        if evidence is None:
                            return self._result(
                                input_data.task.task_id,
                                input_data.task.rework_count,
                                "AnalystAgent",
                                "invalid_extraction",
                                f"DimensionResult {dimension_id}/{competitor} cites unknown evidence {evidence_id}.",
                                "Re-run AnalystAgent and bind dimension findings only to existing Evidence.",
                                failed_schema="DimensionResult.evidence_ids",
                            )
                        if evidence.relevance_level == "unrelated":
                            return self._result(
                                input_data.task.task_id,
                                input_data.task.rework_count,
                                "AnalystAgent",
                                "invalid_extraction",
                                f"DimensionResult {dimension_id}/{competitor} cites unrelated evidence {evidence_id}.",
                                "Re-run AnalystAgent and remove unrelated Evidence from dimension findings.",
                                failed_schema="DimensionResult.evidence_ids.relevance",
                            )
                        if evidence.competitor and evidence.competitor != competitor:
                            return self._result(
                                input_data.task.task_id,
                                input_data.task.rework_count,
                                "AnalystAgent",
                                "invalid_extraction",
                                f"DimensionResult {dimension_id}/{competitor} cites Evidence {evidence_id} from {evidence.competitor}.",
                                "Re-run AnalystAgent and bind each dimension finding only to Evidence from the same competitor.",
                                failed_schema="DimensionResult.evidence_ids.competitor",
                                instruction_metadata={
                                    "competitor": competitor,
                                    "dimension_id": dimension_id,
                                    "evidence_id": evidence_id,
                                    "evidence_competitor": evidence.competitor,
                                    "fix_type": "rebind_dimension_evidence",
                                },
                            )
        return None

    def _dimension_coverage_result(self, input_data: QaInput) -> dict:
        if input_data.analysis is None:
            return {}
        selected_dimensions = self._selected_dimensions(input_data)
        results = input_data.analysis.dimension_results
        return {
            competitor: {
                dimension_id: {
                    "result_count": sum(
                        1 for item in results if item.competitor == competitor and item.dimension_id == dimension_id
                    ),
                    "insufficient_count": sum(
                        1
                        for item in results
                        if item.competitor == competitor and item.dimension_id == dimension_id and item.insufficient_evidence
                    ),
                }
                for dimension_id in selected_dimensions
            }
            for competitor in input_data.task.competitors
        }

    @staticmethod
    def _analysis_suggestions(analysis) -> list[str]:
        suggestions = []
        objects = [analysis.product_profile, analysis.feature_tree, analysis.pricing_model, analysis.user_persona]
        if any(not getattr(item, "evidence_ids", []) for item in objects):
            suggestions.append("Some structured analysis fields are missing evidence_ids.")
        if "Evidence is insufficient" in analysis.product_profile.positioning:
            suggestions.append("结构化分析证据不足，建议补充更多来源。")
        if not analysis.feature_tree.core_features or not analysis.pricing_model.tiers or not analysis.user_persona.goals:
            suggestions.append("Some structured analysis sections remain sparse and may need more evidence.")
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
            input_summary="Validate schema, evidence coverage, report format, competitor coverage, and SWOT quality",
            retry_count=input_data.retry_count,
            fn=produce,
        )
