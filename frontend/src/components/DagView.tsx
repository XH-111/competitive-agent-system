import { Activity, CheckCircle2, LoaderCircle } from "lucide-react";
import type { Dag, TraceRecord } from "../types";
import { Pill } from "../types";

const schemaByAgent: Record<string, { input: string; output: string }> = {
  PlannerAgent: { input: "PlannerInput", output: "PlannerOutput" },
  CollectorAgent: { input: "CollectorInput", output: "CollectorOutput" },
  EvidenceGate: { input: "EvidenceGateInput", output: "EvidenceGateOutput" },
  PageFetcher: { input: "PageFetchInput", output: "PageFetchOutput" },
  AnalystAgent: { input: "AnalystInput", output: "AnalystOutput" },
  ReportWriterAgent: { input: "ReportWriterInput", output: "ReportWriterOutput" },
  QaAgent: { input: "QaInput", output: "QaOutput" },
  FinalReport: { input: "FinalReportInput", output: "FinalReportOutput" },
  FinalReportAgent: { input: "FinalReportInput", output: "FinalReportOutput" },
};

const agentDescriptions: Record<string, string> = {
  PlannerAgent: "规划分析维度与采集策略",
  CollectorAgent: "按维度搜索公开证据",
  EvidenceGate: "校验证据相关性与覆盖",
  PageFetcher: "抓取高质量来源正文摘要",
  AnalystAgent: "抽取维度级结构化事实",
  ReportWriterAgent: "生成竞品分析报告",
  QaAgent: "检查事实、证据与报告质量",
  FinalReport: "整合最终报告与运行状态",
};

const orderedAgents = [
  "PlannerAgent",
  "CollectorAgent",
  "EvidenceGate",
  "PageFetcher",
  "AnalystAgent",
  "ReportWriterAgent",
  "QaAgent",
  "FinalReport",
];

const collectorDebugAgents = ["PlannerAgent", "CollectorAgent", "QaAgent"];

type DagViewProps = {
  dag?: Dag;
  traces: TraceRecord[];
  qaRouteTo?: string;
  running?: boolean;
  debugStage?: "planner_only" | "collector_only";
};

function traceAgentName(trace: TraceRecord) {
  return trace.agent_name === "FinalReportAgent" ? "FinalReport" : trace.agent_name;
}

function nextRunningAgent(traces: TraceRecord[], qaRouteTo?: string) {
  if (!traces.length) return orderedAgents[0];
  const lastAgent = traceAgentName(traces[traces.length - 1]);
  if (lastAgent === "QaAgent" && qaRouteTo) {
    const route = qaRouteTo === "FinalReportAgent" ? "FinalReport" : qaRouteTo;
    if (orderedAgents.includes(route)) return route;
  }
  const index = orderedAgents.indexOf(lastAgent);
  return index >= 0 && index < orderedAgents.length - 1 ? orderedAgents[index + 1] : undefined;
}

function agentTraceCount(traces: TraceRecord[], agent: string) {
  return traces.filter((trace) => traceAgentName(trace) === agent).length;
}

function collectorDebugProgress(traces: TraceRecord[]) {
  const plannerCount = agentTraceCount(traces, "PlannerAgent");
  const collectorCount = agentTraceCount(traces, "CollectorAgent");
  const qaCount = agentTraceCount(traces, "QaAgent");
  const completedSteps = Math.min(plannerCount, 2) + Math.min(collectorCount, 2) + Math.min(qaCount, 2);

  if (plannerCount === 0) {
    return {
      currentAgent: "PlannerAgent",
      text: "正在规划采集维度和初始查询词……",
      completedSteps,
    };
  }
  if (collectorCount === 0) {
    return {
      currentAgent: "CollectorAgent",
      text: "正在按完整采集计划搜索 evidence……",
      completedSteps,
    };
  }
  if (qaCount === 0) {
    return {
      currentAgent: "QaAgent",
      text: "正在检查 evidence 状态……",
      completedSteps,
    };
  }
  if (plannerCount < 2) {
    return {
      currentAgent: "PlannerAgent",
      text: "正在设计新的查询词……",
      completedSteps,
    };
  }
  if (collectorCount < 2) {
    return {
      currentAgent: "CollectorAgent",
      text: "正在增量补采缺口 evidence……",
      completedSteps,
    };
  }
  if (qaCount < 2) {
    return {
      currentAgent: "QaAgent",
      text: "正在基于合并 evidence 重新检查……",
      completedSteps,
    };
  }
  return {
    currentAgent: undefined,
    text: "Planner、Collector 与 EvidenceQA 已完成",
    completedSteps,
  };
}

