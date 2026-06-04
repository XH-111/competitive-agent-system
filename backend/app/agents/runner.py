from sqlalchemy.orm import Session

from app.agents.analyst import AnalystAgent
from app.agents.collector import CollectorAgent
from app.agents.final_report import FinalReportAgent
from app.agents.planner import PlannerAgent
from app.agents.qa import QaAgent
from app.agents.report_writer import ReportWriterAgent
from app.constants.analysis_dimensions import apply_fixed_dimensions_to_plan, fixed_dimension_ids
from app.schemas import (
    AnalystInput,
    AnalystOutput,
    CollectorInput,
    DemoMode,
    Evidence,
    FinalReportInput,
    PlannerInput,
    PlannerOutput,
    QaInput,
    QaResult,
    ReportWriterInput,
    ReportWriterOutput,
    ReworkContext,
    ReworkHistoryItem,
    Task,
)
from app.services.evidence_service import EvidenceService
from app.services.report_service import ReportService
from app.services.task_service import TaskService
from app.services.trace_service import TraceService


class MockWorkflowRunner:
    def __init__(self, db: Session):
        self.db = db
        self.task_service = TaskService(db)
        self.trace_service = TraceService(db)
        self.evidence_service = EvidenceService(db)
        self.report_service = ReportService(db)
        self.planner = PlannerAgent(self.trace_service)
        self.collector = CollectorAgent(self.trace_service)
        self.analyst = AnalystAgent(self.trace_service)
        self.writer = ReportWriterAgent(self.trace_service)
        self.qa = QaAgent(self.trace_service)
        self.final_report = FinalReportAgent(self.trace_service)

    def run(
        self,
        task_id: str,
        demo_mode: DemoMode = "normal",
        auto_rework: bool = False,
        writer_mode: str = "mock",
        collector_mode: str = "mock",
        analyst_mode: str = "evidence",
        run_id: str | None = None,
    ) -> dict:
        self.run_id = run_id
        self.trace_service.set_run_context(run_id)
        task = self.task_service.update_status(task_id, "running")
        plan = self.planner.run(PlannerInput(task=task, run_id=run_id))
        plan = plan.model_copy(
            update={
                "selected_dimensions": fixed_dimension_ids(),
                "analysis_dimension_plan": apply_fixed_dimensions_to_plan(plan.analysis_dimension_plan, task),
                "planner_notes": [
                    *plan.planner_notes,
                    "Collector/Analyst/Writer use fixed competitive dimensions for current evaluation: pricing, feature, persona, strength, weakness, opportunity, threat.",
                ],
            }
        )
        planner_query_hints = plan.analysis_dimension_plan.query_hints if plan.analysis_dimension_plan else {}
        history: list[ReworkHistoryItem] = []

        if demo_mode == "qa_missing_evidence":
            qa_result = self.qa.run(QaInput(task=task, run_id=run_id, evidence=[], demo_mode=demo_mode)).qa_result
            self._save_qa(qa_result, history)
            if not auto_rework:
                self.task_service.update_status(task_id, self._status_for_qa(qa_result), rework_count=qa_result.rework_count)
                return {"plan": plan, "qa_result": qa_result, "report": None}
            return self._auto_rework(
                task=task,
                plan=plan,
                qa_result=qa_result,
                history=history,
                evidence=[],
                analysis=None,
                writer_output=None,
                writer_mode=writer_mode,
                collector_mode=collector_mode,
                analyst_mode=analyst_mode,
                planner_query_hints=planner_query_hints,
            )

        evidence, analysis, writer_output = self._produce_outputs(
            task=task,
            plan=plan,
            demo_mode=demo_mode,
            retry_count=0,
            writer_mode=writer_mode,
            collector_mode=collector_mode,
            analyst_mode=analyst_mode,
            planner_query_hints=planner_query_hints,
        )

        qa_result = self.qa.run(
            QaInput(
                task=task,
                evidence=evidence,
                analysis=analysis,
                report_output=writer_output,
                demo_mode=demo_mode,
            )
        ).qa_result
        self._save_qa(qa_result, history)

        if demo_mode != "normal" and not auto_rework:
            self.task_service.update_status(task_id, self._status_for_qa(qa_result), rework_count=qa_result.rework_count)
            return {"plan": plan, "qa_result": qa_result, "report": None}

        if qa_result.status == "failed" and auto_rework:
            return self._auto_rework(
                task=task,
                plan=plan,
                qa_result=qa_result,
                history=history,
                evidence=evidence,
                analysis=analysis,
                writer_output=writer_output,
                writer_mode=writer_mode,
                collector_mode=collector_mode,
                analyst_mode=analyst_mode,
                planner_query_hints=planner_query_hints,
            )

        return self._finalize_or_fail(task, plan, qa_result, history, evidence, writer_output)

    def _produce_outputs(
        self,
        *,
        task: Task,
        plan: PlannerOutput,
        demo_mode: DemoMode,
        retry_count: int,
        writer_mode: str,
        collector_mode: str,
        analyst_mode: str,
        planner_query_hints: dict[str, list[str]] | None = None,
        evidence: list[Evidence] | None = None,
        analysis: AnalystOutput | None = None,
        rework_context: ReworkContext | None = None,
    ) -> tuple[list[Evidence], AnalystOutput, ReportWriterOutput]:
        if evidence is None:
            collector_output = self.collector.run(
                CollectorInput(
                    task=task,
                    run_id=self.run_id,
                    retry_count=retry_count,
                    collector_mode=collector_mode,
                    planner_query_hints=planner_query_hints or {},
                    gate_context={
                        "rework_context": rework_context.model_dump(mode="json") if rework_context else None,
                        "targeted_recollection": self._targeted_recollection_summary(rework_context),
                    },
                )
            )
            evidence = collector_output.evidence
            evidence = self.evidence_service.save_many(task.task_id, evidence, run_id=self.run_id)

        if analysis is None:
            analysis = self.analyst.run(
                AnalystInput(
                    task=task,
                    run_id=self.run_id,
                    evidence=evidence,
                    retry_count=retry_count,
                    force_invalid_extraction=demo_mode == "qa_invalid_extraction",
                    analyst_mode=analyst_mode,
                    selected_dimensions=plan.selected_dimensions,
                    rework_context=rework_context,
                )
            )

        writer_output = self.writer.run(
            ReportWriterInput(
                task=task,
                run_id=self.run_id,
                knowledge=analysis,
                evidence=evidence,
                retry_count=retry_count,
                force_bad_format=demo_mode == "qa_bad_report",
                writer_mode=writer_mode,
                selected_dimensions=plan.selected_dimensions,
                writer_guidance=plan.downstream_guidance.writer if plan.downstream_guidance else [],
                intent_classification=plan.intent_classification,
                rework_context=rework_context,
            )
        )
        return evidence, analysis, writer_output

    def _auto_rework(
        self,
        *,
        task: Task,
        plan: PlannerOutput,
        qa_result: QaResult,
        history: list[ReworkHistoryItem],
        evidence: list[Evidence],
        analysis: AnalystOutput | None,
        writer_output: ReportWriterOutput | None,
        writer_mode: str,
        collector_mode: str,
        analyst_mode: str,
        planner_query_hints: dict[str, list[str]] | None,
    ) -> dict:
        current_task = task
        current_qa = qa_result
        current_evidence = evidence
        current_analysis = analysis
        current_writer_output = writer_output

        while current_qa.status == "failed":
            instruction = current_qa.rework_instructions[0] if current_qa.rework_instructions else None
            if instruction is None or current_qa.route_to is None:
                break
            rework_context = self._rework_context_from_qa(current_qa)

            history_item = ReworkHistoryItem(
                round=current_qa.rework_count,
                from_status=current_qa.status,
                error_type=instruction.error_type,
                route_to=current_qa.route_to,
                action=instruction.suggested_action,
                reason=instruction.reason,
                failed_schema=instruction.failed_schema,
                claim_id=instruction.claim_id,
                failed_claim=instruction.failed_claim,
                metadata=instruction.metadata or {},
            )
            history.append(history_item)

            current_task = self.task_service.update_status(
                current_task.task_id,
                "qa_failed",
                rework_count=current_qa.rework_count,
            )

            if current_qa.route_to == "CollectorAgent":
                collector_output = self.collector.run(
                    CollectorInput(
                        task=current_task,
                        run_id=self.run_id,
                        retry_count=current_qa.rework_count,
                        collector_mode=collector_mode,
                        planner_query_hints=planner_query_hints or {},
                        gate_context={
                            "rework_context": rework_context.model_dump(mode="json") if rework_context else None,
                            "targeted_recollection": self._targeted_recollection_summary(rework_context),
                        },
                    )
                )
                current_evidence = collector_output.evidence
                current_evidence = self.evidence_service.save_many(current_task.task_id, current_evidence, run_id=self.run_id)
                current_analysis = self.analyst.run(
                    AnalystInput(
                        task=current_task,
                        run_id=self.run_id,
                        evidence=current_evidence,
                        retry_count=current_qa.rework_count,
                        analyst_mode=analyst_mode,
                        selected_dimensions=plan.selected_dimensions,
                        rework_context=rework_context,
                    )
                )
                current_writer_output = self.writer.run(
                    ReportWriterInput(
                        task=current_task,
                        run_id=self.run_id,
                        knowledge=current_analysis,
                        evidence=current_evidence,
                        retry_count=current_qa.rework_count,
                        writer_mode=writer_mode,
                        selected_dimensions=plan.selected_dimensions,
                        writer_guidance=plan.downstream_guidance.writer if plan.downstream_guidance else [],
                        intent_classification=plan.intent_classification,
                        rework_context=rework_context,
                    )
                )
            elif current_qa.route_to == "AnalystAgent":
                current_analysis = self.analyst.run(
                    AnalystInput(
                        task=current_task,
                        run_id=self.run_id,
                        evidence=current_evidence,
                        retry_count=current_qa.rework_count,
                        analyst_mode=analyst_mode,
                        selected_dimensions=plan.selected_dimensions,
                        rework_context=rework_context,
                    )
                )
                current_writer_output = self.writer.run(
                    ReportWriterInput(
                        task=current_task,
                        run_id=self.run_id,
                        knowledge=current_analysis,
                        evidence=current_evidence,
                        retry_count=current_qa.rework_count,
                        writer_mode=writer_mode,
                        selected_dimensions=plan.selected_dimensions,
                        writer_guidance=plan.downstream_guidance.writer if plan.downstream_guidance else [],
                        intent_classification=plan.intent_classification,
                        rework_context=rework_context,
                    )
                )
            elif current_qa.route_to == "ReportWriterAgent":
                if current_analysis is None:
                    current_analysis = self.analyst.run(
                        AnalystInput(
                            task=current_task,
                            run_id=self.run_id,
                            evidence=current_evidence,
                            retry_count=current_qa.rework_count,
                            analyst_mode=analyst_mode,
                            selected_dimensions=plan.selected_dimensions,
                            rework_context=rework_context,
                        )
                    )
                current_writer_output = self.writer.run(
                    ReportWriterInput(
                        task=current_task,
                        run_id=self.run_id,
                        knowledge=current_analysis,
                        evidence=current_evidence,
                        retry_count=current_qa.rework_count,
                        writer_mode=writer_mode,
                        selected_dimensions=plan.selected_dimensions,
                        writer_guidance=plan.downstream_guidance.writer if plan.downstream_guidance else [],
                        intent_classification=plan.intent_classification,
                        rework_context=rework_context,
                    )
                )
            else:
                break

            current_qa = self.qa.run(
                QaInput(
                    task=current_task,
                    run_id=self.run_id,
                    evidence=current_evidence,
                    analysis=current_analysis,
                    report_output=current_writer_output,
                    retry_count=current_task.rework_count,
                    demo_mode="normal",
                )
            ).qa_result
            history[-1].result_status = current_qa.status
            self._save_qa(current_qa, history)

        return self._finalize_or_fail(
            current_task,
            plan,
            current_qa,
            history,
            current_evidence,
            current_writer_output,
        )

    def _finalize_or_fail(
        self,
        task: Task,
        plan: PlannerOutput,
        qa_result: QaResult,
        history: list[ReworkHistoryItem],
        evidence: list[Evidence],
        writer_output: ReportWriterOutput | None,
    ) -> dict:
        qa_result.rework_history = history

        if qa_result.status == "passed" and writer_output is not None and writer_output.report is not None:
            final_output = self.final_report.run(
                FinalReportInput(
                    task=task,
                    run_id=self.run_id,
                    report=writer_output.report,
                    qa_result=qa_result,
                    evidence=evidence,
                    retry_count=qa_result.rework_count,
                )
            )
            final_output.report = self.report_service.save_report(final_output.report, run_id=self.run_id)
            self.task_service.update_status(task.task_id, "completed", rework_count=qa_result.rework_count)
            self._save_qa(qa_result, history)
            return {"plan": plan, "qa_result": qa_result, "report": final_output.report}

        self.task_service.update_status(task.task_id, self._status_for_qa(qa_result), rework_count=qa_result.rework_count)
        self._save_qa(qa_result, history)
        return {"plan": plan, "qa_result": qa_result, "report": None}

    def _save_qa(self, qa_result: QaResult, history: list[ReworkHistoryItem]) -> None:
        qa_result.rework_history = list(history)
        if self.run_id:
            qa_result.run_id = self.run_id
        self.report_service.save_qa(qa_result, run_id=self.run_id)

    @staticmethod
    def _status_for_qa(qa_result: QaResult) -> str:
        if qa_result.status == "manual_review":
            return "manual_review"
        if qa_result.status == "failed":
            return "qa_failed"
        return "completed"

    @staticmethod
    def _rework_context_from_qa(qa_result: QaResult) -> ReworkContext | None:
        if not qa_result.rework_instructions:
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
        if rework_context is None or not rework_context.related_competitor:
            return {"by_competitor": {}}
        return {
            "by_competitor": {
                rework_context.related_competitor: [
                    {
                        "error_type": rework_context.error_type,
                        "reason": rework_context.reason,
                        "metadata": rework_context.metadata,
                    }
                ]
            }
        }


