import json
import os
import time
from uuid import uuid4

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.agents.analyst import AnalystAgent
from app.agents.base import AgentExecutionError
from app.agents.collector import CollectorAgent
from app.agents.evidence_analyst import EvidenceAnalystAgent
from app.agents.final_report import FinalReportAgent
from app.agents.planner import PlannerAgent
from app.agents.qa import MAX_REWORK, QaAgent
from app.agents.report_agent import ReportAgent
from app.agents.report_writer import ReportWriterAgent
from app.schemas import (
    AnalysisDimension,
    AnalysisDimensionPlan,
    AnalystInput,
    CollectorInput,
    CollectorOutput,
    CollectorConfig,
    DemoMode,
    EvidenceCoverageGap,
    Evidence,
    EvidenceAnalystInput,
    EvidenceAnalystReworkContext,
    EvidenceAnalystReworkTarget,
    FinalReportInput,
    PlannerInput,
    PlannerIncrementalInput,
    PlannerCollectionPlan,
    PlannerDownstreamGuidance,
    PlannerOutput,
    PlannerSummary,
    QaInput,
    QaResult,
    ReworkContext,
    ReportWriterInput,
    ReportAgentInput,
    ReworkInstruction,
    ReworkHistoryItem,
    Task,
    TraceRecord,
)
from app.schemas.workflow_state import WorkflowState
from app.constants.collection_strategy import (
    collector_config_for_strategy,
    content_fetch_max_per_dimension_for_strategy,
)
from app.services.evidence_service import EvidenceService
from app.services.evidence_content_fetcher import EvidenceContentFetcher
from app.services.entity_resolver_service import EntityResolverService
from app.services.knowledge_base_service import KbIngestionService, KbRetrieverService
from app.services.page_fetcher import PageFetcher
from app.services.planner_attempt_service import PlannerAttemptService
from app.services.report_service import ReportService
from app.services.task_run_service import TaskRunService
from app.services.task_service import TaskService
from app.services.trace_service import TraceService
from app.services.workflow_progress_service import set_workflow_progress


class WorkflowCancelled(Exception):
    pass


