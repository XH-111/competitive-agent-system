import { Play, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "./api/client";
import { DagView } from "./components/DagView";
import { EvidencePanel } from "./components/EvidencePanel";
import { KnowledgeHitsPanel } from "./components/KnowledgeHitsPanel";
import { KnowledgeView } from "./components/KnowledgeView";
import { PlannerSummaryCard } from "./components/PlannerSummaryCard";
import { QaPanel } from "./components/QaPanel";
import { ReportView } from "./components/ReportView";
import { TaskForm } from "./components/TaskForm";
import { TaskList } from "./components/TaskList";
import { TraceViewer } from "./components/TraceViewer";
import type { CollectionPlan, CollectorConfig, CollectorDiagnostics, CollectorStatus, Dag, DimensionResult, Evidence, LlmStatus, PlannerAttempt, PlannerRunResult, QaResult, Report, RunTaskOverrides, SearchTestResult, Task, TaskRun, TraceRecord, WorkflowProgress, WorkflowSummary } from "./types";
import { Pill } from "./types";

type EditableCollectionPlanItem = {
  enabled: boolean;
  dimension_id: string;
  label: string;
  queries: string[];
  research_goals: string[];
  source?: string;
  max_results_per_query: number;
  max_evidence_per_dimension: number;
  min_valid_evidence_required: number;
  include_domains: string;
  exclude_domains: string;
};

type EditableCollectionPlan = Record<string, Record<string, EditableCollectionPlanItem>>;

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [task, setTask] = useState<Task>();
  const [dag, setDag] = useState<Dag>();
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [qa, setQa] = useState<QaResult>();
  const [report, setReport] = useState<Report>();
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [runs, setRuns] = useState<TaskRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>();
  const [selectedFact, setSelectedFact] = useState<DimensionResult>();
  const [selectedEvidenceIds, setSelectedEvidenceIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const demoMode = "normal";
  const autoRework = true;
  const writerMode = "llm";
  const [collectorMode, setCollectorMode] = useState<"mock" | "web">("web");
  const analystMode = "llm";
  const workflowEngine: "langgraph" = "langgraph";
  const runStage: "collector_only" = "collector_only";
  const [workflowSummary, setWorkflowSummary] = useState<WorkflowSummary>();
  const [workflowProgress, setWorkflowProgress] = useState<WorkflowProgress>();
  const [plannerAttempts, setPlannerAttempts] = useState<PlannerAttempt[]>([]);
  const [llmStatus, setLlmStatus] = useState<LlmStatus>();
  const [collectorStatus, setCollectorStatus] = useState<CollectorStatus>();
  const [llmTesting, setLlmTesting] = useState(false);
  const [searchTesting, setSearchTesting] = useState(false);
  const [searchTestResult, setSearchTestResult] = useState<SearchTestResult>();
  const [useCollectionPlanOverride, setUseCollectionPlanOverride] = useState(false);
  const [editableCollectionPlan, setEditableCollectionPlan] = useState<EditableCollectionPlan>();
  const [collectionPlanOverrideError, setCollectionPlanOverrideError] = useState<string>();
  const [manualPlanConfirmed, setManualPlanConfirmed] = useState(false);
  const [manualEvidenceSelectionEnabled, setManualEvidenceSelectionEnabled] = useState(false);
  const [manualSelectedEvidenceIds, setManualSelectedEvidenceIds] = useState<string[]>([]);
  const [manualEvidenceSelectionError, setManualEvidenceSelectionError] = useState<string>();

  useEffect(() => {
    loadTasks();
    loadLlmStatus();
    loadCollectorStatus();
  }, []);


  async function loadLlmStatus() {
    await api.llmStatus().then(setLlmStatus).catch(() => setLlmStatus(undefined));
  }

  async function loadCollectorStatus() {
    await api.collectorStatus().then(setCollectorStatus).catch(() => setCollectorStatus(undefined));
  }

  async function loadTasks(selectTaskId?: string) {
    const nextTasks = await api.listTasks();
    setTasks(nextTasks);
    const nextTask = nextTasks.find((item) => item.task_id === selectTaskId) ?? nextTasks[0];
    if (nextTask) {
      setTask(nextTask);
      await refresh(nextTask.task_id);
    }
  }

  async function refresh(taskId: string, runId?: string) {
    const nextRuns = await api.runs(taskId).catch(() => []);
    setRuns(nextRuns);
    const activeRunId = runId ?? nextRuns[0]?.run_id;
    setSelectedRunId(activeRunId);
    const [nextDag, nextEvidence, nextTraces, nextPlannerAttempts] = await Promise.all([
      api.dag(taskId),
      activeRunId ? api.runEvidence(taskId, activeRunId) : api.evidence(taskId),
      activeRunId ? api.runTraces(taskId, activeRunId) : api.traces(taskId),
      activeRunId ? api.runPlannerAttempts(taskId, activeRunId).catch(() => []) : Promise.resolve([]),
    ]);
    setDag(nextDag);
    setEvidence(nextEvidence);
    setTraces(nextTraces);
    setPlannerAttempts(nextPlannerAttempts);
    const recoveredSummary = recoverWorkflowSummary(nextTraces);
    if (recoveredSummary) {
      setWorkflowSummary(recoveredSummary);
      if (recoveredSummary.debug_stage && recoveredSummary.dag) {
        setDag(recoveredSummary.dag);
      }
    } else {
      setWorkflowSummary(undefined);
    }
    const qaRequest = activeRunId ? api.runQa(taskId, activeRunId) : api.qa(taskId);
    const reportRequest = activeRunId ? api.runReport(taskId, activeRunId) : api.report(taskId);
    await qaRequest.then(setQa).catch(() => setQa(undefined));
    await reportRequest.then((nextReport) => {
      setReport(nextReport);
      const legacyKnowledge = nextReport.json_report.knowledge as { dimension_results?: DimensionResult[] } | undefined;
      setSelectedFact(
        nextReport.dimension_results?.[0]
        ?? nextReport.json_report.dimension_results?.[0]
        ?? legacyKnowledge?.dimension_results?.[0],
      );
      setSelectedEvidenceIds([]);
    }).catch(() => {
      setReport(undefined);
      setSelectedFact(undefined);
      setSelectedEvidenceIds([]);
    });
  }

  async function selectTask(nextTask: Task) {
    setTask(nextTask);
    setDag(undefined);
    setEvidence([]);
    setQa(undefined);
    setReport(undefined);
    setTraces([]);
    setRuns([]);
    setSelectedRunId(undefined);
    setSelectedFact(undefined);
    setSelectedEvidenceIds([]);
    setManualSelectedEvidenceIds([]);
    setManualEvidenceSelectionError(undefined);
    setUseCollectionPlanOverride(false);
    setEditableCollectionPlan(undefined);
    setCollectionPlanOverrideError(undefined);
    setManualPlanConfirmed(false);
    setWorkflowProgress(undefined);
    setWorkflowSummary(undefined);
    setPlannerAttempts([]);
    await refresh(nextTask.task_id);
  }

  async function handleCreated(created: Task) {
    setTask(created);
    setDag(undefined);
    setEvidence([]);
    setQa(undefined);
    setReport(undefined);
    setTraces([]);
    setRuns([]);
    setSelectedRunId(undefined);
    setSelectedFact(undefined);
    setSelectedEvidenceIds([]);
    setManualSelectedEvidenceIds([]);
    setManualEvidenceSelectionEnabled(false);
    setManualEvidenceSelectionError(undefined);
    setUseCollectionPlanOverride(false);
    setEditableCollectionPlan(undefined);
    setCollectionPlanOverrideError(undefined);
    setManualPlanConfirmed(false);
    setWorkflowProgress(undefined);
    setWorkflowSummary(undefined);
    setPlannerAttempts([]);
    await loadTasks(created.task_id);
  }

  async function run() {
    if (!task) return;
    let runOverrides: RunTaskOverrides | undefined;
    if (useCollectionPlanOverride) {
      if (!manualPlanConfirmed) {
        setCollectionPlanOverrideError("请先确认人工计划，再运行流程。");
        return;
      }
      try {
        runOverrides = buildRunOverrides(editableCollectionPlan, task);
        setCollectionPlanOverrideError(undefined);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setCollectionPlanOverrideError(message);
        return;
      }
    }
    const effectiveRunStage = runStage;
    const effectiveAutoRework = autoRework;
    const effectiveWorkflowEngine = workflowEngine;
    const knownRunIds = new Set(runs.map((item) => item.run_id));
    let stopProgressPolling = false;
    setBusy(true);
    setTraces([]);
    setQa(undefined);
    setPlannerAttempts([]);
    setWorkflowProgress(undefined);
    setManualEvidenceSelectionError(undefined);
    const progressPolling = pollRunProgress(
      task.task_id,
      knownRunIds,
      () => stopProgressPolling,
      false,
    );
    try {
      const result = await api.runTask(
        task.task_id,
        demoMode,
        effectiveAutoRework,
        writerMode,
        collectorMode,
        analystMode,
        effectiveWorkflowEngine,
        effectiveRunStage,
        runOverrides,
      ) as PlannerRunResult;
      setWorkflowSummary(result.workflow_summary);
      const runId = result.workflow_summary?.run_id ?? result.report?.run_id;
      setDag(result.dag ?? result.workflow_summary?.dag);
      setEvidence(result.evidence ?? result.collector_output?.evidence ?? []);
      setQa(result.qa_result ?? undefined);
      setReport(result.report ?? undefined);
      setSelectedFact(
        result.report?.dimension_results?.[0]
        ?? result.report?.json_report.dimension_results?.[0]
      );
      setSelectedEvidenceIds([]);
      const [nextTasks, nextRuns, nextTraces, nextPlannerAttempts] = await Promise.all([
        api.listTasks(),
        api.runs(task.task_id),
        runId ? api.runTraces(task.task_id, runId) : Promise.resolve([]),
        runId ? api.runPlannerAttempts(task.task_id, runId).catch(() => []) : Promise.resolve([]),
      ]);
      setTasks(nextTasks);
      setTask(nextTasks.find((item) => item.task_id === task.task_id) ?? task);
      setRuns(nextRuns);
      setSelectedRunId(runId ?? undefined);
      setTraces(nextTraces);
      setPlannerAttempts(nextPlannerAttempts);
    } finally {
      stopProgressPolling = true;
      await progressPolling;
      setBusy(false);
    }
  }

  async function continueWithSelectedEvidence() {
    if (!task || !selectedRunId) return;
    if (!manualSelectedEvidenceIds.length) {
      setManualEvidenceSelectionError("请至少选择一条 Evidence。");
      return;
    }
    let runOverrides: RunTaskOverrides = {
      manual_evidence_selection_enabled: true,
      selected_evidence_ids: manualSelectedEvidenceIds,
      source_run_id: selectedRunId,
    };
    if (useCollectionPlanOverride) {
      try {
        runOverrides = {
          ...buildRunOverrides(editableCollectionPlan, task),
          ...runOverrides,
        };
        setCollectionPlanOverrideError(undefined);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setCollectionPlanOverrideError(message);
        return;
      }
    }
    setBusy(true);
    setManualEvidenceSelectionError(undefined);
    setQa(undefined);
    try {
      const result = await api.runTask(
        task.task_id,
        demoMode,
        false,
        writerMode,
        collectorMode,
        analystMode,
        "langgraph",
        undefined,
        runOverrides,
      ) as PlannerRunResult;
      setWorkflowSummary(result.workflow_summary);
      const runId = result.workflow_summary?.run_id ?? result.run_id ?? selectedRunId;
      await refresh(task.task_id, runId);
    } catch (error) {
      setManualEvidenceSelectionError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function pollRunProgress(
    taskId: string,
    knownRunIds: Set<string>,
    shouldStop: () => boolean,
    plannerOnly = false,
  ) {
    let activeRunId: string | undefined;
    while (!shouldStop()) {
      try {
        const nextRuns = await api.runs(taskId);
        setRuns(nextRuns);
        activeRunId = activeRunId
          ?? nextRuns.find((item) => !knownRunIds.has(item.run_id))?.run_id
          ?? nextRuns.find((item) => item.status === "running")?.run_id;
        if (activeRunId) {
          setSelectedRunId(activeRunId);
          const [nextTraces, nextPlannerAttempts] = await Promise.all([
            api.runTraces(taskId, activeRunId),
            api.runPlannerAttempts(taskId, activeRunId).catch(() => []),
          ]);
          setTraces(nextTraces);
          setPlannerAttempts(nextPlannerAttempts);
          await api.runProgress(taskId, activeRunId).then(setWorkflowProgress).catch(() => undefined);
          if (!plannerOnly) {
            await api.runQa(taskId, activeRunId).then(setQa).catch(() => undefined);
          }
        }
      } catch {
        // The workflow POST may briefly hold backend resources; retry on the next interval.
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
  }

  async function testLlm() {
    setLlmTesting(true);
    try {
      setLlmStatus(await api.testLlm());
    } finally {
      setLlmTesting(false);
    }
  }

  async function testSearch() {
    setSearchTesting(true);
    try {
      const query = task ? `${task.competitors[0]} ${task.industry} 功能 定价 官网` : "飞书 B2B SaaS 功能 定价 官网";
      setSearchTestResult(await api.testSearch(query));
      await loadCollectorStatus();
    } finally {
      setSearchTesting(false);
    }
  }

  const collectorDiagnostics = latestCollectorDiagnostics(traces) ?? workflowSummary?.collector_diagnostics;
  const llmStatusLabel = !llmStatus
    ? "LLM 状态未知"
    : !llmStatus.api_key_configured
      ? "未配置 API Key"
      : llmStatus.last_check_status === "success"
        ? "测试成功"
        : llmStatus.last_check_status === "failed"
          ? "测试失败"
          : "已配置，未测试";
  const searchStatusLabel = !collectorStatus
    ? "搜索状态未知"
    : !collectorStatus.api_key_configured
      ? "未配置"
      : searchTestResult?.success
        ? "测试成功"
        : searchTestResult && !searchTestResult.success
          ? "测试失败"
          : "已配置";

  return (
    <main className="min-h-screen">
      <header className="border-b border-line bg-ink px-5 py-4 text-white">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">CIS 多 Agent 竞品分析系统</h1>
            <p className="text-sm text-slate-300">基于 LangGraph、证据绑定、增量补采和质量检查的竞品分析流程</p>
          </div>
          {task && <div className="flex items-center gap-3 text-sm"><span>当前任务：{task.task_id}</span><Pill value={task.status} /></div>}
        </div>
      </header>

      <div className="p-4">
        <div className="mb-4">
          <TaskForm onCreated={handleCreated} />
        </div>

        {task && (
          <section className="mb-4 border border-line bg-white p-4 text-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="font-semibold">任务规划方式</h2>
                <p className="mt-1 text-xs text-slate-500">
                  自动规划调用 Planner LLM；手动规划直接使用你配置的维度、调研问题和查询词。
                </p>
              </div>
              <div className="inline-flex border border-line bg-panel p-1">
                <button
                  type="button"
                  className={`px-3 py-1.5 text-sm font-semibold ${
                    !useCollectionPlanOverride ? "bg-white text-accent shadow-sm" : "text-slate-600"
                  }`}
                  onClick={() => {
                    setUseCollectionPlanOverride(false);
                    setCollectionPlanOverrideError(undefined);
                    setManualPlanConfirmed(false);
                  }}
                >
                  自动规划
                </button>
                <button
                  type="button"
                  className={`px-3 py-1.5 text-sm font-semibold ${
                    useCollectionPlanOverride ? "bg-white text-accent shadow-sm" : "text-slate-600"
                  }`}
                  onClick={() => {
                    setUseCollectionPlanOverride(true);
                    setEditableCollectionPlan((current) => current ?? emptyEditablePlan(task));
                    setCollectionPlanOverrideError(undefined);
                    setManualPlanConfirmed(false);
                  }}
                >
                  手动规划
                </button>
              </div>
            </div>

            {useCollectionPlanOverride && (
              <div className="mt-4 border-t border-line pt-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                  <div className="text-xs text-slate-600">
                    首轮不会调用 Planner LLM；人工计划会保存为第 1 轮，后续 QA 仍可调用 Planner 生成增量补采词。
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="border border-line bg-white px-3 py-1.5 text-xs font-semibold"
                      onClick={() => {
                        const plan = workflowSummary?.collection_plan ?? workflowSummary?.planner_collection_plan_original;
                        if (plan) {
                          setEditableCollectionPlan(
                            editablePlanFromCollectionPlan(plan, collectorDefaultsForTask(task)),
                          );
                          setCollectionPlanOverrideError(undefined);
                          setManualPlanConfirmed(false);
                        }
                      }}
                    >
                      使用当前计划填充
                    </button>
                    <button
                      type="button"
                      className="border border-line bg-white px-3 py-1.5 text-xs font-semibold"
                      onClick={() => {
                        setEditableCollectionPlan(emptyEditablePlan(task));
                        setCollectionPlanOverrideError(undefined);
                        setManualPlanConfirmed(false);
                      }}
                    >
                      新建空白计划
                    </button>
                  </div>
                </div>
                <CollectionPlanEditor
                  value={editableCollectionPlan}
                  defaults={collectorDefaultsForTask(task)}
                  disabled={manualPlanConfirmed}
                  onChange={(nextPlan) => {
                    setEditableCollectionPlan(nextPlan);
                    setCollectionPlanOverrideError(undefined);
                    setManualPlanConfirmed(false);
                  }}
                />
                <div className="mt-3 flex flex-wrap items-center justify-end gap-2 border-t border-line pt-3">
                  {manualPlanConfirmed ? (
                    <>
                      <span className="mr-auto text-xs font-semibold text-success">人工计划已确认</span>
                      <button
                        type="button"
                        className="border border-line bg-white px-3 py-2 text-sm font-semibold"
                        onClick={() => setManualPlanConfirmed(false)}
                      >
                        继续编辑
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="bg-accent px-4 py-2 text-sm font-semibold text-white"
                      onClick={() => {
                        try {
                          buildRunOverrides(editableCollectionPlan, task);
                          setManualPlanConfirmed(true);
                          setCollectionPlanOverrideError(undefined);
                        } catch (error) {
                          setCollectionPlanOverrideError(
                            error instanceof Error ? error.message : String(error),
                          );
                        }
                      }}
                    >
                      确认人工计划
                    </button>
                  )}
                </div>
                {collectionPlanOverrideError && (
                  <div className="mt-2 border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
                    手动规划配置错误：{collectionPlanOverrideError}
                  </div>
                )}
              </div>
            )}
          </section>
        )}

        <div className="mb-4">
          <TaskList tasks={tasks} currentTaskId={task?.task_id} onSelect={selectTask} />
        </div>

        {task && (
          <div className="mb-4 flex flex-wrap items-center gap-3 rounded border border-line bg-white p-3 text-sm">
            <span className="font-semibold">当前任务：{task.task_id}</span>
            <Pill value={task.status} />
            <span className="text-slate-600">名称：{task.product_name}</span>
            <span className="text-slate-600">竞品：{task.competitors.join("、")}</span>
          </div>
        )}

        {task && runs.length > 0 && (
          <section className="mb-4 rounded border border-line bg-white p-3">
            <div className="mb-2 flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold">Run History</h2>
              <span className="text-xs text-slate-500">当前查看：{selectedRunId ?? "latest"}</span>
            </div>
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {runs.map((run) => (
                <button
                  key={run.run_id}
                  type="button"
                  onClick={() => refresh(task.task_id, run.run_id)}
                  className={`rounded border px-3 py-2 text-left text-xs ${selectedRunId === run.run_id ? "border-accent bg-blue-50" : "border-line bg-panel"}`}
                >
                  <div className="font-semibold">{run.run_id.slice(0, 14)}</div>
                  <div className="mt-1 text-slate-600">{new Date(run.started_at).toLocaleString()}</div>
                  <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1">
                    <span>{run.workflow_engine}</span>
                    <span>{run.collector_mode}</span>
                    <span>{run.analyst_mode}</span>
                    <span>{run.writer_mode}</span>
                  </div>
                  <div className="mt-1">final_status: {run.final_status ?? run.status}</div>
                  <div>elapsed: {run.elapsed_time_ms ?? 0}ms</div>
                </button>
              ))}
            </div>
          </section>
        )}

        <div className="mb-4 flex flex-wrap items-center gap-3">
          <select
            className="rounded border border-line bg-white px-3 py-2 text-sm"
            value={collectorMode}
            onChange={(event) => setCollectorMode(event.target.value as "mock" | "web")}
          >
            <option value="web">联网采集</option>
            <option value="mock">本地备用采集</option>
          </select>
          <span className="rounded border border-line bg-white px-3 py-2 text-sm font-semibold text-accent">
            LangGraph 主流程
          </span>
          <span className="rounded border border-line bg-white px-3 py-2 text-sm">
            LLM：{llmStatusLabel}
          </span>
          <span className="rounded border border-line bg-white px-3 py-2 text-sm">
            搜索：{searchStatusLabel}
          </span>
          <button
            type="button"
            onClick={testLlm}
            disabled={llmTesting}
            className="rounded border border-line bg-white px-3 py-2 text-sm font-semibold disabled:opacity-50"
          >
            {llmTesting ? "测试中..." : "测试 LLM 连接"}
          </button>
          <button
            type="button"
            onClick={testSearch}
            disabled={searchTesting}
            className="rounded border border-line bg-white px-3 py-2 text-sm font-semibold disabled:opacity-50"
          >
            {searchTesting ? "测试中..." : "测试搜索连接"}
          </button>
          <button onClick={run} disabled={!task || busy} className="inline-flex items-center gap-2 rounded bg-accent px-4 py-2 font-semibold text-white disabled:opacity-50">
            <Play size={16} /> 运行竞品分析流程
          </button>
          <span className="text-sm text-slate-600">执行当前主流程，并生成 Evidence、问题回答、结构化报告、QA 和 Trace。</span>
        </div>

        {!workflowSummary?.debug_stage && <KnowledgeHitsPanel workflowSummary={workflowSummary} />}

        <div className="space-y-4">
          <DagView dag={dag} traces={traces} qaRouteTo={qa?.route_to} running={busy} debugStage={workflowSummary?.debug_stage ?? runStage} progress={workflowProgress} />
          <PlannerSummaryCard workflowSummary={workflowSummary} plannerAttempts={plannerAttempts} />
          {workflowSummary?.debug_stage === "collector_only" && (
            <>
              <details className="rounded border border-line bg-white">
                <summary className="cursor-pointer list-none p-4">
                  <h2 className="text-lg font-semibold">Collector 采集结果</h2>
                  <p className="mt-1 text-xs text-slate-500">点击展开采集统计</p>
                </summary>
                <div className="border-t border-line p-4">
                <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
                  <DebugMetric label="搜索 query 总数" value={collectorDiagnostics?.query_count ?? 0} />
                  <DebugMetric label="Evidence 总数" value={collectorDiagnostics?.evidence_count ?? evidence.length} />
                  <DebugMetric label="失败 query 数" value={collectorDiagnostics?.failed_queries?.length ?? 0} />
                  <DebugMetric label="采集计划来源" value={collectorDiagnostics?.collector_search_plan_source ?? "-"} />
                </div>
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                  <MetricMap title="按竞品统计" values={collectorDiagnostics?.evidence_count_by_competitor} />
                  <MetricMap title="按维度统计" values={collectorDiagnostics?.evidence_count_by_dimension} />
                </div>
                {!!collectorDiagnostics?.failed_queries?.length && (
                  <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm">
                    <div className="font-semibold">失败查询</div>
                    <div className="mt-1">{collectorDiagnostics.failed_queries.join("、")}</div>
                  </div>
                )}
                </div>
              </details>
              <EvidencePanel
                evidence={evidence}
                evidenceIds={selectedEvidenceIds}
                selectable={manualEvidenceSelectionEnabled}
                selectedManualEvidenceIds={manualSelectedEvidenceIds}
                onManualEvidenceSelectionChange={setManualSelectedEvidenceIds}
              />
              <EvidenceQuestionAnswerPanel
                workflowSummary={workflowSummary}
                onEvidenceIdsSelect={setSelectedEvidenceIds}
              />
              <QaPanel qa={qa} workflowSummary={workflowSummary} />
            </>
          )}
          <KnowledgeView report={report} evidence={evidence} onEvidenceIdsSelect={(ids) => {
            setSelectedFact(undefined);
            setSelectedEvidenceIds(ids);
          }} />
          {!report && evidence.length > 0 && (
            <EvidencePanel evidence={evidence} evidenceIds={selectedEvidenceIds} />
          )}
          <ReportView report={report} evidence={evidence} competitors={task?.competitors} selectedFact={selectedFact} selectedEvidenceIds={selectedEvidenceIds} onSelect={(fact) => {
            setSelectedFact(fact);
            setSelectedEvidenceIds(fact.evidence_ids);
          }} />
          <QaPanel qa={qa} workflowSummary={workflowSummary} />
          <TraceViewer traces={traces} />
        </div>
      </div>
    </main>
  );
}

type EditableCollectorDefaults = {
  max_results_per_query: number;
  max_evidence_per_dimension: number;
  min_valid_evidence_required: number;
};

function collectorDefaultsForTask(task: Task): EditableCollectorDefaults {
  if (task.collection_strategy_mode === "simple") {
    return {
      max_results_per_query: 3,
      max_evidence_per_dimension: 5,
      min_valid_evidence_required: 1,
    };
  }
  if (task.collection_strategy_mode === "expert") {
    return {
      max_results_per_query: 8,
      max_evidence_per_dimension: 20,
      min_valid_evidence_required: 3,
    };
  }
  return {
    max_results_per_query: 5,
    max_evidence_per_dimension: 10,
    min_valid_evidence_required: 2,
  };
}

function editablePlanFromCollectionPlan(
  plan: CollectionPlan,
  defaults: EditableCollectorDefaults,
): EditableCollectionPlan {
  return Object.fromEntries(
    Object.entries(plan.collector_search_plan ?? {}).map(([competitor, dimensions]) => [
      competitor,
      Object.fromEntries(
        Object.entries(dimensions ?? {}).map(([dimensionKey, item]) => [
          dimensionKey,
          {
            enabled: true,
            dimension_id: item.dimension_id ?? dimensionKey,
            label: item.label ?? item.dimension_id ?? dimensionKey,
            queries: [...(item.queries ?? [])],
            research_goals: [...(item.research_goals ?? [])],
            source: item.source,
            max_results_per_query: defaults.max_results_per_query,
            max_evidence_per_dimension: defaults.max_evidence_per_dimension,
            min_valid_evidence_required: defaults.min_valid_evidence_required,
            include_domains: "",
            exclude_domains: "",
          },
        ]),
      ),
    ]),
  );
}

function emptyEditablePlan(task: Task): EditableCollectionPlan {
  return Object.fromEntries(task.competitors.map((competitor) => [competitor, {}]));
}

function buildRunOverrides(editablePlan: EditableCollectionPlan | undefined, task: Task): RunTaskOverrides {
  const collectionPlan = buildCollectionPlanOverride(editablePlan);
  return {
    collection_plan_override: collectionPlan,
    collector_config: buildCollectorConfig(editablePlan, collectorDefaultsForTask(task)),
    skip_initial_planner: true,
  };
}

function buildCollectionPlanOverride(editablePlan?: EditableCollectionPlan): CollectionPlan {
  if (!editablePlan) {
    throw new Error("请先创建人工计划并新增至少一个维度。");
  }
  const collectorSearchPlan: CollectionPlan["collector_search_plan"] = {};
  for (const [competitor, dimensions] of Object.entries(editablePlan)) {
    for (const [dimensionKey, item] of Object.entries(dimensions)) {
      const dimensionId = item.dimension_id.trim() || dimensionKey;
      const queries = item.queries.map((query) => query.trim()).filter(Boolean);
      if (!item.enabled || !queries.length) continue;
      collectorSearchPlan[competitor] = collectorSearchPlan[competitor] ?? {};
      collectorSearchPlan[competitor][dimensionId] = {
        dimension_id: dimensionId,
        label: item.label.trim() || dimensionId,
        queries,
        research_goals: item.research_goals.map((goal) => goal.trim()).filter(Boolean),
        source: "manual_override",
      };
    }
  }
  if (!Object.keys(collectorSearchPlan).length) {
    throw new Error("至少启用一个维度，并保留一条查询词。");
  }
  return { collector_search_plan: collectorSearchPlan };
}

function buildCollectorConfig(
  editablePlan: EditableCollectionPlan | undefined,
  defaults: EditableCollectorDefaults,
): CollectorConfig {
  if (!editablePlan) {
    throw new Error("请先创建人工计划并新增至少一个维度。");
  }
  const overrides: NonNullable<CollectorConfig["overrides"]> = [];
  for (const [competitor, dimensions] of Object.entries(editablePlan)) {
    for (const [dimensionKey, item] of Object.entries(dimensions)) {
      if (!item.enabled) continue;
      const dimensionId = item.dimension_id.trim() || dimensionKey;
      overrides.push({
        competitor,
        dimension_id: dimensionId,
        max_results_per_query: positiveNumber(item.max_results_per_query, defaults.max_results_per_query),
        max_evidence_per_dimension: positiveNumber(item.max_evidence_per_dimension, defaults.max_evidence_per_dimension),
        min_valid_evidence_required: positiveNumber(item.min_valid_evidence_required, defaults.min_valid_evidence_required),
        include_domains: splitDomainList(item.include_domains),
        exclude_domains: splitDomainList(item.exclude_domains),
      });
    }
  }
  return {
    default: {
      max_results_per_query: defaults.max_results_per_query,
      max_evidence_per_dimension: defaults.max_evidence_per_dimension,
      min_valid_evidence_required: defaults.min_valid_evidence_required,
      include_domains: [],
      exclude_domains: [],
    },
    overrides,
  };
}

function positiveNumber(value: number, fallback: number) {
  return Number.isFinite(value) && value > 0 ? Math.round(value) : fallback;
}

function splitDomainList(value: string) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

function CollectionPlanEditor({
  value,
  onChange,
  defaults,
  disabled = false,
}: {
  value?: EditableCollectionPlan;
  onChange: (nextPlan: EditableCollectionPlan) => void;
  defaults: EditableCollectorDefaults;
  disabled?: boolean;
}) {
  if (!value) {
    return (
      <div className="rounded border border-dashed border-line bg-panel px-3 py-6 text-center text-sm text-slate-500">
        点击“新建空白计划”后，为每个竞品新增维度。
      </div>
    );
  }
  const plan = value;

  function updateDimension(competitor: string, dimensionKey: string, patch: Partial<EditableCollectionPlanItem>) {
    if (disabled) return;
    onChange({
      ...plan,
      [competitor]: {
        ...plan[competitor],
        [dimensionKey]: {
          ...plan[competitor][dimensionKey],
          ...patch,
        },
      },
    });
  }

  function addDimension(competitor: string) {
    if (disabled) return;
    const dimensions = plan[competitor] ?? {};
    const nextIndex = Object.keys(dimensions).length + 1;
    const dimensionKey = `custom_dimension_${nextIndex}`;
    onChange({
      ...plan,
      [competitor]: {
        ...dimensions,
        [dimensionKey]: {
          enabled: true,
          dimension_id: dimensionKey,
          label: "自定义维度",
          queries: [`${competitor} 自定义维度 官方`],
          research_goals: [],
          source: "manual_override",
          max_results_per_query: defaults.max_results_per_query,
          max_evidence_per_dimension: defaults.max_evidence_per_dimension,
          min_valid_evidence_required: defaults.min_valid_evidence_required,
          include_domains: "",
          exclude_domains: "",
        },
      },
    });
  }

  function deleteDimension(competitor: string, dimensionKey: string) {
    if (disabled) return;
    const nextDimensions = { ...plan[competitor] };
    delete nextDimensions[dimensionKey];
    onChange({
      ...plan,
      [competitor]: nextDimensions,
    });
  }

  return (
    <div className="space-y-4">
      {Object.entries(value).map(([competitor, dimensions]) => (
        <div key={competitor} className="rounded border border-line bg-panel p-3">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="font-semibold">{competitor}</div>
            <button
              type="button"
              disabled={disabled}
              className="border border-line bg-white px-3 py-1.5 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => addDimension(competitor)}
            >
              新增维度
            </button>
          </div>
          <div className="space-y-3">
            {Object.entries(dimensions).map(([dimensionKey, item]) => (
              <div key={dimensionKey} className="relative rounded border border-line bg-white p-3">
                <button
                  type="button"
                  disabled={disabled}
                  className="absolute right-2 top-2 inline-flex h-7 w-7 items-center justify-center border border-red-200 bg-red-50 text-danger hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-40"
                  onClick={() => deleteDimension(competitor, dimensionKey)}
                  title="删除维度"
                  aria-label={`删除 ${item.label || item.dimension_id}`}
                >
                  <X size={15} />
                </button>
                <div className="grid gap-2 pr-9 lg:grid-cols-[80px_1fr_1fr]">
                  <label className="inline-flex items-center gap-2 text-sm font-semibold">
                    <input
                      type="checkbox"
                      disabled={disabled}
                      checked={item.enabled}
                      onChange={(event) => updateDimension(competitor, dimensionKey, { enabled: event.target.checked })}
                    />
                    启用
                  </label>
                  <input
                    disabled={disabled}
                    className="rounded border border-line px-3 py-2 text-sm"
                    value={item.dimension_id}
                    onChange={(event) => updateDimension(competitor, dimensionKey, { dimension_id: event.target.value })}
                    placeholder="dimension_id"
                  />
                  <input
                    disabled={disabled}
                    className="rounded border border-line px-3 py-2 text-sm"
                    value={item.label}
                    onChange={(event) => updateDimension(competitor, dimensionKey, { label: event.target.value })}
                    placeholder="维度名称"
                  />
                </div>
                <textarea
                  disabled={disabled}
                  className="mt-2 h-24 w-full rounded border border-line px-3 py-2 font-mono text-xs"
                  value={item.queries.join("\n")}
                  onChange={(event) => updateDimension(competitor, dimensionKey, { queries: event.target.value.split("\n") })}
                  placeholder="每行一条查询词"
                />
                <textarea
                  disabled={disabled}
                  className="mt-2 h-16 w-full rounded border border-line px-3 py-2 text-xs"
                  value={item.research_goals.join("\n")}
                  onChange={(event) => updateDimension(competitor, dimensionKey, { research_goals: event.target.value.split("\n") })}
                  placeholder="每行一个调研问题，EvidenceAnalyst 将逐题回答"
                />
                <div className="mt-3 grid gap-2 md:grid-cols-3">
                  <label className="text-xs text-slate-600">
                    每条 query 返回结果数
                    <input
                      type="number"
                      disabled={disabled}
                      min={1}
                      max={50}
                      className="mt-1 w-full rounded border border-line px-3 py-2 text-sm"
                      value={item.max_results_per_query}
                      onChange={(event) => updateDimension(competitor, dimensionKey, { max_results_per_query: Number(event.target.value) })}
                    />
                  </label>
                  <label className="text-xs text-slate-600">
                    本维度最多 evidence 数
                    <input
                      type="number"
                      disabled={disabled}
                      min={1}
                      max={50}
                      className="mt-1 w-full rounded border border-line px-3 py-2 text-sm"
                      value={item.max_evidence_per_dimension}
                      onChange={(event) => updateDimension(competitor, dimensionKey, { max_evidence_per_dimension: Number(event.target.value) })}
                    />
                  </label>
                  <label className="text-xs text-slate-600">
                    QA 至少有效 evidence 数
                    <input
                      type="number"
                      disabled={disabled}
                      min={1}
                      max={50}
                      className="mt-1 w-full rounded border border-line px-3 py-2 text-sm"
                      value={item.min_valid_evidence_required}
                      onChange={(event) => updateDimension(competitor, dimensionKey, { min_valid_evidence_required: Number(event.target.value) })}
                    />
                  </label>
                </div>
                <div className="mt-2 grid gap-2 md:grid-cols-2">
                  <label className="text-xs text-slate-600">
                    include domains
                    <textarea
                      disabled={disabled}
                      className="mt-1 h-16 w-full rounded border border-line px-3 py-2 font-mono text-xs"
                      value={item.include_domains}
                      onChange={(event) => updateDimension(competitor, dimensionKey, { include_domains: event.target.value })}
                      placeholder="example.com, docs.example.com"
                    />
                  </label>
                  <label className="text-xs text-slate-600">
                    exclude domains
                    <textarea
                      disabled={disabled}
                      className="mt-1 h-16 w-full rounded border border-line px-3 py-2 font-mono text-xs"
                      value={item.exclude_domains}
                      onChange={(event) => updateDimension(competitor, dimensionKey, { exclude_domains: event.target.value })}
                      placeholder="reddit.com, pinterest.com"
                    />
                  </label>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function latestCollectorDiagnostics(traces: TraceRecord[]): CollectorDiagnostics | undefined {
  const trace = [...traces].reverse().find((item) => item.agent_name === "CollectorAgent");
  if (!trace?.output_summary) return undefined;
  try {
    const parsed = JSON.parse(trace.output_summary) as CollectorDiagnostics;
    return parsed.collector_mode_requested ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function recoverWorkflowSummary(traces: TraceRecord[]): WorkflowSummary | undefined {
  const trace = [...traces].reverse().find((item) => item.agent_name === "WorkflowEngine" && item.output_summary);
  if (!trace?.output_summary) return undefined;
  try {
    const parsed = JSON.parse(trace.output_summary) as WorkflowSummary;
    return parsed.workflow_engine_used ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function DebugMetric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded border border-line bg-panel p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 font-semibold">{value}</div>
    </div>
  );
}

function MetricMap({ title, values }: { title: string; values?: Record<string, number> }) {
  return (
    <div className="rounded border border-line bg-panel p-3 text-sm">
      <div className="mb-2 font-semibold">{title}</div>
      <div className="flex flex-wrap gap-2">
        {Object.entries(values ?? {}).map(([key, value]) => (
          <span key={key} className="rounded border border-line bg-white px-2 py-1">
            {key}：{value}
          </span>
        ))}
        {!Object.keys(values ?? {}).length && <span className="text-slate-500">暂无数据</span>}
      </div>
    </div>
  );
}

function EvidenceQuestionAnswerPanel({
  workflowSummary,
  onEvidenceIdsSelect,
}: {
  workflowSummary?: WorkflowSummary;
  onEvidenceIdsSelect: (ids: string[]) => void;
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(3);
  const output = workflowSummary?.evidence_analyst_output;
  const results = output?.question_results ?? [];
  const diagnostics = output?.diagnostics;
  const answerCount = diagnostics?.question_answer_count
    ?? results.reduce((sum, item) => sum + (item.question_answers?.length ?? 0), 0);
  const answeredCount = diagnostics?.answered_question_count
    ?? results.reduce(
      (sum, item) => sum + item.question_answers.filter((answer) => answer.answer_status === "answered").length,
      0,
    );
  const enabled = diagnostics?.evidence_analyst_enabled;
  const totalPages = Math.max(1, Math.ceil(results.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pagedResults = results.slice((safePage - 1) * pageSize, safePage * pageSize);

  useEffect(() => {
    setPage(1);
  }, [results.length, pageSize]);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  if (!output && !workflowSummary?.node_sequence?.includes("evidence_analyst")) {
    return null;
  }

  return (
    <details className="rounded border border-line bg-white">
      <summary className="cursor-pointer list-none p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Evidence 问题回答结果</h2>
          <p className="mt-1 text-sm text-slate-600">
            共 {results.length} 个竞品维度组，点击展开查看问题与答案
          </p>
        </div>
        {enabled === false && (
          <span className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-semibold text-warning">
            未启用 LLM 回答
          </span>
        )}
        </div>
      </summary>
      <div className="border-t border-line p-4">
      <div className="mb-3 flex flex-wrap items-start justify-end gap-3">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-500">每页</span>
          <select
            className="rounded border border-line bg-white px-2 py-1"
            value={pageSize}
            onChange={(event) => setPageSize(Number(event.target.value))}
          >
            <option value={3}>3 组</option>
            <option value={5}>5 组</option>
            <option value={10}>10 组</option>
          </select>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
        <DebugMetric label="Evidence 总数" value={diagnostics?.total_evidence ?? 0} />
        <DebugMetric label="竞品维度组" value={diagnostics?.target_group_count ?? results.length} />
        <DebugMetric label="问题回答数" value={answerCount} />
        <DebugMetric label="已回答问题" value={answeredCount} />
      </div>

      {diagnostics?.skip_reason && (
        <div className="mt-3 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-warning">
          {diagnostics.skip_reason}
        </div>
      )}

      <div className="mt-4 space-y-3">
        {pagedResults.map((item) => (
          <details
            key={`${item.competitor ?? "unknown"}-${item.dimension_id ?? "unknown"}`}
            className="rounded border border-line bg-panel p-3"
          >
            <summary className="cursor-pointer">
              <div className="inline-flex flex-wrap items-center gap-2">
                <span className="font-semibold">{item.competitor ?? "-"}</span>
                <span className="rounded border border-line bg-white px-2 py-0.5 text-xs">{item.dimension_id ?? "-"}</span>
                <span className="text-xs text-slate-500">{item.question_answers.length} questions</span>
              </div>
            </summary>
            <div className="mt-3 space-y-3">
              {item.dimension_goal && (
                <div className="rounded border border-line bg-white p-3 text-sm">
                  <div className="mb-1 text-xs font-semibold text-slate-500">维度目标</div>
                  {item.dimension_goal}
                </div>
              )}
              {item.question_answers.map((answer) => (
                <button
                  key={answer.question_id}
                  type="button"
                  className="block w-full rounded border border-line bg-white p-3 text-left text-sm hover:border-blue-300 hover:bg-blue-50"
                  onClick={() => onEvidenceIdsSelect(answer.evidence_ids)}
                >
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-slate-500">{answer.question_id}</span>
                    <span className={`rounded border px-2 py-0.5 text-xs font-semibold ${answerStatusClass(answer.answer_status)}`}>
                      {answer.answer_status}
                    </span>
                    <span className="text-xs text-slate-500">引用 {answer.evidence_ids.length} 条 evidence</span>
                  </div>
                  <div className="font-semibold">{answer.question}</div>
                  <div className="mt-2 whitespace-pre-wrap text-slate-700">{answer.answer}</div>
                  {!!answer.evidence_ids.length && (
                    <div className="mt-2 text-xs text-accent">
                      evidence: {answer.evidence_ids.join(", ")}
                    </div>
                  )}
                  {!!answer.suggestions?.length && (
                    <div className="mt-2 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-warning">
                      补采建议：{answer.suggestions.join("；")}
                    </div>
                  )}
                </button>
              ))}
              {item.dimension_summary && (
                <div className="rounded border border-line bg-white p-3 text-sm">
                  <div className="mb-1 text-xs font-semibold text-slate-500">维度小结</div>
                  {item.dimension_summary}
                </div>
              )}
              {!!item.warnings.length && (
                <div className="rounded border border-amber-300 bg-amber-50 p-2 text-xs text-warning">
                  warnings: {item.warnings.join(", ")}
                </div>
              )}
            </div>
          </details>
        ))}
        {!results.length && (
          <div className="rounded border border-line bg-panel p-3 text-sm text-slate-500">
            暂无 EvidenceAnalyst 输出。确认运行时 Analyst 模式为 LLM，且 DAG 已执行到 EvidenceAnalystAgent。
          </div>
        )}
      </div>
      {!!results.length && (
        <QuestionAnswerPagination
          page={safePage}
          totalPages={totalPages}
          totalItems={results.length}
          pageSize={pageSize}
          onPageChange={setPage}
        />
      )}
      </div>
    </details>
  );
}

function QuestionAnswerPagination({
  page,
  totalPages,
  totalItems,
  pageSize,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  totalItems: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}) {
  const start = totalItems ? (page - 1) * pageSize + 1 : 0;
  const end = Math.min(totalItems, page * pageSize);
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3 text-xs text-slate-600">
      <span>
        显示 {start}-{end} / {totalItems} 组
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded border border-line bg-white px-2 py-1 font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          disabled={page <= 1}
          onClick={() => onPageChange(1)}
        >
          首页
        </button>
        <button
          type="button"
          className="rounded border border-line bg-white px-2 py-1 font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          上一页
        </button>
        <span className="rounded border border-line bg-panel px-2 py-1">
          {page} / {totalPages}
        </span>
        <button
          type="button"
          className="rounded border border-line bg-white px-2 py-1 font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </button>
        <button
          type="button"
          className="rounded border border-line bg-white px-2 py-1 font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          disabled={page >= totalPages}
          onClick={() => onPageChange(totalPages)}
        >
          末页
        </button>
      </div>
    </div>
  );
}

function answerStatusClass(value: string) {
  if (value === "answered") return "border-green-300 bg-green-50 text-success";
  if (value === "partial") return "border-amber-300 bg-amber-50 text-warning";
  return "border-slate-300 bg-slate-50 text-slate-600";
}