def default_dag(status: str) -> dict:
    completed = status == "completed"
    manual = status == "manual_review"
    active = status in {"running", "qa_failed", "completed", "manual_review"}
    return {
        "nodes": [
            {"id": "PlannerAgent", "label": "规划任务范围和 DAG", "status": "completed" if active else "pending"},
            {"id": "CollectorAgent", "label": "采集 Mock 证据", "status": "completed" if completed or manual else ("completed" if status == "qa_failed" else "pending")},
            {"id": "EvidenceGate", "label": "校验证据相关性", "status": "completed" if completed else ("failed" if status == "qa_failed" else ("manual_review" if manual else "pending"))},
            {"id": "AnalystAgent", "label": "抽取结构化竞品知识", "status": "completed" if completed or manual else ("completed" if status == "qa_failed" else "pending")},
            {"id": "ReportWriterAgent", "label": "撰写带证据报告", "status": "completed" if completed else ("failed" if status == "qa_failed" else "pending")},
            {"id": "QaAgent", "label": "校验输出质量", "status": "completed" if completed else ("manual_review" if manual else ("failed" if status == "qa_failed" else "pending"))},
            {"id": "FinalReport", "label": "最终报告", "status": "completed" if completed else ("manual_review" if manual else "pending")},
        ],
        "edges": [
            {"source": "PlannerAgent", "target": "CollectorAgent", "label": "计划"},
            {"source": "CollectorAgent", "target": "EvidenceGate", "label": "证据"},
            {"source": "EvidenceGate", "target": "AnalystAgent", "label": "相关证据通过"},
            {"source": "EvidenceGate", "target": "CollectorAgent", "label": "相关证据不足"},
            {"source": "AnalystAgent", "target": "ReportWriterAgent", "label": "知识"},
            {"source": "ReportWriterAgent", "target": "QaAgent", "label": "草稿"},
            {"source": "QaAgent", "target": "FinalReport", "label": "通过"},
            {"source": "QaAgent", "target": "CollectorAgent", "label": "缺少证据"},
            {"source": "QaAgent", "target": "AnalystAgent", "label": "抽取错误"},
            {"source": "QaAgent", "target": "ReportWriterAgent", "label": "返工"},
        ],
    }
