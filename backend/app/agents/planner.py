from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from app.agents.base import run_with_trace
from app.domain import domain_pack_reference, resolve_domain_pack
from app.schemas import (
    AnalysisDimension,
    AnalysisDimensionPlan,
    Dag,
    DagEdge,
    DagNode,
    PlannerCompetitorCandidate,
    PlannerDownstreamGuidance,
    PlannerExtractedContext,
    PlannerInput,
    PlannerOutput,
    PlannerScopeSnapshot,
    PlannerStage,
    PlannerSurveyInput,
    QuestionnaireFollowUpRecommendation,
    Task,
)
from app.services.llm_client import LlmClient, parse_llm_json
from app.services.trace_service import TraceService


class PlannerAgent:
    name = "PlannerAgent"
    ALLOWED_DIMENSIONS = {
        "feature",
        "pricing",
        "persona",
        "positioning",
        "feedback",
        "prioritization",
        "ux",
        "risk",
        "market",
        "hypothesis",
    }
    CATEGORY_COMPETITOR_CANDIDATES = {
        "smartphone": [
            ("Apple iPhone", "Global flagship smartphone benchmark leader.", 0.93),
            ("Samsung Galaxy", "Core Android flagship benchmark reference.", 0.92),
            ("Xiaomi", "Price-performance flagship competitor.", 0.88),
            ("Huawei", "Premium flagship and ecosystem competitor.", 0.87),
            ("Honor", "Mainstream premium smartphone competitor.", 0.82),
            ("OnePlus", "Performance-oriented flagship competitor.", 0.8),
        ],
        "crm": [
            ("Salesforce", "Category-leading enterprise CRM benchmark.", 0.94),
            ("HubSpot", "SMB and mid-market CRM benchmark.", 0.9),
            ("Microsoft Dynamics 365", "Enterprise CRM suite benchmark.", 0.88),
            ("Zoho CRM", "Value-oriented CRM benchmark.", 0.82),
            ("Pipedrive", "Sales-pipeline-focused CRM benchmark.", 0.78),
        ],
        "ai_note_taking": [
            ("Notion AI", "Popular AI-assisted workspace and notes benchmark.", 0.9),
            ("Microsoft OneNote", "Large-scale note-taking incumbent.", 0.84),
            ("Evernote", "Longstanding note-taking benchmark.", 0.8),
            ("Obsidian", "Power-user knowledge and notes benchmark.", 0.79),
            ("Mem", "AI-native notes and recall benchmark.", 0.76),
            ("Fireflies.ai", "Meeting-note and AI transcription competitor.", 0.75),
        ],
    }

    def __init__(self, trace_service: TraceService, llm_client: LlmClient | None = None):
        self.trace_service = trace_service
        self.llm_client = llm_client or LlmClient()

    def run(self, input_data: PlannerInput) -> PlannerOutput:
        task = input_data.task

        def produce() -> PlannerOutput:
            return self._plan(task)

        return run_with_trace(
            trace_service=self.trace_service,
            task_id=task.task_id,
            run_id=input_data.run_id,
            agent_name=self.name,
            to_agent="CollectorAgent",
            message_type="plan",
            schema_name="PlannerOutput",
            input_summary=f"{task.product_name} vs {', '.join(task.competitors)}; region={task.region}; industry={task.industry}",
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def _plan(self, task: Task) -> PlannerOutput:
        diagnostics = self._base_diagnostics()
        messages = self._messages(task)
        llm_response = self.llm_client.chat_json(messages)
        diagnostics.update(
            {
                "llm_call_attempted": llm_response.attempted,
                "llm_call_success": llm_response.success,
                "llm_elapsed_time_ms": llm_response.elapsed_time_ms,
                "llm_error_type": llm_response.error_type,
                "llm_error_message": llm_response.error_message,
                "llm_response_preview": llm_response.response_preview,
            }
        )

        if not llm_response.available:
            diagnostics.update(
                {
                    "planner_mode_used": "deterministic",
                    "fallback_used": True,
                    "llm_fallback_reason": llm_response.fallback_reason,
                }
            )
            return self._deterministic_output(task, llm_response.fallback_reason, diagnostics)

        try:
            payload = self._normalize_llm_payload(parse_llm_json(llm_response.content or ""))
            diagnostics.update(
                {
                    "planner_mode_used": "llm_enhanced",
                    "llm_schema_validation_success": True,
                    "llm_schema_validation_errors": [],
                }
            )
            return self._enhanced_output_from_payload(task, payload, diagnostics)
        except Exception as exc:  # noqa: BLE001 - planner must not break workflow execution.
            diagnostics.update(
                {
                    "planner_mode_used": "deterministic",
                    "fallback_used": True,
                    "llm_fallback_reason": f"Planner LLM enhancement failed: {exc}",
                    "llm_schema_validation_success": False,
                    "llm_schema_validation_errors": [str(exc)],
                }
            )
            return self._deterministic_output(task, f"Planner LLM enhancement failed: {exc}", diagnostics)

    def _enhanced_output_from_payload(self, task: Task, payload: dict[str, Any], diagnostics: dict[str, Any]) -> PlannerOutput:
        extracted = self._build_extracted_context(task, payload)
        selected_dimensions = self._resolve_selected_dimensions(
            task,
            raw_dimensions=payload.get("selected_dimensions"),
            intent=extracted.intent_classification,
            focus_points=extracted.analysis_focus_points,
        )
        planning_profile = self._build_planning_profile(task, extracted, selected_dimensions)
        survey_needed, survey_recommended, survey_reason = self._resolve_survey_signals_refined(task, extracted, planning_profile["scope_type"])
        extracted = extracted.model_copy(update={"survey_needed": survey_needed, "survey_reason": survey_reason})
        analysis_dimension_plan = self._build_dimension_plan(
            task,
            extracted,
            selected_dimensions,
            extracted.analysis_focus_points,
            survey_needed,
            planning_profile=planning_profile,
        )
        survey_inputs = self._build_survey_inputs(payload.get("survey_inputs"), extracted)
        downstream_guidance = self._build_guidance(
            task,
            extracted=extracted,
            selected_dimensions=selected_dimensions,
            survey_inputs=survey_inputs,
            planning_profile=planning_profile,
        )
        scope_snapshots = self._build_scope_snapshots(task, extracted, selected_dimensions, planning_profile["candidate_competitors"])
        resolved_domain_pack = self._resolved_domain_pack(task, extracted)
        domain_pack = self._domain_pack_reference(task, extracted, planning_profile["scope_type"])
        planner_notes = self._normalize_string_list(payload.get("planner_notes"))
        planner_notes.append("Planner 已使用 LLM 增强意图抽取，并通过确定性规则归一化计划。")
        planner_notes.append("Planner 会区分用户确认范围、系统推断范围和建议范围；confirmed user scope remains separated from inferred and suggested scope metadata.")
        missing_information = self._build_missing_information(task, extracted, planning_profile, payload.get("missing_information"))
        confidence = self._safe_confidence(payload.get("confidence", extracted.confidence), fallback=extracted.confidence or 0.75)
        diagnostics.update(
            {
                "intent_classification": extracted.intent_classification,
                "survey_needed": survey_needed,
                "survey_recommended": survey_recommended,
                "selected_dimension_count": len(selected_dimensions),
                "ambiguity_level": planning_profile["ambiguity_level"],
                "scope_type": planning_profile["scope_type"],
                "scope_size": planning_profile["scope_size"],
                "candidate_competitor_count": len(planning_profile["candidate_competitors"]),
                "domain_pack_id": resolved_domain_pack.domain_pack_id,
                "domain_pack_category_key": resolved_domain_pack.category_key,
            }
        )
        questionnaire_follow_up = self._build_questionnaire_follow_up(
            extracted=extracted,
            selected_dimensions=selected_dimensions,
            survey_needed=survey_needed,
            survey_recommended=survey_recommended,
            survey_inputs=survey_inputs,
            domain_pack=domain_pack,
            downstream_guidance=downstream_guidance,
        )
        return PlannerOutput(
            dag=self._default_dag(),
            plan=self._default_plan(),
            intent_summary=self._safe_text(
                payload.get("intent_summary"),
                fallback=self._default_intent_summary(task, extracted.intent_classification, survey_needed),
            ),
            intent_classification=extracted.intent_classification,
            ambiguity_level=planning_profile["ambiguity_level"],
            scope_type=planning_profile["scope_type"],
            scope_size=planning_profile["scope_size"],
            extracted_context=extracted,
            domain_pack=domain_pack,
            selected_dimensions=selected_dimensions,
            analysis_dimension_plan=analysis_dimension_plan,
            survey_needed=survey_needed,
            survey_recommended=survey_recommended,
            survey_objective=survey_inputs.objective if survey_inputs else None,
            survey_inputs=survey_inputs,
            questionnaire_follow_up=questionnaire_follow_up,
            confirmed_scope=scope_snapshots["confirmed_scope"],
            inferred_scope=scope_snapshots["inferred_scope"],
            suggested_scope=scope_snapshots["suggested_scope"],
            recommended_next_constraints=planning_profile["recommended_next_constraints"],
            assumptions=planning_profile["assumptions"],
            candidate_competitors=planning_profile["candidate_competitors"],
            clarification_targets=planning_profile["clarification_targets"],
            planning_stages=planning_profile["planning_stages"],
            missing_information=missing_information,
            planner_notes=self._dedupe(planner_notes),
            confidence=confidence,
            downstream_guidance=downstream_guidance,
            diagnostics=diagnostics,
        )

    def _deterministic_output(
        self,
        task: Task,
        fallback_reason: str | None,
        diagnostics: dict[str, Any],
    ) -> PlannerOutput:
        intent = self._infer_intent_from_task(task)
        initial_survey_needed = self._deterministic_survey_needed(task, intent)
        focus_points = self._deterministic_focus_points(task, intent)
        selected_dimensions = self._resolve_selected_dimensions(
            task,
            raw_dimensions=None,
            intent=intent,
            focus_points=focus_points,
        )
        extracted = PlannerExtractedContext(
            intent_classification=intent,
            industry=task.industry,
            domain=task.industry,
            product_name=task.product_name,
            product_type=task.industry,
            target_users=self._infer_target_users(task),
            region=task.region,
            competitors_mentioned=self._valid_competitors(task.competitors),
            analysis_focus_points=focus_points,
            requested_outputs=self._deterministic_requested_outputs(task, initial_survey_needed),
            survey_needed=initial_survey_needed,
            survey_reason=self._deterministic_survey_reason(task, intent) if initial_survey_needed else None,
            missing_information=[],
            confidence=0.35,
        )
        planning_profile = self._build_planning_profile(task, extracted, selected_dimensions)
        survey_needed, survey_recommended, survey_reason = self._resolve_survey_signals_refined(task, extracted, planning_profile["scope_type"])
        extracted = extracted.model_copy(update={"survey_needed": survey_needed, "survey_reason": survey_reason})
        survey_inputs = self._build_survey_inputs({}, extracted)
        analysis_dimension_plan = self._build_dimension_plan(
            task,
            extracted,
            selected_dimensions,
            extracted.analysis_focus_points,
            survey_needed,
            planning_profile=planning_profile,
        )
        planner_notes = ["Planner 使用确定性兜底逻辑。"]
        planner_notes.append("Planner 会区分用户确认范围、系统推断范围和建议范围；confirmed user scope remains separated from inferred and suggested scope metadata.")
        if fallback_reason:
            planner_notes.append(fallback_reason)
        downstream_guidance = self._build_guidance(
            task,
            extracted=extracted,
            selected_dimensions=selected_dimensions,
            survey_inputs=survey_inputs,
            planning_profile=planning_profile,
        )
        scope_snapshots = self._build_scope_snapshots(task, extracted, selected_dimensions, planning_profile["candidate_competitors"])
        resolved_domain_pack = self._resolved_domain_pack(task, extracted)
        domain_pack = self._domain_pack_reference(task, extracted, planning_profile["scope_type"])
        missing_information = self._build_missing_information(task, extracted, planning_profile, None)
        extracted = extracted.model_copy(update={"missing_information": missing_information})
        diagnostics.update(
            {
                "intent_classification": extracted.intent_classification,
                "survey_needed": survey_needed,
                "survey_recommended": survey_recommended,
                "selected_dimension_count": len(selected_dimensions),
                "ambiguity_level": planning_profile["ambiguity_level"],
                "scope_type": planning_profile["scope_type"],
                "scope_size": planning_profile["scope_size"],
                "candidate_competitor_count": len(planning_profile["candidate_competitors"]),
                "domain_pack_id": resolved_domain_pack.domain_pack_id,
                "domain_pack_category_key": resolved_domain_pack.category_key,
            }
        )
        questionnaire_follow_up = self._build_questionnaire_follow_up(
            extracted=extracted,
            selected_dimensions=selected_dimensions,
            survey_needed=survey_needed,
            survey_recommended=survey_recommended,
            survey_inputs=survey_inputs,
            domain_pack=domain_pack,
            downstream_guidance=downstream_guidance,
        )
        return PlannerOutput(
            dag=self._default_dag(),
            plan=self._default_plan(),
            intent_summary=self._default_intent_summary(task, intent, survey_needed),
            intent_classification=intent,
            ambiguity_level=planning_profile["ambiguity_level"],
            scope_type=planning_profile["scope_type"],
            scope_size=planning_profile["scope_size"],
            extracted_context=extracted,
            domain_pack=domain_pack,
            selected_dimensions=selected_dimensions,
            analysis_dimension_plan=analysis_dimension_plan,
            survey_needed=survey_needed,
            survey_recommended=survey_recommended,
            survey_objective=survey_inputs.objective if survey_inputs else None,
            survey_inputs=survey_inputs,
            questionnaire_follow_up=questionnaire_follow_up,
            confirmed_scope=scope_snapshots["confirmed_scope"],
            inferred_scope=scope_snapshots["inferred_scope"],
            suggested_scope=scope_snapshots["suggested_scope"],
            recommended_next_constraints=planning_profile["recommended_next_constraints"],
            assumptions=planning_profile["assumptions"],
            candidate_competitors=planning_profile["candidate_competitors"],
            clarification_targets=planning_profile["clarification_targets"],
            planning_stages=planning_profile["planning_stages"],
            missing_information=missing_information,
            planner_notes=planner_notes,
            confidence=0.35,
            downstream_guidance=downstream_guidance,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _build_questionnaire_follow_up(
        *,
        extracted: PlannerExtractedContext,
        selected_dimensions: list[str],
        survey_needed: bool,
        survey_recommended: bool,
        survey_inputs: PlannerSurveyInput | None,
        domain_pack,
        downstream_guidance: PlannerDownstreamGuidance | None,
    ) -> QuestionnaireFollowUpRecommendation:
        return QuestionnaireFollowUpRecommendation(
            recommended=survey_recommended,
            required=survey_needed,
            objective=survey_inputs.objective if survey_inputs else None,
            respondent_type=survey_inputs.respondent_type if survey_inputs else None,
            question_themes=list(survey_inputs.question_themes) if survey_inputs else [],
            hypotheses=list(survey_inputs.hypotheses) if survey_inputs else [],
            guidance=list(downstream_guidance.survey) if downstream_guidance else [],
            selected_dimensions=list(selected_dimensions),
            domain_pack=domain_pack,
            rationale=extracted.survey_reason,
            metadata={
                "intent_classification": extracted.intent_classification,
                "requested_outputs": list(extracted.requested_outputs),
            },
        )

    def _base_diagnostics(self) -> dict[str, Any]:
        return {
            "planner_mode_requested": "llm_enhanced",
            "planner_mode_used": "deterministic",
            "llm_enabled": self.llm_client.is_available,
            "llm_provider": self.llm_client.provider,
            "llm_model": self.llm_client.model,
            "llm_base_url_configured": bool(self.llm_client.base_url),
            "has_api_key": self.llm_client.is_available,
            "llm_call_attempted": False,
            "llm_call_success": False,
            "llm_elapsed_time_ms": 0,
            "llm_error_type": None,
            "llm_error_message": None,
            "llm_response_preview": None,
            "llm_schema_validation_success": None,
            "llm_schema_validation_errors": [],
            "fallback_used": False,
            "llm_fallback_reason": None,
        }

    def _default_dag(self) -> Dag:
        return Dag(
            nodes=[
                DagNode(id="PlannerAgent", label="规划任务范围和 DAG", status="completed"),
                DagNode(id="CollectorAgent", label="采集 Mock 证据", status="pending"),
                DagNode(id="EvidenceGate", label="相关证据前置校验", status="pending"),
                DagNode(id="PageFetcher", label="抓取轻量正文摘要", status="pending"),
                DagNode(id="AnalystAgent", label="抽取结构化竞品知识", status="pending"),
                DagNode(id="ReportWriterAgent", label="撰写带证据报告", status="pending"),
                DagNode(id="QaAgent", label="校验 Schema 和证据", status="pending"),
                DagNode(id="FinalReport", label="最终报告", status="pending"),
            ],
            edges=[
                DagEdge(source="PlannerAgent", target="CollectorAgent", label="计划"),
                DagEdge(source="CollectorAgent", target="EvidenceGate", label="证据"),
                DagEdge(source="EvidenceGate", target="PageFetcher", label="相关证据通过"),
                DagEdge(source="PageFetcher", target="AnalystAgent", label="正文摘要"),
                DagEdge(source="AnalystAgent", target="ReportWriterAgent", label="知识"),
                DagEdge(source="ReportWriterAgent", target="QaAgent", label="草稿"),
                DagEdge(source="QaAgent", target="FinalReport", label="通过"),
                DagEdge(source="QaAgent", target="CollectorAgent", label="缺少证据"),
                DagEdge(source="QaAgent", target="AnalystAgent", label="抽取错误"),
                DagEdge(source="QaAgent", target="ReportWriterAgent", label="格式错误"),
            ],
        )

    @staticmethod
    def _default_plan() -> list[str]:
        return [
            "Collect competitor evidence for positioning, features, pricing, and target users.",
            "Normalize findings into structured competitor knowledge schemas.",
            "Produce source-bound claims with evidence_ids and run QA validation.",
        ]

    def _build_extracted_context(self, task: Task, payload: dict[str, Any]) -> PlannerExtractedContext:
        raw_context = payload.get("extracted_context")
        if isinstance(raw_context, dict):
            merged_context = dict(raw_context)
        else:
            merged_context = {}
        merged_context.setdefault("intent_classification", payload.get("intent_classification", self._infer_intent_from_task(task)))
        merged_context.setdefault("industry", payload.get("industry", task.industry))
        merged_context.setdefault("domain", payload.get("field") or payload.get("domain") or task.industry)
        merged_context.setdefault("product_name", payload.get("product_name", task.product_name))
        merged_context.setdefault("product_type", payload.get("product_type", task.industry))
        merged_context.setdefault("target_users", payload.get("target_users") or self._infer_target_users(task))
        merged_context.setdefault("region", self._resolved_region(task, payload.get("region")))
        merged_context.setdefault(
            "competitors_mentioned",
            self._explicit_competitors_for_task(task, payload.get("competitors_mentioned") or merged_context.get("competitors_mentioned")),
        )
        merged_context.setdefault("analysis_focus_points", payload.get("analysis_focus_points") or self._deterministic_focus_points(task, merged_context["intent_classification"]))
        merged_context.setdefault("requested_outputs", payload.get("requested_outputs") or self._deterministic_requested_outputs(task, bool(payload.get("survey_needed"))))
        merged_context.setdefault("survey_needed", payload.get("survey_needed", self._deterministic_survey_needed(task, merged_context["intent_classification"])))
        merged_context.setdefault("survey_reason", payload.get("survey_reason"))
        merged_context.setdefault("missing_information", self._clean_missing_information(payload.get("missing_information")))
        merged_context.setdefault("confidence", self._safe_confidence(payload.get("confidence"), fallback=0.8))
        try:
            context = PlannerExtractedContext.model_validate(merged_context)
        except ValidationError as exc:
            raise ValueError(f"Planner extracted_context validation failed: {exc}") from exc
        if context.survey_needed and not context.survey_reason:
            context = context.model_copy(update={"survey_reason": self._deterministic_survey_reason(task, context.intent_classification)})
        return context

    def _build_dimension_plan(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        selected_dimensions: list[str],
        focus_points: list[str],
        survey_needed: bool,
        *,
        planning_profile: dict[str, Any],
    ) -> AnalysisDimensionPlan:
        dimension_plans: list[AnalysisDimension] = []
        resolved_domain_pack = self._resolved_domain_pack(task, extracted)
        category_key = resolved_domain_pack.category_key or self._category_key(task, extracted)
        industry_label = self._effective_industry(task, extracted)
        for priority, dimension in enumerate(selected_dimensions, start=1):
            keywords = self._dimension_keywords(task, extracted, dimension, category_key=category_key)
            dimension_plans.append(
                AnalysisDimension(
                    dimension_id=dimension,
                    label=dimension.replace("_", " ").title(),
                    description=f"Planner-selected dimension for {industry_label} analysis.",
                    keywords=self._dedupe([word for word in keywords if word]),
                    required=self._dimension_required(dimension, extracted_intent=planning_profile["intent_classification"]),
                    priority=priority,
                    metadata={"owner": "planner", "survey_related": dimension in {"feedback", "hypothesis", "prioritization"}},
                )
            )
        research_goals = [
            *[f"Compare competitor signals for {focus_point}." for focus_point in focus_points[:4]],
            "Confirm competitor-specific evidence before strong conclusions.",
        ]
        if survey_needed:
            research_goals.append("Prepare structured feedback or questionnaire objectives for downstream survey support.")
        query_hints = {
            candidate.name: self._build_query_hints_for_competitor(
                candidate.name,
                task,
                extracted,
                selected_dimensions,
                survey_needed,
                scope_type=planning_profile["scope_type"],
            )
            for candidate in planning_profile["candidate_competitors"]
        }
        category_hints = self._category_query_hints(task, extracted, selected_dimensions, survey_needed, planning_profile["scope_type"])
        if category_hints:
            query_hints["category_scope"] = category_hints
        return AnalysisDimensionPlan(
            selected_dimensions=selected_dimensions,
            dimension_plans=dimension_plans,
            research_goals=self._dedupe(research_goals),
            query_hints=query_hints,
            metadata={
                "planner_owner": "PlannerAgent",
                "survey_needed": survey_needed,
                "focus_points": focus_points,
                "ambiguity_level": planning_profile["ambiguity_level"],
                "scope_type": planning_profile["scope_type"],
                "scope_size": planning_profile["scope_size"],
                "domain_pack_id": resolved_domain_pack.domain_pack_id,
                "clarification_targets": planning_profile["clarification_targets"],
                "planning_stage_ids": [stage.stage_id for stage in planning_profile["planning_stages"]],
                "candidate_competitors": [candidate.name for candidate in planning_profile["candidate_competitors"]],
            },
        )

    def _build_survey_inputs(self, raw_inputs: Any, extracted: PlannerExtractedContext) -> PlannerSurveyInput | None:
        if not extracted.survey_needed:
            return None
        data = raw_inputs if isinstance(raw_inputs, dict) else {}
        objective = self._safe_text(
            data.get("objective"),
            fallback=f"Collect structured user feedback for {extracted.product_name or 'the target product'} and its competitors.",
        )
        respondent_type = self._safe_text(
            data.get("respondent_type"),
            fallback="Current users, target buyers, or evaluators who can compare alternatives.",
        )
        question_themes = self._normalize_string_list(data.get("question_themes"))
        if not question_themes:
            question_themes = ["pain points", "improvement opportunities", "feature prioritization", "adoption barriers"]
        hypotheses = self._normalize_string_list(data.get("hypotheses"))
        if not hypotheses:
            hypotheses = ["Users have unmet workflow pain points that existing competitors do not fully address."]
        return PlannerSurveyInput(
            objective=objective,
            respondent_type=respondent_type,
            question_themes=question_themes,
            hypotheses=hypotheses,
            metadata={"planner_generated": True, "survey_reason": extracted.survey_reason},
        )

    def _build_guidance(
        self,
        task: Task,
        *,
        extracted: PlannerExtractedContext,
        selected_dimensions: list[str],
        survey_inputs: PlannerSurveyInput | None,
        planning_profile: dict[str, Any],
    ) -> PlannerDownstreamGuidance:
        candidate_names = ", ".join(candidate.name for candidate in planning_profile["candidate_competitors"][:5])
        effective_region = extracted.region or task.region
        effective_industry = self._effective_industry(task, extracted)
        collector = [
            f"Prioritize competitor-specific public evidence for: {', '.join(selected_dimensions)}.",
            f"Use region filter '{effective_region}' and industry context '{effective_industry}' when forming evidence queries.",
            "Prefer official, documentation, pricing, and high-relevance sources before weaker public mentions.",
        ]
        if planning_profile["scope_type"] in {"category_scan", "broad_competitive_analysis", "strategic_ambiguous", "semi_specific_benchmark"} and candidate_names:
            collector.append(f"Treat competitor discovery as part of collection and validate these likely candidates first: {candidate_names}.")
        analyst = [
            f"Compare competitors through these dimensions: {', '.join(selected_dimensions)}.",
            "Keep conclusions conservative when evidence coverage is thin or competitor-specific signals are missing.",
            "Separate confirmed evidence from assumptions and unresolved gaps.",
        ]
        writer = [
            f"Emphasize {extracted.intent_classification.replace('_', ' ')} in the report framing.",
            "Structure claims around competitor-specific evidence, uncertainty, and validation next steps.",
            "Call out evidence gaps explicitly instead of smoothing them over.",
        ]
        if planning_profile["ambiguity_level"] != "low":
            writer.append("Frame broad or underspecified requests as staged analysis, and clearly separate inferred scope from confirmed scope.")
        if planning_profile["scope_type"] in {"category_scan", "broad_competitive_analysis"}:
            writer.append("Use category-scan wording until a final competitor set and user segment are confirmed.")
        qa = [
            "Verify that claims align with the requested intent and focus points.",
            "Check competitor coverage, evidence relevance, and unsupported cross-competitor inference.",
            "If survey support is requested, ensure the report does not pretend survey data already exists.",
        ]
        if planning_profile["clarification_targets"]:
            qa.append(f"Flag unresolved scope assumptions around: {', '.join(planning_profile['clarification_targets'])}.")
        survey = []
        if survey_inputs:
            survey = [
                f"Objective: {survey_inputs.objective}",
                f"Respondent type: {survey_inputs.respondent_type}",
                f"Question themes: {', '.join(survey_inputs.question_themes)}.",
                "Keep outputs aggregate-only and safe for conversion into SurveyEvidence.",
            ]
        return PlannerDownstreamGuidance(
            collector=collector,
            analyst=analyst,
            writer=writer,
            qa=qa,
            survey=survey,
        )

    def _build_planning_profile(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        selected_dimensions: list[str],
    ) -> dict[str, Any]:
        text = self._task_text(task)
        valid_competitors = extracted.competitors_mentioned or self._valid_competitors(task.competitors)
        explicit_compare = any(keyword in text for keyword in ("compare", "benchmark", "vs", "versus", "对比", "优劣", "竞品"))
        broad_category = self._is_broad_category_request(text, self._effective_industry(task, extracted))
        self_referential = any(
            keyword in text
            for keyword in ("our product", "our crm", "our tool", "our app", "our platform", "this product", "this tool", "this app", "我们的产品", "这个产品")
        )
        mixed_intent = self._has_survey_language(text) and any(
            keyword in text for keyword in ("analyze", "analysis", "compare", "benchmark", "分析", "对比", "improve", "improvement", "priorit")
        )
        specificity = self._specificity_profile(task, extracted)
        competitor_specific = specificity["explicit_competitor_count"] >= 2 and specificity["concrete_focus_count"] >= 2

        if self_referential and specificity["explicit_competitor_count"] == 0 and specificity["score"] <= 2:
            scope_type = "strategic_ambiguous"
        elif broad_category and specificity["explicit_competitor_count"] == 0:
            scope_type = "category_scan"
        elif competitor_specific and specificity["score"] >= 4:
            scope_type = "specific_product_benchmark"
        elif mixed_intent and specificity["score"] <= 2 and specificity["explicit_competitor_count"] <= 1:
            scope_type = "mixed_intent"
        elif broad_category and valid_competitors:
            scope_type = "semi_specific_benchmark"
        elif specificity["explicit_competitor_count"] >= 2 or explicit_compare:
            scope_type = "specific_product_benchmark"
        else:
            scope_type = "broad_competitive_analysis"

        if scope_type == "specific_product_benchmark":
            ambiguity_level = "low" if specificity["score"] >= 4 else "medium"
            scope_size = "narrow" if specificity["explicit_competitor_count"] >= 2 else "medium"
        elif scope_type in {"semi_specific_benchmark", "mixed_intent"}:
            ambiguity_level = "medium" if specificity["score"] >= 2 else "high"
            scope_size = "medium"
        else:
            ambiguity_level = "high"
            scope_size = "broad"
        candidate_competitors = self._candidate_competitors(task, extracted, scope_type)
        clarification_targets = self._clarification_targets(task, extracted, selected_dimensions, scope_type)
        recommended_next_constraints = self._recommended_next_constraints(task, extracted, selected_dimensions, clarification_targets, scope_type)
        assumptions = self._assumptions(task, extracted, scope_type)
        planning_stages = self._planning_stages(task, extracted, selected_dimensions, scope_type, candidate_competitors)
        return {
            "ambiguity_level": ambiguity_level,
            "scope_type": scope_type,
            "scope_size": scope_size,
            "intent_classification": extracted.intent_classification,
            "candidate_competitors": candidate_competitors,
            "clarification_targets": clarification_targets,
            "recommended_next_constraints": recommended_next_constraints,
            "assumptions": assumptions,
            "planning_stages": planning_stages,
        }

    def _build_missing_information(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        planning_profile: dict[str, Any],
        raw_missing_information: Any,
    ) -> list[str]:
        base = self._clean_missing_information(raw_missing_information)
        missing = [
            *self._clean_missing_information(extracted.missing_information),
            *base,
            *self._default_missing_information(task),
        ]
        for target in planning_profile["clarification_targets"]:
            if target == "competitor_set":
                missing.append("Competitor set is still underspecified; confirm 3-6 named competitors or accept a planner-inferred category scan shortlist.")
            elif target == "target_region":
                missing.append("Target region is still broad; specify the primary market such as China, US, EU, or Global enterprise.")
            elif target == "target_users":
                missing.append("Target user segment is unclear; specify buyer type, user role, or user maturity segment.")
            elif target == "comparison_dimensions":
                missing.append("Comparison dimensions are still broad; confirm whether to emphasize feature, pricing, UX, persona, or prioritization.")
            elif target == "output_type":
                missing.append("Output type is not fully constrained; confirm whether the main deliverable is a benchmark report, opportunity map, survey brief, or prioritization memo.")
            elif target == "business_purpose":
                missing.append("Business purpose is unclear; specify whether the analysis should support strategy, product improvement, GTM messaging, or validation.")
            elif target == "subject_product":
                missing.append("The subject product is underspecified; provide product name, product category, or internal product context to anchor the comparison.")
        return self._dedupe(missing)

    @classmethod
    def _resolve_selected_dimensions(
        cls,
        task: Task,
        *,
        raw_dimensions: Any,
        intent: str,
        focus_points: list[str],
    ) -> list[str]:
        base = cls._normalize_dimensions(raw_dimensions, intent)
        text = cls._task_text(task)
        boosted = list(base)
        if any(keyword in text for keyword in ("ux", "usability", "体验", "note-taking", "notes")):
            boosted.insert(0, "ux")
        if any(keyword in text for keyword in ("crm", "saas", "workflow", "sales")):
            boosted.append("positioning")
        if any(keyword in text for keyword in ("improve", "improvement", "feedback", "pain point", "priorit", "改进", "痛点")):
            boosted.extend(["feedback", "prioritization"])
        if any("pricing" in point.lower() or "定价" in point for point in focus_points):
            boosted.append("pricing")
        cleaned = [dimension for dimension in boosted if dimension in cls.ALLOWED_DIMENSIONS]
        return cls._dedupe(cleaned)[:6]

    @staticmethod
    def _task_text(task: Task) -> str:
        return f"{task.product_name} {task.industry} {' '.join(task.competitors)} {task.region}".lower()

    @staticmethod
    def _valid_competitors(competitors: list[str]) -> list[str]:
        placeholders = {"tbd", "unknown", "competitor", "competitors", "other", "others", "n/a", "待定", "未知"}
        valid: list[str] = []
        for competitor in competitors:
            clean = competitor.strip()
            normalized = clean.lower().replace(" ", "")
            if not clean or normalized in placeholders:
                continue
            valid.append(clean)
        return valid

    @classmethod
    def _is_broad_category_request(cls, text: str, industry: str) -> bool:
        combined = f"{text} {industry.lower()}"
        markers = (
            "flagship smartphone",
            "smartphone",
            "phone",
            "crm",
            "note-taking",
            "note taking",
            "tools",
            "tool",
            "category",
            "market",
            "analyze flagship smartphones",
            "compare ai note-taking tools",
            "benchmark our crm",
        )
        return any(marker in combined for marker in markers)

    def _candidate_competitors(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        scope_type: str,
    ) -> list[PlannerCompetitorCandidate]:
        valid_competitors = extracted.competitors_mentioned or self._valid_competitors(task.competitors)
        candidates: list[PlannerCompetitorCandidate] = []
        seen: set[str] = set()
        for priority, competitor in enumerate(valid_competitors, start=1):
            candidates.append(
                PlannerCompetitorCandidate(
                    name=competitor,
                    reason="Provided directly in the task input.",
                    confidence=0.95 if len(valid_competitors) <= 4 else 0.85,
                    priority=priority,
                    metadata={"source": "task.competitors"},
                )
            )
            seen.add(competitor.lower())

        if len(valid_competitors) >= 2 and scope_type in {"specific_product_benchmark", "semi_specific_benchmark", "mixed_intent"}:
            return candidates[:6]

        resolved_domain_pack = self._resolved_domain_pack(task, extracted)
        category_key = resolved_domain_pack.category_key or self._category_key(task, extracted)
        inferred = list(resolved_domain_pack.competitor_candidates) or self.CATEGORY_COMPETITOR_CANDIDATES.get(category_key, [])
        start = len(candidates) + 1
        for offset, (name, reason, confidence) in enumerate(inferred, start=start):
            if name.lower() in seen:
                continue
            candidates.append(
                PlannerCompetitorCandidate(
                    name=name,
                    reason=reason,
                    confidence=confidence if scope_type != "specific_product_benchmark" else min(confidence, 0.72),
                    priority=offset,
                    metadata={"source": f"planner_inference:{category_key or 'generic'}"},
                )
            )
            seen.add(name.lower())
        return candidates[:6]

    def _clarification_targets(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        selected_dimensions: list[str],
        scope_type: str,
    ) -> list[str]:
        targets: list[str] = []
        if scope_type in {"category_scan", "broad_competitive_analysis", "strategic_ambiguous"}:
            targets.append("competitor_set")
        if self._is_generic_region(task.region):
            targets.append("target_region")
        if extracted.target_users == ["target users not clearly specified"] or not extracted.target_users:
            targets.append("target_users")
        if scope_type in {"category_scan", "broad_competitive_analysis", "strategic_ambiguous"} and len(selected_dimensions) <= 4:
            targets.append("comparison_dimensions")
        if scope_type == "strategic_ambiguous":
            targets.append("subject_product")
        if extracted.requested_outputs == ["competitive report", "structured competitor knowledge", "traceable claims"]:
            targets.append("output_type")
        if any(keyword in self._task_text(task) for keyword in ("our product", "this product", "benchmark our crm", "what should we improve", "改进")):
            targets.append("business_purpose")
        return self._dedupe(targets)

    @staticmethod
    def _recommended_next_constraints(
        task: Task,
        extracted: PlannerExtractedContext,
        selected_dimensions: list[str],
        clarification_targets: list[str],
        scope_type: str,
    ) -> list[str]:
        recommendations: list[str] = []
        if "competitor_set" in clarification_targets:
            recommendations.append("Confirm a final competitor shortlist, for example 3-6 named competitors, before heavy evidence collection.")
        if "target_region" in clarification_targets:
            recommendations.append("Narrow the primary market or region so pricing, positioning, and relevance checks stay consistent.")
        if "target_users" in clarification_targets:
            recommendations.append("Specify the target buyer or user segment to sharpen persona and messaging analysis.")
        if "comparison_dimensions" in clarification_targets:
            recommendations.append(f"Prioritize 3-5 comparison dimensions from: {', '.join(selected_dimensions[:5])}.")
        if "output_type" in clarification_targets:
            recommendations.append("Confirm whether the main output should be a benchmark report, improvement brief, survey plan, or prioritization memo.")
        if "business_purpose" in clarification_targets:
            recommendations.append("Clarify whether the analysis is for strategic benchmarking, product improvement, GTM, or customer validation.")
        if scope_type == "mixed_intent" and extracted.survey_reason:
            recommendations.append("Keep survey generation scoped to validation questions rather than assuming survey data already exists.")
        return recommendations

    @staticmethod
    def _assumptions(task: Task, extracted: PlannerExtractedContext, scope_type: str) -> list[str]:
        assumptions = [
            "Assume only public, traceable evidence should be used for downstream benchmarking.",
        ]
        if scope_type in {"category_scan", "broad_competitive_analysis"}:
            assumptions.append("Assume the request starts as a category scan and should narrow to a validated competitor shortlist before strong conclusions.")
        if scope_type == "strategic_ambiguous":
            assumptions.append("Assume the subject product requires additional internal context before competitor positioning can be finalized.")
        if PlannerAgent._is_generic_region(task.region):
            assumptions.append("Assume a globally visible evidence set unless a narrower market is later specified.")
        if extracted.survey_needed:
            assumptions.append("Assume survey support is for future validation and not existing evidence.")
        return assumptions

    def _planning_stages(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        selected_dimensions: list[str],
        scope_type: str,
        candidate_competitors: list[PlannerCompetitorCandidate],
    ) -> list[PlannerStage]:
        if scope_type in {"category_scan", "broad_competitive_analysis", "strategic_ambiguous", "semi_specific_benchmark", "mixed_intent"}:
            stages = [
                PlannerStage(
                    stage_id="define_scope",
                    label="Define Scope",
                    objective=f"Constrain the task scope for {task.industry} before deep evidence collection.",
                    outputs=["confirmed category or product scope", "clarified business purpose", "region constraint"],
                    priority=1,
                    metadata={"scope_type": scope_type},
                ),
                PlannerStage(
                    stage_id="identify_candidates",
                    label="Identify Candidates",
                    objective="Validate a focused competitor shortlist from task inputs and planner inference.",
                    outputs=["validated competitor shortlist", "candidate rationale"],
                    depends_on=["define_scope"],
                    priority=2,
                    metadata={"candidate_count": len(candidate_competitors)},
                ),
                PlannerStage(
                    stage_id="prioritize_dimensions",
                    label="Prioritize Dimensions",
                    objective=f"Select the most decision-useful dimensions from: {', '.join(selected_dimensions[:5])}.",
                    outputs=["final comparison dimensions", "query priorities"],
                    depends_on=["identify_candidates"],
                    priority=3,
                ),
                PlannerStage(
                    stage_id="collect_evidence",
                    label="Collect Evidence",
                    objective="Collect competitor-specific high/medium relevance public evidence.",
                    outputs=["evidence set", "coverage gaps"],
                    depends_on=["prioritize_dimensions"],
                    priority=4,
                ),
                PlannerStage(
                    stage_id="synthesize_findings",
                    label="Synthesize Findings",
                    objective="Convert evidence into structured knowledge and conservative claims.",
                    outputs=["structured knowledge", "claim set", "uncertainty notes"],
                    depends_on=["collect_evidence"],
                    priority=5,
                ),
            ]
            if extracted.survey_needed or self._has_survey_language(self._task_text(task)):
                stages.append(
                    PlannerStage(
                        stage_id="survey_validation_plan",
                        label="Survey Validation Plan",
                        objective="Prepare a survey or questionnaire plan only for unresolved validation themes.",
                        outputs=["survey objective", "question themes", "validation hypotheses"],
                        depends_on=["synthesize_findings"],
                        priority=6,
                    )
                )
            return stages

        return [
            PlannerStage(
                stage_id="collect_evidence",
                label="Collect Evidence",
                objective="Collect competitor-specific public evidence.",
                outputs=["evidence set"],
                priority=1,
            ),
            PlannerStage(
                stage_id="structure_analysis",
                label="Structure Analysis",
                objective="Convert evidence into structured competitor knowledge.",
                outputs=["product profile", "feature tree", "pricing model", "user persona"],
                depends_on=["collect_evidence"],
                priority=2,
            ),
            PlannerStage(
                stage_id="write_and_validate",
                label="Write And Validate",
                objective="Generate evidence-bound claims and validate them through QA.",
                outputs=["report", "claims", "QA result"],
                depends_on=["structure_analysis"],
                priority=3,
            ),
        ]

    def _category_query_hints(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        selected_dimensions: list[str],
        survey_needed: bool,
        scope_type: str,
    ) -> list[str]:
        if scope_type not in {"category_scan", "broad_competitive_analysis", "strategic_ambiguous", "semi_specific_benchmark"}:
            return []
        category = self._effective_industry(task, extracted) or task.product_name
        resolved_domain_pack = self._resolved_domain_pack(task, extracted)
        category_key = resolved_domain_pack.category_key or self._category_key(task, extracted)
        hints = [
            template.format(category=category)
            for template in resolved_domain_pack.category_query_templates
        ] or [
            f"{category} benchmark competitors official pricing features",
            f"{category} buyer reviews feature comparison",
        ]
        if not resolved_domain_pack.category_query_templates and category_key == "smartphone":
            hints.append(f"{category} battery camera charging performance reviews")
        elif not resolved_domain_pack.category_query_templates and category_key == "crm":
            hints.append(f"{category} workflow automation pipeline integrations pricing")
        elif not resolved_domain_pack.category_query_templates and category_key == "ai_note_taking":
            hints.append(f"{category} ai notes meeting capture search workflow reviews")
        elif not resolved_domain_pack.category_query_templates:
            hints.append(f"{category} target users positioning use cases")
        if "ux" in selected_dimensions:
            hints.append(f"{category} usability reviews pain points")
        if survey_needed:
            hints.append(f"{category} feedback pain points improvement priorities")
        return self._dedupe(hints)

    def _category_key(self, task: Task, extracted: PlannerExtractedContext | None = None) -> str | None:
        text = f"{self._task_text(task)} {(extracted.industry if extracted else '')} {(extracted.domain if extracted else '')}".lower()
        if any(keyword in text for keyword in ("smartphone", "phone", "旗舰", "手机")):
            return "smartphone"
        if "crm" in text:
            return "crm"
        if any(keyword in text for keyword in ("note-taking", "note taking", "notes", "meeting notes", "笔记")):
            return "ai_note_taking"
        return None

    @staticmethod
    def _has_explicit_output_request(text: str) -> bool:
        return any(
            marker in text
            for marker in (
                "generate",
                "determine whether",
                "decide whether",
                "identify",
                "recommend",
                "suggest",
                "help me",
                "plan",
                "report",
            )
        )

    @staticmethod
    def _count_concrete_focus_points(focus_points: list[str]) -> int:
        generic_markers = (
            "market",
            "trend",
            "landscape",
            "key competitors",
            "consumer preferences",
            "analysis",
            "comparison",
            "framework",
            "structure",
            "dimensions",
            "competitiveness",
        )
        concrete = 0
        for focus_point in focus_points:
            normalized = focus_point.strip().lower()
            if not normalized:
                continue
            if any(marker in normalized for marker in generic_markers):
                continue
            concrete += 1
        return concrete

    def _specificity_profile(self, task: Task, extracted: PlannerExtractedContext) -> dict[str, int | bool]:
        text = self._task_text(task)
        explicit_competitor_count = len(extracted.competitors_mentioned or self._valid_competitors(task.competitors))
        has_explicit_region = not self._is_generic_region(extracted.region)
        concrete_focus_count = self._count_concrete_focus_points(extracted.analysis_focus_points)
        has_explicit_output_request = self._has_explicit_output_request(text)
        score = 0
        if explicit_competitor_count >= 2:
            score += 2
        elif explicit_competitor_count == 1:
            score += 1
        if has_explicit_region:
            score += 1
        if concrete_focus_count >= 3:
            score += 2
        elif concrete_focus_count >= 1:
            score += 1
        if has_explicit_output_request:
            score += 1
        return {
            "explicit_competitor_count": explicit_competitor_count,
            "has_explicit_region": has_explicit_region,
            "concrete_focus_count": concrete_focus_count,
            "has_explicit_output_request": has_explicit_output_request,
            "score": score,
        }

    @staticmethod
    def _effective_industry(task: Task, extracted: PlannerExtractedContext | None = None) -> str:
        extracted_industry = (extracted.industry if extracted else "") or ""
        task_industry = task.industry or ""
        if extracted_industry.strip():
            return extracted_industry.strip()
        return task_industry.strip()

    def _messages(self, task: Task) -> list[dict[str, str]]:
        system = (
            "你是竞品分析平台中的 PlannerAgent。你的任务是理解用户的中文或中英混合分析需求，并为下游模块返回严格合法的 JSON 规划结果。\n\n"
            "你的输出必须：\n"
            "1. 识别用户的高层意图；\n"
            "2. 抽取产品分析上下文；\n"
            "3. 判断是否需要问卷或用户调研支持；\n"
            "4. 为 CollectorAgent、AnalystAgent、ReportWriterAgent、QaAgent 和 Survey 模块生成中文执行指导；\n"
            "5. 严格遵循要求的 JSON 结构和字段类型。\n\n"
            "JSON 字段名必须保持英文，不要翻译 key。字段内容、planner_notes、downstream_guidance、survey_reason 等解释性文本应尽量使用中文，适合中文企业竞品分析场景。\n\n"
            "intent_classification 必须严格使用以下英文枚举之一：\n"
            "- competitive_analysis\n"
            "- product_positioning\n"
            "- feature_comparison\n"
            "- ux_review\n"
            "- improvement_opportunity\n"
            "- survey_design\n"
            "- survey_analysis\n"
            "- market_research\n"
            "- unknown\n\n"
            "分类规则：\n"
            "- 如果请求同时包含产品对比和问卷生成，用于痛点发现、痛点验证或改进规划，请使用 improvement_opportunity。\n"
            "- 只有当主任务只是创建问卷、且没有更大的改进分析目标时，才使用 survey_design。\n\n"
            "只返回一个 JSON object，字段和类型如下：\n"
            "- intent_summary: string\n"
            "- intent_classification: string\n"
            "- industry: string\n"
            "- domain: string\n"
            "- product_name: string\n"
            "- product_type: string\n"
            "- target_users: string[]\n"
            "- region: string\n"
            "- competitors_mentioned: string[]\n"
            "- analysis_focus_points: string[]\n"
            "- requested_outputs: string[]\n"
            "- survey_needed: boolean\n"
            "- survey_reason: string\n"
            "- missing_information: string[]\n"
            "- confidence: number\n"
            "- ambiguity_level: string\n"
            "- scope_type: string\n"
            "- scope_size: string\n"
            "- selected_dimensions: string[]\n"
            "- survey_objective: string\n"
            '- survey_inputs: {"objective": string, "respondent_type": string, "question_themes": string[], "hypotheses": string[]}\n'
            '- recommended_next_constraints: string[]\n'
            '- assumptions: string[]\n'
            '- candidate_competitors: [{"name": string, "reason": string, "confidence": number, "priority": number}]\n'
            '- clarification_targets: string[]\n'
            '- planning_stages: [{"stage_id": string, "label": string, "objective": string, "outputs": string[], "depends_on": string[], "priority": number}]\n'
            '- planner_notes: string[]\n'
            '- downstream_guidance: {"collector": string[], "analyst": string[], "writer": string[], "qa": string[], "survey": string[]}\n\n'
            "重要类型规则：\n"
            "- 所有 list-like 字段必须是 JSON array。\n"
            "- 需要 array 的地方绝不能返回 string。\n"
            "- 即使只有一个元素，也必须返回 array。\n"
            "- 不要返回 null。\n"
            "- 如果信息不确定，可以合理推断，并把不确定性写入 missing_information。\n"
            "- confidence 必须是 0 到 1 之间的 number。\n"
            "- 只返回 JSON。\n"
            "- 不要输出 Markdown。\n"
            "- 不要输出额外解释。\n"
            "- 不要输出代码块。"
        )
        user = (
            "用户请求示例：\n"
            "“我想分析苹果和三星旗舰手机的优劣，并生成一个关于手机续航问题的用户问卷”\n\n"
            "期望输出风格：\n"
            "{\n"
            '"intent_summary": "分析苹果和三星旗舰手机的优劣，并准备围绕手机续航问题的用户问卷。",\n'
            '"intent_classification": "improvement_opportunity",\n'
            '"industry": "旗舰智能手机",\n'
            '"domain": "智能手机硬件",\n'
            '"product_name": "苹果和三星旗舰手机对比",\n'
            '"product_type": "旗舰手机",\n'
            '"target_users": ["计划购买旗舰手机的消费者", "重度使用者"],\n'
            '"region": "中国",\n'
            '"competitors_mentioned": ["苹果", "三星"],\n'
            '"analysis_focus_points": ["续航表现", "性能取舍", "影像体验", "系统体验"],\n'
            '"requested_outputs": ["竞品优劣分析", "手机续航问卷"],\n'
            '"survey_needed": true,\n'
            '"survey_reason": "用户希望围绕续航问题收集反馈，以支持改进机会分析。",\n'
            '"missing_information": ["未指定问卷样本规模", "未指定受访者细分标准"],\n'
            '"confidence": 0.95,\n'
            '"selected_dimensions": ["feature", "pricing", "persona", "feedback", "prioritization"],\n'
            '"survey_objective": "收集用户对旗舰手机续航问题、使用场景和改进期望的结构化反馈。",\n'
            '"survey_inputs": {\n'
            '"objective": "收集用户对旗舰手机续航问题、使用场景和改进期望的结构化反馈。",\n'
            '"respondent_type": "使用或计划购买苹果、三星旗舰手机的用户。",\n'
            '"question_themes": ["续航痛点", "充电频率", "高负载场景", "改进优先级"],\n'
            '"hypotheses": ["用户对旗舰手机续航的主要不满来自高负载场景掉电过快。"]\n'
            "},\n"
            '"planner_notes": ["已识别为竞品分析 + 问卷生成场景。"],\n'
            '"downstream_guidance": {\n'
            '"collector": ["收集苹果和三星旗舰手机在续航、充电、性能和影像方面的公开证据。"],\n'
            '"analyst": ["比较两家旗舰手机在续航相关优劣势和使用场景差异。"],\n'
            '"writer": ["突出优劣对比、续航问题和问卷设计目的。"],\n'
            '"qa": ["验证所有优劣结论是否有对应证据支撑。"],\n'
            '"survey": ["围绕续航痛点、充电体验和改进优先级设计题目。"]\n'
            "}\n"
            "}\n\n"
            "现在请针对以下 task input 返回一个且仅一个 JSON object：\n"
            f"{task.model_dump(mode='json')}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    @classmethod
    def _normalize_llm_payload(cls, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Planner LLM output must be a JSON object.")

        normalized = dict(payload)
        for field_name in (
            "target_users",
            "competitors_mentioned",
            "analysis_focus_points",
            "requested_outputs",
            "missing_information",
            "selected_dimensions",
            "recommended_next_constraints",
            "assumptions",
            "clarification_targets",
            "planner_notes",
        ):
            normalized_value = cls._normalize_array_like_value(normalized.get(field_name), allow_empty=True)
            if normalized_value is not None:
                normalized[field_name] = normalized_value

        survey_inputs = normalized.get("survey_inputs")
        if isinstance(survey_inputs, dict):
            normalized_survey_inputs = dict(survey_inputs)
            for field_name in ("question_themes", "hypotheses"):
                normalized_value = cls._normalize_array_like_value(normalized_survey_inputs.get(field_name), allow_empty=True)
                if normalized_value is not None:
                    normalized_survey_inputs[field_name] = normalized_value
            normalized["survey_inputs"] = normalized_survey_inputs

        downstream_guidance = normalized.get("downstream_guidance")
        if isinstance(downstream_guidance, dict):
            normalized_guidance = dict(downstream_guidance)
            for field_name in ("collector", "analyst", "writer", "qa", "survey"):
                normalized_value = cls._normalize_array_like_value(normalized_guidance.get(field_name), allow_empty=True)
                if normalized_value is not None:
                    normalized_guidance[field_name] = normalized_value
            normalized["downstream_guidance"] = normalized_guidance

        extracted_context = normalized.get("extracted_context")
        if isinstance(extracted_context, dict):
            normalized_extracted_context = dict(extracted_context)
            for field_name in (
                "target_users",
                "competitors_mentioned",
                "analysis_focus_points",
                "requested_outputs",
                "missing_information",
            ):
                normalized_value = cls._normalize_array_like_value(normalized_extracted_context.get(field_name), allow_empty=True)
                if normalized_value is not None:
                    normalized_extracted_context[field_name] = normalized_value
            normalized["extracted_context"] = normalized_extracted_context

        candidate_competitors = normalized.get("candidate_competitors")
        if isinstance(candidate_competitors, dict):
            normalized["candidate_competitors"] = [candidate_competitors]
        planning_stages = normalized.get("planning_stages")
        if isinstance(planning_stages, dict):
            normalized["planning_stages"] = [planning_stages]

        return normalized

    @staticmethod
    def _normalize_array_like_value(value: Any, *, allow_empty: bool) -> list[str] | None:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else ([] if allow_empty else None)
        if value is None:
            return [] if allow_empty else None
        return None

    @staticmethod
    def _normalize_dimensions(raw_dimensions: Any, intent: str) -> list[str]:
        if isinstance(raw_dimensions, list):
            alias_map = {
                "features": "feature",
                "feature_comparison": "feature",
                "user": "persona",
                "users": "persona",
                "target_users": "persona",
                "position": "positioning",
                "pricing_model": "pricing",
                "user_feedback": "feedback",
                "usability": "ux",
                "prioritize": "prioritization",
                "benchmark": None,
                "benchmarking": None,
                "comparison": None,
            }
            normalized = []
            for item in raw_dimensions:
                raw = str(item).strip().lower().replace(" ", "_")
                if not raw:
                    continue
                mapped = alias_map.get(raw, raw)
                if mapped and mapped in PlannerAgent.ALLOWED_DIMENSIONS:
                    normalized.append(mapped)
        else:
            normalized = []
        if normalized:
            return PlannerAgent._dedupe(normalized)
        defaults = {
            "competitive_analysis": ["positioning", "feature", "pricing", "persona"],
            "product_positioning": ["positioning", "feature", "persona", "pricing"],
            "feature_comparison": ["feature", "positioning", "pricing", "persona"],
            "ux_review": ["ux", "persona", "feature", "risk"],
            "improvement_opportunity": ["feature", "ux", "feedback", "prioritization"],
            "survey_design": ["feedback", "hypothesis", "prioritization", "persona"],
            "survey_analysis": ["feedback", "hypothesis", "persona", "risk"],
            "market_research": ["positioning", "pricing", "persona", "market"],
            "unknown": ["positioning", "feature", "pricing", "persona"],
        }
        return defaults.get(intent, defaults["unknown"])

    @staticmethod
    def _infer_intent_from_task(task: Task) -> str:
        text = f"{task.product_name} {task.industry}".lower()
        if PlannerAgent._has_survey_language(text) and any(
            keyword in text
            for keyword in (
                "analyze",
                "analysis",
                "compare",
                "benchmark",
                "improve",
                "improvement",
                "pain point",
                "priorit",
                "鍒嗘瀽",
                "瀵规瘮",
                "鏀硅繘",
            )
        ):
            return "improvement_opportunity"
        keyword_map = [
            ("survey_design", ["survey", "questionnaire", "问卷", "调研设计"]),
            ("survey_analysis", ["survey analysis", "survey result", "问卷分析", "反馈分析"]),
            ("improvement_opportunity", ["improvement", "opportunity", "pain point", "改进", "优化", "痛点", "机会点", "priorit"]),
            ("ux_review", ["ux", "usability", "体验", "易用性"]),
            ("feature_comparison", ["feature", "功能对比", "compare features"]),
            ("product_positioning", ["positioning", "定位"]),
            ("market_research", ["market", "市场研究", "市场调研"]),
            ("competitive_analysis", ["competitor", "competitive", "竞品", "对标", "分析"]),
        ]
        for label, keywords in keyword_map:
            if any(keyword in text for keyword in keywords):
                return label
        return "competitive_analysis"

    @staticmethod
    def _deterministic_survey_needed(task: Task, intent: str) -> bool:
        text = f"{task.product_name} {task.industry}".lower()
        explicit_survey = PlannerAgent._has_survey_language(text)
        if intent in {"survey_design", "survey_analysis"}:
            return True
        if intent in {"improvement_opportunity", "ux_review"} and explicit_survey and not PlannerAgent._is_broad_category_request(text, task.industry):
            return True
        if intent in {"improvement_opportunity", "ux_review"}:
            return False
        return explicit_survey
        keywords = [
            "improvement",
            "pain point",
            "pros and cons",
            "strengths",
            "weaknesses",
            "advantages",
            "disadvantages",
            "优劣",
            "改进",
            "优化",
            "痛点",
            "questionnaire",
            "survey",
            "priorit",
        ]
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _deterministic_survey_reason(task: Task, intent: str) -> str:
        if intent in {"survey_design", "survey_analysis"}:
            return "The request explicitly points to questionnaire or structured feedback work."
        if intent == "ux_review":
            return "UX-oriented requests benefit from structured user pain-point collection."
        return "The request appears to seek improvement opportunities, pain points, or validation that benefit from survey support."

    @staticmethod
    def _deterministic_focus_points(task: Task, intent: str) -> list[str]:
        base = ["positioning", "features", "pricing", "target users"]
        if intent == "feature_comparison":
            return ["core features", "differentiators", "integration breadth", "pricing trade-offs"]
        if intent == "product_positioning":
            return ["category positioning", "buyer messaging", "target segment fit", "evidence gaps"]
        if intent == "ux_review":
            return ["user workflows", "pain points", "usability risks", "experience gaps"]
        if intent == "improvement_opportunity":
            return ["user pain points", "feature gaps", "improvement opportunities", "validation hypotheses"]
        if intent in {"survey_design", "survey_analysis"}:
            return ["feedback themes", "hypotheses", "feature prioritization", "respondent segmentation"]
        if "saas" in task.industry.lower():
            return ["positioning", "workflow features", "pricing", "enterprise users"]
        return base

    @staticmethod
    def _deterministic_requested_outputs(task: Task, survey_needed: bool) -> list[str]:
        outputs = ["competitive report", "structured competitor knowledge", "traceable claims"]
        if survey_needed:
            outputs.append("survey or questionnaire brief")
        return outputs

    @staticmethod
    def _infer_target_users(task: Task) -> list[str]:
        text = f"{task.product_name} {task.industry}".lower()
        if "saas" in text or "enterprise" in text:
            return ["operators", "team leads", "buyers", "enterprise evaluators"]
        if any(keyword in text for keyword in ("smartphone", "phone", "handset")):
            return ["premium buyers", "switchers", "daily mobile users"]
        if any(keyword in text for keyword in ("crm", "sales")):
            return ["sales teams", "revenue operations", "CRM buyers"]
        if any(keyword in text for keyword in ("note-taking", "notes", "meeting notes")):
            return ["knowledge workers", "meeting-heavy teams", "individual note takers"]
        if "retail" in text or "ecommerce" in text or "电商" in text:
            return ["consumers", "category managers", "marketing teams"]
        if "coding" in text or "developer" in text:
            return ["developers", "engineering teams", "technical evaluators"]
        return ["target users not clearly specified"]

    @staticmethod
    def _default_missing_information(task: Task) -> list[str]:
        missing = []
        if not task.region.strip():
            missing.append("Region is missing.")
        return missing

    def _default_intent_summary(self, task: Task, intent: str, survey_needed: bool) -> str:
        suffix = " Survey support is required." if survey_needed else ""
        competitors = self._valid_competitors(task.competitors)
        competitor_phrase = ", ".join(competitors) if competitors else "a still-to-be-confirmed competitor set"
        return (
            f"Plan a {intent.replace('_', ' ')} workflow for {task.product_name} in {task.industry}, "
            f"compare competitors {competitor_phrase}, and keep evidence traceable.{suffix}"
        )

    def _build_query_hints_for_competitor(
        self,
        competitor: str,
        task: Task,
        extracted: PlannerExtractedContext,
        selected_dimensions: list[str],
        survey_needed: bool,
        *,
        scope_type: str,
    ) -> list[str]:
        industry_label = self._effective_industry(task, extracted)
        resolved_domain_pack = self._resolved_domain_pack(task, extracted)
        hints = [
            template.format(competitor=competitor, industry=industry_label)
            for template in resolved_domain_pack.competitor_query_templates
        ] or [
            f"{competitor} official {industry_label}",
            f"{competitor} pricing official",
            f"{competitor} features documentation",
        ]
        if scope_type in {"category_scan", "broad_competitive_analysis", "semi_specific_benchmark"}:
            hints.append(f"{competitor} market positioning buyer segments")
        if "persona" in selected_dimensions:
            hints.append(f"{competitor} customers use cases")
        if "ux" in selected_dimensions:
            hints.append(f"{competitor} reviews usability pain points")
        if survey_needed:
            hints.append(f"{competitor} review feedback user pain points")
        return self._dedupe(hints)

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @classmethod
    def _has_survey_language(cls, text: str) -> bool:
        return any(
            keyword in text
            for keyword in (
                "survey",
                "questionnaire",
                "user research",
                "respondent",
                "闂嵎",
                "璋冪爺",
                "鍙嶉",
            )
        )

    @staticmethod
    def _is_generic_region(region: str | None) -> bool:
        if not region or not region.strip():
            return True
        return region.strip().lower() in {"global", "worldwide", "all markets", "unknown", "tbd", "n/a"}

    def _resolved_region(self, task: Task, proposed_region: Any) -> str:
        if not self._is_generic_region(task.region):
            return task.region
        if isinstance(proposed_region, str) and proposed_region.strip() and proposed_region.strip().lower() in self._task_text(task):
            return proposed_region.strip()
        return task.region

    def _explicit_competitors_for_task(self, task: Task, raw_competitors: Any) -> list[str]:
        proposed = self._normalize_string_list(raw_competitors)
        task_text = self._task_text(task)
        valid_task_competitors = self._valid_competitors(task.competitors)
        explicit = [
            competitor
            for competitor in self._valid_competitors(proposed)
            if competitor in valid_task_competitors or competitor.lower() in task_text
        ]
        if explicit:
            return self._dedupe(explicit)
        return valid_task_competitors

    def _clean_missing_information(self, raw_missing_information: Any) -> list[str]:
        blocked_markers = (
            "No dedicated free-form user brief is stored in Task",
            "Planner inferred intent from product_name",
        )
        cleaned = self._normalize_string_list(raw_missing_information)
        return [item for item in cleaned if not any(marker in item for marker in blocked_markers)]

    def _resolve_survey_signals(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        scope_type: str,
    ) -> tuple[bool, bool, str | None]:
        text = self._task_text(task)
        explicit_survey = self._has_survey_language(text)
        decision_markers = (
            "determine whether",
            "decide whether",
            "recommend whether",
            "whether user research is necessary",
            "questionnaire should be created",
        )
        deliverable_markers = (
            "generate a survey",
            "create a survey",
            "generate survey",
            "create survey",
            "generate a questionnaire",
            "create a questionnaire",
            "survey design",
            "questionnaire design",
            "survey direction",
            "user survey direction",
            "suggest what we should investigate with a user survey",
            "investigate with a user survey",
        )
        requested_outputs = [output.lower() for output in extracted.requested_outputs]
        decision_output = any(
            any(marker in output for marker in ("survey", "questionnaire", "user research", "research"))
            and any(marker in output for marker in ("assessment", "decision", "feasibility", "necessity", "whether"))
            for output in requested_outputs
        )
        deliverable_output = any(
            any(marker in output for marker in ("survey", "questionnaire", "user research", "research"))
            and not any(marker in output for marker in ("assessment", "decision", "feasibility", "necessity", "whether"))
            for output in requested_outputs
        )
        recommendation_only = any(marker in text for marker in decision_markers) or decision_output
        strong_survey_markers = deliverable_markers
        requested_survey_output = deliverable_output
        requested_survey_output = requested_survey_output or any(
            any(marker in output.lower() for marker in ("survey", "questionnaire", "user research", "闂嵎"))
            for output in extracted.requested_outputs
        )
        strong_request = (any(marker in text for marker in strong_survey_markers) or extracted.survey_needed or requested_survey_output) and not recommendation_only
        survey_recommended = explicit_survey or extracted.intent_classification in {"improvement_opportunity", "survey_design", "survey_analysis", "ux_review"}
        if extracted.intent_classification in {"survey_design", "survey_analysis"}:
            return True, True, "The request explicitly asks for questionnaire design or structured survey analysis."
        if strong_request and scope_type not in {"category_scan", "broad_competitive_analysis", "strategic_ambiguous"}:
            return True, True, "The request explicitly includes survey or questionnaire work as part of the deliverable."
        if recommendation_only:
            return False, True, "The request asks whether survey work is warranted, so survey support should be treated as a decision outcome or follow-up option."
        if extracted.intent_classification in {"improvement_opportunity", "ux_review"}:
            return False, survey_recommended, "A follow-up survey could validate pain points, unmet needs, or prioritization after the benchmark."
        return False, survey_recommended, "A follow-up survey could validate user perceptions after the benchmark." if survey_recommended else None

    def _resolve_survey_signals_refined(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        scope_type: str,
    ) -> tuple[bool, bool, str | None]:
        del scope_type
        text = self._task_text(task)
        explicit_survey = self._has_survey_language(text)
        decision_markers = (
            "determine whether",
            "decide whether",
            "recommend whether",
            "whether user research is necessary",
            "questionnaire should be created",
        )
        deliverable_markers = (
            "generate a survey",
            "create a survey",
            "generate survey",
            "create survey",
            "generate a questionnaire",
            "create a questionnaire",
            "survey design",
            "questionnaire design",
            "survey direction",
            "user survey direction",
            "suggest what we should investigate with a user survey",
            "investigate with a user survey",
        )
        requested_outputs = [output.lower() for output in extracted.requested_outputs]
        survey_terms = ("survey", "questionnaire", "user research")
        decision_terms = ("assessment", "decision", "feasibility", "necessity", "whether")
        decision_output = any(
            any(marker in output for marker in survey_terms)
            and any(marker in output for marker in decision_terms)
            for output in requested_outputs
        )
        deliverable_output = any(
            any(marker in output for marker in survey_terms)
            and not any(marker in output for marker in decision_terms)
            for output in requested_outputs
        )
        recommendation_only = any(marker in text for marker in decision_markers) or decision_output
        strong_request = (
            any(marker in text for marker in deliverable_markers)
            or extracted.survey_needed
            or deliverable_output
        ) and not recommendation_only
        survey_recommended = explicit_survey or extracted.intent_classification in {
            "improvement_opportunity",
            "survey_design",
            "survey_analysis",
            "ux_review",
        }
        if extracted.intent_classification in {"survey_design", "survey_analysis"}:
            return True, True, "The request explicitly asks for questionnaire design or structured survey analysis."
        if strong_request:
            return True, True, "The request explicitly includes survey or questionnaire work as part of the deliverable."
        if recommendation_only:
            return False, True, "The request asks whether survey work is warranted, so survey support should be treated as a decision outcome or follow-up option."
        if extracted.intent_classification in {"improvement_opportunity", "ux_review"}:
            return False, survey_recommended, "A follow-up survey could validate pain points, unmet needs, or prioritization after the benchmark."
        return False, survey_recommended, "A follow-up survey could validate user perceptions after the benchmark." if survey_recommended else None

    @staticmethod
    def _dimension_required(dimension: str, *, extracted_intent: str) -> bool:
        core_required = {
            "competitive_analysis": {"positioning", "feature", "pricing", "persona"},
            "product_positioning": {"positioning", "feature", "pricing", "persona"},
            "feature_comparison": {"positioning", "feature", "pricing", "persona"},
            "market_research": {"positioning", "pricing", "persona", "market"},
            "unknown": {"positioning", "feature", "pricing", "persona"},
        }
        if dimension in core_required.get(extracted_intent, set()):
            return True
        if extracted_intent in {"improvement_opportunity", "survey_design", "survey_analysis", "ux_review"}:
            return dimension in {"feedback", "prioritization", "hypothesis", "ux"}
        return False

    def _dimension_keywords(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        dimension: str,
        *,
        category_key: str | None,
    ) -> list[str]:
        resolved_domain_pack = self._resolved_domain_pack(task, extracted)
        pack_keywords = resolved_domain_pack.dimension_keywords.get(dimension)
        if pack_keywords:
            return [dimension, *list(pack_keywords)]
        if category_key == "smartphone":
            smartphone = {
                "positioning": [dimension, "flagship positioning", "premium segment", "brand differentiation"],
                "feature": [dimension, "battery life", "charging speed", "camera performance", "os experience"],
                "pricing": [dimension, "retail price", "flagship pricing", "premium pricing", "model variants"],
                "persona": [dimension, "premium buyers", "switchers", "mobile power users", "use cases"],
                "ux": [dimension, "daily use", "os experience", "usability", "pain points"],
                "feedback": [dimension, "user feedback", "complaints", "reviews", "pain points"],
                "prioritization": [dimension, "improvement priorities", "purchase drivers", "feature importance"],
                "risk": [dimension, "ecosystem risk", "brand risk", "trade-offs"],
                "hypothesis": [dimension, "validation", "assumption", "user expectations"],
                "market": [dimension, "flagship market", "premium segment", "buyer trends"],
            }
            return smartphone.get(dimension, [dimension])
        if category_key == "crm":
            crm = {
                "positioning": [dimension, "crm positioning", "segment fit", "buyer messaging"],
                "feature": [dimension, "workflow automation", "pipeline", "integrations", "reporting"],
                "pricing": [dimension, "pricing", "seat pricing", "plans", "enterprise pricing"],
                "persona": [dimension, "sales teams", "revenue operations", "crm buyers", "use cases"],
                "ux": [dimension, "usability", "adoption friction", "workflow pain points"],
                "feedback": [dimension, "user feedback", "pain points", "reviews", "complaints"],
                "prioritization": [dimension, "feature requests", "buyer priorities", "improvement priorities"],
                "risk": [dimension, "switching risk", "implementation risk", "lock-in"],
                "hypothesis": [dimension, "validation", "assumption", "adoption blockers"],
                "market": [dimension, "crm market", "segment trends", "buyer expectations"],
            }
            return crm.get(dimension, [dimension])
        if category_key == "ai_note_taking":
            notes = {
                "positioning": [dimension, "category positioning", "workspace differentiation", "use case fit"],
                "feature": [dimension, "meeting notes", "ai search", "knowledge capture", "organization"],
                "pricing": [dimension, "pricing", "subscription", "team plan", "free tier"],
                "persona": [dimension, "knowledge workers", "teams", "students", "meeting-heavy users"],
                "ux": [dimension, "usability", "capture workflow", "recall workflow", "pain points"],
                "feedback": [dimension, "user feedback", "reviews", "complaints", "adoption pain points"],
                "prioritization": [dimension, "buyer priorities", "workflow importance", "improvement priorities"],
                "risk": [dimension, "privacy risk", "workflow switching risk", "lock-in"],
                "hypothesis": [dimension, "validation", "assumption", "user expectations"],
                "market": [dimension, "category trends", "buyer expectations", "competitive landscape"],
            }
            return notes.get(dimension, [dimension])
        defaults = {
            "positioning": [dimension, "positioning", "differentiation", self._effective_industry(task, extracted)],
            "feature": [dimension, "features", "workflow", "integration"],
            "pricing": [dimension, "pricing", "package", "cost"],
            "persona": [dimension, "target users", "buyer", "use cases"],
            "ux": [dimension, "ux", "usability", "pain points"],
            "feedback": [dimension, "feedback", "pain points", "reviews"],
            "hypothesis": [dimension, "hypothesis", "validation", "assumption"],
            "prioritization": [dimension, "prioritization", "feature requests", "importance"],
            "risk": [dimension, "risk", "trade-offs", "constraints"],
            "market": [dimension, "market", "buyer trends", "category signals"],
        }
        return defaults.get(dimension, [dimension])

    def _resolved_domain_pack(self, task: Task, extracted: PlannerExtractedContext):
        return resolve_domain_pack(task, extracted)

    def _domain_pack_reference(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        scope_type: str,
    ):
        pack = self._resolved_domain_pack(task, extracted)
        return domain_pack_reference(
            pack,
            industry_label=self._effective_industry(task, extracted),
            metadata={"scope_type": scope_type},
        )

    def _build_scope_snapshots(
        self,
        task: Task,
        extracted: PlannerExtractedContext,
        selected_dimensions: list[str],
        candidate_competitors: list[PlannerCompetitorCandidate],
    ) -> dict[str, PlannerScopeSnapshot]:
        confirmed_competitors = self._valid_competitors(task.competitors)
        inferred_competitors = [
            candidate.name
            for candidate in candidate_competitors
            if candidate.name not in confirmed_competitors and candidate.metadata.get("source", "").startswith("planner_inference:")
        ]
        confirmed_scope = PlannerScopeSnapshot(
            competitors=confirmed_competitors,
            region=None if self._is_generic_region(task.region) else task.region.strip(),
            industry=task.industry or None,
            product_name=task.product_name or None,
            metadata={"source": "task_input"},
        )
        inferred_scope = PlannerScopeSnapshot(
            competitors=inferred_competitors,
            region=extracted.region if (extracted.region and not self._is_generic_region(extracted.region) and self._is_generic_region(task.region)) else None,
            industry=None if extracted.industry == task.industry else extracted.industry,
            product_name=None if extracted.product_name == task.product_name else extracted.product_name,
            target_users=[] if extracted.target_users == ["target users not clearly specified"] else extracted.target_users,
            selected_dimensions=selected_dimensions,
            requested_outputs=extracted.requested_outputs,
            metadata={"source": "planner_inference"},
        )
        suggested_scope = PlannerScopeSnapshot(
            competitors=[candidate.name for candidate in candidate_competitors[:4]],
            region=None if self._is_generic_region(task.region) else task.region.strip(),
            industry=task.industry or None,
            product_name=task.product_name or None,
            selected_dimensions=selected_dimensions[:4],
            requested_outputs=extracted.requested_outputs,
            metadata={"source": "planner_suggestion"},
        )
        return {
            "confirmed_scope": confirmed_scope,
            "inferred_scope": inferred_scope,
            "suggested_scope": suggested_scope,
        }

    @staticmethod
    def _safe_text(value: Any, *, fallback: str | None = None) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback

    @staticmethod
    def _safe_confidence(value: Any, *, fallback: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return fallback
        return max(0.0, min(1.0, parsed))

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result
