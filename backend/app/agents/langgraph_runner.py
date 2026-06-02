import json
import time
from uuid import uuid4

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.agents.analyst import AnalystAgent
from app.agents.collector import CollectorAgent
from app.agents.final_report import FinalReportAgent
from app.agents.planner import PlannerAgent
from app.agents.qa import MAX_REWORK, QaAgent
from app.agents.report_writer import ReportWriterAgent
from app.schemas import (
    AnalystInput,
    CollectorInput,
    DemoMode,
    FinalReportInput,
    PlannerInput,
    PlannerCoreSummary,
    QaInput,
    QaResult,
    ReworkContext,
    ReportWriterInput,
    ReworkInstruction,
    ReworkHistoryItem,
    SurveyExtensionSummary,
    Task,
    TraceRecord,
    WorkflowSummaryCorePayload,
    WorkflowSummaryDiagnosticsPayload,
    WorkflowSummaryExtensionPayload,
)
from app.schemas.workflow_state import WorkflowState
from app.services.evidence_service import EvidenceService
from app.services.page_fetcher import PageFetcher
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
            "collector_output": None,
            "analyst_output": None,
            "report_writer_output": None,
            "qa_output": None,
            "final_report_output": None,
            "evidence_gate_output": {},
            "page_fetch_output": {},
            "evidence": [],
            "intent_summary": None,
            "intent_classification": None,
            "ambiguity_level": None,
            "scope_type": None,
            "scope_size": None,
            "extracted_context": None,
            "domain_pack": None,
            "selected_dimensions": [],
            "analysis_dimension_plan": None,
            "planner_core_summary": None,
            "downstream_guidance": None,
            "survey_needed": False,
            "survey_recommended": False,
            "survey_objective": None,
            "survey_inputs": None,
            "survey_extension": None,
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
            "capability_map": None,
            "survey_evidence": [],
            "chunks": [],
            "retrieval_results": [],
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
            "workflow_summary_core": None,
            "workflow_summary_extensions": None,
            "workflow_summary_diagnostics": None,
            "workflow_summary": {},
            "run_isolation_strategy": "run_id",
            "run_cleanup_summary": {},
        }
        try:
            final_state = self.graph.invoke(initial_state)
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
                "collector": "collector",
                "analyst": "analyst",
                "report_writer": "report_writer",
                "final_report": "final_report",
                "end": END,
            },
        )
        graph.add_edge("final_report", END)
        return graph.compile()

    def planner_node(self, state: WorkflowState) -> WorkflowState:
        task = state["task"]
        output = self.planner.run(PlannerInput(task=task, run_id=state.get("run_id"), retry_count=state["rework_count"]))
        return {
            **state,
            "planner_output": output,
            "intent_summary": output.intent_summary,
            "intent_classification": output.intent_classification,
            "ambiguity_level": output.ambiguity_level,
            "scope_type": output.scope_type,
            "scope_size": output.scope_size,
            "extracted_context": output.extracted_context,
            "selected_dimensions": output.selected_dimensions,
            "analysis_dimension_plan": output.analysis_dimension_plan,
            "planner_core_summary": PlannerCoreSummary(
                intent_classification=output.intent_classification,
                selected_dimensions=output.selected_dimensions,
                writer_guidance=output.downstream_guidance.writer if output.downstream_guidance else [],
                domain_pack=output.domain_pack,
            ),
            "domain_pack": output.domain_pack,
            "downstream_guidance": output.downstream_guidance,
            "survey_needed": output.survey_needed,
            "survey_recommended": output.survey_recommended,
            "survey_objective": output.survey_objective,
            "survey_inputs": output.survey_inputs,
            "survey_extension": SurveyExtensionSummary(
                survey_needed=output.survey_needed,
                survey_recommended=output.survey_recommended,
                survey_objective=output.survey_objective,
                survey_inputs=output.survey_inputs.model_dump(mode="json") if output.survey_inputs else None,
                survey_guidance=output.downstream_guidance.survey if output.downstream_guidance else [],
            ),
            "questionnaire_follow_up": output.questionnaire_follow_up,
            "confirmed_scope": output.confirmed_scope,
            "inferred_scope": output.inferred_scope,
            "suggested_scope": output.suggested_scope,
            "recommended_next_constraints": output.recommended_next_constraints,
            "assumptions": output.assumptions,
            "candidate_competitors": output.candidate_competitors,
            "clarification_targets": output.clarification_targets,
            "planning_stages": output.planning_stages,
            "planner_notes": output.planner_notes,
            "planner_confidence": output.confidence,
            "node_sequence": [*state["node_sequence"], "planner"],
        }

    def collector_node(self, state: WorkflowState) -> WorkflowState:
        task = self._current_task(state)
        if state["demo_mode"] == "qa_missing_evidence" and state["rework_count"] == 0:
            return {**state, "task": task, "evidence": [], "collector_output": None, "node_sequence": [*state["node_sequence"], "collector"]}
        planner_query_hints = (
            state["analysis_dimension_plan"].query_hints if state.get("analysis_dimension_plan") is not None else {}
        )
        output = self.collector.run(
            CollectorInput(
                task=task,
                run_id=state.get("run_id"),
                retry_count=state["rework_count"],
                collector_mode=state["collector_mode"],
                planner_query_hints=planner_query_hints,
                gate_context={
                    **state.get("evidence_gate_output", {}),
                    "rework_context": state.get("rework_context").model_dump(mode="json") if state.get("rework_context") else None,
                    "targeted_recollection": self._targeted_recollection_summary(state.get("rework_context")),
                },
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
        missing = [competitor for competitor, count in relevant_count.items() if count < 1]
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
                        "rework_count": state["rework_count"],
                        "final_status": "manual_review",
                    }
                )
                next_task = self.task_service.update_status(task.task_id, "manual_review", rework_count=state["rework_count"])
            elif state["auto_rework"]:
                next_rework_count = state["rework_count"] + 1
                if next_rework_count >= state["max_rework"]:
                    final_status = "manual_review"
                    suggested_route = None
                    routes.append(
                        {
                            "from_node": "evidence_gate",
                            "to_node": "final_report",
                            "reason": "max_rework_reached",
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
                        "rework_count": state["rework_count"],
                        "final_status": "insufficient_evidence",
                    }
                )
                next_task = self.task_service.update_status(task.task_id, "qa_failed", rework_count=state["rework_count"])
            qa_result = QaResult(
                task_id=task.task_id,
                run_id=state.get("run_id"),
                status="manual_review" if final_status == "manual_review" else "failed",
                hard_errors=[f"以下竞品缺少相关公开证据：{', '.join(missing)}。"],
                rework_instructions=[
                    ReworkInstruction(
                        target_agent="CollectorAgent",
                        error_type="missing_relevant_evidence",
                        reason=f"以下竞品缺少相关公开证据：{', '.join(missing)}。",
                        suggested_action="请先补齐 high/medium relevance Evidence，再进入 AnalystAgent 和 ReportWriterAgent。",
                        failed_schema="Evidence.relevance",
                    )
                ],
                route_to="CollectorAgent" if suggested_route == "CollectorAgent" else None,
                rework_count=next_rework_count,
            )
            qa_result = self.report_service.save_qa(qa_result, run_id=state.get("run_id"))
        else:
            qa_result = state.get("qa_result")

        output = {
            "evidence_gate_passed": passed,
            "missing_relevant_evidence_competitors": missing,
            "relevant_evidence_count_by_competitor": relevant_count,
            "unrelated_evidence_count_by_competitor": unrelated_count,
            "suggested_route": suggested_route,
            "suggested_action": "Proceed to AnalystAgent." if passed else "Collect more competitor-specific high/medium relevance Evidence.",
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
        output = self.analyst.run(
            AnalystInput(
                task=task,
                run_id=state.get("run_id"),
                evidence=state.get("evidence", []),
                retry_count=state["rework_count"],
                force_invalid_extraction=state["demo_mode"] == "qa_invalid_extraction" and state["rework_count"] == 0,
                analyst_mode=state["analyst_mode"],
                selected_dimensions=state.get("selected_dimensions", []),
                rework_context=state.get("rework_context"),
            )
        )
        return {
            **state,
            "task": task,
            "analyst_output": output,
            "capability_map": output.capability_map,
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
                survey_needed=state.get("survey_needed", False),
                survey_recommended=state.get("survey_recommended", False),
                survey_objective=state.get("survey_objective"),
                survey_inputs=state.get("survey_inputs"),
                questionnaire_follow_up=state.get("questionnaire_follow_up"),
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
                retry_count=state["rework_count"],
                demo_mode=state["demo_mode"],
            )
        )
        qa_result = output.qa_result
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
        qa_result = state.get("qa_result")
        writer_output = state.get("report_writer_output")
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
        if qa_result.status == "manual_review" or qa_result.rework_count >= state["max_rework"]:
            state["final_status"] = "manual_review"
            return "final_report"
        if not state["auto_rework"]:
            return "final_report"
        if qa_result.route_to == "CollectorAgent":
            return "collector"
        if qa_result.route_to == "AnalystAgent":
            return "analyst"
        if qa_result.route_to == "ReportWriterAgent":
            return "report_writer"
        state["final_status"] = "manual_review"
        return "final_report"

    def _current_task(self, state: WorkflowState) -> Task:
        return self.task_service.get_task(state["task_id"])

    @staticmethod
    def _agent_to_node(agent_name: str) -> str:
        return {
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
        core = WorkflowSummaryCorePayload(
            run_id=state.get("run_id"),
            task_id=state.get("task_id"),
            workflow_engine_requested=state.get("workflow_engine_requested"),
            workflow_engine_used="langgraph",
            workflow_role="primary",
            intent_classification=state.get("intent_classification"),
            ambiguity_level=state.get("ambiguity_level"),
            scope_type=state.get("scope_type"),
            scope_size=state.get("scope_size"),
            selected_dimensions=state.get("selected_dimensions", []),
            downstream_guidance=state.get("downstream_guidance").model_dump(mode="json")
            if state.get("downstream_guidance")
            else None,
            confirmed_scope=state.get("confirmed_scope").model_dump(mode="json") if state.get("confirmed_scope") else None,
            inferred_scope=state.get("inferred_scope").model_dump(mode="json") if state.get("inferred_scope") else None,
            suggested_scope=state.get("suggested_scope").model_dump(mode="json") if state.get("suggested_scope") else None,
            recommended_next_constraints=state.get("recommended_next_constraints", []),
            clarification_targets=state.get("clarification_targets", []),
            candidate_competitors=[item.model_dump(mode="json") for item in state.get("candidate_competitors", [])],
            planning_stages=[item.model_dump(mode="json") for item in state.get("planning_stages", [])],
            domain_pack=state.get("domain_pack"),
            swot_analysis=state.get("swot_analysis").model_dump(mode="json") if state.get("swot_analysis") else None,
            rework_context=state.get("rework_context").model_dump(mode="json") if state.get("rework_context") else None,
            node_sequence=state.get("node_sequence", []),
            conditional_routes_taken=state.get("conditional_routes_taken", []),
            rework_count=state.get("qa_result").rework_count if state.get("qa_result") else state.get("rework_count", 0),
            final_status=state.get("final_status") or (state.get("qa_result").status if state.get("qa_result") else "failed"),
            elapsed_time_ms=elapsed_time_ms,
        )
        extensions = WorkflowSummaryExtensionPayload(
            primary_workflow_kind="competitive_analysis_langgraph",
            follow_up_workflow_kind="survey_questionnaire_sidecar",
            survey=state.get("survey_extension")
            or SurveyExtensionSummary(
                survey_needed=state.get("survey_needed", False),
                survey_recommended=state.get("survey_recommended", False),
                survey_objective=state.get("survey_objective"),
                survey_inputs=state.get("survey_inputs").model_dump(mode="json") if state.get("survey_inputs") else None,
                survey_guidance=state.get("downstream_guidance").survey if state.get("downstream_guidance") else [],
            ),
            questionnaire_follow_up=state.get("questionnaire_follow_up"),
        )
        diagnostics = WorkflowSummaryDiagnosticsPayload(
            workflow_data_source="execution_backed_summary",
            runner_capability_notes=[
                "LangGraph is the primary competitive-analysis workflow path.",
                "Questionnaire and survey work are follow-up sidecar flows, not always-on nodes in the main DAG.",
            ],
            evidence_gate_output=state.get("evidence_gate_output", {}),
            page_fetch_output=state.get("page_fetch_output", {}),
            run_isolation_strategy=state.get("run_isolation_strategy", "run_id"),
            run_cleanup_summary=state.get("run_cleanup_summary", {}),
            error_message="; ".join(state.get("errors", [])) if state.get("errors") else None,
        )
        summary = {
            **core.model_dump(mode="json", exclude_none=True),
            "survey_needed": extensions.survey.survey_needed if extensions.survey else False,
            "survey_recommended": extensions.survey.survey_recommended if extensions.survey else False,
            "swot_validation": state.get("qa_result").metadata.get("swot_validation")
            if state.get("qa_result") and state.get("qa_result").metadata
            else None,
            "primary_workflow_kind": extensions.primary_workflow_kind,
            "follow_up_workflow_kind": extensions.follow_up_workflow_kind,
            "survey_integration_mode": "follow_up_sidecar",
            "workflow_data_source": diagnostics.workflow_data_source,
            "runner_capability_notes": diagnostics.runner_capability_notes,
            "evidence_gate_output": diagnostics.evidence_gate_output,
            "page_fetch_output": diagnostics.page_fetch_output,
            "run_isolation_strategy": diagnostics.run_isolation_strategy,
            "run_cleanup_summary": diagnostics.run_cleanup_summary,
            "error_message": diagnostics.error_message,
            "core": core.model_dump(mode="json", exclude_none=True),
            "extensions": extensions.model_dump(mode="json", exclude_none=True),
            "diagnostics": diagnostics.model_dump(mode="json", exclude_none=True),
        }
        return summary

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
            related_evidence_id=None,
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
