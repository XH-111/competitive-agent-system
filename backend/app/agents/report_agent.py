from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.agents.base import run_with_trace
from app.schemas import (
    Evidence,
    Report,
    ReportAgentInput,
    ReportAgentOutput,
    ReportEvidenceRef,
    ReportSection,
)
from app.services.llm_client import LlmClient, parse_llm_json
from app.services.trace_service import TraceService


class ReportAgent:
    name = "ReportAgent"

    def __init__(self, trace_service: TraceService, llm_client: LlmClient | None = None):
        self.trace_service = trace_service
        self.llm_client = llm_client or LlmClient()

    def run(self, input_data: ReportAgentInput) -> ReportAgentOutput:
        def produce() -> ReportAgentOutput:
            return self._produce(input_data)

        return run_with_trace(
            trace_service=self.trace_service,
            task_id=input_data.task.task_id,
            run_id=input_data.run_id,
            agent_name=self.name,
            to_agent="FinalReport",
            message_type="report",
            schema_name="ReportAgentOutput",
            input_summary=(
                "Generate structured competitor report from "
                f"{len(input_data.evidence_analyst_output.question_results)} EvidenceAnalyst question groups"
            ),
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def _produce(self, input_data: ReportAgentInput) -> ReportAgentOutput:
        diagnostics = self._diagnostics(input_data)
        if not self.llm_client.is_available:
            diagnostics.update({"llm_call_attempted": False, "fallback_used": True, "fallback_reason": "llm_unavailable"})
            return self._fallback_output(input_data, diagnostics)

        response = self.llm_client.chat_json(self._messages(input_data))
        diagnostics.update(
            {
                "llm_call_attempted": response.attempted,
                "llm_call_success": response.success,
                "llm_elapsed_time_ms": response.elapsed_time_ms,
                "llm_model": self.llm_client.model,
                "llm_provider": self.llm_client.provider,
            }
        )
        if not response.available or not response.success:
            diagnostics.update(
                {
                    "fallback_used": True,
                    "fallback_reason": response.error_message or response.fallback_reason or "llm_call_failed",
                }
            )
            return self._fallback_output(input_data, diagnostics)

        try:
            payload = parse_llm_json(response.content or "")
            return self._output_from_payload(input_data, payload, diagnostics)
        except Exception as exc:  # noqa: BLE001 - report generation should degrade, not break the workflow.
            diagnostics.update(
                {
                    "fallback_used": True,
                    "fallback_reason": "invalid_llm_json",
                    "llm_schema_validation_errors": [str(exc)],
                }
            )
            return self._fallback_output(input_data, diagnostics)

    def _messages(self, input_data: ReportAgentInput) -> list[dict[str, str]]:
        system = (
            "你是 ReportAgent，负责把 EvidenceAnalystAgent 的 question_results 整理成结构化中文竞品分析报告。"
            "你不能重新读取网页原文，不能重新抽取 evidence，不能使用外部知识。"
            "只能基于输入中的 question、answer、answer_status、evidence_ids、competitor、dimension_id 和 dimension_goal 写报告。"
            "每个 dimension_id 必须生成一个章节。文章要连贯自然，像正式报告，不要像日志列表。"
            "answered 可以写成明确结论；partial 必须写成“现有证据部分显示”；not_found 只能写进 limitations，不能写成确定结论。"
            "严格输出 JSON。顶层字段只能是 report_title 和 sections。不要输出 markdown_report。"
            "不要 markdown 代码块，不要额外字段。JSON 字符串中的引号和换行必须正确转义，绝对不要在字符串里输出未转义的原始换行。"
        )
        user = {
            "output_schema": {
                "report_title": "string",
                "sections": [
                    {
                        "section_id": "string, use dimension_id",
                        "section_no": "string, like 2.1",
                        "title": "string",
                        "summary": "string",
                        "competitor_analyses": [
                            {
                                "competitor": "string",
                                "analysis": "string",
                                "strengths": ["string"],
                                "weaknesses": ["string"],
                                "evidence_ids": ["string"],
                            }
                        ],
                        "comparison": "string",
                        "limitations": ["string"],
                        "evidence_ids": ["string"],
                        "confidence": "high|medium|low",
                    }
                ],
            },
            "writing_rules": [
                "每个 dimension_id 只生成一个章节。",
                "summary 写本维度的综合结论。",
                "competitor_analyses 按竞品分别写分析、优势、不足和引用证据。",
                "comparison 写竞品之间的优劣对比。",
                "limitations 写证据不足，尤其是 partial 和 not_found 的问题。",
                "evidence_ids 只能使用输入 question_answers 里出现过的证据 ID。",
            ],
            "context": self._prompt_context(input_data),
        }
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ]

    def _output_from_payload(
        self,
        input_data: ReportAgentInput,
        payload: dict[str, Any],
        diagnostics: dict[str, Any],
    ) -> ReportAgentOutput:
        if not isinstance(payload, dict):
            raise ValueError("ReportAgent LLM output must be a JSON object.")
        allowed = {"report_title", "sections"}
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise ValueError(f"ReportAgent LLM output contains unsupported fields: {', '.join(unexpected)}")

        report_title = self._clean_text(payload.get("report_title")) or self._report_title(input_data)
        sections = self._validate_sections(payload.get("sections"), input_data)
        if not sections:
            raise ValueError("ReportAgent LLM output must contain at least one section.")
        evidence_refs = self._evidence_refs(input_data.evidence, self._used_evidence_ids(sections))
        markdown_report = self._markdown_from_sections(report_title, sections)
        return self._build_output(
            input_data=input_data,
            report_title=report_title,
            sections=sections,
            evidence_refs=evidence_refs,
            markdown_report=markdown_report,
            diagnostics={**diagnostics, "fallback_used": False},
        )

    def _fallback_output(self, input_data: ReportAgentInput, diagnostics: dict[str, Any]) -> ReportAgentOutput:
        report_title = self._report_title(input_data)
        sections = self._fallback_sections(input_data)
        evidence_refs = self._evidence_refs(input_data.evidence, self._used_evidence_ids(sections))
        markdown_report = self._markdown_from_sections(report_title, sections)
        return self._build_output(
            input_data=input_data,
            report_title=report_title,
            sections=sections,
            evidence_refs=evidence_refs,
            markdown_report=markdown_report,
            diagnostics=diagnostics,
        )

    def _build_output(
        self,
        *,
        input_data: ReportAgentInput,
        report_title: str,
        sections: list[ReportSection],
        evidence_refs: dict[str, ReportEvidenceRef],
        markdown_report: str,
        diagnostics: dict[str, Any],
    ) -> ReportAgentOutput:
        json_report = {
            "report_agent": {
                "report_title": report_title,
                "sections": [section.model_dump(mode="json") for section in sections],
                "evidence_refs": {evidence_id: ref.model_dump(mode="json") for evidence_id, ref in evidence_refs.items()},
                "markdown_report": markdown_report,
                "diagnostics": diagnostics,
            },
            "evidence_analyst_output": input_data.evidence_analyst_output.model_dump(mode="json"),
        }
        report = Report(task_id=input_data.task.task_id, run_id=input_data.run_id, markdown=markdown_report, json_report=json_report)
        try:
            return ReportAgentOutput(
                report=report,
                report_title=report_title,
                sections=sections,
                evidence_refs=evidence_refs,
                markdown_report=markdown_report,
                diagnostics=diagnostics,
            )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

    def _prompt_context(self, input_data: ReportAgentInput) -> dict[str, Any]:
        return {
            "task": {
                "product_name": input_data.task.product_name,
                "competitors": input_data.task.competitors,
                "industry": input_data.task.industry,
                "region": input_data.task.region,
            },
            "dimensions": self._dimension_context(input_data),
            "diagnostics": input_data.evidence_analyst_output.diagnostics,
        }

    def _dimension_context(self, input_data: ReportAgentInput) -> list[dict[str, Any]]:
        labels = self._dimension_labels(input_data)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for result in input_data.evidence_analyst_output.question_results:
            dimension_id = result.dimension_id or "unknown"
            grouped.setdefault(dimension_id, []).append(
                {
                    "competitor": result.competitor,
                    "dimension_goal": result.dimension_goal,
                    "dimension_summary": result.dimension_summary,
                    "warnings": result.warnings,
                    "question_answers": [answer.model_dump(mode="json") for answer in result.question_answers],
                }
            )
        return [
            {
                "dimension_id": dimension_id,
                "section_no": f"2.{index}",
                "dimension_label": labels.get(dimension_id, dimension_id),
                "question_results": items,
            }
            for index, (dimension_id, items) in enumerate(grouped.items(), start=1)
        ]

    def _validate_sections(self, value: Any, input_data: ReportAgentInput) -> list[ReportSection]:
        if not isinstance(value, list):
            return []
        expected = {item["dimension_id"]: item for item in self._dimension_context(input_data)}
        sections: list[ReportSection] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, dict):
                continue
            section_id = self._clean_text(raw.get("section_id")) or self._clean_text(raw.get("dimension_id"))
            if not section_id or section_id not in expected or section_id in seen:
                continue
            seen.add(section_id)
            fallback = expected[section_id]
            sections.append(
                ReportSection(
                    section_id=section_id,
                    section_no=self._clean_text(raw.get("section_no")) or fallback["section_no"],
                    title=self._clean_text(raw.get("title")) or fallback["dimension_label"],
                    summary=self._status_safe_text(raw.get("summary")) or "当前证据不足，暂不形成确定总结。",
                    competitor_analyses=self._dict_list(raw.get("competitor_analyses")),
                    comparison=self._status_safe_text(raw.get("comparison")) or None,
                    limitations=self._string_list(raw.get("limitations")),
                    evidence_ids=self._filter_evidence_ids(raw.get("evidence_ids"), input_data),
                    confidence=self._confidence(raw.get("confidence")),
                )
            )

        missing = [dimension_id for dimension_id in expected if dimension_id not in seen]
        if missing:
            fallback_sections = {section.section_id: section for section in self._fallback_sections(input_data)}
            sections.extend(fallback_sections[dimension_id] for dimension_id in missing if dimension_id in fallback_sections)
        return sections

    def _fallback_sections(self, input_data: ReportAgentInput) -> list[ReportSection]:
        labels = self._dimension_labels(input_data)
        by_dimension: dict[str, list[Any]] = {}
        for result in input_data.evidence_analyst_output.question_results:
            by_dimension.setdefault(result.dimension_id or "unknown", []).append(result)

        sections: list[ReportSection] = []
        for index, (dimension_id, results) in enumerate(by_dimension.items(), start=1):
            answers = [answer for result in results for answer in result.question_answers]
            evidence_ids = sorted({evidence_id for answer in answers for evidence_id in answer.evidence_ids})
            limitations: list[str] = []
            competitor_analyses: list[dict[str, Any]] = []
            for result in results:
                answered = [answer for answer in result.question_answers if answer.answer_status == "answered"]
                partial = [answer for answer in result.question_answers if answer.answer_status == "partial"]
                not_found = [answer for answer in result.question_answers if answer.answer_status == "not_found"]
                if partial:
                    limitations.extend(f"{result.competitor}: 现有证据部分覆盖 {answer.question}" for answer in partial)
                if not_found:
                    limitations.extend(f"{result.competitor}: 当前证据未覆盖 {answer.question}" for answer in not_found)
                competitor_analyses.append(
                    {
                        "competitor": result.competitor,
                        "analysis": self._competitor_analysis_text(result),
                        "strengths": [answer.answer for answer in answered[:3]],
                        "weaknesses": [f"现有证据部分显示：{answer.answer}" for answer in partial[:3]]
                        + [f"当前证据未覆盖：{answer.question}" for answer in not_found[:3]],
                        "evidence_ids": sorted({evidence_id for answer in result.question_answers for evidence_id in answer.evidence_ids}),
                    }
                )
            sections.append(
                ReportSection(
                    section_id=dimension_id,
                    section_no=f"2.{index}",
                    title=labels.get(dimension_id, dimension_id),
                    summary=self._section_summary(results),
                    competitor_analyses=competitor_analyses,
                    comparison=self._comparison_text(results),
                    limitations=list(dict.fromkeys(limitations)),
                    evidence_ids=evidence_ids,
                    confidence=self._section_confidence(answers),
                )
            )
        return sections

    @staticmethod
    def _competitor_analysis_text(result: Any) -> str:
        parts: list[str] = []
        for answer in result.question_answers:
            if answer.answer_status == "answered":
                parts.append(answer.answer)
            elif answer.answer_status == "partial":
                parts.append(f"现有证据部分显示：{answer.answer}")
            else:
                parts.append(f"当前证据未覆盖：{answer.question}")
        return " ".join(parts)[:1600] or result.dimension_summary or "当前证据不足，暂不形成确定分析。"

    @staticmethod
    def _section_summary(results: list[Any]) -> str:
        summaries = [result.dimension_summary for result in results if result.dimension_summary]
        if summaries:
            return " ".join(summaries)[:1600]
        return "本章节基于 EvidenceAnalyst 已回答的问题进行整理。"

    @staticmethod
    def _comparison_text(results: list[Any]) -> str:
        competitors = [result.competitor for result in results if result.competitor]
        if len(competitors) < 2:
            return "当前章节仅有一个竞品的可用回答，暂不做强对比。"
        return f"本章节覆盖 {', '.join(competitors)}。对比判断仅基于已回答问题；partial 和 not_found 内容不作为确定结论。"

    @staticmethod
    def _section_confidence(answers: list[Any]) -> str:
        if not answers:
            return "low"
        answered = sum(1 for answer in answers if answer.answer_status == "answered")
        partial = sum(1 for answer in answers if answer.answer_status == "partial")
        if answered == len(answers):
            return "high"
        if answered or partial:
            return "medium"
        return "low"

    def _evidence_refs(self, evidence: list[Evidence], used_evidence_ids: set[str]) -> dict[str, ReportEvidenceRef]:
        refs: dict[str, ReportEvidenceRef] = {}
        by_id = {item.evidence_id: item for item in evidence}
        for evidence_id in sorted(used_evidence_ids):
            item = by_id.get(evidence_id)
            if not item:
                refs[evidence_id] = ReportEvidenceRef(evidence_id=evidence_id)
                continue
            refs[evidence_id] = ReportEvidenceRef(
                evidence_id=evidence_id,
                title=item.page_title or self._title_from_text(item.snippet) or item.source_domain or evidence_id,
                url=item.url,
                source_domain=item.source_domain,
                source_quality=item.source_quality,
                snippet=(item.snippet or item.content_excerpt or "")[:500],
            )
        return refs

    def _markdown_from_sections(self, title: str, sections: list[ReportSection]) -> str:
        lines = [f"# {title}", "", "## 1. 报告摘要", "本报告基于 EvidenceAnalyst 的问题回答生成。", "", "## 2. 分维度分析"]
        for section in sections:
            lines.extend(["", f"### {section.section_no} {section.title}", "", section.summary])
            for item in section.competitor_analyses:
                lines.extend(["", f"#### {item.get('competitor') or '综合'}", str(item.get("analysis") or "")])
            if section.comparison:
                lines.extend(["", f"**优劣对比**：{section.comparison}"])
            if section.limitations:
                lines.extend(["", "**证据不足**：", *[f"- {item}" for item in section.limitations]])
            if section.evidence_ids:
                lines.append(f"\n证据引用：{', '.join(section.evidence_ids)}")
        return "\n".join(lines)

    def _dimension_labels(self, input_data: ReportAgentInput) -> dict[str, str]:
        labels: dict[str, str] = {}
        if input_data.collection_plan:
            for dimensions in input_data.collection_plan.collector_search_plan.values():
                for key, item in dimensions.items():
                    labels[item.dimension_id or key] = item.label or item.dimension_id or key
        return labels

    @staticmethod
    def _diagnostics(input_data: ReportAgentInput) -> dict[str, Any]:
        dimensions = {result.dimension_id for result in input_data.evidence_analyst_output.question_results}
        answers = [answer for result in input_data.evidence_analyst_output.question_results for answer in result.question_answers]
        return {
            "report_agent_mode": "structured_llm_from_evidence_analyst_questions",
            "llm_enabled": True,
            "dimension_count": len(dimensions),
            "question_group_count": len(input_data.evidence_analyst_output.question_results),
            "question_count": len(answers),
            "answered_count": sum(1 for answer in answers if answer.answer_status == "answered"),
            "partial_count": sum(1 for answer in answers if answer.answer_status == "partial"),
            "not_found_count": sum(1 for answer in answers if answer.answer_status == "not_found"),
        }

    def _report_title(self, input_data: ReportAgentInput) -> str:
        competitors = " vs ".join(input_data.task.competitors)
        return f"竞品分析报告｜{competitors}｜行业：{input_data.task.industry}｜地区：{input_data.task.region}"

    @staticmethod
    def _used_evidence_ids(sections: list[ReportSection]) -> set[str]:
        return {evidence_id for section in sections for evidence_id in section.evidence_ids}

    @staticmethod
    def _filter_evidence_ids(value: Any, input_data: ReportAgentInput) -> list[str]:
        valid = {item.evidence_id for item in input_data.evidence}
        raw = [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []
        if valid:
            raw = [item for item in raw if item in valid]
        return list(dict.fromkeys(raw))

    @staticmethod
    def _confidence(value: Any) -> str:
        return str(value) if value in {"high", "medium", "low"} else "medium"

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []

    @staticmethod
    def _dict_list(value: Any) -> list[dict[str, Any]]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _status_safe_text(value: Any) -> str:
        return str(value or "").strip()[:3000]

    @staticmethod
    def _clean_text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _title_from_text(value: str | None) -> str | None:
        text = (value or "").strip()
        return text[:60] if text else None
