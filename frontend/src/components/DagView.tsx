import { Activity, CheckCircle2, LoaderCircle } from "lucide-react";
import type { Dag, TraceRecord, WorkflowProgress } from "../types";
import { Pill } from "../types";

const MAX_EVIDENCE_REWORK = 3;

const schemaByAgent: Record<string, { input: string; output: string }> = {
  PlannerAgent: { input: "PlannerInput", output: "PlannerOutput" },
  CollectorAgent: { input: "CollectorInput", output: "CollectorOutput" },
  EvidenceContentFetcher: { input: "Evidence[]", output: "Enriched Evidence[]" },
  EvidenceAnalystAgent: { input: "Evidence[]", output: "EvidenceAnalystOutput" },
  AnalystAgent: { input: "AnalystInput", output: "AnalystOutput" },
  ReportWriterAgent: { input: "ReportWriterInput", output: "ReportWriterOutput" },
  QaAgent: { input: "QaInput", output: "QaOutput" },
  FinalReport: { input: "FinalReportInput", output: "FinalReportOutput" },
  FinalReportAgent: { input: "FinalReportInput", output: "FinalReportOutput" },
};

const agentDescriptions: Record<string, string> = {
  PlannerAgent: "规划分析维度与采集策略",
  CollectorAgent: "按维度搜索公开证据",
  EvidenceContentFetcher: "抽取高质量 Evidence 正文",
  EvidenceAnalystAgent: "逐条 Evidence 抽取事实",
  AnalystAgent: "抽取维度级结构化事实",
  ReportWriterAgent: "生成竞品分析报告",
  QaAgent: "检查事实、证据与报告质量",
  FinalReport: "整合最终报告与运行状态",
};

const orderedAgents = [
  "PlannerAgent",
  "CollectorAgent",
  "EvidenceContentFetcher",
  "EvidenceAnalystAgent",
  "AnalystAgent",
  "ReportWriterAgent",
  "QaAgent",
  "FinalReport",
];

const collectorDebugAgents = [
  "PlannerAgent",
  "CollectorAgent",
  "QaAgent",
  "EvidenceContentFetcher",
  "EvidenceAnalystAgent",
];

type DagViewProps = {
  dag?: Dag;
  traces: TraceRecord[];
  qaRouteTo?: string;
  running?: boolean;
  debugStage?: "planner_only" | "collector_only";
  progress?: WorkflowProgress;
};

type TraceOutput = {
  qa_status?: string;
  qa_stage?: "full" | "evidence" | "analyst";
};

function traceAgentName(trace: TraceRecord) {
  return trace.agent_name === "FinalReportAgent" ? "FinalReport" : trace.agent_name;
}

