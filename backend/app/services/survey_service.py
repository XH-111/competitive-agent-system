import csv
import json
import re
from datetime import datetime
from io import StringIO
from typing import Any

from sqlalchemy import literal_column
from sqlalchemy.orm import Session

from app.agents.pain_point_research_agent import PainPointResearchAgent
from app.agents.questionnaire_agent import QuestionnaireAgent
from app.agents.survey_analysis_agent import SurveyAnalysisAgent
from app.db_models import SurveyAnalysisRecordRow, SurveyRecordRow, SurveyResponseBatchRecordRow, TaskRecord
from app.schemas import Evidence, PlannerDownstreamGuidance, PlannerSurveyInput, SurveyEvidence
from app.schemas.survey import (
    QuestionnaireLaunchContract,
    QuestionnaireLaunchRecommendation,
    Survey,
    SurveyAddQuestionRequest,
    SurveyAnalysis,
    SurveyGenerateRequest,
    SurveyQuestionCreate,
    SurveyReorderRequest,
    SurveyResponseBatch,
    SurveyReviseRequest,
    SurveyRevisionResponse,
    SurveyTopicGenerateRequest,
    SurveyUpdateRequest,
    SurveyUploadResponse,
)
from app.services.evidence_service import EvidenceService
from app.services.feedback_ingestion_service import FeedbackIngestionService
from app.services.report_service import ReportService
from app.services.survey_demo_data import (
    PHONE_DEMO_RESPONSE_CSV,
    PHONE_DEMO_SURVEY_PAYLOAD,
    build_phone_demo_analysis,
    build_phone_demo_response_csv_for_survey,
    normalize_phone_demo_response_csv_for_survey,
)
from app.services.survey_llm_client import SurveyLLMConfigurationError
from app.services.task_run_service import TaskRunService
from app.utils.csv_exporter import export_survey_response_template_csv, export_survey_template_csv, response_field_name
from app.utils.csv_parser import (
    SurveyCsvFormatError,
    SurveyCsvValidationError,
    infer_survey_from_csv,
    parse_survey_response_csv,
)


