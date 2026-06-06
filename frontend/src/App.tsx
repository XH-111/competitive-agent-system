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
import type { CollectorDiagnostics, CollectorStatus, Dag, DemoMode, DimensionResult, Evidence, LlmStatus, QaResult, Report, SearchTestResult, Task, TaskRun, TraceRecord, WriterDiagnostics, WorkflowSummary } from "./types";
import { Pill } from "./types";

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
  const [workflowSummary, setWorkflowSummary] = useState<WorkflowSummary>();
  const [llmStatus, setLlmStatus] = useState<LlmStatus>();
  const [collectorStatus, setCollectorStatus] = useState<CollectorStatus>();
  const [llmTesting, setLlmTesting] = useState(false);
  const [searchTesting, setSearchTesting] = useState(false);
  const [searchTestResult, setSearchTestResult] = useState<SearchTestResult>();
  const [apiRecorderSnapshot, setApiRecorderSnapshot] = useState(getApiRecorderSnapshot());

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
    const [nextDag, nextEvidence, nextTraces] = await Promise.all([
      api.dag(taskId),
      activeRunId ? api.runEvidence(taskId, activeRunId) : api.evidence(taskId),
      activeRunId ? api.runTraces(taskId, activeRunId) : api.traces(taskId)
    ]);
    setDag(nextDag);
    setEvidence(nextEvidence);
    setTraces(nextTraces);
    const recoveredSummary = recoverWorkflowSummary(nextTraces);
    if (recoveredSummary) {
      setWorkflowSummary(recoveredSummary);
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
    setWorkflowSummary(undefined);
    await refresh(nextTask.task_id);
  }

  async function handleCreated(created: Task) {
    await loadTasks(created.task_id);
  }

  async function run() {
    if (!task) return;
    setBusy(true);
    try {
      const result = await api.runTask(task.task_id, demoMode, autoRework, writerMode, collectorMode, analystMode, workflowEngine) as { report?: Report | null; workflow_summary?: WorkflowSummary };
      setWorkflowSummary(result.workflow_summary);
      const runId = result.workflow_summary?.run_id ?? result.report?.run_id;
      await loadTasks(task.task_id);
      if (runId) {
        await refresh(task.task_id, runId);
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
      setBusy(false);
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
  const collectorDiagnostics = latestCollectorDiagnostics(traces);
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
            <Play size={16} /> 运行 Demo 工作流
          </button>
          <label className="inline-flex items-center gap-2 rounded border border-line bg-white px-3 py-2 text-sm">
            <input
              type="checkbox"
              checked={autoRework}
              onChange={(event) => setAutoRework(event.target.checked)}
            />
            auto_rework=true
          </label>
          <span className="text-sm text-slate-600">执行所选 Mock Agent DAG，并生成 DAG、报告、证据、QA 和 Trace。</span>
        </div>

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

        <PlannerSummaryCard workflowSummary={workflowSummary} collectorDiagnostics={collectorDiagnostics} />
        <KnowledgeHitsPanel workflowSummary={workflowSummary} />

        <div className="space-y-4">
          <DagView dag={dag} traces={traces} qaRouteTo={qa?.route_to} />
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
