from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.runner import default_dag
from app.agents.runner_factory import create_workflow_runner
from app.database import get_db
from app.schemas import CollectorConfig, CreateTaskRequest, PlannerCollectionPlan
from app.services.evidence_service import EvidenceService
from app.services.llm_client import LlmClient
from app.services.planner_attempt_service import PlannerAttemptService
from app.services.report_service import ReportService
from app.services.task_run_service import TaskRunService
from app.services.task_service import TaskService
from app.services.trace_service import TraceService
from app.services.web_search_client import WebSearchClient
from app.services.workflow_progress_service import get_workflow_progress

router = APIRouter(prefix="/api")


class RunTaskRequest(BaseModel):
    collection_plan_override: PlannerCollectionPlan | None = None
    collector_config: CollectorConfig | None = None
    skip_initial_planner: bool = False
    manual_evidence_selection_enabled: bool = False
    selected_evidence_ids: list[str] = []
    source_run_id: str | None = None


@router.get("/llm/status")
def llm_status():
    return LlmClient().status()


@router.post("/llm/test")
def test_llm_connection():
    return LlmClient().test_connection()


@router.get("/collector/status")
def collector_status():
    return WebSearchClient().status()


@router.get("/search/status")
def search_status():
    return WebSearchClient().status()


@router.post("/search/test")
def test_search_connection(payload: dict = Body(default_factory=dict)):
    query = payload.get("query") or "飞书 B2B SaaS 功能 定价 官网"
    return WebSearchClient().test_connection(str(query))


@router.post("/tasks")
def create_task(request: CreateTaskRequest, db: Session = Depends(get_db)):
    return TaskService(db).create_task(request)


@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db)):
    return TaskService(db).list_tasks()


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    try:
        return TaskService(db).get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.post("/tasks/{task_id}/run")
