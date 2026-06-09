import { Play } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "./api/client";
import { apiRecorder, getApiRecorderSnapshot } from "./api/recorder";
import { DagView } from "./components/DagView";
import { DemoGuide } from "./components/DemoGuide";
import { EvidencePanel } from "./components/EvidencePanel";
import { KnowledgeHitsPanel } from "./components/KnowledgeHitsPanel";
import { KnowledgeView } from "./components/KnowledgeView";
import { PlannerSummaryCard } from "./components/PlannerSummaryCard";
import { QaPanel } from "./components/QaPanel";
import { ReportView } from "./components/ReportView";
import { TaskForm } from "./components/TaskForm";
import { TaskList } from "./components/TaskList";
import { TraceViewer } from "./components/TraceViewer";
import type { CollectionPlan, CollectorConfig, CollectorDiagnostics, CollectorStatus, Dag, DemoMode, DimensionResult, Evidence, LlmStatus, PlannerAttempt, PlannerRunResult, QaResult, Report, RunTaskOverrides, SearchTestResult, Task, TaskRun, TraceRecord, WorkflowProgress, WriterDiagnostics, WorkflowSummary } from "./types";
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
  const [demoMode, setDemoMode] = useState<DemoMode>("normal");
  const [autoRework, setAutoRework] = useState(true);
  const [writerMode, setWriterMode] = useState<"mock" | "llm">("llm");
  const [collectorMode, setCollectorMode] = useState<"mock" | "web">("web");
  const [analystMode, setAnalystMode] = useState<"mock" | "evidence" | "llm">("llm");
  const [workflowEngine, setWorkflowEngine] = useState<"custom" | "langgraph">("langgraph");
  const [runStage, setRunStage] = useState<"full" | "planner_only" | "collector_only">("full");
  const [workflowSummary, setWorkflowSummary] = useState<WorkflowSummary>();
  const [workflowProgress, setWorkflowProgress] = useState<WorkflowProgress>();
  const [plannerAttempts, setPlannerAttempts] = useState<PlannerAttempt[]>([]);
  const [llmStatus, setLlmStatus] = useState<LlmStatus>();
  const [collectorStatus, setCollectorStatus] = useState<CollectorStatus>();
  const [llmTesting, setLlmTesting] = useState(false);
  const [searchTesting, setSearchTesting] = useState(false);
  const [searchTestResult, setSearchTestResult] = useState<SearchTestResult>();
  const [apiRecorderSnapshot, setApiRecorderSnapshot] = useState(getApiRecorderSnapshot());
  const [useCollectionPlanOverride, setUseCollectionPlanOverride] = useState(false);
  const [editableCollectionPlan, setEditableCollectionPlan] = useState<EditableCollectionPlan>();
  const [collectionPlanOverrideError, setCollectionPlanOverrideError] = useState<string>();
  const [manualEvidenceSelectionEnabled, setManualEvidenceSelectionEnabled] = useState(false);
  const [manualSelectedEvidenceIds, setManualSelectedEvidenceIds] = useState<string[]>([]);
  const [manualEvidenceSelectionError, setManualEvidenceSelectionError] = useState<string>();

  useEffect(() => {
    loadTasks();
    loadLlmStatus();
    loadCollectorStatus();
  }, []);

  useEffect(() => apiRecorder.subscribe(() => {
    setApiRecorderSnapshot(getApiRecorderSnapshot());
  }), []);

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
    setWorkflowProgress(undefined);
    setWorkflowSummary(undefined);
    setPlannerAttempts([]);
    await refresh(nextTask.task_id);
  }

  async function handleCreated(created: Task) {
    await loadTasks(created.task_id);
  }

  async function run() {
    if (!task) return;
    let runOverrides: RunTaskOverrides | undefined;
    if (useCollectionPlanOverride) {
      try {
        runOverrides = buildRunOverrides(editableCollectionPlan);
        setCollectionPlanOverrideError(undefined);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setCollectionPlanOverrideError(message);
        return;
      }
    }
    const effectiveRunStage = manualEvidenceSelectionEnabled && runStage === "full" ? "collector_only" : runStage;
    const effectiveAutoRework = manualEvidenceSelectionEnabled && runStage === "full" ? false : autoRework;
    const effectiveWorkflowEngine = effectiveRunStage !== "full" ? "langgraph" : workflowEngine;
    const knownRunIds = new Set(runs.map((item) => item.run_id));
    let stopProgressPolling = false;
    setBusy(true);
    setTraces([]);
    setQa(undefined);
    setWorkflowProgress(undefined);
    setManualEvidenceSelectionError(undefined);
    if (manualEvidenceSelectionEnabled && runStage === "full") {
      setManualSelectedEvidenceIds([]);
    }
    const progressPolling = pollRunProgress(
      task.task_id,
      knownRunIds,
      () => stopProgressPolling,
      effectiveRunStage === "planner_only",
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
        effectiveRunStage === "full" ? undefined : effectiveRunStage,
        runOverrides,
      ) as PlannerRunResult;
      setWorkflowSummary(result.workflow_summary);
      const runId = result.workflow_summary?.run_id ?? result.report?.run_id;
      if (effectiveRunStage !== "full") {
        setDag(result.dag ?? result.workflow_summary?.dag);
        setEvidence(effectiveRunStage === "collector_only" ? (result.evidence ?? result.collector_output?.evidence ?? []) : []);
        setQa(effectiveRunStage === "collector_only" ? (result.qa_result ?? undefined) : undefined);
        setReport(undefined);
        setSelectedFact(undefined);
        setSelectedEvidenceIds([]);
        const [nextTasks, nextRuns, nextTraces] = await Promise.all([
          api.listTasks(),
          api.runs(task.task_id),
          runId ? api.runTraces(task.task_id, runId) : Promise.resolve([]),
        ]);
        setTasks(nextTasks);
        setTask(nextTasks.find((item) => item.task_id === task.task_id) ?? task);
        setRuns(nextRuns);
        setSelectedRunId(runId ?? undefined);
        setTraces(nextTraces);
      } else {
        await loadTasks(task.task_id);
        if (runId) {
          await refresh(task.task_id, runId);
        }
      }
      if (!result.report) {
        setReport(undefined);
        setSelectedFact(undefined);
        setSelectedEvidenceIds([]);
      }
      if (demoMode === "qa_missing_evidence") {
        setEvidence([]);
      }
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
          ...buildRunOverrides(editableCollectionPlan),
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
      setRunStage("full");
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
          const nextTraces = await api.runTraces(taskId, activeRunId);
          setTraces(nextTraces);
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

  const writerDiagnostics = report?.json_report.writer_diagnostics as WriterDiagnostics | undefined;
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
  const writerDiagnosticMessage = writerDiagnostics
    ? writerDiagnostics.fallback_used
      ? `已选择 ${writerDiagnostics.writer_mode_requested ?? writerMode} ReportWriter，但本次使用 ${writerDiagnostics.writer_mode_used ?? "mock"}。原因：${writerDiagnostics.llm_fallback_reason ?? writerDiagnostics.llm_error_message ?? "未知"}`
      : writerDiagnostics.writer_mode_used === "llm"
        ? `LLM ReportWriter 调用成功，耗时 ${writerDiagnostics.llm_elapsed_time_ms ?? 0}ms，模型：${writerDiagnostics.llm_model ?? "未记录"}。`
        : `本次使用 ${writerDiagnostics.writer_mode_used ?? "mock"} ReportWriter。`
    : undefined;
  const collectorDiagnosticMessage = collectorDiagnostics?.fallback_used
    ? `Web Collector 调用失败，本次 fallback 到 Mock Evidence。原因：${collectorDiagnostics.fallback_reason ?? "未知"}`
    : collectorDiagnostics?.collector_mode_used === "web"
      ? `Web Collector 调用成功，采集 Evidence ${collectorDiagnostics.evidence_count ?? 0} 条。`
      : undefined;
  const searchStatusLabel = !collectorStatus
    ? "搜索状态未知"
    : !collectorStatus.api_key_configured
      ? "未配置"
      : searchTestResult?.success
        ? "测试成功"
        : searchTestResult && !searchTestResult.success
          ? "测试失败"
          : "已配置";
  const searchTestMessage = searchTestResult
    ? searchTestResult.success
      ? `搜索工具已连接，返回 ${searchTestResult.result_count} 条结果。`
      : `搜索工具调用失败：${searchTestResult.error_type ?? searchTestResult.error_message ?? "未知错误"}，本次 Web Collector 会 fallback 到 Mock。`
    : undefined;

  return (
    <main className="min-h-screen">
      <header className="border-b border-line bg-ink px-5 py-4 text-white">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">CIS 多 Agent 竞品分析系统</h1>
            <p className="text-sm text-slate-300">基于结构化 Schema、证据绑定、QA 打回和 Trace 回放的竞品分析 Demo</p>
          </div>
          {task && <div className="flex items-center gap-3 text-sm"><span>当前任务：{task.task_id}</span><Pill value={task.status} /></div>}
        </div>
      </header>

      <div className="p-4">
        <div className="mb-4 grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)]">
          <TaskForm onCreated={handleCreated} />
          <DemoGuide />
        </div>

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
            value={demoMode}
            onChange={(event) => setDemoMode(event.target.value as DemoMode)}
          >
            <option value="normal">正常流程</option>
            <option value="qa_missing_evidence">QA 失败：缺少证据</option>
            <option value="qa_invalid_extraction">QA 失败：抽取冲突</option>
            <option value="qa_bad_report">QA 失败：报告格式错误</option>
          </select>
          <select
            className="rounded border border-line bg-white px-3 py-2 text-sm"
            value={writerMode}
            onChange={(event) => setWriterMode(event.target.value as "mock" | "llm")}
          >
            <option value="mock">Mock ReportWriter</option>
            <option value="llm">LLM ReportWriter</option>
          </select>
          <select
            className="rounded border border-line bg-white px-3 py-2 text-sm"
            value={collectorMode}
            onChange={(event) => setCollectorMode(event.target.value as "mock" | "web")}
          >
            <option value="mock">Mock Collector</option>
            <option value="web">Web Collector</option>
          </select>
          <select
            className="rounded border border-line bg-white px-3 py-2 text-sm"
            value={analystMode}
            onChange={(event) => setAnalystMode(event.target.value as "mock" | "evidence" | "llm")}
          >
            <option value="mock">Mock Analyst</option>
            <option value="evidence">Evidence-based Analyst</option>
            <option value="llm">LLM Analyst</option>
          </select>
          <select
            className="rounded border border-line bg-white px-3 py-2 text-sm"
            value={workflowEngine}
            onChange={(event) => setWorkflowEngine(event.target.value as "custom" | "langgraph")}
          >
            <option value="custom">Custom Runner</option>
            <option value="langgraph">LangGraph Runner</option>
          </select>
          <select
            className="rounded border border-line bg-white px-3 py-2 text-sm"
            value={runStage}
            onChange={(event) => {
              const nextStage = event.target.value as "full" | "planner_only" | "collector_only";
              setRunStage(nextStage);
              if (nextStage !== "full") {
                setWorkflowEngine("langgraph");
                setAutoRework(false);
              }
            }}
          >
            <option value="full">完整工作流</option>
            <option value="planner_only">只测试 Planner</option>
            <option value="collector_only">测试证据采集链路</option>
          </select>
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
            <Play size={16} /> {runStage === "planner_only"
              ? "只运行 PlannerAgent"
              : runStage === "collector_only"
                ? "运行证据采集链路"
                : "运行 Demo 工作流"}
          </button>
          <label className="inline-flex items-center gap-2 rounded border border-line bg-white px-3 py-2 text-sm">
            <input
              type="checkbox"
              checked={autoRework}
              onChange={(event) => setAutoRework(event.target.checked)}
              disabled={runStage !== "full"}
            />
            auto_rework={autoRework ? "true" : "false"}
          </label>
          <label className="inline-flex items-center gap-2 rounded border border-line bg-white px-3 py-2 text-sm">
            <input
              type="checkbox"
              checked={manualEvidenceSelectionEnabled}
              onChange={(event) => {
                setManualEvidenceSelectionEnabled(event.target.checked);
                setManualEvidenceSelectionError(undefined);
                setManualSelectedEvidenceIds([]);
              }}
              disabled={runStage !== "full"}
            />
            手动选择 Evidence
          </label>
          <span className="text-sm text-slate-600">执行所选 Mock Agent DAG，并生成 DAG、报告、证据、QA 和 Trace。</span>
        </div>

        <section className="mb-4 rounded border border-line bg-white p-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <label className="inline-flex items-center gap-2 font-semibold">
              <input
                type="checkbox"
                checked={useCollectionPlanOverride}
                onChange={(event) => setUseCollectionPlanOverride(event.target.checked)}
              />
              手动覆盖 Collector collection_plan
            </label>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="rounded border border-line bg-white px-3 py-2 text-sm font-semibold"
                onClick={() => {
                  const plan = workflowSummary?.collection_plan ?? workflowSummary?.planner_collection_plan_original;
                  if (plan) {
                    setEditableCollectionPlan(editablePlanFromCollectionPlan(plan));
                    setCollectionPlanOverrideError(undefined);
                  }
                }}
              >
                用当前计划填充
              </button>
              <button
                type="button"
                className="rounded border border-line bg-white px-3 py-2 text-sm font-semibold"
                onClick={() => {
                  setEditableCollectionPlan(undefined);
                  setCollectionPlanOverrideError(undefined);
                }}
              >
                清空
              </button>
            </div>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            启用后，本次运行会让 Collector 和 EvidenceQA 使用这里确认过的维度和查询词。Planner 原始输出仍会保存，便于对比。
          </p>
          {useCollectionPlanOverride && (
            <div className="mt-3">
              <CollectionPlanEditor
                value={editableCollectionPlan}
                onChange={(nextPlan) => {
                  setEditableCollectionPlan(nextPlan);
                  setCollectionPlanOverrideError(undefined);
                }}
              />
              {collectionPlanOverrideError && (
                <div className="mt-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
                  collection_plan 配置错误：{collectionPlanOverrideError}
                </div>
              )}
              {workflowSummary?.manual_collection_plan_override_used && (
                <div className="mt-2 rounded border border-green-200 bg-green-50 px-3 py-2 text-xs text-success">
                  上次运行已使用手动 collection_plan override。
                </div>
              )}
            </div>
          )}
        </section>

        <div className="mb-4 flex flex-wrap items-center gap-3 rounded border border-dashed border-line bg-white p-3 text-sm">
          <span className="font-semibold">API Recorder</span>
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={apiRecorderSnapshot.enabled}
              onChange={(event) => apiRecorder.setEnabled(event.target.checked)}
            />
            enabled
          </label>
          <span>recorded: {apiRecorderSnapshot.records.length}</span>
          <button
            type="button"
            onClick={() => apiRecorder.clear()}
            className="rounded border border-line bg-white px-3 py-2 text-sm font-semibold"
          >
            Clear Recording
          </button>
          <button
            type="button"
            onClick={() => apiRecorder.exportRaw()}
            className="rounded border border-line bg-white px-3 py-2 text-sm font-semibold"
          >
            Export Raw JSON
          </button>
          <button
            type="button"
            onClick={() => apiRecorder.exportGroupedMarkdown()}
            className="rounded border border-line bg-white px-3 py-2 text-sm font-semibold"
          >
            Export Grouped Markdown
          </button>
          <span className="text-xs text-slate-500">
            Records frontend request/response inputs and outputs during your manual session.
          </span>
        </div>

        {(llmStatus || collectorStatus || writerDiagnosticMessage || collectorDiagnosticMessage || workflowSummary) && (
          <div className="mb-4 rounded border border-line bg-white p-3 text-sm">
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              <span>LLM Provider：{llmStatus?.llm_provider ?? "未读取"}</span>
              <span>模型：{llmStatus?.llm_model ?? "未读取"}</span>
              <span>Base URL：{llmStatus?.base_url_configured ? "已配置" : "未配置"}</span>
              <span>API Key：{llmStatus?.api_key_configured ? "已配置" : "未配置"}</span>
              <span>状态：{llmStatusLabel}</span>
              <span>Search Provider：{collectorStatus?.search_provider ?? "未读取"}</span>
              <span>Search API Key：{collectorStatus?.api_key_configured ? "已配置" : "未配置"}</span>
              <span>Web Collector：{collectorStatus?.enabled ? "已启用" : "未启用"}</span>
              <span>搜索状态：{searchStatusLabel}</span>
            </div>
            {llmStatus?.last_error && <div className="mt-2 text-danger">测试错误：{llmStatus.last_error}</div>}
            {searchTestMessage && (
              <div className={`mt-2 rounded border px-3 py-2 ${searchTestResult?.success ? "border-green-300 bg-green-50 text-success" : "border-amber-300 bg-amber-50 text-warning"}`}>
                {searchTestMessage}
              </div>
            )}
            {writerDiagnostics && (
              <div className="mt-2 grid gap-2 md:grid-cols-4">
                <span>requested：{writerDiagnostics.writer_mode_requested ?? "-"}</span>
                <span>used：{writerDiagnostics.writer_mode_used ?? "-"}</span>
                <span>fallback：{writerDiagnostics.fallback_used ? "true" : "false"}</span>
                <span>llm_call：{writerDiagnostics.llm_call_attempted ? (writerDiagnostics.llm_call_success ? "success" : "failed") : "not_attempted"}</span>
              </div>
            )}
            {workflowSummary && (
              <div className="mt-2 rounded border border-line bg-panel px-3 py-2">
                <div className="flex flex-wrap gap-x-5 gap-y-1">
                  <span>Workflow Engine: {workflowSummary.workflow_engine_used ?? "-"}</span>
                  <span>requested: {workflowSummary.workflow_engine_requested ?? "-"}</span>
                  <span>rework_count: {workflowSummary.rework_count ?? 0}</span>
                  <span>final_status: {workflowSummary.final_status ?? "-"}</span>
                  {!!workflowSummary.selected_dimensions?.length && <span>planner dimensions: {workflowSummary.selected_dimensions.join(", ")}</span>}
                  {workflowSummary.workflow_engine_used === "langgraph" && <span className="font-semibold text-accent">LangGraph Runner</span>}
                </div>
                {!!workflowSummary.downstream_guidance?.writer?.length && (
                  <div className="mt-1 text-xs text-slate-600">
                    writer guidance: {workflowSummary.downstream_guidance.writer.slice(0, 3).join(" | ")}
                  </div>
                )}
                {!!workflowSummary.conditional_routes_taken?.length && (
                  <div className="mt-1 text-xs text-slate-600">
                    routes: {workflowSummary.conditional_routes_taken.map((item) => `${item.from_node ?? "qa"} -> ${item.to_node ?? "-"} (${item.reason ?? "qa"})`).join(" | ")}
                  </div>
                )}
                {workflowSummary.evidence_gate_output && (
                  <div className={`mt-2 rounded border px-3 py-2 text-xs ${workflowSummary.evidence_gate_output.evidence_gate_passed ? "border-green-300 bg-green-50 text-success" : "border-amber-300 bg-amber-50 text-warning"}`}>
                    EvidenceGate: {workflowSummary.evidence_gate_output.evidence_gate_passed ? "通过" : "相关证据不足"}
                    {!!workflowSummary.evidence_gate_output.missing_relevant_evidence_competitors?.length && (
                      <span> | missing: {workflowSummary.evidence_gate_output.missing_relevant_evidence_competitors.join(", ")}</span>
                    )}
                    {workflowSummary.evidence_gate_output.suggested_route && <span> | route_to: {workflowSummary.evidence_gate_output.suggested_route}</span>}
                  </div>
                )}
              </div>
            )}
            {writerDiagnosticMessage && (
              <div className={`mt-2 rounded border px-3 py-2 ${writerDiagnostics?.fallback_used ? "border-amber-300 bg-amber-50 text-warning" : "border-green-300 bg-green-50 text-success"}`}>
                {writerDiagnosticMessage}
              </div>
            )}
            {collectorDiagnosticMessage && (
              <div className={`mt-2 rounded border px-3 py-2 ${collectorDiagnostics?.fallback_used ? "border-amber-300 bg-amber-50 text-warning" : "border-green-300 bg-green-50 text-success"}`}>
                {collectorDiagnosticMessage}
                {!!collectorDiagnostics?.effective_queries_preview_by_competitor && (
                  <div className="mt-1 text-xs">
                    planner queries: {Object.entries(collectorDiagnostics.effective_queries_preview_by_competitor).map(([competitor, queries]) => `${competitor}: ${(queries ?? []).slice(0, 2).join(" / ")}`).join(" | ")}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <PlannerSummaryCard workflowSummary={workflowSummary} collectorDiagnostics={collectorDiagnostics} plannerAttempts={plannerAttempts} />
        {!workflowSummary?.debug_stage && <KnowledgeHitsPanel workflowSummary={workflowSummary} />}

        <div className="space-y-4">
          <DagView dag={dag} traces={traces} qaRouteTo={qa?.route_to} running={busy} debugStage={workflowSummary?.debug_stage ?? (runStage === "full" ? undefined : runStage)} progress={workflowProgress} />
          {workflowSummary?.debug_stage === "collector_only" && (
            <>
              <section className="rounded border border-line bg-white p-4">
                <h2 className="mb-3 text-lg font-semibold">Collector 采集结果</h2>
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
              </section>
              {manualEvidenceSelectionEnabled && (
                <section className="rounded border border-blue-200 bg-blue-50 p-4 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <div className="font-semibold text-accent">手动选择 Evidence 已开启</div>
                      <div className="mt-1 text-xs text-slate-600">
                        已选择 {manualSelectedEvidenceIds.length} / {evidence.length} 条。确认后将跳过 EvidenceGate，直接进入 Analyst / Report。
                      </div>
                    </div>
                    <button
                      type="button"
                      className="rounded bg-accent px-4 py-2 font-semibold text-white disabled:opacity-50"
                      disabled={busy || !manualSelectedEvidenceIds.length || !selectedRunId}
                      onClick={continueWithSelectedEvidence}
                    >
                      使用选中 Evidence 继续
                    </button>
                  </div>
                  {manualEvidenceSelectionError && (
                    <div className="mt-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
                      {manualEvidenceSelectionError}
                    </div>
                  )}
                </section>
              )}
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
              <section className="rounded border border-line bg-white p-4">
                <h2 className="mb-3 text-lg font-semibold">调试原始 JSON</h2>
                <DebugJson title="PlannerOutput" value={workflowSummary.planner_output} />
                <DebugJson title="CollectorOutput Schema" value={COLLECTOR_OUTPUT_SCHEMA} />
                <DebugJson title="CollectorOutput" value={workflowSummary.collector_output} />
                <DebugJson title="QaOutput" value={workflowSummary.qa_output} />
              </section>
            </>
          )}
          {!workflowSummary?.debug_stage && (
            <>
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
            </>
          )}
          <TraceViewer traces={traces} />
        </div>
      </div>
    </main>
  );
}

function editablePlanFromCollectionPlan(plan: CollectionPlan): EditableCollectionPlan {
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
            max_results_per_query: 8,
            max_evidence_per_dimension: 3,
            min_valid_evidence_required: 1,
            include_domains: "",
            exclude_domains: "",
          },
        ]),
      ),
    ]),
  );
}