function parseTraceOutput(trace?: TraceRecord): TraceOutput {
  if (!trace?.output_summary) return {};
  try {
    const parsed = JSON.parse(trace.output_summary) as TraceOutput;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function latestTraceForAgent(traces: TraceRecord[], agent: string) {
  return [...traces].reverse().find((trace) => traceAgentName(trace) === agent);
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
  const contentFetchCount = agentTraceCount(traces, "EvidenceContentFetcher");
  const evidenceAnalystCount = agentTraceCount(traces, "EvidenceAnalystAgent");
  const completedSteps = plannerCount + collectorCount + qaCount + contentFetchCount + evidenceAnalystCount;
  const totalSteps = Math.max(completedSteps + 1, 5);
  const lastTrace = traces.length ? traces[traces.length - 1] : undefined;
  const lastAgent = lastTrace ? traceAgentName(lastTrace) : undefined;
  const latestQaTraceForStage = latestTraceForAgent(traces, "QaAgent");
  const latestQaStageOutput = parseTraceOutput(latestQaTraceForStage);

  if (lastAgent === "QaAgent" && latestQaStageOutput.qa_stage === "analyst" && latestQaStageOutput.qa_status !== "failed") {
    return {
      currentAgent: undefined,
      text: "Planner、Collector、EvidenceQA、正文抽取、EvidenceAnalyst 与 AnalystQA 已完成",
      completedSteps,
      totalSteps: completedSteps || 1,
    };
  }
  if (lastAgent === "PlannerAgent") {
    return {
      currentAgent: "CollectorAgent",
      text: collectorCount === 0 ? "正在按完整采集计划搜索 evidence..." : "正在增量补采缺口 evidence...",
      completedSteps,
      totalSteps,
    };
  }
  if (lastAgent === "CollectorAgent") {
    const afterAnalystQa = latestQaStageOutput.qa_stage === "analyst" && latestQaStageOutput.qa_status === "failed";
    return {
      currentAgent: "QaAgent",
      text: afterAnalystQa ? "正在检查增量补采 evidence 状态..." : "正在检查 evidence 状态...",
      completedSteps,
      totalSteps,
    };
  }
  if (lastAgent === "EvidenceContentFetcher") {
    return {
      currentAgent: "EvidenceAnalystAgent",
      text: "正在基于正文和摘要回答 Planner 问题...",
      completedSteps,
      totalSteps,
    };
  }
  if (lastAgent === "EvidenceAnalystAgent") {
    return {
      currentAgent: "QaAgent",
      text: "正在检查 Analyst 问题回答状态...",
      completedSteps,
      totalSteps,
    };
  }
  if (
    lastAgent === "QaAgent"
    && latestQaStageOutput.qa_status === "failed"
    && (latestQaTraceForStage?.retry_count ?? 0) < MAX_EVIDENCE_REWORK
  ) {
    return {
      currentAgent: "PlannerAgent",
      text: latestQaStageOutput.qa_stage === "analyst" ? "正在根据 not_found 问题设计新的查询词..." : "正在设计新的查询词...",
      completedSteps,
      totalSteps,
    };
  }

  if (evidenceAnalystCount > 0) {
    return {
      currentAgent: undefined,
      text: "Planner、Collector、EvidenceQA、正文抽取与事实抽取已完成",
      completedSteps,
      totalSteps: completedSteps || 1,
    };
  }
  if (plannerCount === 0) {
    return {
      currentAgent: "PlannerAgent",
      text: "正在规划采集维度和初始查询词……",
      completedSteps,
      totalSteps,
    };
  }
  if (plannerCount > collectorCount) {
    return {
      currentAgent: "CollectorAgent",
      text: collectorCount === 0 ? "正在按完整采集计划搜索 evidence……" : "正在增量补采缺口 evidence……",
      completedSteps,
      totalSteps,
    };
  }
  if (collectorCount > qaCount) {
    return {
      currentAgent: "QaAgent",
      text: qaCount === 0 ? "正在检查 evidence 状态……" : "正在基于合并 evidence 重新检查……",
      completedSteps,
      totalSteps,
    };
  }

  const latestQaTrace = latestTraceForAgent(traces, "QaAgent");
  const latestQaOutput = parseTraceOutput(latestQaTrace);
  const latestQaFailed = latestQaOutput.qa_status === "failed"
    || latestQaTrace?.schema_validation_result === "failed";
  if (latestQaFailed && (latestQaTrace?.retry_count ?? 0) < MAX_EVIDENCE_REWORK) {
    return {
      currentAgent: "PlannerAgent",
      text: "正在设计新的查询词……",
      completedSteps,
      totalSteps,
    };
  }

  if (contentFetchCount > 0) {
    return {
      currentAgent: "EvidenceAnalystAgent",
      text: "正在逐条抽取 Evidence 事实……",
      completedSteps,
      totalSteps,
    };
  }

  return {
    currentAgent: "EvidenceContentFetcher",
    text: "正在抽取高质量 Evidence 正文……",
    completedSteps,
    totalSteps,
  };
}

export function DagView({ dag, traces, qaRouteTo, running = false, debugStage, progress: runProgress }: DagViewProps) {
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
    ? runProgress?.current_agent && runProgress.status === "running"
      ? runProgress.current_agent
      : debugStage === "planner_only"
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
      ? Math.max(
          4,
          Math.round(
            ((collectorProgress?.completedSteps ?? 0) / Math.max(collectorProgress?.totalSteps ?? 5, 1)) * 100,
          ),
        )
      : Math.max(4, Math.round((completedCount / activeAgents.length) * 100))
    : debugStage
      ? 100
      : Math.round((completedCount / visibleAgents.length) * 100);
  const currentLabel = currentAgent === "FinalReport" ? "FinalReportAgent" : currentAgent;
  const statusText = running
    ? runProgress?.message && runProgress.status === "running"
      ? runProgress.message
      : debugStage === "collector_only"
      ? collectorProgress?.text ?? "正在等待节点状态……"
      : `正在处理：${currentLabel ?? "等待节点状态"}`
    : debugStage === "planner_only"
      ? "PlannerAgent 已完成，后续节点已冻结"
      : debugStage === "collector_only"
        ? "Planner、Collector、EvidenceQA、正文抽取与事实抽取已完成，其他节点已冻结"
        : `已完成 ${completedCount} / ${visibleAgents.length} 个节点`;
  const displayedCompletedSteps = Math.min(
    collectorProgress?.completedSteps ?? 0,
    collectorProgress?.totalSteps ?? 5,
  );

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
              ? `${displayedCompletedSteps} / ${collectorProgress?.totalSteps ?? 5}`
              : `${completedCount} / ${visibleAgents.length}`}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded bg-slate-100">
          <div className="h-full bg-accent transition-all duration-500" style={{ width: `${progress}%` }} />
        </div>
      </div>

      <div className={`grid gap-3 ${debugStage === "planner_only" ? "lg:grid-cols-4" : "lg:grid-cols-8"}`}>
        {nodes.map((node, index) => {
          const agentTraces = traces.filter((trace) => traceAgentName(trace) === node.id);
          const elapsed = agentTraces.reduce((sum, trace) => sum + trace.elapsed_time_ms, 0);
          const schemas = schemaByAgent[node.id] ?? { input: "-", output: "-" };
          const failed = agentTraces.some((trace) => trace.schema_validation_result === "failed");
          const isCurrent = currentAgent === node.id;
          const nodeProgressPercent = runProgress?.total
            ? Math.min(100, Math.max(0, Math.round(((runProgress.current ?? 0) / runProgress.total) * 100)))
            : 0;
          const showNodeProgress = isCurrent
            && running
            && runProgress?.status === "running"
            && runProgress.current_agent === node.id
            && Boolean(runProgress.message)
            && Boolean(runProgress.total);
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
                isCurrent ? "border-blue-400 bg-blue-50 shadow-sm" : "border-line bg-panel"
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
                {showNodeProgress && (
                  <div className="mt-2 rounded border border-blue-200 bg-white px-2 py-1.5 text-accent">
                    <div className="font-semibold">{runProgress?.message}</div>
                    {runProgress?.detail && (
                      <div className="mt-0.5 text-slate-500">{runProgress.detail}</div>
                    )}
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-blue-100">
                      <div
                        className="h-full bg-accent transition-all duration-500"
                        style={{ width: `${nodeProgressPercent}%` }}
                      />
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

      {qaRouteTo && (
        <div className="mt-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-danger">
          QA 打回路径: QaAgent -&gt; {qaRouteTo}
        </div>
      )}
    </section>
  );
}