class LangGraphWorkflowRunner:
    def __init__(self, db: Session):
        self.db = db
        self.task_service = TaskService(db)
        self.trace_service = TraceService(db)
        self.evidence_service = EvidenceService(db)
        self.evidence_content_fetcher = EvidenceContentFetcher()
        self.report_service = ReportService(db)
        self.task_run_service = TaskRunService(db)
        self.planner_attempt_service = PlannerAttemptService(db)
        self.kb_ingestion_service = KbIngestionService(db)
        self.kb_retriever_service = KbRetrieverService(db)
        self.entity_resolver_service = EntityResolverService()
        self.planner = PlannerAgent(self.trace_service)
        self.collector = CollectorAgent(self.trace_service)
        self.evidence_analyst = EvidenceAnalystAgent(self.trace_service)
        self.report_agent = ReportAgent(self.trace_service)
        self.analyst = AnalystAgent(self.trace_service)
        self.writer = ReportWriterAgent(self.trace_service)
        self.qa = QaAgent(self.trace_service)
        self.final_report = FinalReportAgent(self.trace_service)
        self.page_fetcher = PageFetcher()
        self.graph = self._build_graph()

    @staticmethod
    def _default_collector_config(task: Task, override: CollectorConfig | None) -> CollectorConfig:
        return override or collector_config_for_strategy(task.collection_strategy_mode)

    def _check_cancelled(self, run_id: str | None) -> None:
        if run_id and self.task_run_service.is_cancel_requested(run_id):
            raise WorkflowCancelled(f"Workflow run {run_id} was cancelled by user request.")

    def _cancel_run_response(self, task_id: str, run_id: str, started: float) -> dict:
        elapsed = int((time.perf_counter() - started) * 1000)
        message = "Workflow cancelled by user request."
        set_workflow_progress(
            run_id,
            current_agent="WorkflowEngine",
            current_stage="cancelled",
            message=message,
            status="cancelled",
        )
        self.task_service.update_status(task_id, "cancelled")
        summary = {
            "run_id": run_id,
            "task_id": task_id,
            "workflow_engine_requested": "langgraph",
            "workflow_engine_used": "langgraph",
            "node_sequence": [],
            "conditional_routes_taken": [],
            "final_status": "cancelled",
            "elapsed_time_ms": elapsed,
            "error_message": message,
        }
        self._save_workflow_trace(task_id, run_id, summary, elapsed)
        finished_run = self.task_run_service.finish_run(
            run_id,
            status="cancelled",
            final_status="cancelled",
            elapsed_time_ms=elapsed,
            error_message=message,
        )
        return {
            "run": finished_run,
            "run_id": run_id,
            "plan": None,
            "qa_result": None,
            "report": None,
            "knowledge_hits": [],
            "workflow_summary": summary,
        }

    def run(
        self,
        task_id: str,
        demo_mode: DemoMode = "normal",
        auto_rework: bool = False,
        writer_mode: str = "mock",
        collector_mode: str = "mock",
        analyst_mode: str = "evidence",
        workflow_engine_requested: str = "langgraph",
        content_mode: str | None = None,
        debug_stage: str | None = None,
        collection_plan_override: PlannerCollectionPlan | None = None,
        collector_config: CollectorConfig | None = None,
        skip_initial_planner: bool = False,
        manual_evidence_selection_enabled: bool = False,
        selected_evidence_ids: list[str] | None = None,
        source_run_id: str | None = None,
    ) -> dict:
        started = time.perf_counter()
        if manual_evidence_selection_enabled:
            return self._run_manual_evidence_selection(
                task_id=task_id,
                source_run_id=source_run_id,
                selected_evidence_ids=selected_evidence_ids or [],
                started=started,
                demo_mode=demo_mode,
                writer_mode=writer_mode,
                collector_mode=collector_mode,
                analyst_mode=analyst_mode,
                workflow_engine_requested=workflow_engine_requested,
                content_mode=content_mode,
                collection_plan_override=collection_plan_override,
                collector_config=collector_config,
            )
        task_run = self.task_run_service.create_run(
            task_id=task_id,
            workflow_engine="langgraph",
            collector_mode=collector_mode,
            analyst_mode=analyst_mode,
            writer_mode=writer_mode,
            content_mode=content_mode,
            demo_mode=demo_mode,
            auto_rework=auto_rework,
        )
        self.trace_service.set_run_context(task_run.run_id)
        task = self.task_service.update_status(task_id, "running", rework_count=0)
        initial_state: WorkflowState = {
            "task_id": task_id,
            "run_id": task_run.run_id,
            "trace_id": f"workflow_{uuid4().hex[:10]}",
            "task": task,
            "task_run": task_run,
            "run_status": "running",
            "workflow_engine_requested": workflow_engine_requested,
            "workflow_engine_used": "langgraph",
            "demo_mode": demo_mode,
            "collector_mode": collector_mode,
            "analyst_mode": analyst_mode,
            "writer_mode": writer_mode,
            "content_mode": content_mode,
            "auto_rework": auto_rework,
            "rework_count": 0,
            "max_rework": MAX_REWORK,
            "planner_output": None,
            "collection_plan_override": collection_plan_override,
            "skip_initial_planner": skip_initial_planner,
            "manual_collection_plan_override_used": False,
            "collector_config": self._default_collector_config(task, collector_config),
            "collector_config_source": "request_override" if collector_config else f"task_strategy:{task.collection_strategy_mode}",
            "planner_incremental_output": None,
            "incremental_collection_plan": None,
            "collector_output": None,
            "analyst_output": None,
            "evidence_analyst_output": None,
            "evidence_analyst_rework_context": None,
            "report_agent_output": None,
            "report_writer_output": None,
            "qa_output": None,
            "final_report_output": None,
            "evidence_gate_output": {},
            "evidence_content_fetch_output": {},
            "page_fetch_output": {},
            "entity_resolution": {},
            "competitor_aliases": {},
            "evidence": [],
            "intent_summary": None,
            "intent_classification": None,
            "ambiguity_level": None,
            "scope_type": None,
            "scope_size": None,
            "extracted_context": None,
            "selected_dimensions": [],
            "analysis_dimension_plan": None,
            "downstream_guidance": None,
            "survey_needed": False,
            "survey_recommended": False,
            "survey_objective": None,
            "survey_inputs": None,
            "confirmed_scope": None,
            "inferred_scope": None,
            "suggested_scope": None,
            "recommended_next_constraints": [],
            "assumptions": [],
            "candidate_competitors": [],
            "clarification_targets": [],
            "planning_stages": [],
            "planner_notes": [],
            "planner_confidence": None,
            "dimension_results": [],
            "survey_evidence": [],
            "chunks": [],
            "retrieval_results": [],
            "retrieved_knowledge_chunks": [],
            "knowledge_hits": [],
            "claim_support_results": [],
            "swot_analysis": None,
            "rework_context": None,
            "report": None,
            "qa_result": None,
            "route_to": None,
            "final_status": None,
            "errors": [],
            "node_sequence": [],
            "conditional_routes_taken": [],
            "workflow_summary": {},
            "run_isolation_strategy": "run_id",
            "run_cleanup_summary": {},
        }
        try:
            if debug_stage == "planner_only":
                return self._run_planner_only(initial_state, task_run, started)
            if debug_stage == "collector_only":
                return self._run_collector_only(initial_state, task_run, started)
            final_state = self.graph.invoke(initial_state, config={"recursion_limit": 24})
            elapsed = int((time.perf_counter() - started) * 1000)
            summary = self._workflow_summary(final_state, elapsed)
            self._save_workflow_trace(task_id, task_run.run_id, summary, elapsed)
            final_state["workflow_summary"] = summary
            finished_run = self.task_run_service.finish_run(
                task_run.run_id,
                status=self._run_status_from_summary(summary),
                final_status=summary.get("final_status"),
                elapsed_time_ms=elapsed,
                error_message=summary.get("error_message"),
            )
            return {
                "run": finished_run,
                "run_id": task_run.run_id,
                "plan": final_state.get("planner_output"),
                "qa_result": final_state.get("qa_result"),
                "report": final_state.get("report"),
                "knowledge_hits": final_state.get("knowledge_hits", []),
                "workflow_summary": summary,
            }
        except WorkflowCancelled:
            return self._cancel_run_response(task_id, task_run.run_id, started)
        except Exception as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.task_run_service.finish_run(
                task_run.run_id,
                status="failed",
                final_status="failed",
                elapsed_time_ms=elapsed,
                error_message=str(exc),
            )
            raise

    def _run_manual_evidence_selection(
        self,
        *,
        task_id: str,
        source_run_id: str | None,
        selected_evidence_ids: list[str],
        started: float,
        demo_mode: DemoMode,
        writer_mode: str,
        collector_mode: str,
        analyst_mode: str,
        workflow_engine_requested: str,
        content_mode: str | None,
        collection_plan_override: PlannerCollectionPlan | None,
        collector_config: CollectorConfig | None,
    ) -> dict:
        if not source_run_id:
            raise ValueError("source_run_id is required for manual evidence selection.")
        selected_ids = list(dict.fromkeys(item for item in selected_evidence_ids if item))
        if not selected_ids:
            raise ValueError("selected_evidence_ids must contain at least one Evidence id.")

        task_run = self.task_run_service.get_run(task_id, source_run_id)
        self.trace_service.set_run_context(task_run.run_id)
        task = self.task_service.update_status(task_id, "running", rework_count=0)
        planner_output = self._planner_output_for_run(source_run_id)
        source_evidence = self.evidence_service.list_for_task(task_id, run_id=source_run_id)
        evidence_by_id = {item.evidence_id: item for item in source_evidence}
        missing_ids = [evidence_id for evidence_id in selected_ids if evidence_id not in evidence_by_id]
        if missing_ids:
            raise ValueError(f"selected Evidence ids not found in source run: {', '.join(missing_ids)}")
        selected_evidence = [evidence_by_id[evidence_id] for evidence_id in selected_ids]
        effective_collector_config = self._default_collector_config(task, collector_config)
        collection_plan = collection_plan_override or planner_output.collection_plan
        collection_plan_override_used = collection_plan_override is not None
        selected_dimensions = (
            self._collection_plan_dimensions(collection_plan)
            if collection_plan_override_used
            else planner_output.selected_dimensions
        )
        entity_resolution = self.entity_resolver_service.resolve_for_task(task)
        competitor_aliases = {
            competitor: result.get("aliases", [])
            for competitor, result in entity_resolution.items()
        }
        initial_state: WorkflowState = {
            "task_id": task_id,
            "run_id": source_run_id,
            "trace_id": f"workflow_{uuid4().hex[:10]}",
            "task": task,
            "task_run": task_run,
            "run_status": "running",
            "workflow_engine_requested": workflow_engine_requested,
            "workflow_engine_used": "langgraph",
            "demo_mode": demo_mode,
            "collector_mode": collector_mode,
            "analyst_mode": analyst_mode,
            "writer_mode": writer_mode,
            "content_mode": content_mode,
            "auto_rework": False,
            "rework_count": 0,
            "max_rework": MAX_REWORK,
            "planner_output": planner_output,
            "collection_plan_override": collection_plan_override,
            "manual_collection_plan_override_used": collection_plan_override_used,
            "collector_config": effective_collector_config,
            "collector_config_source": "request_override" if collector_config else f"task_strategy:{task.collection_strategy_mode}",
            "planner_incremental_output": None,
            "incremental_collection_plan": None,
            "planner_summary": planner_output.planner_summary.model_dump(mode="json"),
            "collection_plan": collection_plan,
            "collector_output": None,
            "analyst_output": None,
            "evidence_analyst_output": None,
            "evidence_analyst_rework_context": None,
            "report_agent_output": None,
            "report_writer_output": None,
            "qa_output": None,
            "final_report_output": None,
            "evidence_gate_output": {
                "evidence_gate_passed": True,
                "manual_evidence_selection_used": True,
                "selected_evidence_ids": selected_ids,
                "selected_evidence_count": len(selected_evidence),
                "skip_reason": "manual_evidence_selection",
                "suggested_action": "Proceed to AnalystAgent with manually selected Evidence.",
            },
            "evidence_content_fetch_output": {},
            "page_fetch_output": {},
            "entity_resolution": entity_resolution,
            "competitor_aliases": competitor_aliases,
            "evidence": selected_evidence,
            "intent_summary": planner_output.planner_summary.task_goal,
            "intent_classification": planner_output.planner_summary.intent_classification,
            "ambiguity_level": getattr(planner_output, "ambiguity_level", None),
            "scope_type": getattr(planner_output, "scope_type", None),
            "scope_size": getattr(planner_output, "scope_size", None),
            "extracted_context": getattr(planner_output, "extracted_context", None),
            "selected_dimensions": selected_dimensions,
            "analysis_dimension_plan": planner_output.analysis_dimension_plan,
            "downstream_guidance": planner_output.downstream_guidance,
            "survey_needed": getattr(planner_output, "survey_needed", False),
            "survey_recommended": getattr(planner_output, "survey_recommended", False),
            "survey_objective": getattr(planner_output, "survey_objective", None),
            "survey_inputs": getattr(planner_output, "survey_inputs", None),
            "confirmed_scope": getattr(planner_output, "confirmed_scope", None),
            "inferred_scope": getattr(planner_output, "inferred_scope", None),
            "suggested_scope": getattr(planner_output, "suggested_scope", None),
            "recommended_next_constraints": getattr(planner_output, "recommended_next_constraints", []),
            "assumptions": getattr(planner_output, "assumptions", []),
            "candidate_competitors": getattr(planner_output, "candidate_competitors", []),
            "clarification_targets": getattr(planner_output, "clarification_targets", []),
            "planning_stages": getattr(planner_output, "planning_stages", []),
            "planner_notes": planner_output.planner_notes,
            "planner_confidence": getattr(planner_output, "planner_confidence", None),
            "dimension_results": [],
            "survey_evidence": [],
            "chunks": [],
            "retrieval_results": [],
            "retrieved_knowledge_chunks": [],
            "knowledge_hits": [],
            "claim_support_results": [],
            "swot_analysis": None,
            "rework_context": None,
            "report": None,
            "qa_result": None,
            "route_to": None,
            "final_status": None,
            "errors": [],
            "node_sequence": ["manual_evidence_selection"],
            "conditional_routes_taken": [
                {
                    "from_node": "manual_evidence_selection",
                    "to_node": "evidence_content_fetcher",
                    "reason": "skip_evidence_gate",
                    "rework_count": 0,
                }
            ],
            "workflow_summary": {},
            "run_isolation_strategy": "run_id",
            "run_cleanup_summary": {},
            "manual_evidence_selection_used": True,
            "manual_selected_evidence_ids": selected_ids,
        }
        set_workflow_progress(
            task_run.run_id,
            current_agent="EvidenceContentFetcher",
            current_stage="manual_selection_content_fetch",
            message="正在抓取人工选择 Evidence 的正文...",
            node_statuses={
                "PlannerAgent": "skipped",
                "CollectorAgent": "skipped",
            },
        )
        state = self.evidence_content_fetcher_node(initial_state)
        state = self.evidence_analyst_node(state)
        set_workflow_progress(
            task_run.run_id,
            current_agent="QaAgent",
            current_stage="analyst_qa",
            message="正在检查人工选择 Evidence 的问题回答覆盖状态...",
        )
        self._qa_visual_delay(state.get("collector_mode") == "web")
        analyst_qa_output = self.qa.run(
            QaInput(
                task=self._current_task(state),
                run_id=task_run.run_id,
                qa_stage="analyst",
                evidence=state.get("evidence", []),
                evidence_analyst_output=state.get("evidence_analyst_output"),
                selected_dimensions=state.get("selected_dimensions", []),
                analysis_dimension_plan=state.get("analysis_dimension_plan"),
                collection_plan=state.get("collection_plan"),
                collector_config=state.get("collector_config"),
                retry_count=0,
            )
        )
        analyst_qa_result = self.report_service.save_qa(analyst_qa_output.qa_result, run_id=task_run.run_id)
        analyst_incremental_attempts: list[dict] = []
        analyst_attempt_no = 0
        while analyst_attempt_no < MAX_REWORK:
            self._check_cancelled(task_run.run_id)
            coverage_gap_payload = analyst_qa_result.metadata.get("coverage_gap") if analyst_qa_result.metadata else None
            if not (
                analyst_qa_result.status == "failed"
                and isinstance(coverage_gap_payload, dict)
                and coverage_gap_payload.get("targets")
            ):
                break
            analyst_attempt_no += 1
            coverage_gap = EvidenceCoverageGap.model_validate(coverage_gap_payload)
            attempts = self.planner_attempt_service.list_for_run(task_run.run_id)
            base_attempt_no = attempts[-1].attempt_no if attempts else None
            set_workflow_progress(
                task_run.run_id,
                current_agent="PlannerAgent",
                current_stage="manual_selection_analyst_rework_planning",
                message=f"正在规划第 {analyst_attempt_no} / {MAX_REWORK} 轮人工选择后的问题补采查询词...",
                current=analyst_attempt_no,
                total=MAX_REWORK,
                unit="attempt",
            )
            incremental_output = self.planner.plan_incremental_collection(
                PlannerIncrementalInput(
                    task=self._current_task(state),
                    run_id=task_run.run_id,
                    retry_count=analyst_attempt_no,
                    base_planner_output=planner_output,
                    coverage_gap=coverage_gap,
                    base_attempt_no=base_attempt_no,
                )
            )
            self.planner_attempt_service.save(
                run_id=task_run.run_id,
                status="fallback" if incremental_output.diagnostics.get("fallback_used") else "generated",
                planner_output=incremental_output.incremental_collection_plan,
                diagnostics=incremental_output.diagnostics,
                rework_context={"source": "ManualEvidenceSelectionAnalystQA", "coverage_gap": coverage_gap.model_dump(mode="json")},
                raw_llm_response=incremental_output._raw_llm_response,
            )
            set_workflow_progress(
                task_run.run_id,
                current_agent="CollectorAgent",
                current_stage="manual_selection_analyst_rework_collect",
                message=f"正在执行第 {analyst_attempt_no} / {MAX_REWORK} 轮人工选择后的问题补采...",
                current=analyst_attempt_no,
                total=MAX_REWORK,
                unit="attempt",
            )
            incremental_collector_output = self.collector.run(
                CollectorInput(
                    task=self._current_task(state),
                    run_id=task_run.run_id,
                    retry_count=analyst_attempt_no,
                    collector_mode=state["collector_mode"],
                    collection_plan=state.get("collection_plan"),
                    incremental_collection_plan=incremental_output.incremental_collection_plan,
                    collector_config=state.get("collector_config"),
                    partial_collection_plan_allowed=state.get("manual_collection_plan_override_used", False),
                    selected_dimensions=state.get("selected_dimensions", []),
                    analysis_dimension_plan=state.get("analysis_dimension_plan"),
                    rework_context=state.get("rework_context"),
                    competitor_aliases=state.get("competitor_aliases", {}),
                )
            )
            incremental_evidence = self.evidence_service.save_many(
                self._current_task(state).task_id,
                incremental_collector_output.evidence,
                run_id=task_run.run_id,
            )
            new_evidence_ids_by_target: dict[tuple[str, str], list[str]] = {}
            for item in incremental_evidence:
                dimension_id = (item.entity_match_signals or {}).get("collector_dimension")
                if item.competitor and dimension_id:
                    new_evidence_ids_by_target.setdefault((item.competitor, str(dimension_id)), []).append(item.evidence_id)
            evidence_analyst_rework_context = EvidenceAnalystReworkContext(
                targets=[
                    EvidenceAnalystReworkTarget(
                        competitor=target.competitor,
                        dimension_id=target.dimension_id,
                        question_id=target.question_id or "",
                        question=target.question or target.reason,
                        new_evidence_ids=new_evidence_ids_by_target.get((target.competitor, target.dimension_id), []),
                    )
                    for target in coverage_gap.targets
                    if target.question_id and target.question
                ]
            )
            state = {
                **state,
                "evidence": [*state.get("evidence", []), *incremental_evidence],
                "evidence_content_fetch_target_ids": [item.evidence_id for item in incremental_evidence],
                "collector_output": incremental_collector_output,
                "evidence_analyst_rework_context": evidence_analyst_rework_context,
                "node_sequence": [*state["node_sequence"], "collector_incremental_analystqa"],
            }
            state = self.evidence_content_fetcher_node(state)
            self._check_cancelled(task_run.run_id)
            state = self.evidence_analyst_node(state)
            self._check_cancelled(task_run.run_id)
            analyst_qa_output = self.qa.run(
                QaInput(
                    task=self._current_task(state),
                    run_id=task_run.run_id,
                    qa_stage="analyst",
                    evidence=state.get("evidence", []),
                    evidence_analyst_output=state.get("evidence_analyst_output"),
                    selected_dimensions=state.get("selected_dimensions", []),
                    analysis_dimension_plan=state.get("analysis_dimension_plan"),
                    collection_plan=state.get("collection_plan"),
                    collector_config=state.get("collector_config"),
                    retry_count=analyst_attempt_no,
                )
            )
            analyst_qa_result = self.report_service.save_qa(analyst_qa_output.qa_result, run_id=task_run.run_id)
            analyst_incremental_attempts.append(
                {
                    "attempt_no": analyst_attempt_no,
                    "incremental_collection_plan": incremental_output.incremental_collection_plan.model_dump(mode="json"),
                    "collector_output": incremental_collector_output.model_dump(mode="json"),
                    "qa_result": analyst_qa_result.model_dump(mode="json"),
                }
            )
        state = {
            **state,
            "qa_output": analyst_qa_output,
            "qa_result": analyst_qa_result,
            "rework_count": analyst_attempt_no,
            "final_status": f"analyst_qa_{analyst_qa_result.status}",
            "conditional_routes_taken": [
                *state.get("conditional_routes_taken", []),
                *(
                    [
                        {
                            "from_node": "analyst_qa",
                            "to_node": "planner_incremental_analystqa",
                            "reason": "analyst_question_not_found",
                            "rework_count": len(analyst_incremental_attempts),
                        }
                    ]
                    if analyst_incremental_attempts
                    else []
                ),
            ],
            "node_sequence": [*state["node_sequence"], "analyst_qa"],
        }
        if state.get("evidence_analyst_output") is not None:
            state = self.report_agent_node(state)
        elapsed = int((time.perf_counter() - started) * 1000)
        summary = self._workflow_summary(state, elapsed)
        saved_report = state.get("report")
        manual_selection_dag = {
            "nodes": [
                {"id": "PlannerAgent", "label": "复用来源 run 的规划结果", "status": "skipped"},
                {"id": "CollectorAgent", "label": "人工选择 Evidence，不重新采集", "status": "skipped"},
                {"id": "QaAgent", "label": "AnalystQA 问题回答覆盖检查", "status": "completed"},
                {"id": "EvidenceContentFetcher", "label": "抓取人工选择 Evidence 正文", "status": "completed"},
                {"id": "EvidenceAnalystAgent", "label": "基于所选 Evidence 回答规划问题", "status": "completed"},
                {"id": "ReportAgent", "label": "基于 EvidenceAnalystOutput 生成报告", "status": "completed" if saved_report else "skipped"},
            ],
            "edges": [
                {"source": "EvidenceContentFetcher", "target": "EvidenceAnalystAgent", "label": "正文内容"},
                {"source": "EvidenceAnalystAgent", "target": "QaAgent", "label": "AnalystQA"},
                {"source": "QaAgent", "target": "ReportAgent", "label": "通过后生成报告"},
            ],
        }
        summary.update(
            {
                "debug_stage": "collector_only",
                "dag": manual_selection_dag,
                "manual_evidence_selection_used": True,
                "manual_selected_evidence_ids": selected_ids,
                "manual_selected_evidence_count": len(selected_evidence),
                "source_run_id": source_run_id,
                "analyst_qa_output": analyst_qa_output.model_dump(mode="json"),
                "analyst_qa_result": analyst_qa_result.model_dump(mode="json"),
                "analyst_incremental_attempts": analyst_incremental_attempts,
            }
        )
        self._save_workflow_trace(task_id, source_run_id, summary, elapsed)
        final_failed = analyst_qa_result.status == "failed"
        set_workflow_progress(
            task_run.run_id,
            current_agent="WorkflowEngine",
            current_stage="completed" if not final_failed else "qa_failed",
            message="人工选择 Evidence 后续流程已完成。" if not final_failed else "AnalystQA 未通过，流程已结束。",
            status="completed" if not final_failed else "qa_failed",
            node_statuses={
                "QaAgent": "failed" if final_failed else "completed",
                "ReportAgent": "completed" if state.get("report") is not None else "skipped",
            },
        )
        self.task_service.update_status(task_id, "qa_failed" if final_failed else "completed", rework_count=analyst_attempt_no)
        finished_run = self.task_run_service.finish_run(
            source_run_id,
            status="qa_failed" if final_failed else "completed",
            final_status=summary.get("final_status"),
            elapsed_time_ms=elapsed,
            error_message=summary.get("error_message"),
        )
        return {
            "run": finished_run,
            "run_id": source_run_id,
            "plan": planner_output,
            "qa_result": state.get("qa_result"),
            "report": state.get("report"),
            "knowledge_hits": state.get("knowledge_hits", []),
            "workflow_summary": summary,
            "evidence": state.get("evidence", []),
        }

    def _run_planner_only(self, initial_state: WorkflowState, task_run, started: float) -> dict:
        planner_state = self.planner_node(initial_state)
        planner_output = planner_state["planner_output"]
        elapsed = int((time.perf_counter() - started) * 1000)
        planner_json = planner_output.model_dump(mode="json")
        frozen_dag = {
            "nodes": [
                {"id": "PlannerAgent", "label": "规划分析维度与采集策略", "status": "completed"},
                {"id": "CollectorAgent", "label": "已冻结，不执行证据采集", "status": "skipped"},
                {"id": "AnalystAgent", "label": "已冻结，不执行事实抽取", "status": "skipped"},
                {"id": "ReportWriterAgent", "label": "已冻结，不执行报告生成", "status": "skipped"},
            ],
            "edges": [],
        }
        summary = {
            "run_id": task_run.run_id,
            "task_id": initial_state["task_id"],
            "workflow_engine_requested": initial_state["workflow_engine_requested"],
            "workflow_engine_used": "langgraph",
            "debug_stage": "planner_only",
            "planner_summary": planner_output.planner_summary.model_dump(mode="json"),
            "intent_summary": planner_output.planner_summary.task_goal,
            "intent_classification": planner_output.planner_summary.intent_classification,
            "selected_dimensions": planner_output.selected_dimensions,
            "analysis_dimension_plan": (
                planner_output.analysis_dimension_plan.model_dump(mode="json")
                if planner_output.analysis_dimension_plan
                else None
            ),
            "collection_plan": planner_output.collection_plan.model_dump(mode="json"),
            "downstream_guidance": (
                planner_output.downstream_guidance.model_dump(mode="json")
                if planner_output.downstream_guidance
                else None
            ),
            "diagnostics": planner_output.diagnostics,
            "planner_notes": planner_output.planner_notes,
            "planner_output": planner_json,
            "dag": frozen_dag,
            "node_sequence": ["planner"],
            "conditional_routes_taken": [],
            "rework_count": initial_state["rework_count"],
            "final_status": "planner_completed",
            "elapsed_time_ms": elapsed,
            "run_isolation_strategy": "run_id",
        }
        self._save_workflow_trace(initial_state["task_id"], task_run.run_id, summary, elapsed)
        self.task_service.update_status(initial_state["task_id"], "completed", rework_count=initial_state["rework_count"])
        finished_run = self.task_run_service.finish_run(
            task_run.run_id,
            status="completed",
            final_status="planner_completed",
            elapsed_time_ms=elapsed,
        )
        return {
            "run": finished_run,
            "run_id": task_run.run_id,
            "plan": planner_output,
            "planner_output": planner_json,
            "planner_summary": summary["planner_summary"],
            "intent_summary": planner_output.planner_summary.task_goal,
            "intent_classification": planner_output.planner_summary.intent_classification,
            "selected_dimensions": planner_output.selected_dimensions,
            "analysis_dimension_plan": summary["analysis_dimension_plan"],
            "collection_plan": summary["collection_plan"],
            "downstream_guidance": summary["downstream_guidance"],
            "diagnostics": planner_output.diagnostics,
            "planner_notes": planner_output.planner_notes,
            "dag": frozen_dag,
            "qa_result": None,
            "report": None,
            "knowledge_hits": [],
            "workflow_summary": summary,
        }

    def _run_collector_only(self, initial_state: WorkflowState, task_run, started: float) -> dict:
        planner_state = self.planner_node(initial_state)
        self._check_cancelled(task_run.run_id)
        collector_error: str | None = None
        try:
            collector_state = self.collector_node(planner_state)
        except AgentExecutionError as exc:
            collector_error = str(exc)
            diagnostics = (
                exc.output.get("diagnostics", {})
                if isinstance(exc.output, dict)
                else {}
            )
            collector_output = CollectorOutput(evidence=[], diagnostics=diagnostics)
            collector_state = {
                **planner_state,
                "collector_output": collector_output,
                "evidence": [],
                "node_sequence": [*planner_state["node_sequence"], "collector"],
                "errors": [*planner_state.get("errors", []), collector_error],
            }
        self._check_cancelled(task_run.run_id)

        planner_output = collector_state["planner_output"]
        collector_output = collector_state["collector_output"]
        set_workflow_progress(
            task_run.run_id,
            current_agent="QaAgent",
            current_stage="evidence_qa",
            message="正在检查 Evidence 覆盖状态...",
        )
        self._qa_visual_delay(collector_state.get("collector_mode") == "web")
        qa_output = self.qa.run(
            QaInput(
                task=self._current_task(collector_state),
                run_id=task_run.run_id,
                qa_stage="evidence",
                evidence=collector_state.get("evidence", []),
                selected_dimensions=collector_state.get("selected_dimensions", []),
                analysis_dimension_plan=collector_state.get("analysis_dimension_plan"),
                collection_plan=collector_state.get("collection_plan"),
                collector_config=collector_state.get("collector_config"),
                collector_trace_summary=collector_output.diagnostics,
                retry_count=0,
            )
        )
        qa_result = self.report_service.save_qa(qa_output.qa_result, run_id=task_run.run_id)
        initial_qa_output = qa_output
        initial_qa_result = qa_result
        incremental_output = None
        incremental_collector_output = None
        incremental_attempts: list[dict] = []
        current_collector_diagnostics = collector_output.diagnostics
        attempt_no = 0
        while attempt_no < MAX_REWORK:
            self._check_cancelled(task_run.run_id)
            coverage_gap_payload = qa_result.metadata.get("coverage_gap") if qa_result.metadata else None
            if not (qa_result.status == "failed" and isinstance(coverage_gap_payload, dict) and coverage_gap_payload.get("targets")):
                break
            attempt_no += 1
            coverage_gap = EvidenceCoverageGap.model_validate(coverage_gap_payload)
            attempts = self.planner_attempt_service.list_for_run(task_run.run_id)
            base_attempt_no = attempts[-1].attempt_no if attempts else None
            set_workflow_progress(
                task_run.run_id,
                current_agent="PlannerAgent",
                current_stage="evidence_rework_planning",
                message=f"正在规划第 {attempt_no} / {MAX_REWORK} 轮 Evidence 补采查询词...",
                current=attempt_no,
                total=MAX_REWORK,
                unit="attempt",
            )
            incremental_output = self.planner.plan_incremental_collection(
                PlannerIncrementalInput(
                    task=self._current_task(collector_state),
                    run_id=task_run.run_id,
                    retry_count=attempt_no,
                    base_planner_output=planner_output,
                    coverage_gap=coverage_gap,
                    base_attempt_no=base_attempt_no,
                )
            )
            self.planner_attempt_service.save(
                run_id=task_run.run_id,
                status="fallback" if incremental_output.diagnostics.get("fallback_used") else "generated",
                planner_output=incremental_output.incremental_collection_plan,
                diagnostics=incremental_output.diagnostics,
                rework_context={"source": "EvidenceQA", "coverage_gap": coverage_gap.model_dump(mode="json")},
                raw_llm_response=incremental_output._raw_llm_response,
            )
            set_workflow_progress(
                task_run.run_id,
                current_agent="CollectorAgent",
                current_stage="evidence_rework_collect",
                message=f"正在执行第 {attempt_no} / {MAX_REWORK} 轮 Evidence 补采...",
                current=attempt_no,
                total=MAX_REWORK,
                unit="attempt",
            )
            incremental_collector_output = self.collector.run(
                CollectorInput(
                    task=self._current_task(collector_state),
                    run_id=task_run.run_id,
                    retry_count=attempt_no,
                    collector_mode=collector_state["collector_mode"],
                    collection_plan=collector_state.get("collection_plan"),
                    incremental_collection_plan=incremental_output.incremental_collection_plan,
                collector_config=collector_state.get("collector_config"),
                partial_collection_plan_allowed=collector_state.get("manual_collection_plan_override_used", False),
                selected_dimensions=collector_state.get("selected_dimensions", []),
                    analysis_dimension_plan=collector_state.get("analysis_dimension_plan"),
                    rework_context=collector_state.get("rework_context"),
                    competitor_aliases=collector_state.get("competitor_aliases", {}),
                )
            )
            incremental_evidence = self.evidence_service.save_many(
                self._current_task(collector_state).task_id,
                incremental_collector_output.evidence,
                run_id=task_run.run_id,
            )
            merged_evidence = [*collector_state.get("evidence", []), *incremental_evidence]
            merged_collector_diagnostics = {
                **current_collector_diagnostics,
                "collection_plan_used": True,
                "collector_search_plan_missing": False,
                "collector_search_plan_used": True,
                "failed_queries": list(
                    dict.fromkeys(
                        [
                            *current_collector_diagnostics.get("failed_queries", []),
                            *incremental_collector_output.diagnostics.get("failed_queries", []),
                        ]
                    )
                ),
                "full": collector_output.diagnostics,
                "incremental": incremental_collector_output.diagnostics,
                "merged_evidence_count": len(merged_evidence),
            }
            set_workflow_progress(
                task_run.run_id,
                current_agent="QaAgent",
                current_stage="evidence_rework_qa",
                message="正在检查补采后的 merged Evidence...",
                current=attempt_no,
                total=MAX_REWORK,
                unit="attempt",
            )
            self._qa_visual_delay(collector_state.get("collector_mode") == "web")
            qa_output = self.qa.run(
                QaInput(
                    task=self._current_task(collector_state),
                    run_id=task_run.run_id,
                    qa_stage="evidence",
                    evidence=merged_evidence,
                    selected_dimensions=collector_state.get("selected_dimensions", []),
                    analysis_dimension_plan=collector_state.get("analysis_dimension_plan"),
                    collection_plan=collector_state.get("collection_plan"),
                    collector_config=collector_state.get("collector_config"),
                    collector_trace_summary=merged_collector_diagnostics,
                    retry_count=attempt_no,
                )
            )
            qa_result = self.report_service.save_qa(qa_output.qa_result, run_id=task_run.run_id)
            collector_state = {**collector_state, "evidence": merged_evidence}
            current_collector_diagnostics = merged_collector_diagnostics
            incremental_attempts.append(
                {
                    "attempt_no": attempt_no,
                    "incremental_collection_plan": incremental_output.incremental_collection_plan.model_dump(mode="json"),
                    "collector_output": incremental_collector_output.model_dump(mode="json"),
                    "qa_result": qa_result.model_dump(mode="json"),
                }
            )
        self._check_cancelled(task_run.run_id)
        collector_state = self.evidence_content_fetcher_node(collector_state)
        self._check_cancelled(task_run.run_id)
        collector_state = self.evidence_analyst_node(collector_state)
        self._check_cancelled(task_run.run_id)
        set_workflow_progress(
            task_run.run_id,
            current_agent="QaAgent",
            current_stage="analyst_qa",
            message="正在检查问题回答覆盖状态...",
        )
        self._qa_visual_delay(collector_state.get("collector_mode") == "web")
        analyst_qa_output = self.qa.run(
            QaInput(
                task=self._current_task(collector_state),
                run_id=task_run.run_id,
                qa_stage="analyst",
                evidence=collector_state.get("evidence", []),
                evidence_analyst_output=collector_state.get("evidence_analyst_output"),
                selected_dimensions=collector_state.get("selected_dimensions", []),
                analysis_dimension_plan=collector_state.get("analysis_dimension_plan"),
                collection_plan=collector_state.get("collection_plan"),
                collector_config=collector_state.get("collector_config"),
                retry_count=0,
            )
        )
        analyst_qa_result = self.report_service.save_qa(analyst_qa_output.qa_result, run_id=task_run.run_id)
        analyst_incremental_attempts: list[dict] = []
        analyst_attempt_no = 0
        while analyst_attempt_no < MAX_REWORK:
            self._check_cancelled(task_run.run_id)
            coverage_gap_payload = analyst_qa_result.metadata.get("coverage_gap") if analyst_qa_result.metadata else None
            if not (
                analyst_qa_result.status == "failed"
                and isinstance(coverage_gap_payload, dict)
                and coverage_gap_payload.get("targets")
            ):
                break
            analyst_attempt_no += 1
            coverage_gap = EvidenceCoverageGap.model_validate(coverage_gap_payload)
            attempts = self.planner_attempt_service.list_for_run(task_run.run_id)
            base_attempt_no = attempts[-1].attempt_no if attempts else None
            set_workflow_progress(
                task_run.run_id,
                current_agent="PlannerAgent",
                current_stage="analyst_rework_planning",
                message=f"正在规划第 {analyst_attempt_no} / {MAX_REWORK} 轮问题补采查询词...",
                current=analyst_attempt_no,
                total=MAX_REWORK,
                unit="attempt",
            )
            incremental_output = self.planner.plan_incremental_collection(
                PlannerIncrementalInput(
                    task=self._current_task(collector_state),
                    run_id=task_run.run_id,
                    retry_count=analyst_attempt_no,
                    base_planner_output=planner_output,
                    coverage_gap=coverage_gap,
                    base_attempt_no=base_attempt_no,
                )
            )
            self.planner_attempt_service.save(
                run_id=task_run.run_id,
                status="fallback" if incremental_output.diagnostics.get("fallback_used") else "generated",
                planner_output=incremental_output.incremental_collection_plan,
                diagnostics=incremental_output.diagnostics,
                rework_context={"source": "AnalystQA", "coverage_gap": coverage_gap.model_dump(mode="json")},
                raw_llm_response=incremental_output._raw_llm_response,
            )
            set_workflow_progress(
                task_run.run_id,
                current_agent="CollectorAgent",
                current_stage="analyst_rework_collect",
                message=f"正在执行第 {analyst_attempt_no} / {MAX_REWORK} 轮问题补采...",
                current=analyst_attempt_no,
                total=MAX_REWORK,
                unit="attempt",
            )
            incremental_collector_output = self.collector.run(
                CollectorInput(
                    task=self._current_task(collector_state),
                    run_id=task_run.run_id,
                    retry_count=analyst_attempt_no,
                    collector_mode=collector_state["collector_mode"],
                    collection_plan=collector_state.get("collection_plan"),
                    incremental_collection_plan=incremental_output.incremental_collection_plan,
                    collector_config=collector_state.get("collector_config"),
                    partial_collection_plan_allowed=collector_state.get("manual_collection_plan_override_used", False),
                    selected_dimensions=collector_state.get("selected_dimensions", []),
                    analysis_dimension_plan=collector_state.get("analysis_dimension_plan"),
                    rework_context=collector_state.get("rework_context"),
                    competitor_aliases=collector_state.get("competitor_aliases", {}),
                )
            )
            incremental_evidence = self.evidence_service.save_many(
                self._current_task(collector_state).task_id,
                incremental_collector_output.evidence,
                run_id=task_run.run_id,
            )
            new_evidence_ids_by_target: dict[tuple[str, str], list[str]] = {}
            for item in incremental_evidence:
                dimension_id = (item.entity_match_signals or {}).get("collector_dimension")
                if item.competitor and dimension_id:
                    new_evidence_ids_by_target.setdefault((item.competitor, str(dimension_id)), []).append(item.evidence_id)
            evidence_analyst_rework_context = EvidenceAnalystReworkContext(
                targets=[
                    EvidenceAnalystReworkTarget(
                        competitor=target.competitor,
                        dimension_id=target.dimension_id,
                        question_id=target.question_id or "",
                        question=target.question or target.reason,
                        new_evidence_ids=new_evidence_ids_by_target.get((target.competitor, target.dimension_id), []),
                    )
                    for target in coverage_gap.targets
                    if target.question_id and target.question
                ]
            )
            collector_state = {
                **collector_state,
                "evidence": [*collector_state.get("evidence", []), *incremental_evidence],
                "evidence_content_fetch_target_ids": [item.evidence_id for item in incremental_evidence],
                "collector_output": incremental_collector_output,
                "evidence_analyst_rework_context": evidence_analyst_rework_context,
                "node_sequence": [*collector_state["node_sequence"], "collector_incremental_analystqa"],
            }
            set_workflow_progress(
                task_run.run_id,
                current_agent="EvidenceContentFetcher",
                current_stage="analyst_rework_content_fetch",
                message="正在抓取本轮补采 Evidence 正文...",
                current=0,
                total=len(incremental_evidence),
                unit="evidence",
            )
            collector_state = self.evidence_content_fetcher_node(collector_state)
            self._check_cancelled(task_run.run_id)
            collector_state = self.evidence_analyst_node(collector_state)
            self._check_cancelled(task_run.run_id)
            set_workflow_progress(
                task_run.run_id,
                current_agent="QaAgent",
                current_stage="analyst_rework_qa",
                message="正在检查补采后的问题回答状态...",
                current=analyst_attempt_no,
                total=MAX_REWORK,
                unit="attempt",
            )
            self._qa_visual_delay(collector_state.get("collector_mode") == "web")
            analyst_qa_output = self.qa.run(
                QaInput(
                    task=self._current_task(collector_state),
                    run_id=task_run.run_id,
                    qa_stage="analyst",
                    evidence=collector_state.get("evidence", []),
                    evidence_analyst_output=collector_state.get("evidence_analyst_output"),
                    selected_dimensions=collector_state.get("selected_dimensions", []),
                    analysis_dimension_plan=collector_state.get("analysis_dimension_plan"),
                    collection_plan=collector_state.get("collection_plan"),
                    collector_config=collector_state.get("collector_config"),
                    retry_count=analyst_attempt_no,
                )
            )
            analyst_qa_result = self.report_service.save_qa(analyst_qa_output.qa_result, run_id=task_run.run_id)
            analyst_incremental_attempts.append(
                {
                    "attempt_no": analyst_attempt_no,
                    "incremental_collection_plan": incremental_output.incremental_collection_plan.model_dump(mode="json"),
                    "collector_output": incremental_collector_output.model_dump(mode="json"),
                    "qa_result": analyst_qa_result.model_dump(mode="json"),
                }
            )
        content_fetch_output = collector_state.get("evidence_content_fetch_output", {})
        evidence_analyst_output = collector_state.get("evidence_analyst_output")
        if evidence_analyst_output is not None:
            self._check_cancelled(task_run.run_id)
            collector_state = self.report_agent_node({**collector_state, "rework_count": analyst_attempt_no})
        report_agent_output = collector_state.get("report_agent_output")
        saved_report = collector_state.get("report")
        elapsed = int((time.perf_counter() - started) * 1000)
        collector_failed = collector_error is not None
        frozen_dag = {
            "nodes": [
                {"id": "PlannerAgent", "label": "规划分析维度与采集策略", "status": "completed"},
                {
                    "id": "CollectorAgent",
                    "label": "按 Planner collection_plan 采集 Evidence",
                    "status": "failed" if collector_failed else "completed",
                },
                {"id": "EvidenceGate", "label": "legacy：调试模式不执行", "status": "skipped"},
                {"id": "EvidenceContentFetcher", "label": "Tavily Extract 正文抽取", "status": "completed"},
                {"id": "EvidenceAnalystAgent", "label": "逐条 Evidence 事实抽取", "status": "completed"},
                {"id": "ReportAgent", "label": "基于 EvidenceAnalystOutput 生成最终报告", "status": "completed" if saved_report else "skipped"},
                {"id": "PageFetcher", "label": "legacy，本链路不执行", "status": "skipped"},
                {"id": "AnalystAgent", "label": "已冻结，不抽取结构化事实", "status": "skipped"},
                {"id": "ReportWriterAgent", "label": "已冻结，不生成报告", "status": "skipped"},
                {"id": "QaAgent", "label": "EvidenceQA 证据质量检查", "status": "completed"},
                {"id": "FinalReport", "label": "已冻结，不生成最终报告", "status": "skipped"},
            ],
            "edges": [
                {"source": "PlannerAgent", "target": "CollectorAgent", "label": "collection_plan"},
                {"source": "CollectorAgent", "target": "QaAgent", "label": "EvidenceQA"},
                {"source": "QaAgent", "target": "EvidenceContentFetcher", "label": "正文抽取"},
                {"source": "EvidenceContentFetcher", "target": "EvidenceAnalystAgent", "label": "事实抽取"},
                {"source": "EvidenceAnalystAgent", "target": "ReportAgent", "label": "final_report"},
            ],
        }
        summary = {
            "run_id": task_run.run_id,
            "task_id": initial_state["task_id"],
            "workflow_engine_requested": initial_state["workflow_engine_requested"],
            "workflow_engine_used": "langgraph",
            "debug_stage": "collector_only",
            "planner_summary": planner_output.planner_summary.model_dump(mode="json"),
            "selected_dimensions": planner_output.selected_dimensions,
            "analysis_dimension_plan": planner_output.analysis_dimension_plan.model_dump(mode="json"),
            "manual_collection_plan_override_used": collector_state.get("manual_collection_plan_override_used", False),
            "collection_strategy_mode": self._current_task(collector_state).collection_strategy_mode,
            "collector_config_source": collector_state.get("collector_config_source"),
            "collector_config": (
                collector_state.get("collector_config").model_dump(mode="json")
                if collector_state.get("collector_config")
                else None
            ),
            "collection_plan": collector_state.get("collection_plan").model_dump(mode="json"),
            "planner_collection_plan_original": planner_output.collection_plan.model_dump(mode="json"),
            "downstream_guidance": planner_output.downstream_guidance.model_dump(mode="json"),
            "diagnostics": planner_output.diagnostics,
            "planner_notes": planner_output.planner_notes,
            "planner_output": planner_output.model_dump(mode="json"),
            "collector_output": collector_output.model_dump(mode="json"),
            "collector_diagnostics": collector_output.diagnostics,
            "incremental_collector_output": (
                incremental_collector_output.model_dump(mode="json")
                if incremental_collector_output
                else None
            ),
            "incremental_collector_diagnostics": (
                incremental_collector_output.diagnostics
                if incremental_collector_output
                else None
            ),
            "initial_qa_output": initial_qa_output.model_dump(mode="json"),
            "initial_qa_result": initial_qa_result.model_dump(mode="json"),
            "qa_output": qa_output.model_dump(mode="json"),
            "qa_result": qa_result.model_dump(mode="json"),
            "analyst_qa_output": analyst_qa_output.model_dump(mode="json"),
            "analyst_qa_result": analyst_qa_result.model_dump(mode="json"),
            "evidence_content_fetch_output": content_fetch_output,
            "evidence_analyst_output": (
                evidence_analyst_output.model_dump(mode="json")
                if evidence_analyst_output
                else None
            ),
            "report_agent_output": (
                report_agent_output.model_dump(mode="json")
                if report_agent_output
                else None
            ),
            "markdown_report": saved_report.markdown if saved_report else None,
            "incremental_collection_plan": (
                incremental_output.incremental_collection_plan.model_dump(mode="json")
                if incremental_output
                else None
            ),
            "planner_incremental_output": incremental_output.model_dump(mode="json") if incremental_output else None,
            "incremental_attempts": incremental_attempts,
            "analyst_incremental_attempts": analyst_incremental_attempts,
            "dag": frozen_dag,
            "node_sequence": (
                ["planner", "collector", "qa"]
                + [
                    node
                    for _attempt in incremental_attempts
                    for node in ["planner_incremental", "collector_incremental", "qa_incremental"]
                ]
                + ["evidence_content_fetcher"]
                + ["evidence_analyst"]
                + ["analyst_qa"]
                + [
                    node
                    for _attempt in analyst_incremental_attempts
                    for node in [
                        "planner_incremental_analystqa",
                        "collector_incremental_analystqa",
                        "evidence_content_fetcher",
                        "evidence_analyst",
                        "analyst_qa_incremental",
                    ]
                ]
                + (["report_agent"] if saved_report else [])
            ),
            "conditional_routes_taken": (
                [
                    {
                        "from_node": "analyst_qa",
                        "to_node": "planner_incremental_analystqa",
                        "reason": "analyst_question_not_found",
                        "rework_count": len(analyst_incremental_attempts),
                    }
                ]
                if analyst_incremental_attempts
                else []
            ),
            "rework_count": attempt_no + analyst_attempt_no,
            "final_status": f"analyst_qa_{analyst_qa_result.status}",
            "elapsed_time_ms": elapsed,
            "run_isolation_strategy": "run_id",
        }
        self._save_workflow_trace(initial_state["task_id"], task_run.run_id, summary, elapsed)
        final_failed = qa_result.status == "failed" or analyst_qa_result.status == "failed"
        task_status = "qa_failed" if final_failed else "completed"
        run_status = "qa_failed" if final_failed else "completed"
        set_workflow_progress(
            task_run.run_id,
            current_agent="WorkflowEngine",
            current_stage=run_status,
            message="主流程已完成。" if not final_failed else "QA 未通过，流程已结束。",
            status=run_status,
            node_statuses={
                "QaAgent": "failed" if final_failed else "completed",
                "ReportAgent": "completed" if saved_report else "skipped",
            },
        )
        self.task_service.update_status(initial_state["task_id"], task_status, rework_count=attempt_no + analyst_attempt_no)
        finished_run = self.task_run_service.finish_run(
            task_run.run_id,
            status=run_status,
            final_status=summary["final_status"],
            elapsed_time_ms=elapsed,
            error_message=collector_error,
        )
        return {
            "run": finished_run,
            "run_id": task_run.run_id,
            "plan": planner_output,
            "planner_output": summary["planner_output"],
            "collector_output": summary["collector_output"],
            "qa_output": summary["qa_output"],
            "evidence": collector_state.get("evidence", []),
            "qa_result": qa_result,
            "dag": frozen_dag,
            "report": saved_report,
            "knowledge_hits": [],
            "workflow_summary": summary,
        }

    @staticmethod
    def _collection_plan_dimensions(collection_plan: PlannerCollectionPlan) -> list[str]:
        seen: set[str] = set()
        dimensions: list[str] = []
        for by_dimension in collection_plan.collector_search_plan.values():
            for dimension_id, item in by_dimension.items():
                value = item.dimension_id or dimension_id
                if value in seen:
                    continue
                seen.add(value)
                dimensions.append(value)
        return dimensions

    def _build_graph(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("planner", self.planner_node)
        graph.add_node("collector", self.collector_node)
        graph.add_node("evidence_gate", self.evidence_gate_node)
        graph.add_node("page_fetcher", self.page_fetcher_node)
        graph.add_node("analyst", self.analyst_node)
        graph.add_node("report_writer", self.report_writer_node)
        graph.add_node("qa", self.qa_node)
        graph.add_node("final_report", self.final_report_node)
        graph.set_entry_point("planner")
        graph.add_edge("planner", "collector")
        graph.add_edge("collector", "evidence_gate")
        graph.add_conditional_edges(
            "evidence_gate",
            self.route_after_evidence_gate,
            {
                "collector": "collector",
                "page_fetcher": "page_fetcher",
                "final_report": "final_report",
            },
        )
        graph.add_edge("page_fetcher", "analyst")
        graph.add_edge("analyst", "report_writer")
        graph.add_edge("report_writer", "qa")
        graph.add_conditional_edges(
            "qa",
            self.route_after_qa,
            {
                "planner": "planner",
                "collector": "collector",
                "analyst": "analyst",
                "report_writer": "report_writer",
                "final_report": "final_report",
            },
        )
        graph.add_edge("final_report", END)
        return graph.compile()

    def planner_node(self, state: WorkflowState) -> WorkflowState:
        self._check_cancelled(state.get("run_id"))
        task = state["task"]
        run_id = state.get("run_id")
        manual_plan = state.get("collection_plan_override")
        skip_initial_planner = bool(
            state.get("skip_initial_planner")
            and manual_plan
            and state.get("planner_output") is None
        )
        set_workflow_progress(
            run_id,
            current_agent="PlannerAgent",
            current_stage="manual_planning" if skip_initial_planner else "planning",
            message=(
                "正在加载人工规划..."
                if skip_initial_planner
                else "正在规划分析维度和采集查询词..."
            ),
        )
        if skip_initial_planner:
            output = self._manual_planner_output(task, manual_plan)
        else:
            try:
                output = self.planner.run(
                    PlannerInput(task=task, run_id=run_id, retry_count=state["rework_count"])
                )
            except Exception as exc:
                if run_id:
                    self.planner_attempt_service.save(
                        run_id=run_id,
                        status="failed",
                        planner_output=None,
                        diagnostics={
                            "planner_mode_used": "failed",
                            "fallback_used": False,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        },
                        rework_context=state.get("rework_context"),
                    )
                raise
        if run_id:
            self.planner_attempt_service.save(
                run_id=run_id,
                status="fallback" if output.diagnostics.get("fallback_used") else "generated",
                planner_output=output,
                diagnostics=output.diagnostics,
                rework_context=state.get("rework_context"),
                raw_llm_response=output._raw_llm_response,
            )
        collection_plan = state.get("collection_plan_override") or output.collection_plan
        collection_plan_override_used = state.get("collection_plan_override") is not None
        analysis_dimension_plan = output.analysis_dimension_plan
        selected_dimensions = (
            self._collection_plan_dimensions(collection_plan)
            if collection_plan_override_used
            else output.selected_dimensions
        )
        entity_resolution = self.entity_resolver_service.resolve_for_task(task)
        competitor_aliases = {
            competitor: result.get("aliases", [])
            for competitor, result in entity_resolution.items()
        }
        return {
            **state,
            "planner_output": output,
            "planner_summary": output.planner_summary.model_dump(mode="json"),
            "collection_plan": collection_plan,
            "manual_collection_plan_override_used": collection_plan_override_used,
            "entity_resolution": entity_resolution,
            "competitor_aliases": competitor_aliases,
            "intent_summary": output.planner_summary.task_goal,
            "intent_classification": output.planner_summary.intent_classification,
            "selected_dimensions": selected_dimensions,
            "analysis_dimension_plan": analysis_dimension_plan,
            "downstream_guidance": output.downstream_guidance,
            "planner_notes": output.planner_notes,
            "node_sequence": [*state["node_sequence"], "planner"],
        }

    @staticmethod
    def _manual_planner_output(task: Task, collection_plan: PlannerCollectionPlan) -> PlannerOutput:
        dimension_items: dict[str, AnalysisDimension] = {}
        for dimensions in collection_plan.collector_search_plan.values():
            for dimension_id, item in dimensions.items():
                existing = dimension_items.get(dimension_id)
                goals = list(dict.fromkeys([
                    *(existing.research_goals if existing else []),
                    *item.research_goals,
                ]))
                dimension_items[dimension_id] = AnalysisDimension(
                    dimension_id=dimension_id,
                    label=item.label,
                    description=f"人工规划的 {item.label} 调研维度",
                    required=True,
                    priority=len(dimension_items) + 1,
                    keywords=[],
                    query_templates=[],
                    research_goals=goals,
                    source="manual",
                    metadata={"planning_source": "manual"},
                )
        selected_dimensions = list(dimension_items)
        if not selected_dimensions:
            raise ValueError("Manual planning requires at least one enabled dimension.")
        diagnostics = {
            "planner_mode_requested": "manual",
            "planner_mode_used": "manual",
            "planner_output_type": "full",
            "llm_enabled": False,
            "llm_call_attempted": False,
            "llm_call_success": False,
            "llm_prompt_tokens": 0,
            "llm_completion_tokens": 0,
            "llm_total_tokens": 0,
            "fallback_used": False,
            "manual_planning": True,
        }
        return PlannerOutput(
            planner_summary=PlannerSummary(
                intent_classification="competitive_analysis",
                product_name=task.product_name,
                industry=task.industry,
                region=task.region,
                competitors=task.competitors,
                product_type=task.industry,
                task_goal=(
                    f"围绕 {task.product_name}，按人工配置的维度分析 "
                    f"{'、'.join(task.competitors)}，并生成有证据支撑的竞品报告。"
                ),
            ),
            selected_dimensions=selected_dimensions,
            analysis_dimension_plan=AnalysisDimensionPlan(
                selected_dimensions=selected_dimensions,
                dimension_plans=list(dimension_items.values()),
                research_goals=list(dict.fromkeys(
                    goal
                    for item in dimension_items.values()
                    for goal in item.research_goals
                )),
                metadata={"planning_source": "manual"},
            ),
            collection_plan=collection_plan,
            downstream_guidance=PlannerDownstreamGuidance(),
            missing_information=[],
            planner_notes=["首轮规划由用户人工配置，未调用 Planner LLM。"],
            diagnostics=diagnostics,
        )

    def collector_node(self, state: WorkflowState) -> WorkflowState:
        self._check_cancelled(state.get("run_id"))
        task = self._current_task(state)
        set_workflow_progress(
            state.get("run_id"),
            current_agent="CollectorAgent",
            current_stage="collect",
            message="正在按采集计划搜索 Evidence...",
        )
        if state["demo_mode"] == "qa_missing_evidence" and state["rework_count"] == 0:
            return {**state, "task": task, "evidence": [], "collector_output": None, "node_sequence": [*state["node_sequence"], "collector"]}
        output = self.collector.run(
            CollectorInput(
                task=task,
                run_id=state.get("run_id"),
                retry_count=state["rework_count"],
                collector_mode=state["collector_mode"],
                collection_plan=state.get("collection_plan"),
                incremental_collection_plan=state.get("incremental_collection_plan"),
                collector_config=state.get("collector_config"),
                partial_collection_plan_allowed=state.get("manual_collection_plan_override_used", False),
                selected_dimensions=state.get("selected_dimensions", []),
                analysis_dimension_plan=state.get("analysis_dimension_plan"),
                rework_context=state.get("rework_context"),
                competitor_aliases=state.get("competitor_aliases", {}),
            )
        )
        evidence = self.evidence_service.save_many(task.task_id, output.evidence, run_id=state.get("run_id"))
        return {
            **state,
            "task": task,
            "collector_output": output,
            "evidence": evidence,
            "node_sequence": [*state["node_sequence"], "collector"],
        }

    def evidence_gate_node(self, state: WorkflowState) -> WorkflowState:
        self._check_cancelled(state.get("run_id"))
        task = self._current_task(state)
        evidence = state.get("evidence", [])
        relevant_count = {
            competitor: sum(1 for item in evidence if item.competitor == competitor and item.relevance_level in {"high", "medium"})
            for competitor in task.competitors
        }
        unrelated_count = {
            competitor: sum(1 for item in evidence if item.competitor == competitor and item.relevance_level == "unrelated")
            for competitor in task.competitors
        }
        gate_details = self._evidence_gate_details(
            task,
            evidence,
            relevant_count,
            unrelated_count,
            state.get("selected_dimensions", []),
        )
        missing = gate_details["missing_competitors"]
        passed = not missing
        suggested_route = None if passed else "CollectorAgent"
        next_rework_count = state["rework_count"]
        final_status = state.get("final_status")
        routes = list(state["conditional_routes_taken"])
        next_task = task
        if not passed:
            if state["rework_count"] >= state["max_rework"]:
                final_status = "manual_review"
                suggested_route = None
                routes.append(
                    {
                        "from_node": "evidence_gate",
                        "to_node": "final_report",
                        "reason": "max_rework_reached",
                        "details": gate_details["failure_explanation"],
                        "rework_count": state["rework_count"],
                        "final_status": "manual_review",
                    }
                )
                next_task = self.task_service.update_status(task.task_id, "manual_review", rework_count=state["rework_count"])
            elif state["auto_rework"]:
                next_rework_count = state["rework_count"] + 1
                if next_rework_count > state["max_rework"]:
                    final_status = "manual_review"
                    suggested_route = None
                    routes.append(
                        {
                            "from_node": "evidence_gate",
                            "to_node": "final_report",
                            "reason": "max_rework_reached",
                            "details": gate_details["failure_explanation"],
                            "rework_count": next_rework_count,
                            "final_status": "manual_review",
                        }
                    )
                    next_task = self.task_service.update_status(task.task_id, "manual_review", rework_count=next_rework_count)
                else:
                    routes.append(
                        {
                            "from_node": "evidence_gate",
                            "to_node": "collector",
                            "reason": "missing_relevant_evidence",
                            "details": gate_details["failure_explanation"],
                            "rework_count": next_rework_count,
                        }
                    )
                    next_task = self.task_service.update_status(task.task_id, "qa_failed", rework_count=next_rework_count)
            else:
                final_status = "insufficient_evidence"
                routes.append(
                    {
                        "from_node": "evidence_gate",
                        "to_node": "final_report",
                        "reason": "missing_relevant_evidence",
                        "details": gate_details["failure_explanation"],
                        "rework_count": state["rework_count"],
                        "final_status": "insufficient_evidence",
                    }
                )
                next_task = self.task_service.update_status(task.task_id, "qa_failed", rework_count=state["rework_count"])
            qa_result = QaResult(
                task_id=task.task_id,
                run_id=state.get("run_id"),
                status="manual_review" if final_status == "manual_review" else "failed",
                hard_errors=[gate_details["failure_explanation"]],
                rework_instructions=[
                    ReworkInstruction(
                        target_agent="CollectorAgent",
                        error_type="missing_relevant_evidence",
                        reason=gate_details["failure_explanation"],
                        suggested_action=gate_details["suggested_action"],
                        failed_schema="Evidence.relevance",
                        metadata={
                            "source_node": "EvidenceGate",
                            "missing_competitors": missing,
                            "competitor": missing[0] if missing else None,
                            "evidence_gate_details": gate_details,
                            "query_focus": gate_details["query_focus"],
                            "focus_dimensions": gate_details["missing_dimensions_by_competitor"].get(
                                missing[0], []
                            ) if missing else [],
                            "fix_type": "collect_more_evidence",
                        },
                    )
                ],
                route_to="CollectorAgent" if suggested_route == "CollectorAgent" else None,
                rework_count=next_rework_count,
                metadata={"evidence_gate_details": gate_details},
            )
            if state["auto_rework"] and suggested_route == "CollectorAgent":
                qa_result.rework_history = [
                    *list(state.get("qa_result").rework_history if state.get("qa_result") else []),
                    ReworkHistoryItem(
                        round=next_rework_count,
                        from_status=qa_result.status,
                        error_type="missing_relevant_evidence",
                        route_to="CollectorAgent",
                        action=gate_details["suggested_action"],
                        reason=gate_details["failure_explanation"],
                        failed_schema="Evidence.relevance",
                        metadata={
                            "source_node": "EvidenceGate",
                            "missing_competitors": missing,
                            "competitor": missing[0] if missing else None,
                            "evidence_gate_details": gate_details,
                            "query_focus": gate_details["query_focus"],
                            "focus_dimensions": gate_details["missing_dimensions_by_competitor"].get(
                                missing[0], []
                            ) if missing else [],
                            "fix_type": "collect_more_evidence",
                        },
                    ),
                ]
            qa_result = self.report_service.save_qa(qa_result, run_id=state.get("run_id"))
        else:
            qa_result = state.get("qa_result")

        output = {
            "evidence_gate_passed": passed,
            "missing_relevant_evidence_competitors": missing,
            "relevant_evidence_count_by_competitor": relevant_count,
            "unrelated_evidence_count_by_competitor": unrelated_count,
            "dimension_coverage_by_competitor": gate_details["dimension_coverage_by_competitor"],
            "missing_dimensions_by_competitor": gate_details["missing_dimensions_by_competitor"],
            "evidence_diagnostics_by_competitor": gate_details["by_competitor"],
            "failure_explanation": gate_details["failure_explanation"],
            "suggested_route": suggested_route,
            "suggested_action": "Proceed to AnalystAgent." if passed else gate_details["suggested_action"],
        }
        self._save_evidence_gate_trace(task.task_id, state.get("run_id"), output, state["rework_count"])
        return {
            **state,
            "task": next_task,
            "demo_mode": "normal" if (not passed and state["auto_rework"]) else state["demo_mode"],
            "evidence_gate_output": output,
            "qa_result": qa_result,
            "route_to": suggested_route,
            "rework_count": next_rework_count,
            "final_status": final_status,
            "conditional_routes_taken": routes,
            "node_sequence": [*state["node_sequence"], "evidence_gate"],
        }

    def analyst_node(self, state: WorkflowState) -> WorkflowState:
        self._check_cancelled(state.get("run_id"))
        task = self._current_task(state)
        retrieved_chunks = self.kb_retriever_service.retrieve_for_task(
            task,
            selected_dimensions=state.get("selected_dimensions", []),
            top_k=5,
        )
        evidence_with_knowledge = self._inject_knowledge_evidence(
            task,
            state.get("run_id"),
            state.get("evidence", []),
            retrieved_chunks,
        )
        output = self.analyst.run(
            AnalystInput(
                task=task,
                run_id=state.get("run_id"),
                evidence=evidence_with_knowledge,
                retry_count=state["rework_count"],
                force_invalid_extraction=state["demo_mode"] == "qa_invalid_extraction" and state["rework_count"] == 0,
                analyst_mode=state["analyst_mode"],
                selected_dimensions=state.get("selected_dimensions", []),
                rework_context=state.get("rework_context"),
                retrieved_knowledge_chunks=retrieved_chunks,
            )
        )
        return {
            **state,
            "task": task,
            "analyst_output": output,
            "dimension_results": output.dimension_results,
            "evidence": evidence_with_knowledge,
            "retrieved_knowledge_chunks": retrieved_chunks,
            "knowledge_hits": [self._knowledge_hit_payload(item, state.get("run_id")) for item in retrieved_chunks],
            "swot_analysis": output.swot,
            "node_sequence": [*state["node_sequence"], "analyst"],
        }

    def page_fetcher_node(self, state: WorkflowState) -> WorkflowState:
        self._check_cancelled(state.get("run_id"))
        task = self._current_task(state)
        fetch_enabled = state.get("content_mode") == "page" or (state.get("content_mode") is None and state.get("collector_mode") == "web")
        evidence, output = self.page_fetcher.enrich(state.get("evidence", []), run_id=state.get("run_id"), enabled=fetch_enabled)
        output["content_mode_requested"] = state.get("content_mode")
        output["page_fetch_enabled"] = fetch_enabled
        saved_evidence = self.evidence_service.save_many(task.task_id, evidence, run_id=state.get("run_id"))
        self.kb_ingestion_service.enqueue_for_evidence(saved_evidence, task=task, run_id=state.get("run_id"))
        self._save_page_fetcher_trace(task.task_id, state.get("run_id"), output, state["rework_count"])
        return {
            **state,
            "task": task,
            "evidence": saved_evidence,
            "page_fetch_output": output,
            "node_sequence": [*state["node_sequence"], "page_fetcher"],
        }

    def evidence_content_fetcher_node(self, state: WorkflowState) -> WorkflowState:
        self._check_cancelled(state.get("run_id"))
        task = self._current_task(state)
        all_evidence = state.get("evidence", [])
        target_ids = set(state.get("evidence_content_fetch_target_ids", []))
        evidence_to_fetch = (
            [item for item in all_evidence if item.evidence_id in target_ids]
            if target_ids
            else all_evidence
        )
        set_workflow_progress(
            state.get("run_id"),
            current_agent="EvidenceContentFetcher",
            current_stage="content_fetch",
            message="正在准备抓取 Evidence 正文...",
        )
        content_fetch_max_per_dimension = content_fetch_max_per_dimension_for_strategy(
            task.collection_strategy_mode
        )
        evidence, output = self.evidence_content_fetcher.enrich(
            evidence_to_fetch,
            run_id=state.get("run_id"),
            enabled=state.get("collector_mode") == "web",
            max_per_competitor_dimension=content_fetch_max_per_dimension,
            progress_callback=lambda progress: set_workflow_progress(
                state.get("run_id"),
                current_agent="EvidenceContentFetcher",
                current_stage="content_fetch",
                message=(
                    f"正在抓取第 {progress.get('current', 0)} / {progress.get('total', 0)} 条 evidence 正文"
                ),
                current=int(progress.get("current") or 0),
                total=int(progress.get("total") or 0),
                unit=str(progress.get("unit") or "evidence"),
                detail=str(progress.get("detail") or ""),
                metadata={"evidence_id": progress.get("evidence_id")},
            ),
        )
        output["content_fetch_scope"] = (
            "incremental_evidence_only"
            if target_ids
            else (
                "limited_per_competitor_dimension"
                if content_fetch_max_per_dimension
                else "all_eligible_evidence"
            )
        )
        output["collection_strategy_mode"] = task.collection_strategy_mode
        if target_ids:
            enriched_by_id = {item.evidence_id: item for item in evidence}
            merged_evidence = [
                enriched_by_id.get(item.evidence_id, item)
                for item in all_evidence
            ]
        else:
            merged_evidence = evidence
        saved_evidence = self.evidence_service.save_many(
            task.task_id,
            merged_evidence,
            run_id=state.get("run_id"),
        )
        self._save_evidence_content_fetcher_trace(task.task_id, state.get("run_id"), output, state["rework_count"])
        return {
            **state,
            "task": task,
            "evidence": saved_evidence,
            "evidence_content_fetch_target_ids": [],
            "evidence_content_fetch_output": output,
            "node_sequence": [*state["node_sequence"], "evidence_content_fetcher"],
        }

    @staticmethod
    def _qa_visual_delay(enabled: bool) -> None:
        if not enabled or os.getenv("PYTEST_CURRENT_TEST"):
            return
        try:
            delay = float(os.getenv("QA_VISUAL_DELAY_SECONDS", "5") or "0")
        except ValueError:
            delay = 0
        if delay > 0:
            time.sleep(delay)

    def evidence_analyst_node(self, state: WorkflowState) -> WorkflowState:
        self._check_cancelled(state.get("run_id"))
        task = self._current_task(state)
        set_workflow_progress(
            state.get("run_id"),
            current_agent="EvidenceAnalystAgent",
            current_stage="answer_questions",
            message="正在准备基于 Evidence 回答规划问题...",
        )
        output = self.evidence_analyst.run(
            EvidenceAnalystInput(
                task=task,
                run_id=state.get("run_id"),
                evidence=state.get("evidence", []),
                retry_count=state["rework_count"],
                selected_dimensions=state.get("selected_dimensions", []),
                collection_plan=state.get("collection_plan"),
                enabled=state.get("analyst_mode") == "llm",
                previous_output=state.get("evidence_analyst_output"),
                rework_context=state.get("evidence_analyst_rework_context"),
            ),
            progress_callback=lambda progress: set_workflow_progress(
                state.get("run_id"),
                current_agent="EvidenceAnalystAgent",
                current_stage="answer_questions",
                message=(
                    f"正在回答第 {progress.get('start', progress.get('current', 0))}"
                    f"-{progress.get('end', progress.get('current', 0))} / {progress.get('total', 0)} 个问题"
                    if progress.get("start") != progress.get("end")
                    else f"正在回答第 {progress.get('current', 0)} / {progress.get('total', 0)} 个问题"
                ),
                current=int(progress.get("current") or 0),
                total=int(progress.get("total") or 0),
                unit=str(progress.get("unit") or "question"),
                detail=str(progress.get("detail") or ""),
                metadata={
                    "competitor": progress.get("competitor"),
                    "dimension_id": progress.get("dimension_id"),
                },
            ),
        )
        return {
            **state,
            "task": task,
            "evidence_analyst_output": output,
            "evidence_analyst_rework_context": None,
            "node_sequence": [*state["node_sequence"], "evidence_analyst"],
        }

    def report_writer_node(self, state: WorkflowState) -> WorkflowState:
        self._check_cancelled(state.get("run_id"))
        task = self._current_task(state)
        output = self.writer.run(
            ReportWriterInput(
                task=task,
                run_id=state.get("run_id"),
                knowledge=state["analyst_output"],
                evidence=state.get("evidence", []),
                retry_count=state["rework_count"],
                force_bad_format=state["demo_mode"] == "qa_bad_report" and state["rework_count"] == 0,
                writer_mode=state["writer_mode"],
                selected_dimensions=state.get("selected_dimensions", []),
                writer_guidance=state.get("downstream_guidance").writer if state.get("downstream_guidance") else [],
                intent_classification=state.get("intent_classification"),
                rework_context=state.get("rework_context"),
            )
        )
        return {
            **state,
            "task": task,
            "report_writer_output": output,
            "report": output.report,
            "node_sequence": [*state["node_sequence"], "report_writer"],
        }

    def report_agent_node(self, state: WorkflowState) -> WorkflowState:
        self._check_cancelled(state.get("run_id"))
        task = self._current_task(state)
        set_workflow_progress(
            state.get("run_id"),
            current_agent="ReportAgent",
            current_stage="report_generation",
            message="正在生成结构化竞品分析报告...",
        )
        evidence_analyst_output = state.get("evidence_analyst_output")
        if evidence_analyst_output is None:
            return {
                **state,
                "task": task,
                "report_agent_output": None,
                "report": None,
                "node_sequence": [*state["node_sequence"], "report_agent"],
            }
        output = self.report_agent.run(
            ReportAgentInput(
                task=task,
                run_id=state.get("run_id"),
                evidence_analyst_output=evidence_analyst_output,
                evidence=state.get("evidence", []),
                selected_dimensions=state.get("selected_dimensions", []),
                collection_plan=state.get("collection_plan"),
                retry_count=state.get("rework_count", 0),
            )
        )
        saved_report = self.report_service.save_report(output.report, run_id=state.get("run_id"))
        output.report = saved_report
        output.markdown_report = saved_report.markdown
        return {
            **state,
            "task": task,
            "report_agent_output": output,
            "report": saved_report,
            "node_sequence": [*state["node_sequence"], "report_agent"],
        }

    def qa_node(self, state: WorkflowState) -> WorkflowState:
        self._check_cancelled(state.get("run_id"))
        task = self._current_task(state)
        set_workflow_progress(
            state.get("run_id"),
            current_agent="QaAgent",
            current_stage="qa",
            message="正在执行质量检查...",
        )
        output = self.qa.run(
            QaInput(
                task=task,
                run_id=state.get("run_id"),
                evidence=state.get("evidence", []),
                analysis=state.get("analyst_output"),
                report_output=state.get("report_writer_output"),
                selected_dimensions=state.get("selected_dimensions", []),
                analysis_dimension_plan=state.get("analysis_dimension_plan"),
                collector_config=state.get("collector_config"),
                retry_count=state["rework_count"],
                demo_mode=state["demo_mode"],
            )
        )
        qa_result = output.qa_result
        previous_history = list(state.get("qa_result").rework_history) if state.get("qa_result") else []
        if qa_result.status == "failed" and state["auto_rework"] and qa_result.route_to and qa_result.rework_instructions:
            instruction = qa_result.rework_instructions[0]
            qa_result.rework_history = [
                *previous_history,
                ReworkHistoryItem(
                    round=qa_result.rework_count,
                    from_status=qa_result.status,
                    error_type=instruction.error_type,
                    route_to=qa_result.route_to,
                    action=instruction.suggested_action,
                    reason=instruction.reason,
                    failed_schema=instruction.failed_schema,
                    claim_id=instruction.claim_id,
                    failed_claim=instruction.failed_claim,
                    metadata=instruction.metadata or {},
                ),
            ]
        elif previous_history:
            qa_result.rework_history = previous_history
        qa_result = self.report_service.save_qa(qa_result, run_id=state.get("run_id"))
        next_state: WorkflowState = {
            **state,
            "task": task,
            "qa_output": output,
            "qa_result": qa_result,
            "rework_context": self._rework_context_from_qa(qa_result),
            "route_to": qa_result.route_to,
            "rework_count": qa_result.rework_count,
            "node_sequence": [*state["node_sequence"], "qa"],
        }
        if qa_result.status == "failed" and state["auto_rework"] and qa_result.route_to:
            instruction = qa_result.rework_instructions[0] if qa_result.rework_instructions else None
            next_node = self._agent_to_node(qa_result.route_to)
            is_unknown_route = next_node == "final_report"
            next_state["conditional_routes_taken"] = [
                *state["conditional_routes_taken"],
                {
                    "from_node": "qa",
                    "to_node": next_node,
                    "reason": "unknown_route" if is_unknown_route else (instruction.error_type if instruction else "qa_failed"),
                    "rework_count": qa_result.rework_count,
                    **({"final_status": "manual_review"} if is_unknown_route else {}),
                },
            ]
            if is_unknown_route:
                next_state["final_status"] = "manual_review"
                next_state["task"] = self.task_service.update_status(task.task_id, "manual_review", rework_count=qa_result.rework_count)
            else:
                next_state["demo_mode"] = "normal"
                next_state["task"] = self.task_service.update_status(task.task_id, "qa_failed", rework_count=qa_result.rework_count)
        return next_state

    def final_report_node(self, state: WorkflowState) -> WorkflowState:
        self._check_cancelled(state.get("run_id"))
        task = self._current_task(state)
        writer_output = state.get("report_writer_output")
        qa_result = state.get("qa_result")
        if qa_result and qa_result.status == "passed" and writer_output and writer_output.report:
            output = self.final_report.run(
                FinalReportInput(
                    task=task,
                    run_id=state.get("run_id"),
                    report=writer_output.report,
                    qa_result=qa_result,
                    evidence=state.get("evidence", []),
                    retry_count=qa_result.rework_count,
                )
            )
            saved_report = self.report_service.save_report(output.report, run_id=state.get("run_id"))
            self.task_service.update_status(task.task_id, "completed", rework_count=qa_result.rework_count)
            return {
                **state,
                "task": task,
                "final_report_output": output,
                "qa_result": qa_result,
                "report": saved_report,
                "final_status": "completed",
                "node_sequence": [*state["node_sequence"], "final_report"],
            }
        final_status = state.get("final_status") or (self._status_for_qa(qa_result) if qa_result else "failed")
        task_status = final_status if final_status in {"failed", "qa_failed", "manual_review", "completed"} else "qa_failed"
        self.task_service.update_status(task.task_id, task_status, rework_count=qa_result.rework_count if qa_result else state["rework_count"])
        return {**state, "task": task, "report": None, "final_status": final_status, "node_sequence": [*state["node_sequence"], "final_report"]}

    def route_after_evidence_gate(self, state: WorkflowState) -> str:
        gate = state.get("evidence_gate_output", {})
        if gate.get("evidence_gate_passed"):
            return "page_fetcher"
        if state.get("final_status") in {"manual_review", "insufficient_evidence"}:
            return "final_report"
        if state.get("auto_rework") and state.get("rework_count", 0) < state.get("max_rework", MAX_REWORK):
            return "collector"
        return "final_report"

    def route_after_qa(self, state: WorkflowState) -> str:
        qa_result = state.get("qa_result")
        if qa_result is None:
            return "final_report"
        if qa_result.status == "passed":
            return "final_report"
        if qa_result.status == "manual_review" or qa_result.rework_count > state["max_rework"]:
            state["final_status"] = "manual_review"
            return "final_report"
        if not state["auto_rework"]:
            return "final_report"
        if qa_result.route_to == "PlannerAgent":
            return "planner"
        if qa_result.route_to == "CollectorAgent":
            return "collector"
        if qa_result.route_to == "AnalystAgent":
            return "analyst"
        if qa_result.route_to == "ReportWriterAgent":
            return "report_writer"
        state["final_status"] = "manual_review"
        return "final_report"

    @staticmethod
    def _evidence_gate_details(
        task: Task,
        evidence: list,
        relevant_count: dict[str, int],
        unrelated_count: dict[str, int],
        selected_dimensions: list[str] | None = None,
    ) -> dict:
        by_competitor: dict[str, dict] = {}
        missing: list[str] = []
        query_focus: list[str] = []
        selected_dimensions = list(dict.fromkeys(selected_dimensions or []))
        minimum_coverage_ratio = 0.4
        missing_dimensions_by_competitor: dict[str, list[str]] = {}
        dimension_coverage_by_competitor: dict[str, dict] = {}

        for competitor in task.competitors:
            records = [item for item in evidence if item.competitor == competitor]
            high = [item for item in records if item.relevance_level == "high"]
            medium = [item for item in records if item.relevance_level == "medium"]
            low = [item for item in records if item.relevance_level == "low"]
            unrelated = [item for item in records if item.relevance_level == "unrelated"]
            relevant = [*high, *medium]
            top_records = sorted(
                records,
                key=lambda item: (
                    {"high": 3, "medium": 2, "low": 1, "unrelated": 0}.get(item.relevance_level, 0),
                    item.relevance_score,
                    item.confidence,
                ),
                reverse=True,
            )[:3]
            alias_miss_count = sum(
                1
                for item in records
                if not (item.entity_match_signals or {}).get("competitor_alias_matched")
            )
            dimensions_seen = sorted(
                {
                    str((item.entity_match_signals or {}).get("collector_dimension"))
                    for item in records
                    if (item.entity_match_signals or {}).get("collector_dimension")
                }
            )
            relevant_dimensions = sorted(
                {
                    str((item.entity_match_signals or {}).get("collector_dimension"))
                    for item in relevant
                    if (item.entity_match_signals or {}).get("collector_dimension")
                }
            )
            has_dimension_tags = any(
                (item.entity_match_signals or {}).get("collector_dimension") for item in records
            )
            missing_dimensions = [
                dimension_id for dimension_id in selected_dimensions if dimension_id not in relevant_dimensions
            ] if has_dimension_tags else []
            covered_dimension_count = len(selected_dimensions) - len(missing_dimensions)
            coverage_ratio = (
                covered_dimension_count / len(selected_dimensions) if selected_dimensions else (1.0 if relevant else 0.0)
            )
            coverage_passed = bool(relevant) and (
                not has_dimension_tags or coverage_ratio >= minimum_coverage_ratio
            )
            missing_dimensions_by_competitor[competitor] = missing_dimensions
            dimension_coverage_by_competitor[competitor] = {
                "selected_dimension_count": len(selected_dimensions),
                "covered_dimension_count": covered_dimension_count,
                "coverage_ratio": round(coverage_ratio, 3),
                "minimum_coverage_ratio": minimum_coverage_ratio,
                "covered_dimensions": relevant_dimensions,
                "missing_dimensions": missing_dimensions,
                "dimension_tags_available": has_dimension_tags,
                "passed": coverage_passed,
            }
            if not records:
                reason = "no_evidence_collected"
                explanation = f"{competitor} 没有采集到任何公开 Evidence。"
            elif not relevant and alias_miss_count == len(records):
                reason = "alias_or_entity_not_matched"
                explanation = (
                    f"{competitor} 采集到 {len(records)} 条 Evidence，但都没有命中竞品名或别名，"
                    "因此相关性只达到 low/unrelated。"
                )
            elif not relevant:
                reason = "only_low_or_unrelated_evidence"
                explanation = (
                    f"{competitor} 采集到 {len(records)} 条 Evidence，但 high/medium 相关证据为 0；"
                    f"low={len(low)}，unrelated={len(unrelated)}。"
                )
            elif not coverage_passed:
                reason = "insufficient_dimension_coverage"
                explanation = (
                    f"{competitor} 有 {len(relevant)} 条 high/medium Evidence，但仅覆盖 "
                    f"{covered_dimension_count}/{len(selected_dimensions)} 个分析维度，"
                    f"低于 {int(minimum_coverage_ratio * 100)}% 的最低覆盖要求。"
                )
            else:
                reason = "passed"
                explanation = (
                    f"{competitor} 已有 {len(relevant)} 条 high/medium Evidence，"
                    f"覆盖 {covered_dimension_count}/{len(selected_dimensions)} 个分析维度。"
                )

            if not coverage_passed:
                missing.append(competitor)
                query_focus.extend(missing_dimensions[:6] or ["official", "官网", "产品", "评测"])

            by_competitor[competitor] = {
                "competitor": competitor,
                "status": "passed" if coverage_passed else "failed",
                "reason_code": reason,
                "explanation": explanation,
                "total_evidence_count": len(records),
                "relevant_evidence_count": relevant_count.get(competitor, len(relevant)),
                "high_count": len(high),
                "medium_count": len(medium),
                "low_count": len(low),
                "unrelated_count": unrelated_count.get(competitor, len(unrelated)),
                "alias_miss_count": alias_miss_count,
                "collector_dimensions_seen": dimensions_seen,
                "relevant_dimensions_seen": relevant_dimensions,
                "missing_dimensions": missing_dimensions,
                "dimension_coverage_ratio": round(coverage_ratio, 3),
                "top_evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "source_domain": item.source_domain,
                        "source_quality": item.source_quality,
                        "relevance_level": item.relevance_level,
                        "relevance_score": item.relevance_score,
                        "confidence": item.confidence,
                        "relevance_reason": item.relevance_reason,
                        "collector_dimension": (item.entity_match_signals or {}).get("collector_dimension"),
                        "url": item.url,
                    }
                    for item in top_records
                ],
            }

        if missing:
            failure_explanation = (
                "EvidenceGate 未通过："
                + "；".join(by_competitor[competitor]["explanation"] for competitor in missing)
            )
            suggested_action = (
                "重新运行 CollectorAgent，按照缺失维度定向补充官方页、参数页、可靠评测或市场资料，"
                "直到达到最低维度覆盖率。"
            )
        else:
            failure_explanation = "EvidenceGate 通过：所有竞品均达到 high/medium Evidence 的最低维度覆盖率。"
            suggested_action = "Proceed to AnalystAgent."

        return {
            "missing_competitors": missing,
            "failure_explanation": failure_explanation,
            "suggested_action": suggested_action,
            "query_focus": list(dict.fromkeys(query_focus)),
            "by_competitor": by_competitor,
            "missing_dimensions_by_competitor": missing_dimensions_by_competitor,
            "dimension_coverage_by_competitor": dimension_coverage_by_competitor,
        }

    def _current_task(self, state: WorkflowState) -> Task:
        return self.task_service.get_task(state["task_id"])

    def _planner_output_for_run(self, run_id: str) -> PlannerOutput:
        attempts = self.planner_attempt_service.list_for_run(run_id)
        for attempt in reversed(attempts):
            if attempt.planner_output:
                try:
                    return PlannerOutput.model_validate(attempt.planner_output)
                except ValueError:
                    continue
        raise ValueError(f"No persisted PlannerOutput found for run {run_id}.")

    @staticmethod
    def _agent_to_node(agent_name: str) -> str:
        return {
            "PlannerAgent": "planner",
            "CollectorAgent": "collector",
            "AnalystAgent": "analyst",
            "ReportWriterAgent": "report_writer",
        }.get(agent_name, "final_report")

    @staticmethod
    def _status_for_qa(qa_result) -> str:
        if qa_result is None:
            return "failed"
        if qa_result.status == "manual_review":
            return "manual_review"
        if qa_result.status == "failed":
            return "qa_failed"
        return "completed"

    @staticmethod
    def _workflow_summary(state: WorkflowState, elapsed_time_ms: int) -> dict:
        return {
            "run_id": state.get("run_id"),
            "task_id": state.get("task_id"),
            "workflow_engine_requested": state.get("workflow_engine_requested"),
            "workflow_engine_used": "langgraph",
            "planner_summary": state.get("planner_summary", {}),
            "intent_summary": state.get("intent_summary"),
            "intent_classification": state.get("intent_classification"),
            "ambiguity_level": state.get("ambiguity_level"),
            "scope_type": state.get("scope_type"),
            "scope_size": state.get("scope_size"),
            "survey_needed": state.get("survey_needed"),
            "survey_recommended": state.get("survey_recommended"),
            "selected_dimensions": state.get("selected_dimensions", []),
            "manual_collection_plan_override_used": state.get("manual_collection_plan_override_used", False),
            "collection_strategy_mode": state.get("task").collection_strategy_mode if state.get("task") else None,
            "collector_config_source": state.get("collector_config_source"),
            "manual_evidence_selection_used": state.get("manual_evidence_selection_used", False),
            "manual_selected_evidence_ids": state.get("manual_selected_evidence_ids", []),
            "manual_selected_evidence_count": len(state.get("manual_selected_evidence_ids", [])),
            "collector_config": (
                state.get("collector_config").model_dump(mode="json")
                if state.get("collector_config")
                else None
            ),
            "analysis_dimension_plan": state.get("analysis_dimension_plan").model_dump(mode="json")
            if state.get("analysis_dimension_plan")
            else None,
            "collection_plan": (
                state.get("collection_plan").model_dump(mode="json")
                if state.get("collection_plan")
                else None
            ),
            "planner_collection_plan_original": (
                state.get("planner_output").collection_plan.model_dump(mode="json")
                if state.get("planner_output")
                else None
            ),
            "diagnostics": (
                state.get("planner_output").diagnostics if state.get("planner_output") else {}
            ),
            "planner_notes": (
                state.get("planner_output").planner_notes if state.get("planner_output") else []
            ),
            "planner_output": (
                state.get("planner_output").model_dump(mode="json")
                if state.get("planner_output")
                else None
            ),
            "entity_resolution": state.get("entity_resolution", {}),
            "competitor_aliases": state.get("competitor_aliases", {}),
            "downstream_guidance": state.get("downstream_guidance").model_dump(mode="json")
            if state.get("downstream_guidance")
            else None,
            "swot_analysis": state.get("swot_analysis").model_dump(mode="json") if state.get("swot_analysis") else None,
            "rework_context": state.get("rework_context").model_dump(mode="json") if state.get("rework_context") else None,
            "swot_validation": state.get("qa_result").metadata.get("swot_validation")
            if state.get("qa_result") and state.get("qa_result").metadata
            else None,
            "confirmed_scope": state.get("confirmed_scope").model_dump(mode="json") if state.get("confirmed_scope") else None,
            "inferred_scope": state.get("inferred_scope").model_dump(mode="json") if state.get("inferred_scope") else None,
            "suggested_scope": state.get("suggested_scope").model_dump(mode="json") if state.get("suggested_scope") else None,
            "recommended_next_constraints": state.get("recommended_next_constraints", []),
            "clarification_targets": state.get("clarification_targets", []),
            "candidate_competitors": [item.model_dump(mode="json") for item in state.get("candidate_competitors", [])],
            "planning_stages": [item.model_dump(mode="json") for item in state.get("planning_stages", [])],
            "node_sequence": state.get("node_sequence", []),
            "conditional_routes_taken": state.get("conditional_routes_taken", []),
            "evidence_gate_output": state.get("evidence_gate_output", {}),
            "evidence_content_fetch_output": state.get("evidence_content_fetch_output", {}),
            "evidence_analyst_output": (
                state.get("evidence_analyst_output").model_dump(mode="json")
                if state.get("evidence_analyst_output")
                else None
            ),
            "report_agent_output": (
                state.get("report_agent_output").model_dump(mode="json")
                if state.get("report_agent_output")
                else None
            ),
            "page_fetch_output": state.get("page_fetch_output", {}),
            "knowledge_hits": state.get("knowledge_hits", []),
            "retrieved_knowledge_chunk_count": len(state.get("retrieved_knowledge_chunks", [])),
            "knowledge_retrieval_strategy": {
                "retriever": "KbRetrieverService",
                "vector_store": "sqlite_json_embedding",
                "embedding_provider": "local_hash_embedding",
                "similarity": "cosine_similarity",
                "top_k": 5,
                "current_run_evidence_priority": True,
            },
            "run_isolation_strategy": state.get("run_isolation_strategy", "run_id"),
            "run_cleanup_summary": state.get("run_cleanup_summary", {}),
            "rework_count": state.get("qa_result").rework_count if state.get("qa_result") else state.get("rework_count", 0),
            "final_status": state.get("final_status") or (state.get("qa_result").status if state.get("qa_result") else "failed"),
            "elapsed_time_ms": elapsed_time_ms,
            "error_message": "; ".join(state.get("errors", [])) if state.get("errors") else None,
        }

    @staticmethod
    def _run_status_from_summary(summary: dict) -> str:
        final_status = summary.get("final_status")
        if final_status == "completed":
            return "completed"
        if final_status == "manual_review":
            return "manual_review"
        if final_status == "insufficient_evidence":
            return "insufficient_evidence"
        if final_status == "qa_failed":
            return "qa_failed"
        return "failed" if final_status in {"failed", None} else str(final_status)

    @staticmethod
    def _rework_context_from_qa(qa_result: QaResult | None) -> ReworkContext | None:
        if qa_result is None or not qa_result.rework_instructions:
            return None
        instruction = qa_result.rework_instructions[0]
        metadata = instruction.metadata or {}
        route_to = qa_result.route_to if qa_result.route_to in {
            "PlannerAgent",
            "CollectorAgent",
            "PageFetcher",
            "Chunker",
            "Indexer",
            "Retriever",
            "AnalystAgent",
            "ReportWriterAgent",
            "QaAgent",
            "SurveyAgent",
            "QuestionnaireAgent",
            "EvidenceGate",
            "HumanReviewAgent",
            "FinalReport",
            "FinalReportAgent",
            "WorkflowEngine",
        } else None
        return ReworkContext(
            route_to=route_to,
            error_type=instruction.error_type,
            reason=instruction.reason,
            target_agent=instruction.target_agent,
            related_competitor=metadata.get("competitor"),
            related_claim_id=instruction.claim_id,
            related_evidence_id=metadata.get("evidence_id"),
            suggested_action=instruction.suggested_action,
            metadata=metadata,
        )

    @staticmethod
    def _targeted_recollection_summary(rework_context: ReworkContext | None) -> dict:
        if rework_context is None:
            return {"by_competitor": {}}
        competitor = rework_context.related_competitor
        if not competitor:
            return {"by_competitor": {}}
        return {
            "by_competitor": {
                competitor: [
                    {
                        "error_type": rework_context.error_type,
                        "reason": rework_context.reason,
                        "metadata": rework_context.metadata,
                    }
                ]
            }
        }

    def _save_workflow_trace(self, task_id: str, run_id: str, summary: dict, elapsed_time_ms: int) -> None:
        self.trace_service.save(
            TraceRecord(
                trace_id=f"trace_{uuid4().hex[:10]}",
                task_id=task_id,
                run_id=run_id,
                agent_name="WorkflowEngine",
                input_summary=f"workflow_engine_requested={summary['workflow_engine_requested']}",
                output_summary=json.dumps(summary, ensure_ascii=False),
                schema_validation_result="passed",
                model_name="langgraph-stategraph",
                elapsed_time_ms=elapsed_time_ms,
            )
        )

    def _save_evidence_gate_trace(self, task_id: str, run_id: str | None, output: dict, retry_count: int) -> None:
        self.trace_service.save(
            TraceRecord(
                trace_id=f"trace_{uuid4().hex[:10]}",
                task_id=task_id,
                run_id=run_id,
                agent_name="EvidenceGate",
                input_summary="Validate high/medium relevance Evidence before AnalystAgent",
                output_summary=json.dumps(output, ensure_ascii=False),
                schema_validation_result="passed" if output["evidence_gate_passed"] else "failed",
                model_name="langgraph-evidence-gate",
                elapsed_time_ms=0,
                retry_count=retry_count,
                error_message=None if output["evidence_gate_passed"] else "missing_relevant_evidence",
            )
        )

    def _save_page_fetcher_trace(self, task_id: str, run_id: str | None, output: dict, retry_count: int) -> None:
        self.trace_service.save(
            TraceRecord(
                trace_id=f"trace_{uuid4().hex[:10]}",
                task_id=task_id,
                run_id=run_id,
                agent_name="PageFetcher",
                input_summary="Fetch lightweight page excerpts for high/medium relevance Evidence",
                output_summary=json.dumps(output, ensure_ascii=False),
                schema_validation_result="passed",
                model_name="langgraph-page-fetcher",
                elapsed_time_ms=0,
                retry_count=retry_count,
                error_message=None,
            )
        )

    def _save_evidence_content_fetcher_trace(self, task_id: str, run_id: str | None, output: dict, retry_count: int) -> None:
        self.trace_service.save(
            TraceRecord(
                trace_id=f"trace_{uuid4().hex[:10]}",
                task_id=task_id,
                run_id=run_id,
                agent_name="EvidenceContentFetcher",
                input_summary="Extract richer page content for Collector Evidence using Tavily Extract",
                output_summary=json.dumps(output, ensure_ascii=False),
                schema_validation_result="passed",
                model_name="tavily-extract" if output.get("content_fetch_provider") == "tavily" else "content-fetcher",
                elapsed_time_ms=int(output.get("content_fetch_elapsed_time_ms") or 0),
                retry_count=retry_count,
                error_message=None,
            )
        )

    @staticmethod
    def _knowledge_hit_payload(item, run_id: str | None = None) -> dict:
        return {
            "chunk_id": item.chunk_id,
            "text_preview": item.text_preview or item.text[:240],
            "source_url": item.source_url,
            "source_domain": item.source_domain,
            "source_quality": item.source_quality,
            "score": item.score,
            "evidence_id": LangGraphWorkflowRunner._knowledge_evidence_id(run_id, item.chunk_id),
            "original_evidence_id": item.evidence_id,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }

    def _inject_knowledge_evidence(
        self,
        task: Task,
        run_id: str | None,
        current_evidence: list[Evidence],
        retrieved_chunks: list,
    ) -> list[Evidence]:
        if not retrieved_chunks:
            return current_evidence

        existing_ids = {item.evidence_id for item in current_evidence}
        injected: list[Evidence] = []
        for chunk in retrieved_chunks:
            evidence_id = self._knowledge_evidence_id(run_id, chunk.chunk_id)
            if evidence_id in existing_ids:
                continue
            metadata = chunk.metadata or {}
            competitor = metadata.get("competitor") or chunk.competitor
            if competitor and competitor not in task.competitors:
                continue
            confidence = self._knowledge_evidence_confidence(chunk)
            relevance_level = self._knowledge_relevance_level(chunk.score)
            injected.append(
                Evidence(
                    evidence_id=evidence_id,
                    run_id=run_id,
                    competitor=competitor,
                    source_type="knowledge_base",
                    url=chunk.source_url,
                    local_ref=chunk.chunk_id if not chunk.source_url else None,
                    snippet=chunk.text_preview or chunk.text[:500],
                    confidence=confidence,
                    source_domain=chunk.source_domain,
                    source_quality=chunk.source_quality or "unknown",
                    relevance_score=chunk.score,
                    relevance_level=relevance_level,
                    relevance_reason=(
                        f"Long-term knowledge base chunk retrieved by cosine similarity score={chunk.score}; "
                        "linked to original public Evidence through metadata."
                    ),
                    entity_match_signals={
                        "source": "knowledge_base_retrieval",
                        "kb_chunk_id": chunk.chunk_id,
                        "original_evidence_id": chunk.evidence_id,
                        "retrieval_score": chunk.score,
                        "retrieval_strategy": "cosine_similarity_top_k",
                        "knowledge_metadata": metadata,
                    },
                    content_mode="snippet",
                    page_fetch_success=False,
                    content_excerpt=chunk.text,
                    content_chars=len(chunk.text),
                )
            )
            existing_ids.add(evidence_id)

        if not injected:
            return current_evidence
        saved = self.evidence_service.save_many(task.task_id, injected, run_id=run_id)
        return [*current_evidence, *saved]

    @staticmethod
    def _knowledge_evidence_id(run_id: str | None, chunk_id: str) -> str:
        import hashlib

        raw = f"{run_id or 'run'}:{chunk_id}".encode("utf-8")
        return f"ev_kb_{hashlib.sha1(raw).hexdigest()[:10]}"

    @staticmethod
    def _knowledge_relevance_level(score: float) -> str:
        if score >= 0.75:
            return "high"
        if score >= 0.55:
            return "medium"
        if score >= 0.35:
            return "low"
        return "unrelated"

    @staticmethod
    def _knowledge_evidence_confidence(chunk) -> float:
        source_confidence = chunk.metadata.get("confidence") if isinstance(chunk.metadata, dict) else None
        try:
            source_confidence_value = float(source_confidence)
        except (TypeError, ValueError):
            source_confidence_value = 0.7
        return round(max(0.0, min(1.0, (source_confidence_value * 0.6) + (chunk.score * 0.4))), 2)