function buildRunOverrides(editablePlan?: EditableCollectionPlan): RunTaskOverrides {
  const collectionPlan = buildCollectionPlanOverride(editablePlan);
  return {
    collection_plan_override: collectionPlan,
    collector_config: buildCollectorConfig(editablePlan),
  };
}

function buildCollectionPlanOverride(editablePlan?: EditableCollectionPlan): CollectionPlan {
  if (!editablePlan) {
    throw new Error("请先用 Planner 当前计划填充后再运行。");
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

function buildCollectorConfig(editablePlan?: EditableCollectionPlan): CollectorConfig {
  if (!editablePlan) {
    throw new Error("请先用 Planner 当前计划填充后再运行。");
  }
  const overrides: NonNullable<CollectorConfig["overrides"]> = [];
  for (const [competitor, dimensions] of Object.entries(editablePlan)) {
    for (const [dimensionKey, item] of Object.entries(dimensions)) {
      if (!item.enabled) continue;
      const dimensionId = item.dimension_id.trim() || dimensionKey;
      overrides.push({
        competitor,
        dimension_id: dimensionId,
        max_results_per_query: positiveNumber(item.max_results_per_query, 8),
        max_evidence_per_dimension: positiveNumber(item.max_evidence_per_dimension, 3),
        min_valid_evidence_required: positiveNumber(item.min_valid_evidence_required, 1),
        include_domains: splitDomainList(item.include_domains),
        exclude_domains: splitDomainList(item.exclude_domains),
      });
    }
  }
  return {
    default: {
      max_results_per_query: 8,
      max_evidence_per_dimension: 3,
      min_valid_evidence_required: 1,
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
}: {
  value?: EditableCollectionPlan;
  onChange: (nextPlan: EditableCollectionPlan) => void;
}) {
  if (!value) {
    return (
      <div className="rounded border border-dashed border-line bg-panel px-3 py-6 text-center text-sm text-slate-500">
        先运行 Planner 或点击“用当前计划填充”，再编辑维度和查询词。
      </div>
    );
  }
  const plan = value;

  function updateDimension(competitor: string, dimensionKey: string, patch: Partial<EditableCollectionPlanItem>) {
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
          max_results_per_query: 8,
          max_evidence_per_dimension: 3,
          min_valid_evidence_required: 1,
          include_domains: "",
          exclude_domains: "",
        },
      },
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
              className="rounded border border-line bg-white px-3 py-1.5 text-xs font-semibold"
              onClick={() => addDimension(competitor)}
            >
              新增维度
            </button>
          </div>
          <div className="space-y-3">
            {Object.entries(dimensions).map(([dimensionKey, item]) => (
              <div key={dimensionKey} className="rounded border border-line bg-white p-3">
                <div className="grid gap-2 lg:grid-cols-[80px_1fr_1fr]">
                  <label className="inline-flex items-center gap-2 text-sm font-semibold">
                    <input
                      type="checkbox"
                      checked={item.enabled}
                      onChange={(event) => updateDimension(competitor, dimensionKey, { enabled: event.target.checked })}
                    />
                    启用
                  </label>
                  <input
                    className="rounded border border-line px-3 py-2 text-sm"
                    value={item.dimension_id}
                    onChange={(event) => updateDimension(competitor, dimensionKey, { dimension_id: event.target.value })}
                    placeholder="dimension_id"
                  />
                  <input
                    className="rounded border border-line px-3 py-2 text-sm"
                    value={item.label}
                    onChange={(event) => updateDimension(competitor, dimensionKey, { label: event.target.value })}
                    placeholder="维度名称"
                  />
                </div>
                <textarea
                  className="mt-2 h-24 w-full rounded border border-line px-3 py-2 font-mono text-xs"
                  value={item.queries.join("\n")}
                  onChange={(event) => updateDimension(competitor, dimensionKey, { queries: event.target.value.split("\n") })}
                  placeholder="每行一条查询词"
                />
                <textarea
                  className="mt-2 h-16 w-full rounded border border-line px-3 py-2 text-xs"
                  value={item.research_goals.join("\n")}
                  onChange={(event) => updateDimension(competitor, dimensionKey, { research_goals: event.target.value.split("\n") })}
                  placeholder="每行一个研究目标，可留空"
                />
                <div className="mt-3 grid gap-2 md:grid-cols-3">
                  <label className="text-xs text-slate-600">
                    每条 query 返回结果数
                    <input
                      type="number"
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
                      className="mt-1 h-16 w-full rounded border border-line px-3 py-2 font-mono text-xs"
                      value={item.include_domains}
                      onChange={(event) => updateDimension(competitor, dimensionKey, { include_domains: event.target.value })}
                      placeholder="example.com, docs.example.com"
                    />
                  </label>
                  <label className="text-xs text-slate-600">
                    exclude domains
                    <textarea
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
    <section className="rounded border border-line bg-white p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Evidence 问题回答结果</h2>
          <p className="mt-1 text-sm text-slate-600">
            按竞品和维度汇总 evidence，回答 Planner 规划的问题。点击问题会联动下方 Evidence 面板。
          </p>
        </div>
        {enabled === false && (
          <span className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-semibold text-warning">
            未启用 LLM 回答
          </span>
        )}
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
            open
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
    </section>
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

function DebugJson({ title, value }: { title: string; value: unknown }) {
  return (
    <details className="mb-2 rounded border border-line bg-panel p-3">
      <summary className="cursor-pointer font-semibold">{title}</summary>
      <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded border border-line bg-white p-3 text-xs">
        {JSON.stringify(value ?? {}, null, 2)}
      </pre>
    </details>
  );
}

const COLLECTOR_OUTPUT_SCHEMA = {
  CollectorOutput: {
    evidence: [
      {
        evidence_id: "string; generated if missing",
        run_id: "string | null; bound by current TaskRun",
        competitor: "string | null; planned competitor bucket",
        source_type: "web | public_web | knowledge_base | document | pricing_page | review | interview | survey",
        url: "string | null; required unless local_ref exists",
        local_ref: "string | null; required unless url exists",
        collected_at: "ISO datetime",
        snippet: "string; non-empty source summary",
        confidence: "number; 0..1",
        source_domain: "string | null",
        source_quality: "official | documentation | media | review | unknown | low_quality",
        relevance_score: "number; 0..1",
        relevance_level: "high | medium | low | unrelated",
        relevance_reason: "string",
        entity_match_signals: {
          collector_query: "string; query actually used",
          collector_dimension: "string; dimension from Planner collection_plan",
          planner_dimension: "string; dimension propagated for QA",
          collector_query_source: "planner_collection_plan",
          collector_query_intent: "string | null",
          collector_query_source_detail: "string | null",
          confidence_breakdown: "object; optional confidence details",
          "...": "entity resolver and relevance matching signals",
        },
        content_mode: "snippet | page",
        page_fetch_success: "boolean",
        page_title: "string | null",
        content_excerpt: "string | null",
        content_chars: "number | null",
        fetch_status_code: "number | null",
        page_fetch_error: "string | null",
        fetched_at: "ISO datetime | null",
      },
    ],
    diagnostics: {
      collector_mode_requested: "mock | web",
      collector_mode_used: "mock | web",
      search_provider: "string",
      search_base_url_configured: "boolean",
      has_search_api_key: "boolean",
      web_search_attempted: "boolean",
      web_search_success: "boolean",
      collection_plan_used: "boolean",
      collector_search_plan_source: "planner_collection_plan",
      collector_search_plan_missing: "boolean",
      collector_search_plan_used: "boolean",
      entity_aliases_used: "boolean",
      competitor_aliases_by_competitor: "Record<competitor, aliases[]>",
      planned_query_count_by_competitor: "Record<competitor, number>",
      effective_query_count_by_competitor: "Record<competitor, number>",
      effective_queries_preview_by_competitor: "Record<competitor, queries[]>",
      skipped_queries_by_competitor: "Record<competitor, skipped query metadata[]>",
      query_policy: ["planner_collection_plan"],
      query_dimensions_by_competitor: "Record<competitor, query dimension metadata[]>",
      query_count: "number",
      query_count_by_competitor: "Record<competitor, number>",
      query_count_by_dimension: "Record<dimension_id, number>",
      failed_queries: "string[]",
      evidence_count: "number",
      evidence_count_by_competitor: "Record<competitor, number>",
      evidence_count_by_dimension: "Record<dimension_id, number>",
      evidence_count_by_dimension_by_competitor: "Record<competitor, Record<dimension_id, number>>",
      raw_evidence_count: "number",
      deduplicated_evidence_count: "number",
      duplicate_removed_count: "number",
      source_quality_summary: "Record<source_quality, number>",
      low_confidence_count: "number",
      competitor_coverage: "Record<competitor, number>",
      missing_competitors: "string[]",
      fallback_by_competitor: "Record<competitor, string | null>",
      raw_search_result_count_by_competitor: "Record<competitor, number>",
      relevant_evidence_count_by_competitor: "Record<competitor, number>",
      unrelated_evidence_count_by_competitor: "Record<competitor, number>",
      filtered_unrelated_count: "number",
      missing_relevant_evidence_competitors: "string[]",
      fallback_used: "boolean",
      fallback_reason: "string | null",
      elapsed_time_ms: "number",
    },
  },
};