export function DagView({ dag, traces, qaRouteTo, running = false, debugStage }: DagViewProps) {
  const visibleAgents = debugStage === "planner_only"
    ? ["PlannerAgent", "CollectorAgent", "AnalystAgent", "ReportWriterAgent"]
    : orderedAgents;
  const nodes = visibleAgents.map((agent) => dag?.nodes.find((node) => node.id === agent) ?? {
    id: agent,
    label: agentDescriptions[agent],
    status: debugStage === "planner_only" && agent !== "PlannerAgent"
      ? "skipped"
      : debugStage === "collector_only" && !collectorDebugAgents.includes(agent)
        ? "skipped"
        : "pending",
  });
  const tracedAgents = new Set(traces.map(traceAgentName));
  const collectorProgress = debugStage === "collector_only" ? collectorDebugProgress(traces) : undefined;
  const activeAgents = debugStage === "planner_only"
    ? ["PlannerAgent"]
    : debugStage === "collector_only"
      ? collectorDebugAgents
      : visibleAgents;
  const currentAgent = running
    ? debugStage === "planner_only"
      ? "PlannerAgent"
      : debugStage === "collector_only"
        ? collectorProgress?.currentAgent
        : nextRunningAgent(traces, qaRouteTo)
    : undefined;
  const completedCount = debugStage === "collector_only"
    ? activeAgents.filter((agent) => agentTraceCount(traces, agent) > 0).length
    : activeAgents.filter((agent) => tracedAgents.has(agent)).length;
  const progress = running
    ? debugStage === "collector_only"
      ? Math.max(4, Math.round(((collectorProgress?.completedSteps ?? 0) / 6) * 100))
      : Math.max(4, Math.round((completedCount / activeAgents.length) * 100))
    : debugStage
      ? 100
      : Math.round((completedCount / visibleAgents.length) * 100);
  const currentLabel = currentAgent === "FinalReport" ? "FinalReportAgent" : currentAgent;
  const statusText = running
    ? debugStage === "collector_only"
      ? collectorProgress?.text ?? "正在等待节点状态……"
      : `正在处理：${currentLabel ?? "等待节点状态"}`
    : debugStage === "planner_only"
      ? "PlannerAgent 已完成，后续节点已冻结"
      : debugStage === "collector_only"
        ? "Planner、Collector 与 EvidenceQA 已完成，其他节点已冻结"
        : `已完成 ${completedCount} / ${visibleAgents.length} 个节点`;

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
          <span>
            {debugStage === "collector_only"
              ? `${Math.min(collectorProgress?.completedSteps ?? 6, 6)} / 6`
              : `${completedCount} / ${visibleAgents.length}`}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded bg-slate-100">
          <div
            className="h-full bg-accent transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <div className={`grid gap-3 ${debugStage === "planner_only" ? "lg:grid-cols-4" : "lg:grid-cols-8"}`}>
        {nodes.map((node, index) => {
          const agentTraces = traces.filter((trace) => traceAgentName(trace) === node.id);
          const elapsed = agentTraces.reduce((sum, trace) => sum + trace.elapsed_time_ms, 0);
          const schemas = schemaByAgent[node.id] ?? { input: "-", output: "-" };
          const failed = agentTraces.some((trace) => trace.schema_validation_result === "failed");
          const isCurrent = currentAgent === node.id;
          const frozen = (debugStage === "planner_only" && node.id !== "PlannerAgent")
            || (debugStage === "collector_only" && !collectorDebugAgents.includes(node.id));
          const inferredStatus = frozen
            ? "skipped"
            : failed
              ? "failed"
              : isCurrent
                ? "running"
                : agentTraces.length
                  ? "completed"
                  : running
                    ? "pending"
                    : node.status;

          return (
            <div
              key={node.id}
              className={`relative min-h-44 rounded border p-3 transition-colors ${
                isCurrent
                  ? "border-blue-400 bg-blue-50 shadow-sm"
                  : "border-line bg-panel"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-semibold">
                  {node.id === "FinalReport" ? "FinalReportAgent" : node.id}
                </div>
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
                <div>{frozen ? "状态: 冻结，未执行" : `执行次数: ${agentTraces.length}`}</div>
                {!frozen && <div>耗时: {isCurrent ? "处理中" : `${elapsed}ms`}</div>}
              </div>
              {index < nodes.length - 1 && (
                <div className="absolute -right-3 top-1/2 hidden text-slate-400 lg:block">-&gt;</div>
              )}
            </div>
          );
        })}
      </div>

      {qaRouteTo && (
        <div className="mt-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-danger">
          QA 打回路径: QaAgent -&gt; {qaRouteTo}
        </div>
      )}
    </section>
  );
}