class SurveyService:
    def __init__(
        self,
        db: Session,
        questionnaire_agent: QuestionnaireAgent | None = None,
        analysis_agent: SurveyAnalysisAgent | None = None,
        pain_point_agent: PainPointResearchAgent | None = None,
    ):
        self.db = db
        self.questionnaire_agent = questionnaire_agent or QuestionnaireAgent()
        self.analysis_agent = analysis_agent or SurveyAnalysisAgent()
        self.pain_point_agent = pain_point_agent or PainPointResearchAgent()
        self.feedback_ingestion = FeedbackIngestionService()

    def latest_for_run(self, task_id: str, run_id: str) -> Survey | None:
        row = (
            self.db.query(SurveyRecordRow)
            .filter_by(task_id=task_id, run_id=run_id)
            .order_by(SurveyRecordRow.updated_at.desc(), literal_column("rowid").desc())
            .first()
        )
        return self._row_to_survey(row) if row else None

    def latest_for_task(self, task_id: str) -> Survey | None:
        row = (
            self.db.query(SurveyRecordRow)
            .filter_by(task_id=task_id)
            .order_by(SurveyRecordRow.updated_at.desc(), literal_column("rowid").desc())
            .first()
        )
        return self._row_to_survey(row) if row else None

    def planner_context_for_task(self, task_id: str) -> dict[str, Any]:
        task = self.db.get(TaskRecord, task_id)
        if task is None:
            raise KeyError(task_id)
        launch_contract = self.load_questionnaire_launch_contract(task.task_id)
        return {
            "task": self._task_payload(task),
            "planner_context": dict(launch_contract.planner_context),
            "launch_contract": launch_contract.model_dump(mode="json"),
        }

    def generate(self, task_id: str, run_id: str, request: SurveyGenerateRequest) -> Survey:
        task = self.db.get(TaskRecord, task_id)
        if task is None:
            raise KeyError(task_id)
        context = self._context_from_request(task, request)
        context["pain_points"] = self._pain_points_for_context(task, context.get("planner_context") or {}, context)
        try:
            raw = self.questionnaire_agent.generate_survey(context)
        except (SurveyLLMConfigurationError, RuntimeError, ValueError):
            raw = self._fallback_survey_payload(context)
        raw.setdefault("pain_points", context.get("pain_points", []))
        raw.setdefault("planner_snapshot", context.get("planner_context", {}))
        raw.setdefault("report_context_snapshot", context.get("report_context", {}))
        survey = self._survey_from_llm_payload(task_id, run_id, raw, context["claims_json"], status="draft", version=1)
        return self.save_survey(survey)

    def generate_for_task(self, task_id: str, force_generate: bool = False) -> Survey:
        task = self.db.get(TaskRecord, task_id)
        if task is None:
            raise KeyError(task_id)
        run_id = self._latest_run_id_or_manual(task_id)
        launch_contract = self.load_questionnaire_launch_contract(task_id, run_id=run_id)
        planner_context = dict(launch_contract.planner_context)
        report_context = dict(launch_contract.report_context)
        survey_inputs = planner_context.get("survey_inputs") or {}
        survey_needed = bool(planner_context.get("survey_needed"))
        survey_recommended = bool(planner_context.get("survey_recommended", False))
        if not (survey_needed or survey_recommended or force_generate):
            raise ValueError("Planner 未建议生成问卷；用户可使用 force_generate=true 手动生成。")
        context = self._context_from_task_and_planner(task, planner_context, report_context)
        context["force_generate"] = force_generate
        context["pain_points"] = self._pain_points_for_context(task, planner_context, report_context)
        try:
            raw = self.questionnaire_agent.generate_survey(context)
        except (SurveyLLMConfigurationError, RuntimeError, ValueError):
            raw = self._fallback_survey_payload(context)
        raw.setdefault("pain_points", context.get("pain_points", []))
        raw.setdefault("planner_snapshot", planner_context)
        raw.setdefault("report_context_snapshot", report_context)
        survey = self._survey_from_llm_payload(
            task_id,
            run_id,
            raw,
            report_context.get("claims_json", []),
            status="draft",
            version=1,
        )
        survey.metadata.update(
            {
                "source": "planner_output",
                "launch_contract_source": "questionnaire_follow_up_contract",
                "planner_context": planner_context,
                "launch_contract": launch_contract.model_dump(mode="json"),
                "survey_needed": survey_needed,
                "survey_recommended": survey_recommended,
                "survey_objective": planner_context.get("survey_objective"),
                "survey_inputs": survey_inputs,
            }
        )
        return self.save_survey(survey)

    def load_planner_context_for_survey(self, task_id: str) -> dict[str, Any]:
        contract = self.load_questionnaire_launch_contract(task_id)
        planner_context = dict(contract.planner_context)
        diagnostics = dict(planner_context.get("diagnostics") or {})
        diagnostics.setdefault("loader", "load_planner_context_for_survey")
        diagnostics.setdefault("launch_mode", contract.launch_mode)
        diagnostics.setdefault("load_path", list(contract.diagnostics.get("load_path") or []))
        planner_context["diagnostics"] = diagnostics
        return planner_context

    def load_questionnaire_launch_contract(self, task_id: str, run_id: str | None = None) -> QuestionnaireLaunchContract:
        task = self.db.get(TaskRecord, task_id)
        if task is None:
            raise KeyError(task_id)
        resolved_run_id = run_id or self._latest_run_id_or_manual(task_id)
        report_context = self._report_context_for_task_run(task_id, resolved_run_id)
        planner_context, diagnostics = self._planner_context_from_sources(task, resolved_run_id, report_context)
        recommendation = self._recommendation_from_context(planner_context, report_context)
        diagnostics.setdefault("loader", "load_questionnaire_launch_contract")
        diagnostics.setdefault("launch_mode", "follow_up_sidecar")
        return QuestionnaireLaunchContract(
            task_id=task_id,
            run_id=resolved_run_id,
            recommendation=recommendation,
            planner_context=planner_context,
            report_context=report_context,
            diagnostics=diagnostics,
        )

    def generate_from_topic(self, request: SurveyTopicGenerateRequest) -> Survey:
        context = {
            "topic": request.topic,
            "product_name": request.topic,
            "competitors": [],
            "industry": "topic_survey",
            "region": "",
            "report_markdown": "",
            "claims_json": [],
            "uncertain_findings": [],
            "target_respondents": request.target_respondents or "该话题相关目标用户",
            "research_goal": request.research_goal or f"围绕“{request.topic}”收集可统计的用户反馈。",
            "requirements": request.requirements,
            "user_requirements": request.requirements,
            "question_count": request.question_count,
            "planner_context": {
                "survey_inputs": {
                    "objective": request.research_goal or f"围绕“{request.topic}”收集可统计的用户反馈。",
                    "respondent_type": request.target_respondents or "该话题相关目标用户",
                    "question_themes": ["认知与需求", "使用体验", "选择因素", "付费意愿", "开放反馈"],
                    "hypotheses": [],
                    "metadata": {"source": "topic_generation"},
                }
            },
        }
        context["pain_points"] = self._topic_pain_points(request.topic)
        try:
            raw = self.questionnaire_agent.generate_from_topic(context)
        except (AttributeError, SurveyLLMConfigurationError, RuntimeError, ValueError):
            raw = self._fallback_survey_payload(context)
            raw["survey_title"] = f"{request.topic} 调研问卷"
            raw["survey_description"] = "根据用户输入话题生成的独立问卷，可导出答卷模板并上传 CSV 分析。"
            raw["target_respondents"] = context["target_respondents"]
            raw["research_goal"] = context["research_goal"]
            raw["metadata"] = {**dict(raw.get("metadata") or {}), "source": "topic_generation", "topic": request.topic}
        raw.setdefault("pain_points", context.get("pain_points", []))
        survey = self._survey_from_llm_payload(
            "manual_topic",
            "manual_topic",
            raw,
            [],
            status="draft",
            version=1,
        )
        survey.metadata.update(
            {
                "source": "topic_generation",
                "topic": request.topic,
                "requirements": request.requirements,
            }
        )
        return self.save_survey(survey)

    def revise(self, survey_id: str, request: SurveyReviseRequest) -> SurveyRevisionResponse:
        survey = self.get_survey(survey_id)
        try:
            raw = self.questionnaire_agent.revise_survey(
                survey.model_dump(mode="json"),
                request.revision_request,
                request.report_context,
            )
        except (SurveyLLMConfigurationError, RuntimeError, ValueError):
            if not self._is_phone_demo_survey(survey):
                raw = self._revise_generic_payload(survey, request.revision_request)
            else:
                raw = self._revise_phone_demo_payload(survey, request.revision_request)
        if self._is_phone_demo_survey(survey) and not raw:
            raw = self._revise_phone_demo_payload(survey, request.revision_request)
        revised = self._survey_from_llm_payload(
            survey.task_id,
            survey.run_id,
            raw,
            [{"claim_id": claim_id} for claim_id in survey.source_claim_ids],
            status="revised",
            version=survey.version + 1,
            survey_id=survey.survey_id,
            created_at=survey.created_at,
        )
        revised = revised.model_copy(
            update={
                "pain_points": revised.pain_points or survey.pain_points,
                "planner_snapshot": survey.planner_snapshot,
                "report_context_snapshot": survey.report_context_snapshot,
            }
        )
        revised.metadata.update(survey.metadata)
        revised.metadata["refine_instruction"] = request.revision_request
        saved = self.save_survey(revised)
        return SurveyRevisionResponse(
            revision_summary=str(raw.get("revision_summary") or "问卷已按要求修改。"),
            survey=saved,
            removed_questions=list(raw.get("removed_questions") or []),
            added_questions=list(raw.get("added_questions") or []),
        )

    def refine_for_task(self, task_id: str, survey_id: str, instruction: str) -> SurveyRevisionResponse:
        survey = self.get_survey(survey_id)
        if survey.task_id != task_id:
            raise KeyError(survey_id)
        return self.revise(survey_id, SurveyReviseRequest(revision_request=instruction))

    def update_survey(self, survey_id: str, request: SurveyUpdateRequest) -> Survey:
        survey = self.get_survey(survey_id)
        question_updates = {question.question_id: question for question in request.questions or []}
        questions = []
        for question in survey.questions:
            payload = question.model_dump(mode="json")
            update = question_updates.get(question.question_id)
            if update:
                payload.update({key: value for key, value in update.model_dump(exclude_none=True).items() if key != "question_id"})
            questions.append(_prepare_question_for_save(payload, len(questions) + 1))
        if request.questions:
            questions = sorted(questions, key=lambda item: int(item.get("order") or 0))
            questions = [_prepare_question_for_save({**question, "order": index}, index) for index, question in enumerate(questions, start=1)]
        updated = self._replace_survey_content(
            survey,
            title=request.title,
            description=request.description,
            target_respondents=request.target_respondents,
            research_goal=request.research_goal,
            expected_analysis_dimensions=request.expected_analysis_dimensions,
            questions=questions,
            metadata_patch={"last_manual_edit": datetime.utcnow().isoformat()},
        )
        return self.save_survey(updated)

    def add_question(self, survey_id: str, request: SurveyAddQuestionRequest) -> Survey:
        survey = self.get_survey(survey_id)
        new_question = self._question_create_payload(request.question, len(survey.questions) + 1)
        questions = [question.model_dump(mode="json") for question in survey.questions] + [new_question]
        updated = self._replace_survey_content(
            survey,
            questions=[_prepare_question_for_save(question, index) for index, question in enumerate(questions, start=1)],
            metadata_patch={"last_manual_edit": datetime.utcnow().isoformat()},
        )
        return self.save_survey(updated)

    def delete_question(self, survey_id: str, question_id: str) -> Survey:
        survey = self.get_survey(survey_id)
        questions = [question.model_dump(mode="json") for question in survey.questions if question.question_id != question_id]
        if len(questions) == len(survey.questions):
            raise KeyError(question_id)
        updated = self._replace_survey_content(
            survey,
            questions=[_prepare_question_for_save(question, index) for index, question in enumerate(questions, start=1)],
            metadata_patch={"last_manual_edit": datetime.utcnow().isoformat()},
        )
        return self.save_survey(updated)

    def reorder_questions(self, survey_id: str, request: SurveyReorderRequest) -> Survey:
        survey = self.get_survey(survey_id)
        by_id = {question.question_id: question.model_dump(mode="json") for question in survey.questions}
        if set(request.question_ids) != set(by_id):
            raise ValueError("question_ids must contain every existing question exactly once")
        questions = [
            _prepare_question_for_save({**by_id[question_id], "order": index}, index)
            for index, question_id in enumerate(request.question_ids, start=1)
        ]
        updated = self._replace_survey_content(
            survey,
            questions=questions,
            metadata_patch={"last_manual_edit": datetime.utcnow().isoformat()},
        )
        return self.save_survey(updated)

    def get_survey(self, survey_id: str) -> Survey:
        row = self.db.get(SurveyRecordRow, survey_id)
        if row is None:
            raise KeyError(survey_id)
        return self._row_to_survey(row)

    def save_survey(self, survey: Survey) -> Survey:
        survey = survey.model_copy(update={"updated_at": datetime.utcnow()})
        row = self.db.get(SurveyRecordRow, survey.survey_id)
        if row is None:
            row = SurveyRecordRow(survey_id=survey.survey_id, created_at=survey.created_at)
            self.db.add(row)
        row.task_id = survey.task_id
        row.run_id = survey.run_id
        row.status = survey.status
        row.version = survey.version
        row.payload_json = survey.model_dump_json()
        row.created_at = survey.created_at
        row.updated_at = survey.updated_at
        self.db.commit()
        return survey

    def export_csv(self, survey_id: str) -> str:
        survey = self.get_survey(survey_id)
        if survey.status not in {"exported", "responses_uploaded", "analyzed"}:
            self.save_survey(survey.model_copy(update={"status": "exported"}))
        return export_survey_template_csv(survey)

    def export_response_csv(self, survey_id: str) -> str:
        survey = self.get_survey(survey_id)
        if survey.status not in {"exported", "responses_uploaded", "analyzed"}:
            self.save_survey(survey.model_copy(update={"status": "exported"}))
        return export_survey_response_template_csv(survey)

    def export_response_csv_for_task(self, task_id: str) -> str:
        survey = self.latest_for_task(task_id)
        if survey is None:
            survey = self.generate_for_task(task_id)
        if survey.status not in {"exported", "responses_uploaded", "analyzed"}:
            self.save_survey(survey.model_copy(update={"status": "exported"}))
        return export_survey_response_template_csv(survey)

    def export_demo_response_csv(self, survey_id: str) -> str:
        survey = self.get_survey(survey_id)
        if self._is_phone_demo_survey(survey):
            return build_phone_demo_response_csv_for_survey(survey)
        output = StringIO()
        writer = csv.writer(output)
        field_names = [question.field_name for question in survey.questions]
        writer.writerow(field_names)
        for row_index in range(3):
            writer.writerow([self._sample_answer_for_question(question, row_index) for question in survey.questions])
        return output.getvalue()

    def upload_and_analyze(self, survey_id: str, file_name: str, content: str | bytes) -> SurveyUploadResponse:
        survey = self.get_survey(survey_id)
        raw_content = content.encode("utf-8") if isinstance(content, str) else content
        ingestion = self.feedback_ingestion.ingest(file_name, raw_content, survey)
        if ingestion.source_type != "csv":
            raw_stats = self._raw_stats_from_ingestion(survey, ingestion)
            return self._save_upload_analysis(survey, file_name, raw_stats, ingestion=ingestion)
        content = raw_content.decode("utf-8-sig")
        content = self._normalize_response_template_headers(survey, content)
        try:
            raw_stats = parse_survey_response_csv(content, survey)
        except SurveyCsvValidationError:
            if not self._is_phone_demo_survey(survey):
                return self.upload_ad_hoc_and_analyze(survey.task_id, survey.run_id, file_name, content)
            try:
                content = normalize_phone_demo_response_csv_for_survey(survey, content)
                raw_stats = parse_survey_response_csv(content, survey)
            except SurveyCsvValidationError:
                return self.upload_ad_hoc_and_analyze(survey.task_id, survey.run_id, file_name, content)
        return self._save_upload_analysis(survey, file_name, raw_stats, ingestion=ingestion)

    def upload_ad_hoc_and_analyze(self, task_id: str, run_id: str, file_name: str, content: str | bytes) -> SurveyUploadResponse:
        task = self.db.get(TaskRecord, task_id)
        product_name = task.product_name if task else "自定义问卷"
        raw_content = content.encode("utf-8") if isinstance(content, str) else content
        ingestion = self.feedback_ingestion.ingest(file_name, raw_content)
        content = _rows_to_csv(ingestion.rows, ingestion.columns) if ingestion.rows else raw_content.decode("utf-8-sig", errors="replace")
        survey = infer_survey_from_csv(
            content,
            task_id=task_id,
            run_id=run_id,
            title=f"{product_name} 自定义上传问卷分析",
            description="系统根据上传 CSV 表头自动识别题目，不要求与已生成问卷字段一致。",
            target_respondents="用户上传 CSV 样本",
            research_goal=f"分析 {product_name} 相关自定义问卷反馈。",
        )
        survey = self.save_survey(survey)
        raw_stats = self._raw_stats_from_ingestion(survey, ingestion) if ingestion.source_type != "csv" else parse_survey_response_csv(content, survey)
        return self._save_upload_analysis(survey, file_name, raw_stats, force_generic=True, ingestion=ingestion)

    def import_csv_for_task(self, task_id: str, file_name: str, content: str | bytes) -> SurveyUploadResponse:
        survey = self.latest_for_task(task_id)
        if survey is None:
            return self.upload_ad_hoc_and_analyze(task_id, self._latest_run_id_or_manual(task_id), file_name, content)
        if isinstance(content, bytes) and not file_name.lower().endswith(".csv"):
            return self.upload_and_analyze(survey.survey_id, file_name, content)
        if isinstance(content, bytes):
            content = content.decode("utf-8-sig")
        normalized_content = self._normalize_response_template_headers(survey, content)
        return self.upload_and_analyze(survey.survey_id, file_name, normalized_content)

    def latest_analysis_for_task(self, task_id: str) -> SurveyAnalysis | None:
        survey = self.latest_for_task(task_id)
        if survey is None or survey.status != "analyzed":
            return None
        try:
            return self.latest_analysis(survey.survey_id)
        except KeyError:
            return None

    def create_phone_demo(self, task_id: str, run_id: str) -> SurveyUploadResponse:
        task = self.db.get(TaskRecord, task_id)
        if task is None:
            raise KeyError(task_id)
        report_claims = self._report_context_for_task_run(task_id, run_id).get("claims_json", [])
        survey = self._survey_from_llm_payload(
            task_id,
            run_id,
            PHONE_DEMO_SURVEY_PAYLOAD,
            report_claims,
            status="draft",
            version=1,
        )
        survey = self.save_survey(survey)
        raw_stats = parse_survey_response_csv(PHONE_DEMO_RESPONSE_CSV, survey)
        batch = SurveyResponseBatch(
            survey_id=survey.survey_id,
            file_name="sample_phone_survey_responses.csv",
            sample_size=raw_stats["sample_size"],
            valid_count=raw_stats["valid_count"],
            invalid_count=raw_stats["invalid_count"],
            parse_status="success",
            raw_stats=raw_stats,
        )
        self.save_batch(batch)
        analysis = build_phone_demo_analysis(survey, batch, raw_stats)
        self.save_analysis(analysis)
        saved_survey = self.save_survey(survey.model_copy(update={"status": "analyzed"}))
        return SurveyUploadResponse(
            batch_id=batch.batch_id,
            analysis_id=analysis.analysis_id,
            sample_size=batch.sample_size,
            valid_count=batch.valid_count,
            invalid_count=batch.invalid_count,
            analysis_summary=analysis.dashboard_summary,
            raw_stats=raw_stats,
            analysis=analysis,
            survey=saved_survey,
        )

    def _save_upload_analysis(
        self,
        survey: Survey,
        file_name: str,
        raw_stats: dict[str, Any],
        *,
        force_generic: bool = False,
        ingestion: Any | None = None,
    ) -> SurveyUploadResponse:
        batch = SurveyResponseBatch(
            survey_id=survey.survey_id,
            file_name=file_name,
            sample_size=raw_stats["sample_size"],
            valid_count=raw_stats["valid_count"],
            invalid_count=raw_stats["invalid_count"],
            parse_status="success",
            raw_stats=raw_stats,
            metadata={
                "source_type": getattr(ingestion, "source_type", "csv"),
                "file_name": file_name,
                "parse_warnings": getattr(ingestion, "parse_warnings", []),
                "question_mapping": getattr(ingestion, "question_mapping", {}),
            },
        )
        self.save_batch(batch)
        analysis = self._analyze_or_fallback(survey, batch, raw_stats, force_generic=force_generic)
        self.save_analysis(analysis)
        saved_survey = self.save_survey(survey.model_copy(update={"status": "analyzed"}))
        survey_evidence = survey_analysis_to_survey_evidence(analysis, saved_survey, saved_survey.task_id, saved_survey.run_id)
        evidence = survey_evidence_to_evidence(survey_evidence)
        EvidenceService(self.db).save_many(saved_survey.task_id, [evidence], run_id=saved_survey.run_id)
        return SurveyUploadResponse(
            batch_id=batch.batch_id,
            analysis_id=analysis.analysis_id,
            sample_size=batch.sample_size,
            valid_count=batch.valid_count,
            invalid_count=batch.invalid_count,
            analysis_summary=analysis.dashboard_summary,
            raw_stats=raw_stats,
            analysis=analysis,
            survey=saved_survey,
            survey_evidence=survey_evidence.model_dump(mode="json"),
            evidence=evidence.model_dump(mode="json"),
            question_summaries=analysis.question_summaries,
            hypothesis_findings=analysis.hypothesis_findings,
            overall_summary=analysis.dashboard_summary,
            limitations=analysis.limitations,
        )

    def _analyze_or_fallback(
        self,
        survey: Survey,
        batch: SurveyResponseBatch,
        raw_stats: dict[str, Any],
        *,
        force_generic: bool = False,
    ) -> SurveyAnalysis:
        report_context = self._report_context(survey)
        if force_generic:
            return self._build_generic_analysis(survey, batch, raw_stats, report_context)
        try:
            raw_analysis = self.analysis_agent.analyze(
                survey.model_dump(mode="json"),
                raw_stats,
                report_context,
            )
            return SurveyAnalysis(
                survey_id=survey.survey_id,
                batch_id=batch.batch_id,
                summary=str(raw_analysis.get("dashboard_summary") or raw_analysis.get("summary") or ""),
                executive_summary=str(raw_analysis.get("executive_summary") or raw_analysis.get("dashboard_summary") or ""),
                sample_summary=dict(raw_analysis.get("sample_summary") or {}),
                key_findings=list(raw_analysis.get("key_findings") or []),
                question_level_analysis=list(raw_analysis.get("question_level_analysis") or []),
                claim_updates=list(raw_analysis.get("claim_updates") or []),
                user_pain_points=list(raw_analysis.get("user_pain_points") or []),
                willingness_to_pay=raw_analysis.get("willingness_to_pay"),
                switching_risk=raw_analysis.get("switching_risk"),
                survey_evidence=dict(raw_analysis.get("survey_evidence") or {}),
                question_summaries=_question_summaries_from_stats(survey, raw_stats),
                hypothesis_findings=_hypothesis_findings(survey, raw_stats),
                pain_point_validation=list(raw_analysis.get("pain_point_validation") or []),
                pain_point_ranking=list(raw_analysis.get("pain_point_ranking") or []),
                claim_validation_matrix=list(raw_analysis.get("claim_validation_matrix") or []),
                segment_insights=list(raw_analysis.get("segment_insights") or []),
                competitor_switching_analysis=dict(raw_analysis.get("competitor_switching_analysis") or {}),
                pricing_and_wtp_analysis=dict(raw_analysis.get("pricing_and_wtp_analysis") or {}),
                recommended_report_revisions=list(raw_analysis.get("recommended_report_revisions") or []),
                next_research_questions=list(raw_analysis.get("next_research_questions") or []),
                limitations=list(raw_analysis.get("limitations") or _default_limitations(batch.valid_count)),
                dashboard_summary=str(raw_analysis.get("dashboard_summary") or "问卷分析已完成。"),
            )
        except (SurveyLLMConfigurationError, RuntimeError, ValueError):
            if self._is_phone_demo_survey(survey):
                return build_phone_demo_analysis(survey, batch, raw_stats)
            return self._build_generic_analysis(survey, batch, raw_stats, report_context)

    def _build_generic_analysis(
        self,
        survey: Survey,
        batch: SurveyResponseBatch,
        raw_stats: dict[str, Any],
        report_context: dict[str, Any],
    ) -> SurveyAnalysis:
        stats_by_field = _stats_by_field(raw_stats)
        confidence = 0.72 if batch.valid_count >= 30 else 0.6 if batch.valid_count >= 10 else 0.45
        question_level_analysis = []
        key_findings = []
        for question in survey.questions:
            question_stats = stats_by_field.get(question.field_name)
            if not question_stats:
                continue
            summary, notable_stats = _summarize_question_stats(question_stats)
            question_level_analysis.append(
                {
                    "question_id": question.question_id,
                    "field_name": question.field_name,
                    "summary": summary,
                    "notable_stats": notable_stats,
                }
            )
            finding = _finding_from_question(question.question_id, question.field_name, summary, confidence)
            if finding and len(key_findings) < 5:
                key_findings.append(finding)

        user_pain_points = _extract_pain_points(stats_by_field)
        pain_validation = _pain_point_validation(survey, stats_by_field, batch.valid_count)
        pain_ranking = sorted(
            [
                {
                    "pain_id": item["pain_id"],
                    "pain_point": item["pain_point"],
                    "priority_score": item["priority_score"],
                    "validation_result": item["validation_result"],
                }
                for item in pain_validation
            ],
            key=lambda item: item["priority_score"],
            reverse=True,
        )
        claim_matrix = _claim_validation_matrix(survey, pain_validation, report_context)
        recommended_revisions = _recommended_report_revisions(claim_matrix, pain_validation)
        willingness_to_pay = _summarize_focus_area(
            survey,
            stats_by_field,
            ["预算", "价格", "付费", "溢价", "pay", "price", "budget", "premium"],
            "暂未在自定义问卷中识别到明显的预算或付费意愿字段。",
        )
        switching_risk = _summarize_focus_area(
            survey,
            stats_by_field,
            ["换机", "切换", "转向", "品牌", "switch", "change", "brand"],
            "暂未在自定义问卷中识别到明显的品牌切换字段。",
        )
        dashboard_summary = (
            f"已基于上传 CSV 完成自定义问卷分析：共 {batch.sample_size} 条样本、"
            f"{batch.valid_count} 条有效记录、{len(survey.questions)} 个字段。"
        )
        if key_findings:
            dashboard_summary += f" 主要信号：{key_findings[0]['finding']}"
        if pain_validation:
            dashboard_summary += f" 痛点验证最高优先级：{pain_ranking[0]['pain_point']}。"
        executive_summary = dashboard_summary

        return SurveyAnalysis(
            survey_id=survey.survey_id,
            batch_id=batch.batch_id,
            summary=dashboard_summary,
            executive_summary=executive_summary,
            sample_summary={
                "sample_size": batch.sample_size,
                "valid_count": batch.valid_count,
                "invalid_count": batch.invalid_count,
                "limitations": _default_limitations(batch.valid_count),
            },
            key_findings=key_findings,
            question_level_analysis=question_level_analysis,
            claim_updates=_generic_claim_updates(report_context),
            user_pain_points=user_pain_points,
            willingness_to_pay=willingness_to_pay,
            switching_risk=switching_risk,
            survey_evidence={
                "snippet": dashboard_summary,
                "confidence": confidence,
                "metadata": {
                    "sample_size": batch.sample_size,
                    "valid_count": batch.valid_count,
                    "source_type": "survey",
                    "analysis_mode": "pain_point_validation" if pain_validation else ("ad_hoc_csv" if raw_stats.get("source_type") in {None, "csv"} else "ad_hoc_feedback"),
                    "csv_columns": raw_stats.get("csv_columns", []),
                },
            },
            question_summaries=_question_summaries_from_stats(survey, raw_stats),
            hypothesis_findings=_hypothesis_findings(survey, raw_stats),
            pain_point_validation=pain_validation,
            pain_point_ranking=pain_ranking,
            claim_validation_matrix=claim_matrix,
            segment_insights=[],
            competitor_switching_analysis={
                "summary": switching_risk,
                "signals": [item for item in pain_validation if item.get("switching_risk_score", 0) > 0],
            },
            pricing_and_wtp_analysis={"summary": willingness_to_pay},
            recommended_report_revisions=recommended_revisions,
            limitations=_default_limitations(batch.valid_count),
            next_research_questions=[item.get("recommended_report_update", "") for item in pain_validation[:3] if item.get("recommended_report_update")],
            dashboard_summary=dashboard_summary,
        )

    def save_batch(self, batch: SurveyResponseBatch) -> SurveyResponseBatch:
        row = SurveyResponseBatchRecordRow(
            batch_id=batch.batch_id,
            survey_id=batch.survey_id,
            payload_json=batch.model_dump_json(),
            uploaded_at=batch.uploaded_at,
        )
        self.db.merge(row)
        self.db.commit()
        return batch

    def save_analysis(self, analysis: SurveyAnalysis) -> SurveyAnalysis:
        row = SurveyAnalysisRecordRow(
            analysis_id=analysis.analysis_id,
            survey_id=analysis.survey_id,
            batch_id=analysis.batch_id,
            payload_json=analysis.model_dump_json(),
            created_at=analysis.created_at,
        )
        self.db.merge(row)
        self.db.commit()
        return analysis

    def latest_analysis(self, survey_id: str) -> SurveyAnalysis:
        survey = self.get_survey(survey_id)
        if survey.status != "analyzed":
            raise KeyError(survey_id)
        row = (
            self.db.query(SurveyAnalysisRecordRow)
            .filter_by(survey_id=survey_id)
            .order_by(SurveyAnalysisRecordRow.created_at.desc(), literal_column("rowid").desc())
            .first()
        )
        if row is None:
            raise KeyError(survey_id)
        return SurveyAnalysis.model_validate(json.loads(row.payload_json))

    def _latest_run_id_or_manual(self, task_id: str) -> str:
        try:
            return TaskRunService(self.db).latest_for_task(task_id).run_id
        except KeyError:
            return f"manual_{task_id}"

    def _task_payload(self, task: TaskRecord) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "product_name": task.product_name,
            "competitors": json.loads(task.competitors_json),
            "region": task.region,
            "industry": task.industry,
            "status": task.status,
            "rework_count": task.rework_count,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }

    def _planner_context(self, task: TaskRecord) -> dict[str, Any]:
        competitors = json.loads(task.competitors_json)
        survey_inputs = PlannerSurveyInput(
            objective=f"收集目标用户对 {task.product_name} 及竞品（{', '.join(competitors)}）的结构化反馈，验证公开资料难以确认的用户侧偏好。",
            respondent_type=f"{task.region} 地区正在使用或计划购买/评估 {task.industry} 相关产品的用户",
            question_themes=[
                "购买或采用决策因素",
                "核心痛点与满意度",
                "竞品替代与切换原因",
                "价格敏感度与付费意愿",
                "功能优先级与改进期望",
            ],
            hypotheses=[
                "用户决策受功能体验、价格和使用场景共同影响，而不是只由品牌驱动。",
                "公开资料中的竞品卖点需要通过用户侧反馈验证真实重要性。",
                "当用户对当前方案存在明显痛点时，竞品替代风险会上升。",
            ],
            metadata={
                "planner_generated": True,
                "survey_reason": "公开证据不足以直接判断真实用户偏好，需要用户侧反馈补充。",
            },
        )
        return {
            "intent_classification": "competitive_analysis",
            "survey_needed": True,
            "survey_recommended": True,
            "survey_objective": survey_inputs.objective,
            "survey_inputs": survey_inputs.model_dump(mode="json"),
            "questionnaire_follow_up": {
                "recommendation_type": "questionnaire_follow_up",
                "launch_mode": "follow_up_sidecar",
                "recommended": True,
                "required": True,
                "objective": survey_inputs.objective,
                "respondent_type": survey_inputs.respondent_type,
                "question_themes": list(survey_inputs.question_themes),
                "hypotheses": list(survey_inputs.hypotheses),
                "guidance": ["Use the questionnaire as a follow-up validation workflow, not as an always-on main DAG node."],
                "selected_dimensions": ["positioning", "feature", "pricing", "persona", "risk"],
                "rationale": "仅依赖公开证据不足以验证用户侧痛点和偏好。",
                "metadata": {"source": "survey_fallback_planner_context"},
            },
            "extracted_context": {
                "product_name": task.product_name,
                "competitors": competitors,
                "region": task.region,
                "industry": task.industry,
            },
            "selected_dimensions": ["positioning", "feature", "pricing", "persona", "risk"],
            "missing_information": ["公开资料无法确认真实用户痛点、竞品替代意愿和付费意愿。"],
            "assumptions": ["用户侧反馈可以验证公开证据中无法确认的产品痛点。"],
            "candidate_competitors": [{"name": competitor, "confidence": 0.5} for competitor in competitors],
            "downstream_guidance": PlannerDownstreamGuidance(
                survey=["问卷必须服务于竞品分析，避免个人敏感信息；上传后只保留聚合统计和 SurveyEvidence。"],
            ).model_dump(mode="json"),
            "confidence": 0.45,
            "diagnostics": {"source": "survey_fallback_planner_context"},
        }

    def _planner_context_from_sources(
        self,
        task: TaskRecord,
        run_id: str,
        report_context: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        explicit_context = self._questionnaire_follow_up_context(task, run_id, report_context)
        if explicit_context is not None:
            diagnostics = dict(explicit_context.get("diagnostics") or {})
            diagnostics.setdefault("source", "questionnaire_follow_up_contract")
            diagnostics.setdefault("source_section", "report.extensions.questionnaire_follow_up")
            diagnostics.setdefault("load_path", ["report_extensions.questionnaire_follow_up"])
            explicit_context["diagnostics"] = diagnostics
            return explicit_context, diagnostics
        report_snapshot = self._real_planner_context(task.task_id, run_id=run_id, report_context=report_context)
        if report_snapshot:
            diagnostics = dict(report_snapshot.get("diagnostics") or {})
            diagnostics.setdefault("source", "report_json_planner_snapshot")
            diagnostics.setdefault("source_section", "report.core.planner+extensions.survey")
            diagnostics.setdefault("load_path", ["report_core.planner", "report_extensions.survey"])
            report_snapshot["diagnostics"] = diagnostics
            return report_snapshot, diagnostics
        fallback_context = self._planner_context(task)
        diagnostics = dict(fallback_context.get("diagnostics") or {})
        diagnostics.setdefault("source", "survey_fallback_planner_context")
        diagnostics.setdefault("source_section", "task_fallback")
        diagnostics.setdefault("load_path", ["task_fallback_planner_context"])
        fallback_context["diagnostics"] = diagnostics
        return fallback_context, diagnostics

    def _questionnaire_follow_up_context(
        self,
        task: TaskRecord,
        run_id: str,
        report_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        follow_up = report_context.get("questionnaire_follow_up") or {}
        if not isinstance(follow_up, dict) or not follow_up:
            return None
        survey_inputs = {
            "objective": follow_up.get("objective"),
            "respondent_type": follow_up.get("respondent_type"),
            "question_themes": list(follow_up.get("question_themes") or []),
            "hypotheses": list(follow_up.get("hypotheses") or []),
            "metadata": {"source": "questionnaire_follow_up_contract"},
        }
        return {
            "intent_classification": (follow_up.get("metadata") or {}).get("intent_classification") or "competitive_analysis",
            "selected_dimensions": list(follow_up.get("selected_dimensions") or []),
            "downstream_guidance": {
                "survey": list(follow_up.get("guidance") or []),
            },
            "survey_needed": bool(follow_up.get("required", False)),
            "survey_recommended": bool(follow_up.get("recommended", False)),
            "survey_objective": follow_up.get("objective"),
            "survey_inputs": survey_inputs,
            "questionnaire_follow_up": follow_up,
            "missing_information": [],
            "assumptions": [],
            "candidate_competitors": [{"name": competitor, "confidence": 0.5} for competitor in json.loads(task.competitors_json)],
            "diagnostics": {
                "source_run_id": run_id,
                "source": "questionnaire_follow_up_contract",
            },
        }

    def _real_planner_context(
        self,
        task_id: str,
        *,
        run_id: str | None = None,
        report_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        run_id = run_id or self._latest_run_id_or_manual(task_id)
        report_context = report_context or self._report_context_for_task_run(task_id, run_id)
        planner = report_context.get("planner") or {}
        survey_extension = report_context.get("survey_extension") or {}
        if not planner:
            return None
        survey_inputs = survey_extension.get("survey_inputs") or planner.get("survey_inputs") or {}
        return {
            "intent_classification": planner.get("intent_classification") or "competitive_analysis",
            "selected_dimensions": planner.get("selected_dimensions") or [],
            "downstream_guidance": {
                "survey": survey_extension.get("survey_guidance") or planner.get("survey_guidance") or [],
                "writer": planner.get("writer_guidance") or [],
            },
            "survey_needed": bool(survey_extension.get("survey_needed", planner.get("survey_needed", False))),
            "survey_recommended": bool(
                survey_extension.get("survey_recommended", planner.get("survey_recommended", bool(planner.get("selected_dimensions"))))
            ),
            "survey_objective": survey_extension.get("survey_objective", planner.get("survey_objective")),
            "survey_inputs": survey_inputs if isinstance(survey_inputs, dict) else {},
            "questionnaire_follow_up": report_context.get("questionnaire_follow_up") or {},
            "missing_information": [],
            "assumptions": [],
            "candidate_competitors": [],
            "diagnostics": {"source": "report_json_planner_snapshot", "source_section": "core/extensions", "source_run_id": run_id},
        }

    def _context_from_task_and_planner(
        self,
        task: TaskRecord,
        planner_context: dict[str, Any],
        report_context: dict[str, Any],
    ) -> dict[str, Any]:
        survey_inputs = planner_context.get("survey_inputs") or {}
        questionnaire_follow_up = planner_context.get("questionnaire_follow_up") or report_context.get("questionnaire_follow_up") or {}
        return {
            "product_name": task.product_name,
            "competitors": json.loads(task.competitors_json),
            "industry": task.industry,
            "region": task.region,
            "report_markdown": report_context.get("report_markdown", ""),
            "claims_json": report_context.get("claims_json", []),
            "uncertain_findings": survey_inputs.get("hypotheses", []),
            "user_requirements": json.dumps(
                {
                    "intent_classification": planner_context.get("intent_classification"),
                    "survey_needed": planner_context.get("survey_needed"),
                    "survey_objective": planner_context.get("survey_objective"),
                    "survey_inputs": survey_inputs,
                    "extracted_context": planner_context.get("extracted_context"),
                    "selected_dimensions": planner_context.get("selected_dimensions"),
                    "analysis_dimension_plan": planner_context.get("analysis_dimension_plan"),
                    "missing_information": planner_context.get("missing_information"),
                    "assumptions": planner_context.get("assumptions"),
                    "candidate_competitors": planner_context.get("candidate_competitors"),
                    "survey_recommended": planner_context.get("survey_recommended", False),
                    "downstream_guidance": (planner_context.get("downstream_guidance") or {}).get("survey"),
                    "questionnaire_follow_up": questionnaire_follow_up,
                },
                ensure_ascii=False,
            ),
            "question_count": 10,
            "planner_context": planner_context,
            "analysis_dimension_plan": planner_context.get("analysis_dimension_plan"),
            "selected_dimensions": planner_context.get("selected_dimensions") or [],
            "missing_information": planner_context.get("missing_information") or [],
            "assumptions": planner_context.get("assumptions") or [],
            "candidate_competitors": planner_context.get("candidate_competitors") or [],
            "survey_recommended": planner_context.get("survey_recommended", False),
            "questionnaire_follow_up": questionnaire_follow_up,
            "report_context": report_context,
        }

    def _recommendation_from_context(
        self,
        planner_context: dict[str, Any],
        report_context: dict[str, Any],
    ) -> QuestionnaireLaunchRecommendation:
        existing = planner_context.get("questionnaire_follow_up") or report_context.get("questionnaire_follow_up") or {}
        survey_inputs = planner_context.get("survey_inputs") or {}
        return QuestionnaireLaunchRecommendation(
            recommended=bool(existing.get("recommended", planner_context.get("survey_recommended", False))),
            required=bool(existing.get("required", planner_context.get("survey_needed", False))),
            objective=existing.get("objective", planner_context.get("survey_objective")),
            respondent_type=existing.get("respondent_type", survey_inputs.get("respondent_type")),
            question_themes=list(existing.get("question_themes") or survey_inputs.get("question_themes") or []),
            hypotheses=list(existing.get("hypotheses") or survey_inputs.get("hypotheses") or []),
            guidance=list(existing.get("guidance") or (planner_context.get("downstream_guidance") or {}).get("survey") or []),
            selected_dimensions=list(existing.get("selected_dimensions") or planner_context.get("selected_dimensions") or []),
            rationale=existing.get("rationale") or ((planner_context.get("diagnostics") or {}).get("source_section")),
            domain_pack=existing.get("domain_pack") if isinstance(existing.get("domain_pack"), dict) else None,
            metadata=dict(existing.get("metadata") or {}),
        )

    def _context_from_request(self, task: TaskRecord, request: SurveyGenerateRequest) -> dict[str, Any]:
        return {
            "product_name": request.product_name or task.product_name,
            "competitors": request.competitors or json.loads(task.competitors_json),
            "industry": request.industry or task.industry,
            "region": request.region or task.region,
            "report_markdown": request.report_markdown,
            "claims_json": request.claims_json,
            "uncertain_findings": request.uncertain_findings,
            "user_requirements": request.user_requirements,
            "question_count": request.question_count,
            "force_generate": request.force_generate,
            "planner_context": {},
        }

    def _pain_points_for_context(self, task: TaskRecord, planner_context: dict[str, Any], report_context: dict[str, Any]) -> list[dict[str, Any]]:
        return self.pain_point_agent.run(
            {
                "task": self._task_payload(task),
                "planner_context": planner_context,
                "report_markdown": report_context.get("report_markdown", ""),
                "claims_json": report_context.get("claims_json", []),
                "analysis_dimension_plan": planner_context.get("analysis_dimension_plan"),
                "selected_dimensions": planner_context.get("selected_dimensions"),
                "survey_inputs": planner_context.get("survey_inputs"),
                "missing_information": planner_context.get("missing_information"),
                "assumptions": planner_context.get("assumptions"),
                "candidate_competitors": planner_context.get("candidate_competitors"),
                "downstream_guidance_survey": (planner_context.get("downstream_guidance") or {}).get("survey"),
            }
        ).get("pain_points", [])

    @staticmethod
    def _topic_pain_points(topic: str) -> list[dict[str, Any]]:
        return [
            {
                "pain_id": "P1",
                "pain_point": f"目标用户围绕“{topic}”的核心需求或痛点是否真实存在",
                "source_from_report": "topic_generation",
                "confidence": 0.45,
                "why_need_survey": "独立话题缺少竞品报告上下文，需要用问卷验证真实用户需求。",
                "research_questions": ["该需求是否存在？", "它的重要程度如何？"],
                "metadata": {"source": "topic_generation"},
            }
        ]

    def _fallback_survey_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        planner_context = context.get("planner_context") or {}
        survey_inputs = planner_context.get("survey_inputs") or {}
        pain_points = list(context.get("pain_points") or [])
        if pain_points:
            return self._fallback_pain_point_survey_payload(context, pain_points)
        themes = list(survey_inputs.get("question_themes") or ["购买决策因素", "核心痛点", "竞品替代", "价格敏感度", "功能优先级"])
        hypotheses = list(survey_inputs.get("hypotheses") or context.get("uncertain_findings") or [])
        respondent_type = survey_inputs.get("respondent_type") or "目标用户或潜在用户"
        objective = survey_inputs.get("objective") or f"验证 {context.get('product_name', '目标产品')} 的用户侧竞品分析假设。"
        question_specs = [
            ("single_choice", themes[0], "你在选择这类产品时最看重哪个因素？", ["价格", "核心功能", "品牌/生态", "稳定性", "服务支持", "其他"]),
            ("multiple_choice", themes[min(1, len(themes) - 1)], "你当前最明显的使用痛点有哪些？", ["价格偏高", "功能不够好用", "体验不稳定", "售后/服务不足", "学习成本高", "暂时没有明显痛点"]),
            ("rating", themes[min(1, len(themes) - 1)], "你对当前使用方案的整体满意度如何？", ["1", "2", "3", "4", "5"]),
            ("single_choice", themes[min(2, len(themes) - 1)], "什么情况最可能让你转向其他竞品？", ["价格更合适", "关键功能更强", "体验更稳定", "生态兼容更好", "服务更可靠", "暂不考虑切换"]),
            ("number", themes[min(3, len(themes) - 1)], "你可接受的最高预算或月度支出大约是多少？", []),
            ("single_choice", themes[min(3, len(themes) - 1)], "如果产品明显改善你的核心痛点，你是否愿意支付溢价？", ["不愿意", "少量溢价", "中等溢价", "较高溢价", "视具体功能而定"]),
            ("multiple_choice", themes[min(4, len(themes) - 1)], "你最希望优先改进哪些方面？", ["价格/套餐", "核心功能", "性能/稳定性", "生态/兼容", "服务支持", "隐私与安全"]),
            ("rating", themes[min(4, len(themes) - 1)], "你认为竞品宣传卖点与真实使用价值的匹配程度如何？", ["1", "2", "3", "4", "5"]),
            ("single_choice", themes[0], "你通常通过什么信息来源形成购买或采用判断？", ["官方信息", "朋友/同事推荐", "测评/媒体", "社群讨论", "线下体验", "其他"]),
            ("text", "开放反馈", "还有哪些体验、痛点或改进建议需要补充？", []),
        ]
        questions = []
        question_limit = int(context.get("question_count") or len(question_specs))
        for index, (question_type, theme, text, options) in enumerate(question_specs[:question_limit], start=1):
            hypothesis = hypotheses[(index - 1) % len(hypotheses)] if hypotheses else None
            question_id = f"q{index}"
            questions.append(
                {
                    "question_id": question_id,
                    "field_name": _field_name_from_question(question_id, text),
                    "question_text": text,
                    "question_type": question_type,
                    "options": options,
                    "required": question_type != "text",
                    "analysis_goal": f"围绕“{theme}”验证竞品分析假设。",
                    "related_claim_id": None,
                    "maps_to_pain_id": None,
                    "research_purpose": "背景信息与用户侧验证",
                    "analysis_method": "统计各选项分布并识别主要用户侧信号。",
                    "metric_role": "background" if index in {1, 9} else "open_feedback" if question_type == "text" else "pain_priority",
                    "reason": "由 Planner survey_inputs 生成的本地 fallback 问题，保证无 LLM 时仍可运行。",
                    "theme": theme,
                    "hypothesis": hypothesis,
                }
            )
        return {
            "survey_title": f"{context.get('product_name', '目标产品')} 用户侧验证问卷",
            "survey_description": "基于 Planner 输出的 survey_inputs 自动生成，用于补充公开资料无法确认的用户侧证据。",
            "target_respondents": respondent_type,
            "research_goal": objective,
            "questions": questions,
            "pain_points": [],
            "question_pain_mapping": {},
            "expected_analysis_dimensions": themes,
            "csv_columns": [question["field_name"] for question in questions],
            "metadata": {
                "source": "planner_output",
                "planner_generated": True,
                "survey_reason": (survey_inputs.get("metadata") or {}).get("survey_reason", "公开证据需要用户侧反馈补充。"),
            },
        }

    def _fallback_pain_point_survey_payload(self, context: dict[str, Any], pain_points: list[dict[str, Any]]) -> dict[str, Any]:
        survey_inputs = (context.get("planner_context") or {}).get("survey_inputs") or {}
        questions: list[dict[str, Any]] = [
            {
                "question_id": "Q1",
                "field_name": "user_segment",
                "question_text": "你更接近以下哪类用户？",
                "question_type": "single_choice",
                "options": ["当前用户", "潜在购买者", "竞品用户", "行业观察者", "其他"],
                "required": True,
                "analysis_goal": "识别反馈样本的用户类型。",
                "related_claim_id": None,
                "maps_to_pain_id": None,
                "research_purpose": "背景分层",
                "analysis_method": "按用户类型分组观察痛点验证结果。",
                "metric_role": "background",
                "theme": "用户背景",
                "hypothesis": None,
                "reason": "背景题用于解释样本结构。",
            }
        ]
        roles = [
            ("pain_existence", "你是否遇到过以下问题：{pain_point}？", ["经常遇到", "偶尔遇到", "很少遇到", "从未遇到"], "验证痛点存在性"),
            ("pain_severity", "这个问题对你的购买、续费或推荐影响有多大？", ["1", "2", "3", "4", "5"], "衡量痛点严重程度"),
            ("switching_risk", "如果竞品能更好解决该问题，你会考虑转向竞品吗？", ["一定不会", "可能不会", "会纳入比较", "很可能切换", "已经因此切换"], "判断竞品替代风险"),
            ("willingness_to_pay", "如果该问题被明显改善，你愿意接受额外成本吗？", ["不愿意", "少量额外成本", "中等额外成本", "较高额外成本", "视方案而定"], "判断解决痛点的付费意愿"),
        ]
        max_questions = int(context.get("question_count") or 10)
        for pain in pain_points:
            if len(questions) >= max_questions:
                break
            pain_id = str(pain.get("pain_id") or f"P{len(questions)}")
            for metric_role, template, options, purpose in roles:
                if len(questions) >= max_questions:
                    break
                qid = f"Q{len(questions) + 1}"
                field_name = f"{pain_id.lower()}_{metric_role}"
                questions.append(
                    {
                        "question_id": qid,
                        "field_name": field_name,
                        "question_text": template.format(pain_point=pain.get("pain_point", "该痛点")),
                        "question_type": "rating" if metric_role == "pain_severity" else "single_choice",
                        "options": options,
                        "required": True,
                        "analysis_goal": purpose,
                        "related_claim_id": (pain.get("related_claim_ids") or [None])[0],
                        "maps_to_pain_id": pain_id,
                        "research_purpose": purpose,
                        "analysis_method": "统计高频/高影响选项占比，计算该痛点的验证强度和优先级。",
                        "metric_role": metric_role,
                        "theme": "痛点验证",
                        "hypothesis": pain.get("pain_point"),
                        "reason": "该题用于验证报告中提到的痛点是否被目标用户实际感知。",
                    }
                )
        while len(questions) < max_questions and pain_points:
            pain = pain_points[(len(questions) - 1) % len(pain_points)]
            pain_id = str(pain.get("pain_id") or "P1")
            qid = f"Q{len(questions) + 1}"
            questions.append(
                {
                    "question_id": qid,
                    "field_name": f"{pain_id.lower()}_open_feedback_{len(questions)}",
                    "question_text": f"关于“{pain.get('pain_point', '该痛点')}”，你还有哪些具体经历或建议？",
                    "question_type": "text",
                    "options": [],
                    "required": False,
                    "analysis_goal": "收集痛点背后的具体场景和开放反馈。",
                    "related_claim_id": (pain.get("related_claim_ids") or [None])[0],
                    "maps_to_pain_id": pain_id,
                    "research_purpose": "补充开放反馈",
                    "analysis_method": "提取代表性文本样例，仅作为定性补充，不做过度推断。",
                    "metric_role": "open_feedback",
                    "theme": "开放反馈",
                    "hypothesis": pain.get("pain_point"),
                    "reason": "开放题用于补充结构化题无法覆盖的真实用户表达。",
                }
            )
        if max_questions >= 3 and not any(question.get("question_type") == "text" for question in questions) and pain_points:
            pain = pain_points[0]
            pain_id = str(pain.get("pain_id") or "P1")
            questions[-1] = {
                "question_id": questions[-1]["question_id"],
                "field_name": f"{pain_id.lower()}_open_feedback",
                "question_text": f"关于“{pain.get('pain_point', '该痛点')}”，你还有哪些具体经历或建议？",
                "question_type": "text",
                "options": [],
                "required": False,
                "analysis_goal": "收集痛点背后的具体场景和开放反馈。",
                "related_claim_id": (pain.get("related_claim_ids") or [None])[0],
                "maps_to_pain_id": pain_id,
                "research_purpose": "补充开放反馈",
                "analysis_method": "提取代表性文本样例，仅作为定性补充，不做过度推断。",
                "metric_role": "open_feedback",
                "theme": "开放反馈",
                "hypothesis": pain.get("pain_point"),
                "reason": "开放题用于补充结构化题无法覆盖的真实用户表达。",
            }
        return {
            "survey_title": f"{context.get('product_name', '目标产品')} 痛点验证问卷",
            "survey_description": "基于 Planner 和竞品分析报告提取产品痛点，验证痛点是否真实存在及其业务影响。",
            "target_respondents": survey_inputs.get("respondent_type") or "目标用户或潜在用户",
            "research_goal": survey_inputs.get("objective") or f"验证 {context.get('product_name', '目标产品')} 的用户侧产品痛点。",
            "pain_points": pain_points,
            "questions": questions,
            "expected_analysis_dimensions": ["痛点存在性", "痛点严重度", "竞品替代风险", "付费意愿"],
            "csv_columns": [question["field_name"] for question in questions],
            "question_pain_mapping": _build_question_pain_mapping(questions),
            "metadata": {"source": "pain_point_fallback", "planner_generated": True},
        }

    def _revise_generic_payload(self, survey: Survey, revision_request: str) -> dict[str, Any]:
        questions = [question.model_dump(mode="json") for question in survey.questions]
        removed_questions: list[dict[str, str]] = []
        added_questions: list[dict[str, str]] = []
        request_text = revision_request.strip()
        if _contains_any(request_text, ["价格", "预算", "付费", "溢价"]) and not _has_field(questions, "price_sensitivity"):
            questions.append(
                {
                    "question_id": "q0",
                    "field_name": "price_sensitivity",
                    "question_text": "价格变化对你选择该类产品的影响程度如何？",
                    "question_type": "single_choice",
                    "options": ["几乎没有影响", "有一定影响", "影响较大", "是决定因素", "不确定"],
                    "required": True,
                    "analysis_goal": "验证价格敏感度与付费意愿。",
                    "related_claim_id": None,
                    "reason": "根据用户返工要求补充价格敏感度问题。",
                    "theme": "价格敏感度与付费意愿",
                    "hypothesis": None,
                    "order": len(questions) + 1,
                }
            )
            added_questions.append({"question_id": "price_sensitivity", "reason": "补充价格敏感度相关问题"})
        max_questions = _extract_question_limit(request_text)
        if max_questions and len(questions) > max_questions:
            questions, removed = _trim_demo_questions(questions, max_questions)
            removed_questions.extend(removed)
        renumbered = [{**question, "question_id": f"q{index}", "order": index} for index, question in enumerate(questions, start=1)]
        return {
            "revision_summary": f"已根据要求“{request_text}”返工问卷，保留原业务目标并更新版本。",
            "survey_title": survey.title,
            "survey_description": survey.description,
            "target_respondents": survey.target_respondents,
            "research_goal": survey.research_goal,
            "questions": renumbered,
            "removed_questions": removed_questions,
            "added_questions": added_questions,
            "expected_analysis_dimensions": survey.expected_analysis_dimensions,
            "csv_columns": [question["field_name"] for question in renumbered],
            "metadata": {**survey.metadata, "refine_instruction": request_text},
        }

    def _normalize_response_template_headers(self, survey: Survey, content: str) -> str:
        reader = csv.DictReader(StringIO(content.lstrip("\ufeff")))
        if not reader.fieldnames:
            return content
        alias_to_field = {response_field_name(question): question.field_name for question in survey.questions}
        alias_to_field.update({question.question_id: question.field_name for question in survey.questions})
        alias_to_field.update({question.field_name: question.field_name for question in survey.questions})
        raw_fields = [field.strip() for field in reader.fieldnames]
        normalized_fields = [alias_to_field.get(field, field) for field in raw_fields if field != "respondent_id"]
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=normalized_fields)
        writer.writeheader()
        for row in reader:
            writer.writerow(
                {
                    alias_to_field.get(raw_field.strip(), raw_field.strip()): (row.get(raw_field) or "").strip()
                    for raw_field in reader.fieldnames
                    if raw_field.strip() != "respondent_id"
                }
            )
        return output.getvalue()

    def _report_context(self, survey: Survey) -> dict[str, Any]:
        return self._report_context_for_task_run(survey.task_id, survey.run_id)

    def _report_context_for_task_run(self, task_id: str, run_id: str) -> dict[str, Any]:
        try:
            report = ReportService(self.db).get_for_task_run(task_id, run_id)
            json_report = report.json_report or {}
            core = dict(json_report.get("core") or {})
            extensions = dict(json_report.get("extensions") or {})
            return {
                "report_markdown": report.markdown,
                "claims_json": [claim.model_dump(mode="json") for claim in report.claims],
                "planner": dict(core.get("planner") or json_report.get("planner") or {}),
                "survey_extension": dict(extensions.get("survey") or {}),
                "questionnaire_follow_up": dict(extensions.get("questionnaire_follow_up") or {}),
                "report_extensions": extensions,
            }
        except KeyError:
            return {"report_markdown": "", "claims_json": []}

    def _survey_from_llm_payload(
        self,
        task_id: str,
        run_id: str,
        payload: dict[str, Any],
        claims_json: list[Any],
        *,
        status: str,
        version: int,
        survey_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Survey:
        source_claim_ids = [
            claim.get("claim_id")
            for claim in claims_json
            if isinstance(claim, dict) and claim.get("claim_id")
        ]
        questions = []
        for index, question in enumerate(payload.get("questions") or [], start=1):
            normalized_question = _normalize_question_payload(question, index)
            questions.append({**normalized_question, "order": index})
        pain_points = payload.get("pain_points") or []
        if not pain_points:
            pain_points = []
        question_pain_mapping = _build_question_pain_mapping(questions) or dict(payload.get("question_pain_mapping") or {})
        metadata = dict(payload.get("metadata") or {})
        planner_snapshot = dict(metadata.pop("planner_snapshot", {}) or payload.get("planner_snapshot") or {})
        report_context_snapshot = dict(metadata.pop("report_context_snapshot", {}) or payload.get("report_context_snapshot") or {})
        return Survey(
            survey_id=survey_id or f"survey_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}",
            task_id=task_id,
            run_id=run_id,
            title=str(payload.get("survey_title") or payload.get("title") or "竞品分析验证问卷"),
            description=str(payload.get("survey_description") or "用于验证公开资料无法确认的用户侧问题。"),
            target_respondents=str(payload.get("target_respondents") or payload.get("respondent_type") or "目标用户或潜在用户"),
            research_goal=str(payload.get("research_goal") or payload.get("objective") or "验证竞品分析中的用户侧假设。"),
            status=status,
            version=version,
            source_claim_ids=source_claim_ids,
            pain_points=pain_points,
            questions=questions,
            question_pain_mapping=question_pain_mapping,
            extensions={
                "context": {
                    "planner_snapshot": planner_snapshot,
                    "report_context_snapshot": report_context_snapshot,
                }
            },
            planner_snapshot=planner_snapshot,
            report_context_snapshot=report_context_snapshot,
            expected_analysis_dimensions=list(payload.get("expected_analysis_dimensions") or []),
            csv_columns=list(payload.get("csv_columns") or []),
            metadata=metadata,
            created_at=created_at or datetime.utcnow(),
        )

    def _replace_survey_content(
        self,
        survey: Survey,
        *,
        title: str | None = None,
        description: str | None = None,
        target_respondents: str | None = None,
        research_goal: str | None = None,
        expected_analysis_dimensions: list[str] | None = None,
        questions: list[dict[str, Any]] | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> Survey:
        next_questions = questions if questions is not None else [question.model_dump(mode="json") for question in survey.questions]
        metadata = {**survey.metadata, **(metadata_patch or {})}
        return Survey(
            survey_id=survey.survey_id,
            task_id=survey.task_id,
            run_id=survey.run_id,
            title=(title or survey.title).strip(),
            description=(description or survey.description).strip(),
            target_respondents=(target_respondents or survey.target_respondents).strip(),
            research_goal=(research_goal or survey.research_goal).strip(),
            status="revised" if survey.status != "draft" else "draft",
            version=survey.version + 1,
            source_claim_ids=survey.source_claim_ids,
            pain_points=survey.pain_points,
            questions=next_questions,
            question_pain_mapping=_build_question_pain_mapping(next_questions),
            planner_snapshot=survey.planner_snapshot,
            report_context_snapshot=survey.report_context_snapshot,
            expected_analysis_dimensions=expected_analysis_dimensions
            if expected_analysis_dimensions is not None
            else survey.expected_analysis_dimensions,
            csv_columns=[question["field_name"] for question in next_questions],
            metadata=metadata,
            created_at=survey.created_at,
        )

    def _question_create_payload(self, request: SurveyQuestionCreate, order: int) -> dict[str, Any]:
        question_id = f"Q{order}"
        payload = request.model_dump(mode="json")
        payload["question_id"] = question_id
        payload["field_name"] = payload.get("field_name") or _field_name_from_question(question_id, payload["question_text"])
        payload["order"] = order
        return _prepare_question_for_save(payload, order)

    def _raw_stats_from_ingestion(self, survey: Survey, ingestion: Any) -> dict[str, Any]:
        rows = []
        for row in getattr(ingestion, "rows", []) or []:
            mapped = {}
            for column, value in row.items():
                mapped[getattr(ingestion, "question_mapping", {}).get(column, column)] = "" if value is None else str(value)
            rows.append(mapped)
        columns = sorted({key for row in rows for key in row.keys()})
        csv_content = _rows_to_csv(rows, columns)
        if columns:
            try:
                raw_stats = parse_survey_response_csv(csv_content, survey)
            except SurveyCsvValidationError:
                raw_stats = _loose_stats_from_rows(survey, rows, columns)
        else:
            raw_stats = _loose_stats_from_rows(survey, rows, columns)
        raw_stats["source_type"] = getattr(ingestion, "source_type", "unknown")
        raw_stats["file_name"] = getattr(ingestion, "file_name", "")
        raw_stats["parse_warnings"] = getattr(ingestion, "parse_warnings", [])
        raw_stats["question_mapping"] = getattr(ingestion, "question_mapping", {})
        raw_stats["raw_text_blocks"] = getattr(ingestion, "raw_text_blocks", [])
        return raw_stats

    def _is_phone_demo_survey(self, survey: Survey) -> bool:
        field_names = {question.field_name for question in survey.questions}
        return "purchase_priority" in field_names and ("current_brand" in field_names or "max_budget" in field_names) and "智能手机" in survey.title

    def _revise_phone_demo_payload(self, survey: Survey, revision_request: str) -> dict[str, Any]:
        request_text = revision_request.strip()
        questions = [question.model_dump(mode="json") for question in survey.questions]
        removed_questions: list[dict[str, str]] = []
        added_questions: list[dict[str, str]] = []
        actions: list[str] = []

        if _contains_any(request_text, ["删除ai", "删除 AI", "去掉ai", "去掉 AI", "不要ai", "不要 AI"]):
            questions, removed = _remove_questions_by_field(questions, {"ai_feature_interest"})
            removed_questions.extend(removed)
            if removed:
                actions.append("删除 AI 功能兴趣题")

        if _contains_any(request_text, ["付费", "溢价", "加价"]) and not _has_field(questions, "premium_willingness"):
            new_question = {
                "question_id": "Q0",
                "field_name": "premium_willingness",
                "question_text": "如果某手机在续航、影像或 AI 功能上明显更好，你最多愿意额外支付多少？",
                "question_type": "single_choice",
                "options": ["不愿意额外支付", "1-300 元", "301-800 元", "801-1500 元", "1500 元以上"],
                "required": True,
                "analysis_goal": "验证用户是否愿意为明确体验提升支付溢价。",
                "related_claim_id": survey.source_claim_ids[0] if survey.source_claim_ids else None,
                "reason": "付费意愿可以补充判断高端化或差异化功能是否具有商业价值。",
                "order": len(questions) + 1,
            }
            questions.append(new_question)
            added_questions.append({"question_id": "premium_willingness", "reason": "根据修改要求新增付费/溢价意愿题"})
            actions.append("新增付费意愿题")

        max_questions = _extract_question_limit(request_text)
        if max_questions and len(questions) > max_questions:
            questions, removed = _trim_demo_questions(questions, max_questions)
            removed_questions.extend(removed)
            actions.append(f"压缩到 {max_questions} 题以内")

        if not actions:
            actions.append("保留原有核心问题，并更新问卷版本")

        renumbered = []
        for index, question in enumerate(questions, start=1):
            renumbered.append({**question, "question_id": f"Q{index}", "order": index})

        return {
            "revision_summary": f"已根据要求“{request_text}”调整问卷：" + "，".join(actions) + "。",
            "survey_title": survey.title,
            "survey_description": survey.description,
            "target_respondents": survey.target_respondents,
            "research_goal": survey.research_goal,
            "questions": renumbered,
            "removed_questions": removed_questions,
            "added_questions": added_questions,
            "expected_analysis_dimensions": survey.expected_analysis_dimensions,
            "csv_columns": [question["field_name"] for question in renumbered],
        }

    @staticmethod
    def _sample_answer_for_question(question: Any, row_index: int) -> str:
        if question.question_type == "rating":
            return str(min(5, 3 + row_index))
        if question.question_type == "number":
            return str(100 + row_index * 50)
        if question.question_type in {"single_choice", "multiple_choice"}:
            options = list(question.options or [])
            if not options:
                return ""
            if question.question_type == "multiple_choice":
                return ";".join(options[: min(2, len(options))])
            return str(options[row_index % len(options)])
        if question.metric_role == "open_feedback" or question.question_type == "text":
            pain_hint = f" 针对 {question.maps_to_pain_id}" if question.maps_to_pain_id else ""
            return f"示例反馈{row_index + 1}{pain_hint}：希望进一步验证真实用户场景。"
        return ""

    @staticmethod
    def _row_to_survey(row: SurveyRecordRow) -> Survey:
        return Survey.model_validate(json.loads(row.payload_json))


def _stats_by_field(raw_stats: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for stats in raw_stats.get("questions", {}).values():
        if isinstance(stats, dict) and stats.get("field_name"):
            result[str(stats["field_name"])] = stats
    return result


def _normalize_question_payload(question: dict[str, Any], index: int) -> dict[str, Any]:
    raw_type = str(question.get("question_type") or question.get("type") or "text")
    question_type = {"scale": "rating"}.get(raw_type, raw_type)
    if question_type not in {"single_choice", "multiple_choice", "rating", "text", "number"}:
        question_type = "text"
    question_id = str(question.get("question_id") or question.get("id") or f"q{index}")
    question_text = str(question.get("question_text") or question.get("question") or "")
    return {
        **question,
        "question_id": question_id,
        "field_name": str(question.get("field_name") or _field_name_from_question(question_id, question_text)),
        "question_text": question_text or f"问题 {index}",
        "question_type": question_type,
        "options": list(question.get("options") or []),
        "required": bool(question.get("required", True)),
        "analysis_goal": str(question.get("analysis_goal") or question.get("analysis_hint") or "分析该题反馈对竞品判断的影响。"),
        "related_claim_id": question.get("related_claim_id"),
        "maps_to_pain_id": question.get("maps_to_pain_id"),
        "research_purpose": question.get("research_purpose") or question.get("analysis_goal"),
        "analysis_method": question.get("analysis_method") or "统计该题回答分布，并与绑定痛点或研究目标进行交叉解释。",
        "metric_role": _normalize_metric_role(question.get("metric_role")),
        "reason": str(question.get("reason") or "由 SurveyAgent 根据竞品分析上下文生成。"),
        "theme": question.get("theme"),
        "hypothesis": question.get("hypothesis"),
    }


def _normalize_metric_role(value: Any) -> str | None:
    allowed = {
        "background",
        "pain_existence",
        "pain_frequency",
        "pain_severity",
        "pain_priority",
        "switching_risk",
        "competitor_preference",
        "solution_preference",
        "willingness_to_pay",
        "open_feedback",
    }
    text = str(value or "").strip()
    return text if text in allowed else None


def _prepare_question_for_save(question: dict[str, Any], index: int) -> dict[str, Any]:
    payload = _normalize_question_payload(question, index)
    question_type = payload["question_type"]
    options = [str(option).strip() for option in payload.get("options", []) if str(option).strip()]
    if question_type == "rating" and not options:
        options = ["1", "2", "3", "4", "5"]
    elif question_type in {"single_choice", "multiple_choice"} and not options:
        options = ["选项 A", "选项 B", "其他"]
    elif question_type in {"text", "number"}:
        options = []
    field_name = str(payload.get("field_name") or "").strip()
    question_text = str(payload.get("question_text") or "").strip() or f"问题 {index}"
    return {
        **payload,
        "field_name": field_name or _field_name_from_question(str(payload.get("question_id") or f"Q{index}"), question_text),
        "question_text": question_text,
        "options": options,
        "analysis_goal": str(payload.get("analysis_goal") or "分析该题反馈对研究问题的影响。").strip(),
        "research_purpose": payload.get("research_purpose") or str(payload.get("analysis_goal") or "分析该题反馈对研究问题的影响。").strip(),
        "analysis_method": payload.get("analysis_method") or "统计回答分布，结合样本量判断是否形成用户侧信号。",
        "metric_role": _normalize_metric_role(payload.get("metric_role")) or ("open_feedback" if question_type == "text" else None),
        "reason": str(payload.get("reason") or "由问卷编辑器生成或修改。").strip(),
        "order": index,
    }


def _build_question_pain_mapping(questions: list[Any]) -> dict[str, str]:
    mapping = {}
    for question in questions:
        qid = question.question_id if hasattr(question, "question_id") else question.get("question_id")
        pid = question.maps_to_pain_id if hasattr(question, "maps_to_pain_id") else question.get("maps_to_pain_id")
        if qid and pid:
            mapping[str(qid)] = str(pid)
    return mapping


def _rows_to_csv(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    columns = columns or sorted({key for row in rows for key in row.keys()})
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    return output.getvalue()


def _loose_stats_from_rows(survey: Survey, rows: list[dict[str, Any]], columns: list[str]) -> dict[str, Any]:
    stats_by_question = {}
    for question in survey.questions:
        answers = [str(row.get(question.field_name, "")).strip() for row in rows if str(row.get(question.field_name, "")).strip()]
        stats_by_question[question.question_id] = _simple_question_stats(question, answers, len(rows))
    return {
        "sample_size": len(rows),
        "valid_count": len(rows),
        "invalid_count": 0,
        "csv_columns": columns,
        "questions": stats_by_question,
    }


def _simple_question_stats(question: Any, answers: list[str], sample_size: int) -> dict[str, Any]:
    base = {
        "question_id": question.question_id,
        "field_name": question.field_name,
        "question_text": question.question_text,
        "question_type": question.question_type,
        "answer_count": len(answers),
    }
    if question.question_type in {"single_choice", "multiple_choice", "rating"}:
        values = []
        for answer in answers:
            values.extend([item.strip() for item in re.split(r"[,;；、|]\s*", answer) if item.strip()])
        distribution = {}
        for value in values:
            distribution.setdefault(value, {"count": 0, "ratio": 0.0})
            distribution[value]["count"] += 1
        denominator = sample_size or len(values) or 1
        for data in distribution.values():
            data["ratio"] = round(data["count"] / denominator, 4)
        return {**base, "distribution": distribution}
    if question.question_type == "number":
        numbers = []
        for answer in answers:
            try:
                numbers.append(float(answer))
            except ValueError:
                pass
        return {**base, "average": sum(numbers) / len(numbers) if numbers else None}
    return {**base, "text_samples": answers[:20]}


def _field_name_from_question(question_id: str, question_text: str) -> str:
    text = re.sub(r"\s+", "", question_text)
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)[:10]
    return f"{question_id}_{text or 'response'}"


def _summarize_question_stats(stats: dict[str, Any]) -> tuple[str, list[str]]:
    distribution = stats.get("distribution")
    if isinstance(distribution, dict) and distribution:
        top_option, top_data = _top_distribution_item(distribution)
        ratio = _format_ratio(top_data.get("ratio"))
        count = top_data.get("count", 0)
        return (
            f"最高频回答为“{top_option}”，出现 {count} 次，占比 {ratio}。",
            [f"top_option={top_option}", f"count={count}", f"ratio={ratio}"],
        )

    if stats.get("average") is not None:
        average = _format_number(stats.get("average"))
        median = _format_number(stats.get("median"))
        min_value = _format_number(stats.get("min"))
        max_value = _format_number(stats.get("max"))
        return (
            f"数值型反馈均值为 {average}，中位数为 {median}，范围 {min_value}-{max_value}。",
            [f"average={average}", f"median={median}", f"min={min_value}", f"max={max_value}"],
        )

    text_samples = stats.get("text_samples") or []
    if text_samples:
        sample_preview = "；".join(str(item) for item in text_samples[:3])
        return (
            f"收集到 {len(text_samples)} 条文本反馈，代表性样例：{sample_preview}",
            [f"text_count={len(text_samples)}"],
        )

    return "该字段暂无有效回答。", ["answer_count=0"]


def _question_summaries_from_stats(survey: Survey, raw_stats: dict[str, Any]) -> list[dict[str, Any]]:
    stats_by_field = _stats_by_field(raw_stats)
    summaries = []
    for question in survey.questions:
        stats = stats_by_field.get(question.field_name)
        if not stats:
            continue
        summary, _notable = _summarize_question_stats(stats)
        distribution = stats.get("distribution")
        if isinstance(distribution, dict):
            clean_distribution = {
                key: value.get("count", 0) if isinstance(value, dict) else value
                for key, value in distribution.items()
            }
        else:
            clean_distribution = {}
        summaries.append(
            {
                "question_id": question.question_id,
                "question": question.question_text,
                "type": question.question_type,
                "theme": question.theme,
                "hypothesis": question.hypothesis,
                "maps_to_pain_id": question.maps_to_pain_id,
                "metric_role": question.metric_role,
                "summary": summary,
                "distribution": clean_distribution,
                "average": stats.get("average"),
                "median": stats.get("median"),
                "answer_count": stats.get("answer_count", 0),
            }
        )
    return summaries


def _pain_point_validation(survey: Survey, stats_by_field: dict[str, dict[str, Any]], valid_count: int) -> list[dict[str, Any]]:
    if not survey.pain_points:
        return []
    validations = []
    for pain in survey.pain_points:
        related_questions = [question for question in survey.questions if question.maps_to_pain_id == pain.pain_id]
        scores = {
            "frequency_score": _score_for_role(related_questions, stats_by_field, {"pain_existence", "pain_frequency"}),
            "severity_score": _score_for_role(related_questions, stats_by_field, {"pain_severity", "pain_priority"}),
            "switching_risk_score": _score_for_role(related_questions, stats_by_field, {"switching_risk", "competitor_preference"}),
            "willingness_to_pay_score": _score_for_role(related_questions, stats_by_field, {"willingness_to_pay"}),
        }
        available_scores = [score for score in scores.values() if score is not None]
        if available_scores:
            frequency = scores["frequency_score"] or 0.0
            severity = scores["severity_score"] or 0.0
            switching = scores["switching_risk_score"] or 0.0
            wtp = scores["willingness_to_pay_score"] or 0.0
            priority = round(frequency * 0.30 + severity * 0.30 + switching * 0.25 + wtp * 0.15, 3)
        else:
            priority = 0.0
        if priority >= 0.68:
            result = "strongly_supported"
        elif priority >= 0.42:
            result = "partially_supported"
        elif available_scores:
            result = "not_supported"
        else:
            result = "inconclusive"
        validations.append(
            {
                "pain_id": pain.pain_id,
                "pain_point": pain.pain_point,
                "validation_result": result,
                "evidence_summary": f"基于 {len(related_questions)} 道绑定题和 {valid_count} 条有效样本形成用户侧验证信号。",
                "frequency_score": scores["frequency_score"] or 0.0,
                "severity_score": scores["severity_score"] or 0.0,
                "switching_risk_score": scores["switching_risk_score"] or 0.0,
                "willingness_to_pay_score": scores["willingness_to_pay_score"] or 0.0,
                "priority_score": priority,
                "affected_segments": pain.affected_user_scenarios,
                "supporting_questions": [question.question_id for question in related_questions],
                "recommended_report_update": _recommended_update_for_pain(pain.pain_id, result),
                "confidence": _survey_confidence(valid_count, max(len(related_questions), 1), len(available_scores)),
            }
        )
    return validations


def _score_for_role(questions: list[Any], stats_by_field: dict[str, dict[str, Any]], roles: set[str]) -> float | None:
    role_scores = []
    positive_keywords = ["经常", "偶尔", "明显", "决定", "很可能", "已经", "中等", "较高", "愿意", "会纳入"]
    negative_keywords = ["从未", "无影响", "不会", "不愿意", "很少"]
    for question in questions:
        if question.metric_role not in roles:
            continue
        stats = stats_by_field.get(question.field_name)
        if not stats:
            continue
        distribution = stats.get("distribution")
        if isinstance(distribution, dict) and distribution:
            score = 0.0
            for option, data in distribution.items():
                ratio = float(data.get("ratio", 0)) if isinstance(data, dict) else 0.0
                if _contains_any(str(option), positive_keywords):
                    score += ratio
                elif _contains_any(str(option), negative_keywords):
                    score -= ratio * 0.2
            role_scores.append(max(0.0, min(1.0, score)))
        elif isinstance(stats.get("average"), (int, float)):
            role_scores.append(max(0.0, min(1.0, float(stats["average"]) / 5)))
    if not role_scores:
        return None
    return round(sum(role_scores) / len(role_scores), 3)


def _claim_validation_matrix(survey: Survey, pain_validation: list[dict[str, Any]], report_context: dict[str, Any]) -> list[dict[str, Any]]:
    validations_by_pain = {item["pain_id"]: item for item in pain_validation}
    rows = []
    for claim in report_context.get("claims_json", [])[:8]:
        if not isinstance(claim, dict):
            continue
        related = [
            validation
            for pain in survey.pain_points
            for validation in [validations_by_pain.get(pain.pain_id)]
            if validation and claim.get("claim_id") in pain.related_claim_ids
        ]
        if related:
            result = "supported" if any(item["validation_result"] in {"strongly_supported", "partially_supported"} for item in related) else "no_signal"
        else:
            result = "no_signal"
        rows.append(
            {
                "claim_id": claim.get("claim_id"),
                "claim_text": claim.get("text"),
                "survey_result": result,
                "related_pain_ids": [item["pain_id"] for item in related],
                "recommended_revision": "根据痛点验证结果调整强弱表述。" if related else "当前问卷未形成直接信号，报告应保留原证据约束。",
            }
        )
    return rows


def _recommended_report_revisions(claim_matrix: list[dict[str, Any]], pain_validation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    revisions = []
    for item in pain_validation:
        if item["validation_result"] in {"strongly_supported", "partially_supported"}:
            revisions.append(
                {
                    "revision_type": "strengthen" if item["validation_result"] == "strongly_supported" else "keep_with_caution",
                    "target": item["pain_id"],
                    "suggestion": item["recommended_report_update"],
                }
            )
    for row in claim_matrix:
        if row.get("survey_result") == "no_signal":
            revisions.append({"revision_type": "keep_with_caution", "target": row.get("claim_id"), "suggestion": row.get("recommended_revision")})
    return revisions[:8]


def _recommended_update_for_pain(pain_id: str, result: str) -> str:
    if result == "strongly_supported":
        return f"{pain_id} 获得较强用户侧支持，可在报告中作为调查样本内信号强化，但仍需标注样本局限。"
    if result == "partially_supported":
        return f"{pain_id} 获得部分支持，报告中应使用谨慎措辞。"
    if result == "not_supported":
        return f"{pain_id} 当前样本未支持，报告中不应作为强结论。"
    return f"{pain_id} 当前样本信号不足，建议继续补充样本或访谈。"


def _hypothesis_findings(survey: Survey, raw_stats: dict[str, Any]) -> list[dict[str, Any]]:
    summaries = _question_summaries_from_stats(survey, raw_stats)
    hypotheses = [question.hypothesis for question in survey.questions if question.hypothesis]
    if not hypotheses:
        hypotheses = list((survey.metadata.get("survey_inputs") or {}).get("hypotheses") or [])
    findings = []
    for hypothesis in dict.fromkeys(hypotheses):
        related = [summary for summary in summaries if summary.get("hypothesis") == hypothesis]
        if not related:
            related = summaries[:2]
        answered = sum(int(item.get("answer_count") or 0) for item in related)
        support_level = "moderate" if answered >= 10 else "weak" if answered > 0 else "inconclusive"
        finding = "；".join(str(item.get("summary")) for item in related[:2]) or "当前问卷未形成明确统计信号。"
        findings.append(
            {
                "hypothesis": hypothesis,
                "finding": finding,
                "support_level": support_level,
            }
        )
    return findings


def _default_limitations(valid_count: int) -> list[str]:
    limitations = [
        "该分析基于用户上传 CSV，系统未验证抽样方式和样本代表性。",
        "问卷结果只能作为用户侧补充证据，不能替代公开来源证据。",
    ]
    if valid_count < 30:
        limitations.insert(0, "样本量较小，结论需要谨慎解释。")
    return limitations


def _finding_from_question(question_id: str, field_name: str, summary: str, confidence: float) -> dict[str, Any] | None:
    if "暂无有效回答" in summary:
        return None
    return {
        "finding": f"{field_name}：{summary}",
        "supporting_questions": [question_id],
        "confidence": confidence,
        "explanation": "该发现来自用户上传 CSV 的自动统计，用作用户侧补充信号。",
    }


def _extract_pain_points(stats_by_field: dict[str, dict[str, Any]]) -> list[str]:
    text_parts = []
    low_score_fields = []
    for field_name, stats in stats_by_field.items():
        text_parts.extend(str(item) for item in stats.get("text_samples", [])[:10])
        distribution = stats.get("distribution")
        if isinstance(distribution, dict):
            text_parts.extend(str(option) for option in distribution.keys())
        average = stats.get("average")
        if isinstance(average, (int, float)) and average <= 3:
            low_score_fields.append(field_name)

    text_blob = " ".join(text_parts).lower()
    pain_points = []
    keyword_map = [
        ("价格/预算敏感", ["贵", "价格", "预算", "price", "cost", "expensive"]),
        ("续航焦虑", ["续航", "电池", "battery"]),
        ("系统流畅与稳定性", ["卡", "流畅", "稳定", "系统", "lag", "crash"]),
        ("影像/拍照体验", ["拍照", "影像", "相机", "camera"]),
        ("AI 功能实用性", ["ai", "AI", "智能"]),
        ("售后与维修成本", ["售后", "维修", "服务", "repair", "service"]),
    ]
    for label, keywords in keyword_map:
        if _contains_any(text_blob, keywords):
            pain_points.append(label)
    if low_score_fields:
        pain_points.append(f"低评分字段需关注：{', '.join(low_score_fields[:3])}")
    return pain_points[:6]


def _summarize_focus_area(
    survey: Survey,
    stats_by_field: dict[str, dict[str, Any]],
    keywords: list[str],
    fallback: str,
) -> str:
    for question in survey.questions:
        text = f"{question.field_name} {question.question_text}"
        if not _contains_any(text, keywords):
            continue
        stats = stats_by_field.get(question.field_name)
        if not stats:
            continue
        summary, _notable_stats = _summarize_question_stats(stats)
        return f"{question.field_name}：{summary}"
    return fallback


def _generic_claim_updates(report_context: dict[str, Any]) -> list[dict[str, str]]:
    updates = []
    for claim in report_context.get("claims_json", [])[:5]:
        if not isinstance(claim, dict):
            continue
        updates.append(
            {
                "claim_id": str(claim.get("claim_id") or ""),
                "original_claim": str(claim.get("text") or ""),
                "survey_result": "自定义问卷未与该 Claim 建立逐题绑定，当前只作为用户侧补充信号。",
                "impact": "no_clear_signal",
                "recommended_revision": "结合关键发现人工判断是否需要修正原报告表述。",
            }
        )
    return updates


def _top_distribution_item(distribution: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return max(distribution.items(), key=lambda item: int(item[1].get("count", 0)))


def _format_ratio(value: Any) -> str:
    return f"{float(value):.0%}" if isinstance(value, (int, float)) else "-"


def _format_number(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def survey_analysis_to_survey_evidence(
    analysis: SurveyAnalysis,
    survey: Survey,
    task_id: str,
    run_id: str | None = None,
) -> SurveyEvidence:
    sample_size = int(analysis.sample_summary.get("valid_count") or analysis.sample_summary.get("sample_size") or 0)
    question_ids = [question.question_id for question in survey.questions]
    question_summaries = analysis.question_summaries or _question_summaries_from_stats(survey, {})
    hypothesis_findings = analysis.hypothesis_findings
    limitations = analysis.limitations or list(analysis.sample_summary.get("limitations") or [])
    confidence = _survey_confidence(sample_size, len(question_ids), len(question_summaries))
    snippet = analysis.executive_summary or analysis.dashboard_summary
    return SurveyEvidence(
        survey_id=survey.survey_id,
        run_id=run_id or survey.run_id,
        competitor=None,
        question_ids=question_ids,
        sample_size=sample_size,
        is_mock=False,
        snippet=_redact_sensitive_text(snippet),
        confidence=confidence,
        metadata={
            "task_id": task_id,
            "question_summaries": _strip_sensitive_question_summaries(question_summaries),
            "hypothesis_findings": hypothesis_findings,
            "pain_point_validation": analysis.pain_point_validation,
            "pain_point_ranking": analysis.pain_point_ranking,
            "claim_validation_matrix": analysis.claim_validation_matrix,
            "recommended_report_revisions": analysis.recommended_report_revisions,
            "limitations": limitations,
            "source": "survey_feedback_upload",
            "analysis_mode": "pain_point_validation" if analysis.pain_point_validation else "generic_survey_analysis",
        },
    )


def survey_evidence_to_evidence(survey_evidence: SurveyEvidence) -> Evidence:
    return Evidence(
        run_id=survey_evidence.run_id,
        competitor=survey_evidence.competitor,
        source_type="survey",
        local_ref=f"survey://{survey_evidence.survey_id}",
        snippet=survey_evidence.snippet,
        confidence=survey_evidence.confidence,
        source_quality="unknown",
        relevance_score=0.8,
        relevance_level="medium",
        relevance_reason="Aggregated survey feedback generated from user-uploaded CSV responses.",
        entity_match_signals={"survey_id": survey_evidence.survey_id},
        metadata={
            "survey_id": survey_evidence.survey_id,
            "question_ids": survey_evidence.question_ids,
            "sample_size": survey_evidence.sample_size,
            "is_mock": survey_evidence.is_mock,
            "survey_metadata": survey_evidence.metadata,
        },
    )


def _survey_confidence(sample_size: int, question_count: int, summary_count: int) -> float:
    sample_score = min(sample_size / 50, 1.0) * 0.5
    coverage_score = (summary_count / question_count if question_count else 0) * 0.3
    completeness_score = 0.2 if sample_size > 0 else 0.0
    return round(min(sample_score + coverage_score + completeness_score, 0.88), 2)


def _strip_sensitive_question_summaries(question_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = []
    for summary in question_summaries:
        item = dict(summary)
        item.pop("text_samples", None)
        item["summary"] = _redact_sensitive_text(str(item.get("summary", "")))
        cleaned.append(item)
    return cleaned


def _redact_sensitive_text(text: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[redacted_email]", text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[redacted_phone]", text)
    text = re.sub(r"\b\d{3}-\d{3}-\d{4}\b", "[redacted_phone]", text)
    return text


def survey_error_response(exc: Exception) -> tuple[int, dict[str, Any]]:
    if isinstance(exc, SurveyLLMConfigurationError):
        return 503, {
            "error": "SURVEY_LLM_API_KEY is not configured",
            "message": "请在 .env 文件中配置 SURVEY_LLM_API_KEY 后再使用问卷生成功能。",
        }
    if isinstance(exc, SurveyCsvFormatError):
        return 400, {"error": "CSV 格式无法识别", "message": str(exc)}
    if isinstance(exc, SurveyCsvValidationError):
        return 400, {"error": "CSV 缺少必要字段", "missing_fields": exc.missing_fields}
    return 500, {"error": "survey_module_error", "message": str(exc)}


def _contains_any(text: str, needles: list[str]) -> bool:
    normalized = text.lower().replace(" ", "")
    return any(needle.lower().replace(" ", "") in normalized for needle in needles)


def _extract_question_limit(text: str) -> int | None:
    import re

    match = re.search(r"(\d+)\s*[个道]?\s*题", text)
    if not match:
        match = re.search(r"(\d+)\s*个以内", text)
    if not match:
        return None
    limit = int(match.group(1))
    return limit if limit > 0 else None


def _has_field(questions: list[dict[str, Any]], field_name: str) -> bool:
    return any(question.get("field_name") == field_name for question in questions)


def _remove_questions_by_field(questions: list[dict[str, Any]], fields: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    kept = []
    removed = []
    for question in questions:
        if question.get("field_name") in fields:
            removed.append({"question_id": str(question.get("question_id", "")), "reason": "根据修改要求删除"})
        else:
            kept.append(question)
    return kept, removed


def _trim_demo_questions(questions: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    priority = {
        "purchase_priority": 100,
        "switching_reason": 95,
        "max_budget": 90,
        "premium_willingness": 88,
        "battery_satisfaction": 85,
        "camera_importance": 80,
        "ai_feature_interest": 70,
        "current_brand": 60,
        "open_feedback": 40,
    }
    ranked = sorted(
        enumerate(questions),
        key=lambda item: (-priority.get(str(item[1].get("field_name")), 50), item[0]),
    )
    keep_indexes = {index for index, _question in ranked[:limit]}
    kept = [question for index, question in enumerate(questions) if index in keep_indexes]
    removed = [
        {"question_id": str(question.get("question_id", "")), "reason": f"为满足 {limit} 题以内的要求而删除低优先级问题"}
        for index, question in enumerate(questions)
        if index not in keep_indexes
    ]
    return kept, removed
