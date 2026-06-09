import { Activity, CheckCircle2, LoaderCircle } from "lucide-react";
import type { Dag, TraceRecord, WorkflowProgress } from "../types";
import { Pill } from "../types";

const flowAgents = [
  "PlannerAgent",
  "CollectorAgent",
  "QaAgent",
  "EvidenceContentFetcher",
  "EvidenceAnalystAgent",
  "ReportAgent",
];

const schemaByAgent: Record<string, { input: string; output: string }> = {
  PlannerAgent: { input: "PlannerInput", output: "PlannerOutput" },
  CollectorAgent: { input: "CollectorInput", output: "CollectorOutput" },
  QaAgent: { input: "QaInput", output: "QaOutput" },
  EvidenceContentFetcher: { input: "Evidence[]", output: "Enriched Evidence[]" },
  EvidenceAnalystAgent: { input: "Evidence[]", output: "EvidenceAnalystOutput" },
  ReportAgent: { input: "EvidenceAnalystOutput", output: "ReportAgentOutput" },
};

const agentDescriptions: Record<string, string> = {
  PlannerAgent: "规划分析维度与补采查询词",
  CollectorAgent: "按维度搜索公开证据",
  QaAgent: "检查 Evidence 与问题回答覆盖",
  EvidenceContentFetcher: "抓取高质量 Evidence 正文",
  EvidenceAnalystAgent: "基于 Evidence 回答规划问题",
  ReportAgent: "生成结构化竞品分析报告",
};

type DagViewProps = {
  dag?: Dag;
  traces: TraceRecord[];
  qaRouteTo?: string;
  running?: boolean;
  debugStage?: "planner_only" | "collector_only";
  progress?: WorkflowProgress;
};

function traceAgentName(trace: TraceRecord) {
  return trace.agent_name;
}

function agentTraceCount(traces: TraceRecord[], agent: string) {
  return traces.filter((trace) => traceAgentName(trace) === agent).length;
}

export function DagView({ dag, traces, running = false, progress: runProgress }: DagViewProps) {
  const nodes = flowAgents.map((agent) => dag?.nodes.find((node) => node.id === agent) ?? {
    id: agent,
    label: agentDescriptions[agent],
    status: "pending",
  });
  const hasLiveProgress = runProgress?.status === "running"
    && Boolean(runProgress.current_agent)
    && flowAgents.includes(runProgress.current_agent as string);
  const currentAgent = running && hasLiveProgress ? runProgress?.current_agent : undefined;
  const completedCount = flowAgents.filter((agent) => agentTraceCount(traces, agent) > 0).length;
  const progressPercent = running
    ? Math.max(4, Math.round((completedCount / flowAgents.length) * 100))
    : Math.round((completedCount / flowAgents.length) * 100);
  const statusText = running
    ? hasLiveProgress
      ? runProgress?.message || "节点正在处理中..."
      : "等待后端节点状态..."
    : completedCount >= flowAgents.length
      ? "当前主流程已完成"
      : `已完成 ${completedCount} / ${flowAgents.length} 个节点`;

  return (
    <section className="bg-white p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <Activity size={18} /> DAG 执行状态
          </h2>
          <p className="mt-1 text-sm text-slate-600">{statusText}</p>
        </div>
        {running && (
          <div className="inline-flex items-center gap-2 rounded border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-semibold text-accent">
            <LoaderCircle className="animate-spin" size={16} />
            工作流运行中
          </div>
        )}
      </div>

      <div className="mb-4">
        <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
          <span>{running ? "实时执行进度" : "本次运行进度"}</span>
          <span>{Math.min(completedCount, flowAgents.length)} / {flowAgents.length}</span>
        </div>
        <div className="h-2 overflow-hidden rounded bg-slate-100">
          <div className="h-full bg-accent transition-all duration-500" style={{ width: `${progressPercent}%` }} />
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-6">
        {nodes.map((node, index) => {
          const agentTraces = traces.filter((trace) => traceAgentName(trace) === node.id);
          const elapsed = agentTraces.reduce((sum, trace) => sum + trace.elapsed_time_ms, 0);
          const schemas = schemaByAgent[node.id] ?? { input: "-", output: "-" };
          const failed = agentTraces.some((trace) => trace.schema_validation_result === "failed");
          const isCurrent = currentAgent === node.id;
          const inferredStatus = failed
            ? "failed"
            : isCurrent
              ? "running"
              : agentTraces.length
                ? "completed"
                : running
                  ? "pending"
                  : node.status;
          const nodeProgressPercent = runProgress?.total
            ? Math.min(100, Math.max(0, Math.round(((runProgress.current ?? 0) / runProgress.total) * 100)))
            : 0;
          const showNodeProgress = isCurrent
            && hasLiveProgress
            && Boolean(runProgress?.message)
            && Boolean(runProgress?.total);

          return (
            <div
              key={node.id}
              className={`relative min-h-44 rounded border p-3 transition-colors ${
                isCurrent ? "border-blue-400 bg-blue-50 shadow-sm" : "border-line bg-panel"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-semibold">{node.id}</div>
                {isCurrent && <LoaderCircle className="animate-spin text-accent" size={15} />}
                {!isCurrent && inferredStatus === "completed" && <CheckCircle2 className="text-emerald-600" size={15} />}
              </div>
              <p className="mt-1 min-h-8 text-xs text-slate-600">
                {agentDescriptions[node.id] ?? node.label}
              </p>
              <div className="mt-2"><Pill value={inferredStatus} /></div>
              <div className="mt-3 space-y-1 text-xs text-slate-600">
                <div>输入: {schemas.input}</div>
                <div>输出: {schemas.output}</div>
                <div>执行次数: {agentTraces.length}</div>
                <div>耗时: {isCurrent ? "处理中" : `${elapsed}ms`}</div>
                {showNodeProgress && (
                  <div className="mt-2 rounded border border-blue-200 bg-white px-2 py-1.5 text-accent">
                    <div className="font-semibold">{runProgress?.message}</div>
                    {runProgress?.detail && <div className="mt-0.5 text-slate-500">{runProgress.detail}</div>}
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-blue-100">
                      <div className="h-full bg-accent transition-all duration-500" style={{ width: `${nodeProgressPercent}%` }} />
                    </div>
                  </div>
                )}
              </div>
              {index < nodes.length - 1 && (
                <div className="absolute -right-3 top-1/2 hidden text-slate-400 lg:block">-&gt;</div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
