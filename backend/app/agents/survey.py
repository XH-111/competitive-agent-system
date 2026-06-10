import json
from typing import Any

from pydantic import ValidationError

from app.agents.base import run_with_trace
from app.schemas import (
    EvidenceAnalystOutput,
    SurveyAgentInput,
    SurveyAgentOutput,
    SurveyQuestionDraft,
)
from app.services.llm_client import LlmClient, parse_llm_json
from app.services.trace_service import TraceService


MAX_SURVEY_QUESTIONS = 12


class SurveyAgent:
    name = "SurveyAgent"

    def __init__(self, trace_service: TraceService, llm_client: LlmClient | None = None):
        self.trace_service = trace_service
        self.llm_client = llm_client or LlmClient()

    def run(self, input_data: SurveyAgentInput) -> SurveyAgentOutput:
        def produce() -> SurveyAgentOutput:
            return self._produce(input_data)

        return run_with_trace(
            trace_service=self.trace_service,
            task_id=input_data.task.task_id,
            run_id=input_data.run_id,
            agent_name=self.name,
            to_agent="WorkflowEngine",
            message_type="plan",
            schema_name="SurveyAgentOutput",
            input_summary="Design follow-up survey questions from report and EvidenceAnalyst gaps",
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def _produce(self, input_data: SurveyAgentInput) -> SurveyAgentOutput:
        fallback_questions = self._fallback_questions(input_data)
        diagnostics = {
            "survey_agent_mode": "llm_survey_design",
            "llm_enabled": self.llm_client.is_available,
            "llm_provider": self.llm_client.provider,
            "llm_model": self.llm_client.model,
            "llm_call_attempted": False,
            "llm_call_success": False,
            "fallback_used": False,
            "fallback_reason": None,
            "gap_question_count": len(fallback_questions),
        }
        if not fallback_questions:
            diagnostics.update({"survey_agent_mode": "skipped_no_gaps", "fallback_used": False})
            return SurveyAgentOutput(
                survey_title=f"{input_data.task.product_name}补充调研问卷",
                survey_description="本次报告没有发现需要通过问卷补充的信息缺口。",
                questions=[],
                diagnostics=diagnostics,
            )
        if not input_data.enabled or not self.llm_client.is_available:
            diagnostics.update(
                {
                    "survey_agent_mode": "deterministic_fallback",
                    "fallback_used": True,
                    "fallback_reason": "survey_agent_disabled_or_llm_unavailable",
                }
            )
            return self._fallback_output(input_data, fallback_questions, diagnostics)

        response = self.llm_client.chat_json(self._messages(input_data, fallback_questions))
        diagnostics.update(
            {
                "llm_call_attempted": response.attempted,
                "llm_call_success": response.success,
                "llm_elapsed_time_ms": response.elapsed_time_ms,
                "llm_prompt_tokens": response.prompt_tokens,
                "llm_completion_tokens": response.completion_tokens,
                "llm_total_tokens": response.total_tokens,
                "llm_error_type": response.error_type,
                "llm_error_message": response.error_message,
            }
        )
        if not response.available or not response.success:
            diagnostics.update(
                {
                    "survey_agent_mode": "deterministic_fallback",
                    "fallback_used": True,
                    "fallback_reason": response.error_message or response.fallback_reason or "llm_call_failed",
                }
            )
            return self._fallback_output(input_data, fallback_questions, diagnostics)

        try:
            payload = parse_llm_json(response.content or "")
            output = self._validate_payload(input_data, payload, diagnostics)
            output.diagnostics = {**output.diagnostics, **diagnostics, "fallback_used": False}
            return output
        except Exception as exc:  # noqa: BLE001 - survey design should never break the report workflow.
            diagnostics.update(
                {
                    "survey_agent_mode": "deterministic_fallback",
                    "fallback_used": True,
                    "fallback_reason": "invalid_llm_json",
                    "llm_schema_validation_errors": [str(exc)],
                }
            )
            return self._fallback_output(input_data, fallback_questions, diagnostics)

    def _messages(self, input_data: SurveyAgentInput, fallback_questions: list[SurveyQuestionDraft]) -> list[dict[str, str]]:
        system = (
            "你是 SurveyAgent。请为竞品分析报告中未解决或只部分解决的信息缺口设计简洁的中文补充问卷。"
            "问卷面向外部用户、行业专家或一线业务人员填写。不要询问商业机密、个人隐私或敏感个人数据。"
            "每个问题必须保留传入的 competitor、dimension_id、source_question_id、source_gap_type。"
            "survey_title、survey_description、question_text、options、reason 必须全部使用中文。"
            "只返回严格 JSON，不要输出 Markdown 或解释。"
        )
        user = {
            "task": {
                "product_name": input_data.task.product_name,
                "competitors": input_data.task.competitors,
                "industry": input_data.task.industry,
                "region": input_data.task.region,
            },
            "gap_questions": [item.model_dump(mode="json") for item in fallback_questions],
            "report_diagnostics": (
                input_data.report_agent_output.diagnostics
                if input_data.report_agent_output
                else {}
            ),
            "instructions": [
                "最多生成 12 个问题。",
                "除非选项非常明确，否则优先使用 short_text。",
                "问题必须使用中文表达，可以保留必要的英文品牌名、产品名或专有名词。",
                "要求填写者提供具体事实、使用场景、时间、可公开来源链接或截图说明。",
                "对于 partial 缺口，要明确追问缺失的部分。",
                "对于 not_found 缺口，要提出可以产生可验证信息的直接问题。",
            ],
            "output_schema": {
                "survey_title": "string",
                "survey_description": "string",
                "questions": [
                    {
                        "competitor": "string|null",
                        "dimension_id": "string|null",
                        "source_question_id": "string|null",
                        "source_gap_type": "not_found|partial|report_limitation|low_confidence|general_follow_up",
                        "question_text": "string",
                        "question_type": "short_text|single_choice|multiple_choice|rating",
                        "options": ["string"],
                        "required": True,
                        "reason": "string",
                    }
                ],
            },
        }
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ]

    def _validate_payload(self, input_data: SurveyAgentInput, payload: dict[str, Any], diagnostics: dict) -> SurveyAgentOutput:
        if not isinstance(payload, dict):
            raise ValueError("SurveyAgent output must be a JSON object.")
        raw_questions = payload.get("questions") or []
        if not isinstance(raw_questions, list):
            raw_questions = []
        questions: list[SurveyQuestionDraft] = []
        for raw in raw_questions[:MAX_SURVEY_QUESTIONS]:
            if not isinstance(raw, dict):
                continue
            try:
                questions.append(SurveyQuestionDraft.model_validate(raw))
            except ValidationError:
                continue
        if not questions:
            raise ValueError("SurveyAgent output has no valid questions.")
        return SurveyAgentOutput(
            survey_title=str(payload.get("survey_title") or f"{input_data.task.product_name}补充调研问卷")[:200],
            survey_description=str(
                payload.get("survey_description")
                or "请补充公开资料未能充分覆盖的竞品事实、使用场景和可验证来源。"
            )[:1000],
            questions=questions,
            diagnostics=diagnostics,
        )

    def _fallback_output(
        self,
        input_data: SurveyAgentInput,
        questions: list[SurveyQuestionDraft],
        diagnostics: dict,
    ) -> SurveyAgentOutput:
        return SurveyAgentOutput(
            survey_title=f"{input_data.task.product_name}补充调研问卷",
            survey_description=(
                "这份问卷用于补充公开 Evidence 未能充分回答的问题，"
                "请填写你了解的事实、使用经历、时间、依据或可公开来源。"
            ),
            questions=questions[:MAX_SURVEY_QUESTIONS],
            diagnostics=diagnostics,
        )

    @staticmethod
    def _fallback_questions(input_data: SurveyAgentInput) -> list[SurveyQuestionDraft]:
        output = input_data.evidence_analyst_output
        questions: list[SurveyQuestionDraft] = []
        if isinstance(output, EvidenceAnalystOutput):
            for group in output.question_results:
                for answer in group.question_answers:
                    if answer.answer_status not in {"not_found", "partial"}:
                        continue
                    gap_type = answer.answer_status
                    dimension_goal = group.dimension_goal or group.dimension_id or "当前维度"
                    questions.append(
                        SurveyQuestionDraft(
                            competitor=group.competitor,
                            dimension_id=group.dimension_id,
                            source_question_id=answer.question_id,
                            source_gap_type=gap_type,
                            question_text=(
                                f"关于 {group.competitor or '该竞品'} 在「{dimension_goal}」上的情况，"
                                f"请补充以下未充分解决的问题：{answer.question}。"
                                "请尽量提供具体事实、时间、使用场景、依据，以及可公开查看的来源链接或材料。"
                            ),
                            question_type="short_text",
                            options=[],
                            required=True,
                            reason=f"EvidenceAnalyst 将该问题标记为 {gap_type}，需要问卷补充。",
                        )
                    )
        if questions:
            return questions[:MAX_SURVEY_QUESTIONS]
        for competitor in input_data.task.competitors:
            for dimension_id in (input_data.selected_dimensions or ["general"])[:2]:
                questions.append(
                    SurveyQuestionDraft(
                        competitor=competitor,
                        dimension_id=dimension_id,
                        source_gap_type="general_follow_up",
                        question_text=(
                            f"关于 {competitor} 在「{dimension_id}」维度上的表现，你能补充哪些一手信息？"
                            "请提供可观察事实、使用经历、时间、依据或可公开来源。"
                        ),
                        reason="未获得结构化缺口问题时生成的兜底补充问题。",
                    )
                )
        return questions[:MAX_SURVEY_QUESTIONS]
