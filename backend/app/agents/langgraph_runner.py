import json
import time
from uuid import uuid4

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.agents.analyst import AnalystAgent
from app.agents.base import AgentExecutionError
from app.agents.collector import CollectorAgent
from app.agents.final_report import FinalReportAgent
from app.agents.planner import PlannerAgent
from app.agents.qa import MAX_REWORK, QaAgent
from app.agents.report_writer import ReportWriterAgent
from app.schemas import (
    AnalystInput,
    CollectorInput,
    CollectorOutput,
    CollectorConfig,
    DemoMode,
    EvidenceCoverageGap,
    Evidence,
    FinalReportInput,
    PlannerInput,
    PlannerIncrementalInput,
    PlannerCollectionPlan,
    QaInput,
    QaResult,
    ReworkContext,
    ReportWriterInput,
    ReworkInstruction,
    ReworkHistoryItem,
    Task,
    TraceRecord,
)
from app.schemas.workflow_state import WorkflowState
from app.services.evidence_service import EvidenceService
from app.services.entity_resolver_service import EntityResolverService
from app.services.knowledge_base_service import KbIngestionService, KbRetrieverService
from app.services.page_fetcher import PageFetcher
from app.services.planner_attempt_service import PlannerAttemptService
from app.services.report_service import ReportService
from app.services.task_run_service import TaskRunService
from app.services.task_service import TaskService
from app.services.trace_service import TraceService


class LangGraphWorkflowRunner:
    def __init__(self, db: Session):
        self.db = db
        self.task_service = TaskService(db)
        self.trace_service = TraceService(db)
        self.evidence_service = EvidenceService(db)
        self.report_service = ReportService(db)
        self.task_run_service = TaskRunService(db)
        self.planner_attempt_service = PlannerAttemptService(db)
        self.kb_ingestion_service = KbIngestionService(db)
        self.kb_retriever_service = KbRetrieverService(db)
        self.entity_resolver_service = EntityResolverService()
        self.planner = PlannerAgent(self.trace_service)
        self.collector = CollectorAgent(self.trace_service)
        self.analyst = AnalystAgent(self.trace_service)
        self.writer = ReportWriterAgent(self.trace_service)
        self.qa = QaAgent(self.trace_service)
        self.final_report = FinalReportAgent(self.trace_service)
        self.page_fetcher = PageFetcher()
        self.graph = self._build_graph()

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
    ) -> dict:
        started = time.perf_counter()
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
            "manual_collection_plan_override_used": False,
            "collector_config": collector_config,
            "planner_incremental_output": None,
            "incremental_collection_plan": None,
            "collector_output": None,
            "analyst_output": None,
            "report_writer_output": None,
            "qa_output": None,
            "final_report_output": None,
            "evidence_gate_output": {},
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
            "selected_dimensions": collector_state.get("selected_dimensions", planner_output.selected_dimensions),
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
            "selected_dimensions": collector_state.get("selected_dimensions", planner_output.selected_dimensions),
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

        planner_output = collector_state["planner_output"]
        collector_output = collector_state["collector_output"]
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
            coverage_gap_payload = qa_result.metadata.get("coverage_gap") if qa_result.metadata else None
            if not (qa_result.status == "failed" and isinstance(coverage_gap_payload, dict) and coverage_gap_payload.get("targets")):
                break
            attempt_no += 1
            coverage_gap = EvidenceCoverageGap.model_validate(coverage_gap_payload)
            attempts = self.planner_attempt_service.list_for_run(task_run.run_id)
            base_attempt_no = attempts[-1].attempt_no if attempts else None
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
                rework_context=None,
                raw_llm_response=incremental_output._raw_llm_response,
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
                {"id": "PageFetcher", "label": "已冻结，不抓取正文", "status": "skipped"},
                {"id": "AnalystAgent", "label": "已冻结，不抽取结构化事实", "status": "skipped"},
                {"id": "ReportWriterAgent", "label": "已冻结，不生成报告", "status": "skipped"},
                {"id": "QaAgent", "label": "EvidenceQA 证据质量检查", "status": "completed"},
                {"id": "FinalReport", "label": "已冻结，不生成最终报告", "status": "skipped"},
            ],
            "edges": [
                {"source": "PlannerAgent", "target": "CollectorAgent", "label": "collection_plan"},
                {"source": "CollectorAgent", "target": "QaAgent", "label": "EvidenceQA"},
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
            "incremental_collection_plan": (
                incremental_output.incremental_collection_plan.model_dump(mode="json")
                if incremental_output
                else None
            ),
            "planner_incremental_output": incremental_output.model_dump(mode="json") if incremental_output else None,
            "incremental_attempts": incremental_attempts,
            "dag": frozen_dag,
            "node_sequence": (
                ["planner", "collector", "qa"]
                + [
                    node
                    for _attempt in incremental_attempts
                    for node in ["planner_incremental", "collector_incremental", "qa_incremental"]
                ]
            ),
            "conditional_routes_taken": [],
            "rework_count": attempt_no,
            "final_status": f"evidence_qa_{qa_result.status}",
            "elapsed_time_ms": elapsed,
            "run_isolation_strategy": "run_id",
        }
        self._save_workflow_trace(initial_state["task_id"], task_run.run_id, summary, elapsed)
        task_status = "qa_failed" if qa_result.status == "failed" else "completed"
        run_status = "qa_failed" if qa_result.status == "failed" else "completed"
        self.task_service.update_status(initial_state["task_id"], task_status, rework_count=attempt_no)
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
            "report": None,
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
        task = state["task"]
        run_id = state.get("run_id")
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

    def collector_node(self, state: WorkflowState) -> WorkflowState:
        task = self._current_task(state)
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

    def report_writer_node(self, state: WorkflowState) -> WorkflowState:
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

    def qa_node(self, state: WorkflowState) -> WorkflowState:
        task = self._current_task(state)
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