def run_task(
    task_id: str,
    request: RunTaskRequest | None = Body(default=None),
    demo_mode: str = Query("normal", pattern="^(normal|qa_missing_evidence|qa_invalid_extraction|qa_bad_report)$"),
    auto_rework: bool = Query(False),
    writer_mode: str = Query("mock", pattern="^(mock|llm)$"),
    collector_mode: str = Query("mock", pattern="^(mock|web)$"),
    analyst_mode: str = Query("evidence", pattern="^(mock|evidence|llm)$"),
    workflow_engine: str | None = Query(None, pattern="^(custom|langgraph)$"),
    content_mode: str | None = Query(None),
    debug_stage: str | None = Query(None, pattern="^(planner_only|collector_only)$"),
    db: Session = Depends(get_db),
):
    try:
        effective_engine = "langgraph" if debug_stage in {"planner_only", "collector_only"} else workflow_engine
        runner, engine = create_workflow_runner(db, effective_engine)
        if engine == "langgraph":
            return runner.run(
                task_id,
                demo_mode=demo_mode,
                auto_rework=auto_rework,
                writer_mode=writer_mode,
                collector_mode=collector_mode,
                analyst_mode=analyst_mode,
                workflow_engine_requested=workflow_engine or "env/default",
                content_mode=content_mode,
                debug_stage=debug_stage,
                collection_plan_override=request.collection_plan_override if request else None,
                collector_config=request.collector_config if request else None,
                skip_initial_planner=request.skip_initial_planner if request else False,
                manual_evidence_selection_enabled=(
                    request.manual_evidence_selection_enabled if request else False
                ),
                selected_evidence_ids=request.selected_evidence_ids if request else None,
                source_run_id=request.source_run_id if request else None,
            )
        run_service = TaskRunService(db)
        task_run = run_service.create_run(
            task_id=task_id,
            workflow_engine="custom",
            collector_mode=collector_mode,
            analyst_mode=analyst_mode,
            writer_mode=writer_mode,
            content_mode=content_mode,
            demo_mode=demo_mode,
            auto_rework=auto_rework,
        )
        result = runner.run(
            task_id,
            demo_mode=demo_mode,
            auto_rework=auto_rework,
            writer_mode=writer_mode,
            collector_mode=collector_mode,
            analyst_mode=analyst_mode,
            run_id=task_run.run_id,
        )
        result["workflow_summary"] = {
            "workflow_engine_requested": workflow_engine or "env/default",
            "workflow_engine_used": "custom",
            "intent_classification": (
                result["plan"].planner_summary.intent_classification if result.get("plan") else None
            ),
            "ambiguity_level": result["plan"].ambiguity_level if result.get("plan") else None,
            "scope_type": result["plan"].scope_type if result.get("plan") else None,
            "scope_size": result["plan"].scope_size if result.get("plan") else None,
            "survey_needed": result["plan"].survey_needed if result.get("plan") else False,
            "selected_dimensions": result["plan"].selected_dimensions if result.get("plan") else [],
            "recommended_next_constraints": result["plan"].recommended_next_constraints if result.get("plan") else [],
            "clarification_targets": result["plan"].clarification_targets if result.get("plan") else [],
            "candidate_competitors": [item.model_dump(mode="json") for item in result["plan"].candidate_competitors] if result.get("plan") else [],
            "planning_stages": [item.model_dump(mode="json") for item in result["plan"].planning_stages] if result.get("plan") else [],
            "node_sequence": ["planner", "collector", "analyst", "report_writer", "qa"] + (["final_report"] if result.get("report") else []),
            "conditional_routes_taken": [],
            "rework_count": result["qa_result"].rework_count if result.get("qa_result") else 0,
            "final_status": result["qa_result"].status if result.get("qa_result") else "failed",
            "knowledge_hits": [],
            "retrieved_knowledge_chunk_count": 0,
            "knowledge_retrieval_strategy": {
                "retriever": "disabled_for_custom_runner",
                "vector_store": "sqlite_json_embedding",
                "embedding_provider": "local_hash_embedding",
                "similarity": "cosine_similarity",
                "top_k": 0,
                "current_run_evidence_priority": True,
            },
        }
        final_status = result["workflow_summary"]["final_status"]
        finished_run = run_service.finish_run(
            task_run.run_id,
            status="completed" if final_status in {"passed", "completed"} else str(final_status),
            final_status=final_status,
            elapsed_time_ms=None,
        )
        result["run"] = finished_run
        result["run_id"] = finished_run.run_id
        result["knowledge_hits"] = []
        result["workflow_summary"]["run_id"] = finished_run.run_id
        result["workflow_summary"]["run_isolation_strategy"] = "legacy_custom_no_run_binding"
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.get("/tasks/{task_id}/dag")
def get_dag(task_id: str, db: Session = Depends(get_db)):
    try:
        task = TaskService(db).get_task(task_id)
        return default_dag(task.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.get("/tasks/{task_id}/traces")
def get_traces(task_id: str, db: Session = Depends(get_db)):
    run_id = _latest_run_id_or_none(task_id, db)
    return TraceService(db).list_for_task(task_id, run_id=run_id) if run_id else TraceService(db).list_for_task(task_id)


@router.get("/tasks/{task_id}/evidence")
def get_evidence(task_id: str, db: Session = Depends(get_db)):
    run_id = _latest_run_id_or_none(task_id, db)
    return EvidenceService(db).list_for_task(task_id, run_id=run_id) if run_id else EvidenceService(db).list_for_task(task_id)


@router.get("/tasks/{task_id}/qa")
def get_qa(task_id: str, db: Session = Depends(get_db)):
    try:
        run_id = _latest_run_id_or_none(task_id, db)
        return ReportService(db).qa_for_task_run(task_id, run_id) if run_id else ReportService(db).latest_qa(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="QA result not found") from exc


@router.get("/tasks/{task_id}/report")
def get_task_report(task_id: str, db: Session = Depends(get_db)):
    try:
        run_id = _latest_run_id_or_none(task_id, db)
        return ReportService(db).get_for_task_run(task_id, run_id) if run_id else ReportService(db).get_latest_for_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report not found") from exc


@router.get("/reports/{report_id}")
def get_report(report_id: str, db: Session = Depends(get_db)):
    try:
        return ReportService(db).get_report(report_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report not found") from exc


@router.get("/tasks/{task_id}/runs")
def list_task_runs(task_id: str, db: Session = Depends(get_db)):
    return TaskRunService(db).list_for_task(task_id)


@router.get("/tasks/{task_id}/runs/latest")
def get_latest_task_run(task_id: str, db: Session = Depends(get_db)):
    try:
        return TaskRunService(db).latest_for_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task run not found") from exc


@router.get("/tasks/{task_id}/runs/{run_id}")
def get_task_run(task_id: str, run_id: str, db: Session = Depends(get_db)):
    try:
        return TaskRunService(db).get_run(task_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task run not found") from exc


@router.post("/tasks/{task_id}/runs/{run_id}/cancel")
def cancel_task_run(task_id: str, run_id: str, db: Session = Depends(get_db)):
    try:
        return TaskRunService(db).request_cancel(task_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task run not found") from exc


@router.get("/tasks/{task_id}/runs/{run_id}/evidence")
def get_task_run_evidence(task_id: str, run_id: str, db: Session = Depends(get_db)):
    return EvidenceService(db).list_for_task(task_id, run_id=run_id)


@router.get("/tasks/{task_id}/runs/{run_id}/report")
def get_task_run_report(task_id: str, run_id: str, db: Session = Depends(get_db)):
    try:
        return ReportService(db).get_for_task_run(task_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report not found") from exc


@router.get("/tasks/{task_id}/runs/{run_id}/qa")
def get_task_run_qa(task_id: str, run_id: str, db: Session = Depends(get_db)):
    try:
        return ReportService(db).qa_for_task_run(task_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="QA result not found") from exc


@router.get("/tasks/{task_id}/runs/{run_id}/traces")
def get_task_run_traces(task_id: str, run_id: str, db: Session = Depends(get_db)):
    return TraceService(db).list_for_task(task_id, run_id=run_id)


@router.get("/tasks/{task_id}/runs/{run_id}/progress")
def get_task_run_progress(task_id: str, run_id: str, db: Session = Depends(get_db)):
    TaskRunService(db).get_run(task_id, run_id)
    return get_workflow_progress(run_id)


@router.get("/tasks/{task_id}/runs/{run_id}/planner-attempts")
def get_task_run_planner_attempts(task_id: str, run_id: str, db: Session = Depends(get_db)):
    try:
        TaskRunService(db).get_run(task_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    return PlannerAttemptService(db).list_for_run(run_id)


def _latest_run_id_or_none(task_id: str, db: Session) -> str | None:
    try:
        return TaskRunService(db).latest_for_task(task_id).run_id
    except KeyError:
        return None
