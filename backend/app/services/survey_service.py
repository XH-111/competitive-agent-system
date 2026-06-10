import hashlib
import json
import secrets
import string
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agents.survey_response_qa import SurveyResponseQaAgent
from app.database import Base
from app.db_models import (
    KnowledgeChunkRecordRow,
    KnowledgeItemRecordRow,
    SurveyAnswerRecordRow,
    SurveyQuestionRecordRow,
    SurveyRecordRow,
    SurveyResponseRecordRow,
)
from app.services.embedding_service import EmbeddingService
from app.services.task_service import TaskService
from app.services.trace_service import TraceService
from app.schemas import SurveyAgentOutput, SurveyQuestionDraft


MAX_GENERATED_QUESTIONS = 12
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class SurveyService:
    def __init__(self, db: Session, embedding_service: EmbeddingService | None = None):
        self.db = db
        self.embedding_service = embedding_service or EmbeddingService()
        self.response_qa_agent = SurveyResponseQaAgent(TraceService(db))

    def generate_for_run(self, task_id: str, run_id: str) -> dict:
        Base.metadata.create_all(bind=self.db.get_bind())
        task = TaskService(self.db).get_task(task_id)
        summary = self._workflow_summary(task_id, run_id)
        output_payload = summary.get("survey_agent_output")
        if isinstance(output_payload, dict):
            output = SurveyAgentOutput.model_validate(output_payload)
            return self.create_from_agent_output(task_id, run_id, output)
        gaps = self._gap_questions(summary)
        if not gaps:
            gaps = self._fallback_questions(summary, task.competitors)
        output = SurveyAgentOutput(
            survey_title=f"{task.product_name}补充调研问卷",
            survey_description=(
                "这份问卷用于补充公开 Evidence 未能充分回答的问题，"
                "请填写你了解的事实、使用经历、时间、依据或可公开来源。"
            ),
            questions=[SurveyQuestionDraft.model_validate(item) for item in gaps[:MAX_GENERATED_QUESTIONS]],
            diagnostics={"survey_agent_mode": "service_fallback"},
        )
        return self.create_from_agent_output(task_id, run_id, output)

    def create_from_agent_output(self, task_id: str, run_id: str | None, output: SurveyAgentOutput) -> dict:
        Base.metadata.create_all(bind=self.db.get_bind())
        if not output.questions:
            return {
                "survey_id": None,
                "task_id": task_id,
                "run_id": run_id,
                "title": output.survey_title,
                "description": output.survey_description,
                "status": "skipped",
                "response_count": 0,
                "questions": [],
            }
        task = TaskService(self.db).get_task(task_id)
        survey = SurveyRecordRow(
            survey_id=f"survey_{uuid4().hex[:10]}",
            task_id=task_id,
            run_id=run_id,
            title=output.survey_title or f"{task.product_name}补充调研问卷",
            description=output.survey_description,
            invite_code=self._new_invite_code(),
            status="published",
            source_summary_json=json.dumps(
                {
                    "generated_from": "SurveyAgent",
                    "gap_count": len(output.questions),
                    "run_id": run_id,
                    "diagnostics": output.diagnostics,
                },
                ensure_ascii=False,
            ),
        )
        self.db.add(survey)
        for index, question in enumerate(output.questions[:MAX_GENERATED_QUESTIONS], start=1):
            self.db.add(
                SurveyQuestionRecordRow(
                    question_id=f"sq_{uuid4().hex[:10]}",
                    survey_id=survey.survey_id,
                    competitor=question.competitor,
                    dimension_id=question.dimension_id,
                    source_question_id=question.source_question_id,
                    source_gap_type=question.source_gap_type or "unknown_gap",
                    question_text=question.question_text,
                    question_type=question.question_type,
                    options_json=json.dumps(question.options or [], ensure_ascii=False),
                    required=question.required,
                    order_index=index,
                )
            )
        self.db.commit()
        return self.get_survey(survey.survey_id)

    def get_survey(self, survey_id: str) -> dict:
        survey = self.db.get(SurveyRecordRow, survey_id)
        if survey is None:
            raise KeyError("Survey not found")
        return self._survey_payload(survey, include_invite=True)

    def get_by_invite(self, invite_code: str) -> dict:
        survey = self._survey_by_invite(invite_code)
        if survey.status != "published":
            raise KeyError("Survey is not published")
        return self._survey_payload(survey, include_invite=False)

    def list_for_run(self, task_id: str, run_id: str) -> list[dict]:
        rows = (
            self.db.query(SurveyRecordRow)
            .filter_by(task_id=task_id, run_id=run_id)
            .order_by(SurveyRecordRow.created_at.desc())
            .all()
        )
        return [self._survey_payload(row, include_invite=True, include_questions=False) for row in rows]

    def submit_response(self, invite_code: str, answers: list[dict], metadata: dict[str, Any] | None = None) -> dict:
        survey = self._survey_by_invite(invite_code)
        if survey.status != "published":
            raise ValueError("Survey is not accepting responses.")
        questions = {
            row.question_id: row
            for row in self._question_rows(survey.survey_id)
        }
        if not questions:
            raise ValueError("Survey has no questions.")

        normalized_answers: list[tuple[SurveyQuestionRecordRow, dict, str]] = []
        for item in answers:
            question_id = str(item.get("question_id") or "")
            question = questions.get(question_id)
            if question is None:
                continue
            answer_text = self._answer_text(item.get("answer"))
            if question.required and not answer_text:
                raise ValueError(f"Question {question_id} is required.")
            normalized_answers.append((question, {"answer": item.get("answer")}, answer_text))

        missing_required = [
            question.question_id
            for question in questions.values()
            if question.required and question.question_id not in {item[0].question_id for item in normalized_answers}
        ]
        if missing_required:
            raise ValueError(f"Missing required answers: {', '.join(missing_required)}")

        response = SurveyResponseRecordRow(
            response_id=f"sr_{uuid4().hex[:10]}",
            survey_id=survey.survey_id,
            respondent_token=f"anon_{secrets.token_hex(6)}",
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            kb_ingested=False,
        )
        self.db.add(response)
        for question, answer_json, answer_text in normalized_answers:
            self.db.add(
                SurveyAnswerRecordRow(
                    answer_id=f"sa_{uuid4().hex[:10]}",
                    response_id=response.response_id,
                    survey_id=survey.survey_id,
                    question_id=question.question_id,
                    answer_json=json.dumps(answer_json, ensure_ascii=False),
                    answer_text=answer_text,
                )
            )
        self.db.commit()
        self.review_response_for_kb(response.response_id)
        self.ingest_response_to_kb(response.response_id)
        return self.get_response(response.response_id)

    def list_responses(self, survey_id: str) -> list[dict]:
        rows = (
            self.db.query(SurveyResponseRecordRow)
            .filter_by(survey_id=survey_id)
            .order_by(SurveyResponseRecordRow.submitted_at.desc())
            .all()
        )
        return [self._response_payload(row) for row in rows]

    def get_response(self, response_id: str) -> dict:
        row = self.db.get(SurveyResponseRecordRow, response_id)
        if row is None:
            raise KeyError("Survey response not found")
        return self._response_payload(row)

    def ingest_response_to_kb(self, response_id: str) -> dict[str, int]:
        Base.metadata.create_all(bind=self.db.get_bind())
        response = self.db.get(SurveyResponseRecordRow, response_id)
        if response is None:
            raise KeyError("Survey response not found")
        if response.kb_ingested:
            return {"ingested": 0, "skipped_duplicate": 1}
        survey = self.db.get(SurveyRecordRow, response.survey_id)
        if survey is None:
            raise KeyError("Survey not found")
        task = TaskService(self.db).get_task(survey.task_id)
        questions = {row.question_id: row for row in self._question_rows(survey.survey_id)}
        qa_result = self._response_qa_result(response)
        if not qa_result:
            qa_result = self.review_response_for_kb(response_id)
        accepted_answer_ids = set(qa_result.get("accepted_answer_ids") or [])
        if not accepted_answer_ids:
            response.kb_ingested = True
            self.db.commit()
            return {"ingested": 0, "skipped_duplicate": 0, "rejected_by_qa": 1}
        answers = (
            self.db.query(SurveyAnswerRecordRow)
            .filter_by(response_id=response_id)
            .all()
        )

        ingested = 0
        skipped_duplicate = 0
        for answer in answers:
            if answer.answer_id not in accepted_answer_ids:
                continue
            question = questions.get(answer.question_id)
            if question is None or not answer.answer_text.strip():
                continue
            text = self._knowledge_text(survey, question, answer)
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            duplicate = self.db.query(KnowledgeItemRecordRow).filter_by(content_hash=content_hash).first()
            if duplicate is not None:
                skipped_duplicate += 1
                continue
            item_id = f"kb_survey_{uuid4().hex[:10]}"
            now = datetime.utcnow()
            self.db.add(
                KnowledgeItemRecordRow(
                    item_id=item_id,
                    source_type="survey",
                    competitor=question.competitor,
                    industry=task.industry,
                    region=task.region,
                    title=question.question_text[:240],
                    text=text,
                    source_url=f"survey://{survey.survey_id}/{response.response_id}",
                    source_domain="survey",
                    source_quality="survey",
                    confidence=0.65,
                    relevance_score=0.7,
                    evidence_id=f"survey_{answer.answer_id}",
                    fact_id=question.source_question_id,
                    task_id=survey.task_id,
                    run_id=survey.run_id,
                    content_hash=content_hash,
                    created_at=now,
                    updated_at=now,
                )
            )
            metadata = {
                "source_type": "survey",
                "survey_id": survey.survey_id,
                "response_id": response.response_id,
                "question_id": question.question_id,
                "competitor": question.competitor,
                "dimension_id": question.dimension_id,
                "source_gap_type": question.source_gap_type,
                "survey_response_qa_status": qa_result.get("status"),
                "survey_response_qa_score": qa_result.get("score"),
                "requires_verification": True,
                "confidence": 0.65,
                "task_id": survey.task_id,
                "run_id": survey.run_id,
            }
            chunk_id = f"kb_chunk_{hashlib.sha1(f'{item_id}:{text}'.encode('utf-8')).hexdigest()[:12]}"
            embedding = self.embedding_service.embed_text(text)
            self.db.add(
                KnowledgeChunkRecordRow(
                    chunk_id=chunk_id,
                    item_id=item_id,
                    text=text,
                    embedding=json.dumps(embedding),
                    token_count=len(text.split()),
                    metadata_json=json.dumps({**metadata, "chunk_id": chunk_id}, ensure_ascii=False),
                )
            )
            ingested += 1
        response.kb_ingested = True
        self.db.commit()
        return {"ingested": ingested, "skipped_duplicate": skipped_duplicate}

    def review_response_for_kb(self, response_id: str) -> dict[str, Any]:
        response = self.db.get(SurveyResponseRecordRow, response_id)
        if response is None:
            raise KeyError("Survey response not found")
        survey = self.db.get(SurveyRecordRow, response.survey_id)
        if survey is None:
            raise KeyError("Survey not found")
        questions = {row.question_id: row for row in self._question_rows(survey.survey_id)}
        answers = self.db.query(SurveyAnswerRecordRow).filter_by(response_id=response_id).all()
        answer_payloads = []
        for answer in answers:
            question = questions.get(answer.question_id)
            answer_payloads.append(
                {
                    "answer_id": answer.answer_id,
                    "question_id": answer.question_id,
                    "question_text": question.question_text if question else "",
                    "competitor": question.competitor if question else None,
                    "dimension_id": question.dimension_id if question else None,
                    "source_gap_type": question.source_gap_type if question else None,
                    "answer_text": answer.answer_text,
                }
            )
        qa_result = self.response_qa_agent.run(
            task_id=survey.task_id,
            run_id=survey.run_id,
            survey={
                "survey_id": survey.survey_id,
                "title": survey.title,
                "description": survey.description,
            },
            answers=answer_payloads,
        )
        metadata = self._response_metadata(response)
        metadata["survey_response_qa"] = qa_result
        response.metadata_json = json.dumps(metadata, ensure_ascii=False)
        self.db.commit()
        return qa_result

    def _workflow_summary(self, task_id: str, run_id: str) -> dict:
        traces = TraceService(self.db).list_for_task(task_id, run_id=run_id)
        for trace in reversed(traces):
            if trace.agent_name != "WorkflowEngine":
                continue
            try:
                payload = json.loads(trace.output_summary)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise KeyError("Workflow summary not found")

    @staticmethod
    def _gap_questions(summary: dict) -> list[dict]:
        output = summary.get("evidence_analyst_output") or {}
        results = output.get("question_results") or []
        gaps: list[dict] = []
        for group in results:
            competitor = group.get("competitor")
            dimension_id = group.get("dimension_id")
            dimension_goal = group.get("dimension_goal") or dimension_id or "当前维度"
            for answer in group.get("question_answers") or []:
                status = answer.get("answer_status")
                if status not in {"not_found", "partial"}:
                    continue
                source_question = str(answer.get("question") or "")
                prompt = (
                    f"关于 {competitor or '该竞品'} 在「{dimension_goal}」上的情况，"
                    f"请补充以下未充分解决的问题：{source_question}。"
                    "请尽量提供具体事实、时间、使用场景、依据，以及可公开查看的来源链接或材料。"
                )
                gaps.append(
                    {
                        "competitor": competitor,
                        "dimension_id": dimension_id,
                        "source_question_id": answer.get("question_id"),
                        "source_gap_type": status,
                        "question_text": prompt,
                        "question_type": "short_text",
                    }
                )
        return gaps

    @staticmethod
    def _fallback_questions(summary: dict, competitors: list[str]) -> list[dict]:
        selected_dimensions = summary.get("selected_dimensions") or ["通用"]
        output: list[dict] = []
        for competitor in competitors:
            for dimension_id in selected_dimensions[:2]:
                output.append(
                    {
                        "competitor": competitor,
                        "dimension_id": dimension_id,
                        "source_gap_type": "general_follow_up",
                        "question_text": (
                            f"关于 {competitor} 在「{dimension_id}」维度上的表现，你能补充哪些一手信息？"
                            "请提供可观察事实、使用经历、时间、依据或可公开来源。"
                        ),
                        "question_type": "short_text",
                    }
                )
        return output[:MAX_GENERATED_QUESTIONS]

    def _new_invite_code(self) -> str:
        for _ in range(20):
            code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))
            exists = self.db.query(SurveyRecordRow).filter_by(invite_code=code).first()
            if exists is None:
                return code
        return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))

    def _survey_by_invite(self, invite_code: str) -> SurveyRecordRow:
        row = self.db.query(SurveyRecordRow).filter_by(invite_code=invite_code.strip().upper()).first()
        if row is None:
            raise KeyError("Survey invite not found")
        return row

    def _question_rows(self, survey_id: str) -> list[SurveyQuestionRecordRow]:
        return (
            self.db.query(SurveyQuestionRecordRow)
            .filter_by(survey_id=survey_id)
            .order_by(SurveyQuestionRecordRow.order_index.asc())
            .all()
        )

    def _survey_payload(
        self,
        survey: SurveyRecordRow,
        *,
        include_invite: bool,
        include_questions: bool = True,
    ) -> dict:
        payload = {
            "survey_id": survey.survey_id,
            "task_id": survey.task_id,
            "run_id": survey.run_id,
            "title": survey.title,
            "description": survey.description,
            "status": survey.status,
            "created_at": survey.created_at.isoformat(),
            "updated_at": survey.updated_at.isoformat(),
            "response_count": self.db.query(SurveyResponseRecordRow).filter_by(survey_id=survey.survey_id).count(),
        }
        if include_invite:
            payload["invite_code"] = survey.invite_code
        if include_questions:
            payload["questions"] = [self._question_payload(row) for row in self._question_rows(survey.survey_id)]
        return payload

    @staticmethod
    def _question_payload(row: SurveyQuestionRecordRow) -> dict:
        try:
            options = json.loads(row.options_json)
        except json.JSONDecodeError:
            options = []
        return {
            "question_id": row.question_id,
            "survey_id": row.survey_id,
            "competitor": row.competitor,
            "dimension_id": row.dimension_id,
            "source_question_id": row.source_question_id,
            "source_gap_type": row.source_gap_type,
            "question_text": row.question_text,
            "question_type": row.question_type,
            "options": options,
            "required": row.required,
            "order_index": row.order_index,
        }

    def _response_payload(self, response: SurveyResponseRecordRow) -> dict:
        answers = self.db.query(SurveyAnswerRecordRow).filter_by(response_id=response.response_id).all()
        return {
            "response_id": response.response_id,
            "survey_id": response.survey_id,
            "respondent_token": response.respondent_token,
            "submitted_at": response.submitted_at.isoformat(),
            "kb_ingested": response.kb_ingested,
            "qa_result": self._response_qa_result(response),
            "answers": [
                {
                    "answer_id": row.answer_id,
                    "question_id": row.question_id,
                    "answer_text": row.answer_text,
                }
                for row in answers
            ],
        }

    @staticmethod
    def _answer_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _response_metadata(response: SurveyResponseRecordRow) -> dict[str, Any]:
        try:
            payload = json.loads(response.metadata_json)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _response_qa_result(self, response: SurveyResponseRecordRow) -> dict[str, Any]:
        metadata = self._response_metadata(response)
        qa_result = metadata.get("survey_response_qa")
        return qa_result if isinstance(qa_result, dict) else {}

    @staticmethod
    def _knowledge_text(
        survey: SurveyRecordRow,
        question: SurveyQuestionRecordRow,
        answer: SurveyAnswerRecordRow,
    ) -> str:
        return "\n".join(
            [
                "[Survey Response]",
                f"Survey: {survey.survey_id}",
                f"Competitor: {question.competitor or '-'}",
                f"Dimension: {question.dimension_id or '-'}",
                f"Gap type: {question.source_gap_type}",
                f"Question: {question.question_text}",
                f"Answer: {answer.answer_text}",
                "Note: This is survey evidence and requires verification before strong factual claims.",
            ]
        )
