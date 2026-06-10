from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from typing import Any, Callable

from pydantic import ValidationError

from app.agents.base import run_with_trace
from app.schemas import (
    Evidence,
    EvidenceAnalystInput,
    EvidenceAnalystOutput,
    EvidenceAnalystReworkTarget,
    EvidenceDimensionAnswerResult,
    EvidenceQuestionAnswer,
    PlannerCollectionPlanItem,
)
from app.services.llm_client import LlmClient, parse_llm_json
from app.services.trace_service import TraceService


MAX_PARALLEL_ANALYSIS_GROUPS = 3


class EvidenceAnalystAgent:
    name = "EvidenceAnalystAgent"

    def __init__(self, trace_service: TraceService, llm_client: LlmClient | None = None):
        self.trace_service = trace_service
        self.llm_client = llm_client or LlmClient()

    def run(
        self,
        input_data: EvidenceAnalystInput,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> EvidenceAnalystOutput:
        task = input_data.task

        def produce() -> EvidenceAnalystOutput:
            return self._produce(input_data, progress_callback=progress_callback)

        return run_with_trace(
            trace_service=self.trace_service,
            task_id=task.task_id,
            run_id=input_data.run_id,
            agent_name=self.name,
            to_agent="ReportAgent",
            message_type="analysis",
            schema_name="EvidenceAnalystOutput",
            input_summary=f"Answer planner questions from {len(input_data.evidence)} evidence records",
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def _produce(
        self,
        input_data: EvidenceAnalystInput,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> EvidenceAnalystOutput:
        if (
            input_data.rework_context
            and input_data.rework_context.mode == "answer_not_found_only"
            and input_data.previous_output
            and input_data.rework_context.targets
        ):
            return self._produce_incremental(input_data, progress_callback=progress_callback)

        groups = self._analysis_groups(input_data)
        group_questions: dict[tuple[str | None, str | None], int] = {
            key: len(self._questions(self._plan_item(input_data, key[0], key[1]), key[1]))
            for key in groups
        }
        total_questions = sum(group_questions.values())
        completed_questions = 0
        diagnostics: dict[str, Any] = {
            "evidence_analyst_mode": "question_answer_by_competitor_dimension",
            "evidence_analyst_enabled": input_data.enabled,
            "llm_enabled": self.llm_client.is_available,
            "llm_provider": self.llm_client.provider,
            "llm_model": self.llm_client.model,
            "total_evidence": len(input_data.evidence),
            "target_group_count": len(groups),
            "parallel_group_limit": MAX_PARALLEL_ANALYSIS_GROUPS,
            "completed_group_count": 0,
            "failed_group_count": 0,
            "llm_call_attempted": False,
            "llm_call_success_count": 0,
            "llm_call_failed_count": 0,
            "llm_elapsed_time_ms": 0,
            "llm_prompt_tokens": 0,
            "llm_completion_tokens": 0,
            "llm_total_tokens": 0,
            "llm_usage_available": False,
            "schema_validation_errors": [],
        }

        results_by_index: dict[int, EvidenceDimensionAnswerResult] = {}
        if not input_data.enabled:
            for index, ((competitor, dimension_id), evidence_items) in enumerate(groups.items(), start=1):
                results_by_index[index] = self._fallback_result(input_data, competitor, dimension_id, evidence_items, "evidence_analyst_disabled")
            results = [results_by_index[index] for index in sorted(results_by_index)]
            diagnostics.update(
                {
                    "completed_group_count": len(results),
                    "question_answer_count": sum(len(item.question_answers) for item in results),
                    "warning_count": len(results),
                    "skip_reason": "analyst_mode_not_llm",
                }
            )
            return EvidenceAnalystOutput(question_results=results, diagnostics=diagnostics)

        items = list(groups.items())
        max_workers = min(MAX_PARALLEL_ANALYSIS_GROUPS, max(1, len(items)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._answer_group_or_fallback,
                    input_data,
                    competitor,
                    dimension_id,
                    evidence_items,
                    index,
                ): (index, competitor, dimension_id)
                for index, ((competitor, dimension_id), evidence_items) in enumerate(items, start=1)
            }
            for future in as_completed(futures):
                index, competitor, dimension_id = futures[future]
                result, item_diagnostics = future.result()
                question_count = group_questions.get((competitor, dimension_id), 0)
                completed_questions += question_count
                if progress_callback:
                    progress_callback(
                        {
                            "current": min(completed_questions, total_questions),
                            "start": max(1, completed_questions - question_count + 1) if question_count else completed_questions,
                            "end": completed_questions,
                            "total": total_questions,
                            "unit": "question",
                            "detail": f"{competitor or '-'} / {dimension_id or '-'}",
                            "competitor": competitor,
                            "dimension_id": dimension_id,
                            "parallel_group_limit": MAX_PARALLEL_ANALYSIS_GROUPS,
                        }
                )
                results_by_index[index] = result
                diagnostics["completed_group_count"] += 1
                diagnostics["llm_call_attempted"] = diagnostics["llm_call_attempted"] or item_diagnostics["llm_call_attempted"]
                diagnostics["llm_elapsed_time_ms"] += item_diagnostics["llm_elapsed_time_ms"]
                self._accumulate_token_usage(diagnostics, item_diagnostics)
                if item_diagnostics["status"] == "llm":
                    diagnostics["llm_call_success_count"] += 1
                else:
                    diagnostics["failed_group_count"] += 1
                    diagnostics["llm_call_failed_count"] += 1
                    diagnostics["schema_validation_errors"].extend(item_diagnostics.get("errors", []))

        results = [results_by_index[index] for index in sorted(results_by_index)]
        diagnostics["question_answer_count"] = sum(len(item.question_answers) for item in results)
        diagnostics["answered_question_count"] = sum(
            1
            for item in results
            for answer in item.question_answers
            if answer.answer_status == "answered"
        )
        diagnostics["warning_count"] = sum(len(item.warnings) for item in results)
        return EvidenceAnalystOutput(question_results=results, diagnostics=diagnostics)

    def _produce_incremental(
        self,
        input_data: EvidenceAnalystInput,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> EvidenceAnalystOutput:
        assert input_data.previous_output is not None
        assert input_data.rework_context is not None
        targets_by_group: dict[tuple[str, str], list[EvidenceAnalystReworkTarget]] = defaultdict(list)
        for target in input_data.rework_context.targets:
            targets_by_group[(target.competitor, target.dimension_id)].append(target)

        total_questions = sum(len(items) for items in targets_by_group.values())
        completed_questions = 0
        diagnostics: dict[str, Any] = {
            **dict(input_data.previous_output.diagnostics or {}),
            "evidence_analyst_mode": "incremental_answer_not_found_only",
            "incremental_reanswer_target_count": total_questions,
            "incremental_completed_target_count": 0,
            "incremental_failed_target_count": 0,
            "llm_call_attempted": False,
            "llm_call_success_count": 0,
            "llm_call_failed_count": 0,
            "llm_elapsed_time_ms": 0,
            "llm_prompt_tokens": 0,
            "llm_completion_tokens": 0,
            "llm_total_tokens": 0,
            "llm_usage_available": False,
            "schema_validation_errors": [],
        }
        merged_results = [item.model_copy(deep=True) for item in input_data.previous_output.question_results]

        for index, ((competitor, dimension_id), targets) in enumerate(targets_by_group.items(), start=1):
            start_question = completed_questions + 1
            end_question = completed_questions + len(targets)
            evidence_items = self._evidence_for_targets(input_data.evidence, competitor, dimension_id, targets)
            if progress_callback:
                progress_callback(
                    {
                        "current": min(end_question, total_questions),
                        "start": start_question,
                        "end": end_question,
                        "total": total_questions,
                        "unit": "question",
                        "detail": f"{competitor or '-'} / {dimension_id or '-'}",
                        "competitor": competitor,
                        "dimension_id": dimension_id,
                    }
                )
            result, item_diagnostics = self._answer_group(
                input_data,
                competitor,
                dimension_id,
                evidence_items,
                index,
                question_overrides=[(target.question_id, target.question) for target in targets],
            )
            completed_questions += len(targets)
            self._merge_group_answers(merged_results, result)
            diagnostics["incremental_completed_target_count"] += len(targets)
            diagnostics["llm_call_attempted"] = diagnostics["llm_call_attempted"] or item_diagnostics["llm_call_attempted"]
            diagnostics["llm_elapsed_time_ms"] += item_diagnostics["llm_elapsed_time_ms"]
            self._accumulate_token_usage(diagnostics, item_diagnostics)
            if item_diagnostics["status"] == "llm":
                diagnostics["llm_call_success_count"] += 1
            else:
                diagnostics["incremental_failed_target_count"] += len(targets)
                diagnostics["llm_call_failed_count"] += 1
                diagnostics["schema_validation_errors"].extend(item_diagnostics.get("errors", []))

        diagnostics["question_answer_count"] = sum(len(item.question_answers) for item in merged_results)
        diagnostics["answered_question_count"] = sum(
            1
            for item in merged_results
            for answer in item.question_answers
            if answer.answer_status == "answered"
        )
        diagnostics["warning_count"] = sum(len(item.warnings) for item in merged_results)
        return EvidenceAnalystOutput(question_results=merged_results, diagnostics=diagnostics)

    def _answer_group_or_fallback(
        self,
        input_data: EvidenceAnalystInput,
        competitor: str | None,
        dimension_id: str | None,
        evidence_items: list[Evidence],
        index: int,
    ) -> tuple[EvidenceDimensionAnswerResult, dict[str, Any]]:
        if not evidence_items:
            return self._fallback_result(
                input_data,
                competitor,
                dimension_id,
                evidence_items,
                "no_evidence_for_competitor_dimension",
            ), {
                "status": "fallback",
                "llm_call_attempted": False,
                "llm_elapsed_time_ms": 0,
                "llm_prompt_tokens": 0,
                "llm_completion_tokens": 0,
                "llm_total_tokens": 0,
                "llm_usage_available": False,
                "errors": ["no_evidence_for_competitor_dimension"],
            }
        return self._answer_group(input_data, competitor, dimension_id, evidence_items, index)

    def _answer_group(
        self,
        input_data: EvidenceAnalystInput,
        competitor: str | None,
        dimension_id: str | None,
        evidence_items: list[Evidence],
        index: int,
        question_overrides: list[tuple[str, str]] | None = None,
    ) -> tuple[EvidenceDimensionAnswerResult, dict[str, Any]]:
        diagnostics = {
            "group_index": index,
            "competitor": competitor,
            "dimension_id": dimension_id,
            "evidence_count": len(evidence_items),
            "status": "fallback",
            "llm_call_attempted": False,
            "llm_elapsed_time_ms": 0,
            "llm_prompt_tokens": 0,
            "llm_completion_tokens": 0,
            "llm_total_tokens": 0,
            "llm_usage_available": False,
            "errors": [],
        }
        if not self.llm_client.is_available:
            diagnostics["errors"].append("LLM_API_KEY is not configured.")
            return self._fallback_result(input_data, competitor, dimension_id, evidence_items, "llm_unavailable", question_overrides), diagnostics

        response = self.llm_client.chat_json(self._messages(input_data, competitor, dimension_id, evidence_items, question_overrides))
        diagnostics.update(
            {
                "llm_call_attempted": response.attempted,
                "llm_elapsed_time_ms": response.elapsed_time_ms,
                "llm_prompt_tokens": response.prompt_tokens or 0,
                "llm_completion_tokens": response.completion_tokens or 0,
                "llm_total_tokens": response.total_tokens or 0,
                "llm_usage_available": response.total_tokens is not None,
            }
        )
        if not response.available or not response.success:
            diagnostics["errors"].append(response.error_message or response.fallback_reason or "LLM call failed.")
            return self._fallback_result(input_data, competitor, dimension_id, evidence_items, "llm_call_failed", question_overrides), diagnostics

        try:
            payload = parse_llm_json(response.content or "")
            result = self._validate_payload(input_data, competitor, dimension_id, evidence_items, payload, question_overrides)
        except Exception as exc:  # noqa: BLE001 - one group failure must not fail the batch.
            diagnostics["errors"].append(str(exc))
            return self._fallback_result(input_data, competitor, dimension_id, evidence_items, "invalid_llm_json", question_overrides), diagnostics

        diagnostics["status"] = "llm"
        return result, diagnostics

    @staticmethod
    def _accumulate_token_usage(diagnostics: dict[str, Any], item_diagnostics: dict[str, Any]) -> None:
        diagnostics["llm_prompt_tokens"] += int(item_diagnostics.get("llm_prompt_tokens") or 0)
        diagnostics["llm_completion_tokens"] += int(item_diagnostics.get("llm_completion_tokens") or 0)
        diagnostics["llm_total_tokens"] += int(item_diagnostics.get("llm_total_tokens") or 0)
        diagnostics["llm_usage_available"] = bool(
            diagnostics.get("llm_usage_available")
            or item_diagnostics.get("llm_usage_available")
        )

    def _validate_payload(
        self,
        input_data: EvidenceAnalystInput,
        competitor: str | None,
        dimension_id: str | None,
        evidence_items: list[Evidence],
        payload: dict[str, Any],
        question_overrides: list[tuple[str, str]] | None = None,
    ) -> EvidenceDimensionAnswerResult:
        if not isinstance(payload, dict):
            raise ValueError("Evidence analyst output must be a JSON object.")
        plan_item = self._plan_item(input_data, competitor, dimension_id)
        evidence_ids = {item.evidence_id for item in evidence_items}
        expected_questions = question_overrides or [
            (f"q{index}", question)
            for index, question in enumerate(self._questions(plan_item, dimension_id), start=1)
        ]
        expected_by_id = {question_id: question for question_id, question in expected_questions}
        answers = []
        for index, item in enumerate(payload.get("question_answers", []), start=1):
            if not isinstance(item, dict):
                continue
            answer = dict(item)
            fallback_question_id, fallback_question = expected_questions[index - 1] if index <= len(expected_questions) else (f"q{index}", "")
            answer["question_id"] = str(answer.get("question_id") or fallback_question_id)
            if question_overrides and answer["question_id"] not in expected_by_id:
                continue
            answer["question"] = str(answer.get("question") or expected_by_id.get(answer["question_id"]) or fallback_question)
            answer["answer"] = str(answer.get("answer") or "当前证据不足，无法回答该问题。")[:3000]
            answer["evidence_ids"] = [
                str(evidence_id)
                for evidence_id in answer.get("evidence_ids", [])
                if str(evidence_id) in evidence_ids
            ]
            if answer.get("answer_status") not in {"answered", "partial", "not_found"}:
                answer["answer_status"] = "answered" if answer["evidence_ids"] else "not_found"
            answer["suggestions"] = [
                str(value).strip()
                for value in answer.get("suggestions", [])
                if str(value).strip()
            ][:5]
            answers.append(answer)
        answered_ids = {answer["question_id"] for answer in answers}
        for question_id, question in expected_questions:
            if question_id not in answered_ids:
                answers.append(
                    {
                        "question_id": question_id,
                        "question": question,
                        "answer": "当前证据不足，无法回答该问题。",
                        "evidence_ids": [],
                        "answer_status": "not_found",
                        "suggestions": [f"补充能够直接回答该问题的公开来源：{question}"],
                    }
                )

        normalized = {
            "competitor": competitor,
            "dimension_id": dimension_id,
            "dimension_goal": self._dimension_goal(plan_item, dimension_id),
            "research_questions": [question for _, question in expected_questions],
            "question_answers": answers,
            "dimension_summary": str(payload.get("dimension_summary") or "")[:3000],
            "warnings": [str(item) for item in payload.get("warnings", []) if item],
        }
        try:
            return EvidenceDimensionAnswerResult.model_validate(normalized)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

    def _fallback_result(
        self,
        input_data: EvidenceAnalystInput,
        competitor: str | None,
        dimension_id: str | None,
        evidence_items: list[Evidence],
        warning: str,
        question_overrides: list[tuple[str, str]] | None = None,
    ) -> EvidenceDimensionAnswerResult:
        plan_item = self._plan_item(input_data, competitor, dimension_id)
        question_pairs = question_overrides or [
            (f"q{index}", question)
            for index, question in enumerate(self._questions(plan_item, dimension_id), start=1)
        ]
        questions = [question for _, question in question_pairs]
        return EvidenceDimensionAnswerResult(
            competitor=competitor,
            dimension_id=dimension_id,
            dimension_goal=self._dimension_goal(plan_item, dimension_id),
            research_questions=questions,
            question_answers=[
                EvidenceQuestionAnswer(
                    question_id=question_id,
                    question=question,
                    answer="当前未启用或无法完成 Evidence 问题回答，请查看 warnings。",
                    evidence_ids=[],
                    answer_status="not_found",
                )
                for question_id, question in question_pairs
            ],
            dimension_summary="当前未生成基于问题的维度回答。",
            warnings=[warning],
        )

    def _messages(
        self,
        input_data: EvidenceAnalystInput,
        competitor: str | None,
        dimension_id: str | None,
        evidence_items: list[Evidence],
        question_overrides: list[tuple[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        plan_item = self._plan_item(input_data, competitor, dimension_id)
        question_pairs = question_overrides or [
            (f"q{index}", question)
            for index, question in enumerate(self._questions(plan_item, dimension_id), start=1)
        ]
        system = (
            "你是一个基于证据回答竞品分析问题的助手。"
            "你会收到一个目标竞品、一个分析维度、该维度下 planner 规划的问题，以及多条 evidence。"
            "你的任务是根据 evidence 中已有的信息逐个回答 research_questions。"
            "只能根据提供的 evidence 回答，不要使用外部知识，不要为了完整而编造。"
            "如果证据不足，就明确说明当前证据不足。不要输出 Markdown，必须只输出合法 JSON。"
        )
        user = {
            "report_context": {
                "product_name": input_data.task.product_name,
                "industry": input_data.task.industry,
                "region": input_data.task.region,
                "target_competitor": competitor,
                "target_dimension_id": dimension_id,
                "dimension_goal": self._dimension_goal(plan_item, dimension_id),
                "research_questions": [
                    {"question_id": question_id, "question": question}
                    for question_id, question in question_pairs
                ],
            },
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "source_url": item.url,
                    "source_domain": item.source_domain,
                    "source_quality": item.source_quality,
                    "relevance_level": item.relevance_level,
                    "content_mode": item.content_mode,
                    "text": self._evidence_text(item),
                }
                for item in evidence_items
            ],
            "instructions": [
                "请依次回答 research_questions 中的每个问题。",
                "一个问题可以被多个 evidence 支撑，请在 evidence_ids 中列出所有支撑该答案的 evidence_id。",
                "如果某条 evidence 能丰富某个问题的答案，请把信息整合进该问题答案。",
                "answer 最长 3000 字，写清楚但不要过度扩写。",
                "如果证据只能部分回答问题，answer_status 设为 partial。",
                "如果没有 evidence 能回答问题，answer_status 设为 not_found，answer 中简短说明当前证据不足，evidence_ids 为空数组。",
                "回答要对后续写该竞品该维度报告有帮助，可以适度总结，但不要过度推断。",
            ],
            "additional_requirements": [
                "Every question answer must include a suggestions array.",
                "For answered questions, suggestions can be empty.",
                "For partial or not_found questions, suggestions must contain 1 to 3 concise collection suggestions.",
                "Suggestions should describe what public source or search direction would help answer this exact question.",
                "If evidence only has snippet but the snippet directly supports the answer, answer_status can still be answered.",
            ],
            "output_schema": {
                "question_answers": [
                    {
                        "question_id": "string, use the provided question_id",
                        "question": "string",
                        "answer": "string, max 3000 characters",
                        "evidence_ids": ["string"],
                        "answer_status": "answered|partial|not_found",
                        "suggestions": ["string"],
                    }
                ],
                "dimension_summary": "string",
                "warnings": ["string"],
            },
        }
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ]

    @staticmethod
    def _evidence_for_targets(
        evidence: list[Evidence],
        competitor: str,
        dimension_id: str,
        targets: list[EvidenceAnalystReworkTarget],
    ) -> list[Evidence]:
        new_ids = {
            evidence_id
            for target in targets
            for evidence_id in target.new_evidence_ids
            if evidence_id
        }
        candidates = [
            item
            for item in evidence
            if item.competitor == competitor
            and EvidenceAnalystAgent._dimension_id(item) == dimension_id
            and item.relevance_level in {"high", "medium"}
            and item.source_quality != "low_quality"
        ]
        if not new_ids:
            return candidates
        selected = [item for item in candidates if item.evidence_id in new_ids]
        return selected or candidates

    @staticmethod
    def _merge_group_answers(
        results: list[EvidenceDimensionAnswerResult],
        replacement: EvidenceDimensionAnswerResult,
    ) -> None:
        for index, result in enumerate(results):
            if result.competitor == replacement.competitor and result.dimension_id == replacement.dimension_id:
                by_id = {answer.question_id: answer for answer in result.question_answers}
                for answer in replacement.question_answers:
                    by_id[answer.question_id] = answer
                ordered_answers = [
                    by_id.get(answer.question_id, answer)
                    for answer in result.question_answers
                ]
                existing_ids = {answer.question_id for answer in ordered_answers}
                ordered_answers.extend(
                    answer
                    for answer in replacement.question_answers
                    if answer.question_id not in existing_ids
                )
                result.question_answers = ordered_answers
                result.warnings = list(dict.fromkeys([*result.warnings, *replacement.warnings]))
                if replacement.dimension_summary:
                    result.dimension_summary = replacement.dimension_summary
                results[index] = result
                return
        results.append(replacement)

    @staticmethod
    def _group_evidence(evidence: list[Evidence]) -> dict[tuple[str | None, str | None], list[Evidence]]:
        groups: dict[tuple[str | None, str | None], list[Evidence]] = defaultdict(list)
        for item in evidence:
            if item.relevance_level not in {"high", "medium"} or item.source_quality == "low_quality":
                continue
            groups[(item.competitor, EvidenceAnalystAgent._dimension_id(item))].append(item)
        return dict(groups)

    @staticmethod
    def _analysis_groups(input_data: EvidenceAnalystInput) -> dict[tuple[str | None, str | None], list[Evidence]]:
        groups = EvidenceAnalystAgent._group_evidence(input_data.evidence)
        if not input_data.collection_plan:
            return groups
        ordered: dict[tuple[str | None, str | None], list[Evidence]] = {}
        for competitor, dimensions in input_data.collection_plan.collector_search_plan.items():
            for dimension_id in dimensions:
                key = (competitor, dimension_id)
                ordered[key] = groups.get(key, [])
        for key, evidence_items in groups.items():
            ordered.setdefault(key, evidence_items)
        return ordered

    @staticmethod
    def _dimension_id(evidence: Evidence) -> str | None:
        value = (evidence.entity_match_signals or {}).get("collector_dimension")
        return str(value) if value else None

    @staticmethod
    def _evidence_text(evidence: Evidence) -> str:
        return (evidence.content_excerpt or evidence.snippet or "").strip()[:12000]

    @staticmethod
    def _plan_item(
        input_data: EvidenceAnalystInput,
        competitor: str | None,
        dimension_id: str | None,
    ) -> PlannerCollectionPlanItem | None:
        if not input_data.collection_plan or not competitor or not dimension_id:
            return None
        by_competitor = input_data.collection_plan.collector_search_plan.get(competitor)
        if not by_competitor:
            return None
        return by_competitor.get(dimension_id)

    @staticmethod
    def _dimension_goal(plan_item: PlannerCollectionPlanItem | None, dimension_id: str | None) -> str:
        if plan_item:
            goals = "；".join(plan_item.research_goals[:3])
            return f"{plan_item.label}：{goals}" if goals else plan_item.label
        return f"围绕 {dimension_id or '当前维度'} 回答 planner 规划的问题。"

    @staticmethod
    def _questions(plan_item: PlannerCollectionPlanItem | None, dimension_id: str | None) -> list[str]:
        if plan_item and plan_item.research_goals:
            return list(dict.fromkeys(goal for goal in plan_item.research_goals if goal))[:8]
        label = plan_item.label if plan_item else (dimension_id or "当前维度")
        return [
            f"{label} 维度下，当前证据能说明哪些关键事实？",
            f"{label} 维度下，当前证据有哪些不足或无法回答的问题？",
        ]
