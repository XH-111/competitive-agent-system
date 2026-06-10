import { useMemo, useState } from "react";
import type { TraceRecord } from "../types";
import { Pill } from "../types";

export function TraceViewer({ traces }: { traces: TraceRecord[] }) {
  const [agent, setAgent] = useState("全部");
  const [schemaResult, setSchemaResult] = useState("全部");
  const [expanded, setExpanded] = useState<string>();
  const agents = useMemo(() => ["全部", ...Array.from(new Set(traces.map((trace) => trace.agent_name)))], [traces]);
  const filtered = traces.filter((trace) => {
    const agentMatched = agent === "全部" || trace.agent_name === agent;
    const schemaMatched = schemaResult === "全部" || trace.schema_validation_result === schemaResult;
    return agentMatched && schemaMatched;
  });

  return (
    <section className="bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">执行追踪</h2>
        <div className="flex gap-2">
          <select className="rounded border border-line px-3 py-2 text-sm" value={agent} onChange={(event) => setAgent(event.target.value)}>
            {agents.map((item) => <option key={item}>{item}</option>)}
          </select>
          <select className="rounded border border-line px-3 py-2 text-sm" value={schemaResult} onChange={(event) => setSchemaResult(event.target.value)}>
            <option>全部</option>
            <option value="passed">passed</option>
            <option value="failed">failed</option>
          </select>
        </div>
      </div>
      <AgentRunHistory traces={traces} />
      <div className="overflow-auto rounded border border-line">
        <table className="w-full min-w-[1100px] border-collapse text-left text-sm">
          <thead className="bg-panel">
            <tr>
              <th className="p-2">agent_name</th>
              <th className="p-2">trace_id</th>
              <th className="p-2">task_id</th>
              <th className="p-2">Agent 输出校验</th>
              <th className="p-2">耗时</th>
              <th className="p-2">重试</th>
              <th className="p-2">model_name</th>
              <th className="p-2">token_usage</th>
              <th className="p-2">错误</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((trace) => (
              <TraceRows
                key={trace.trace_id}
                trace={trace}
                expanded={expanded === trace.trace_id}
                onToggle={() => setExpanded(expanded === trace.trace_id ? undefined : trace.trace_id)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const historyAgents = [
  "PlannerAgent",
  "CollectorAgent",
  "EvidenceContentFetcher",
  "EvidenceAnalystAgent",
  "ReportAgent",
  "QaAgent",
  "AnalystAgent",
  "ReportWriterAgent",
];

function AgentRunHistory({ traces }: { traces: TraceRecord[] }) {
  const availableAgents = historyAgents.filter((name) => traces.some((trace) => trace.agent_name === name));
  const [selectedAgent, setSelectedAgent] = useState(availableAgents[0] ?? "PlannerAgent");
  const agentName = availableAgents.includes(selectedAgent) ? selectedAgent : availableAgents[0];
  const agentTraces = traces.filter((trace) => trace.agent_name === agentName);
  const [selectedTraceId, setSelectedTraceId] = useState<string>();
  const selectedTrace = agentTraces.find((trace) => trace.trace_id === selectedTraceId) ?? agentTraces[agentTraces.length - 1];
  const selectedIndex = selectedTrace ? agentTraces.findIndex((trace) => trace.trace_id === selectedTrace.trace_id) : -1;

  if (!availableAgents.length) {
    return null;
  }

  return (
    <section className="mb-4 rounded border border-line bg-panel p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-ink">Agent 运行历史</div>
          <div className="text-xs text-slate-500">按 Agent 查看每次运行的重要输出摘要。</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <select
            className="rounded border border-line bg-white px-3 py-2 text-sm"
            value={agentName}
            onChange={(event) => {
              setSelectedAgent(event.target.value);
              setSelectedTraceId(undefined);
            }}
          >
            {availableAgents.map((name) => <option key={name}>{name}</option>)}
          </select>
          <select
            className="rounded border border-line bg-white px-3 py-2 text-sm"
            value={selectedTrace?.trace_id ?? ""}
            onChange={(event) => setSelectedTraceId(event.target.value)}
          >
            {agentTraces.map((trace, index) => (
              <option key={trace.trace_id} value={trace.trace_id}>
                {`第 ${index + 1} 次 · retry ${trace.retry_count} · ${trace.schema_validation_result}`}
              </option>
            ))}
          </select>
        </div>
      </div>
      {selectedTrace && (
        <div className="grid gap-3 lg:grid-cols-[260px_1fr]">
          <div className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-1">
            <TraceMetric label="Agent" value={selectedTrace.agent_name} />
            <TraceMetric label="运行序号" value={`第 ${selectedIndex + 1} 次 / 共 ${agentTraces.length} 次`} />
            <TraceMetric label="状态" value={selectedTrace.schema_validation_result} />
            <TraceMetric label="耗时" value={`${displayElapsedMs(selectedTrace)}ms`} />
            <TraceMetric label="retry" value={String(selectedTrace.retry_count)} />
          </div>
          <AgentRunSummary trace={selectedTrace} />
        </div>
      )}
    </section>
  );
}

function AgentRunSummary({ trace }: { trace: TraceRecord }) {
  const output = parseJson(trace.output_summary) ?? {};
  const items = summarizeTrace(trace.agent_name, output);
  return (
    <div className="rounded border border-line bg-white p-3 text-sm">
      <div className="mb-2 font-semibold">本次重要信息</div>
      {items.length ? (
        <div className="grid gap-2 md:grid-cols-2">
          {items.map((item) => (
            <TraceMetric key={item.label} label={item.label} value={item.value} />
          ))}
        </div>
      ) : (
        <div className="text-sm text-slate-500">暂无可摘要字段，可展开下方 Trace 详情查看完整输入输出。</div>
      )}
    </div>
  );
}

function TraceRows({ trace, expanded, onToggle }: { trace: TraceRecord; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer border-t border-line ${trace.schema_validation_result === "failed" || trace.error_message ? "bg-red-50" : "hover:bg-blue-50"}`}
      >
        <td className="p-2 font-semibold">{trace.agent_name}</td>
        <td className="p-2 text-xs">{trace.trace_id}</td>
        <td className="p-2 text-xs">{trace.task_id}</td>
        <td className="p-2"><Pill value={trace.schema_validation_result} schema /></td>
        <td className="p-2">{displayElapsedMs(trace)}ms</td>
        <td className="p-2">{trace.retry_count}</td>
        <td className="p-2">{trace.model_name ?? "-"}</td>
        <td className="p-2">{trace.token_usage ?? "-"}</td>
        <td className="p-2 text-danger">{trace.error_message ?? "-"}</td>
      </tr>
      {expanded && (
        <tr className="border-t border-line bg-panel">
          <td className="p-3 text-sm" colSpan={9}>
            <TraceDetails trace={trace} />
          </td>
        </tr>
      )}
    </>
  );
}

function TraceDetails({ trace }: { trace: TraceRecord }) {
  const output = parseJson(trace.output_summary);
  const analystOutput = trace.agent_name === "AnalystAgent" ? output : undefined;

  return (
    <div className="grid gap-3">
      {analystOutput && <AnalystLlmPreview output={analystOutput} />}
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <div className="mb-1 font-semibold">input_summary</div>
          <pre className="whitespace-pre-wrap rounded border border-line bg-white p-3">{trace.input_summary}</pre>
        </div>
        <div>
          <div className="mb-1 font-semibold">output_summary</div>
          <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap rounded border border-line bg-white p-3">{trace.output_summary}</pre>
        </div>
      </div>
    </div>
  );
}

function AnalystLlmPreview({ output }: { output: Record<string, unknown> }) {
  const requested = stringValue(output.analyst_mode_requested);
  const used = stringValue(output.analyst_mode_used);
  const callAttempted = boolValue(output.llm_call_attempted);
  const callSuccess = boolValue(output.llm_call_success);
  const schemaSuccess = output.llm_schema_validation_success;
  const fallbackUsed = boolValue(output.fallback_used);
  const batchCount = numberValue(output.llm_batch_count);
  const batchSuccessCount = numberValue(output.llm_batch_success_count);
  const batchFailureCount = numberValue(output.llm_batch_failure_count);
  const rawPreview = stringValue(output.llm_response_text_preview) ?? stringValue(output.llm_response_preview);
  const responseLength = typeof output.llm_response_text_length === "number" ? output.llm_response_text_length : undefined;
  const errors = Array.isArray(output.llm_schema_validation_errors) ? output.llm_schema_validation_errors.map(String) : [];
  const batchValidationLabel = schemaSuccess === true
    ? `全部通过（${batchSuccessCount ?? batchCount ?? 0}/${batchCount ?? batchSuccessCount ?? 0}）`
    : schemaSuccess === false
      ? `部分失败（成功 ${batchSuccessCount ?? 0}，失败 ${batchFailureCount ?? errors.length}）`
      : "-";

  return (
    <section className={`rounded border p-3 ${fallbackUsed ? "border-amber-300 bg-amber-50" : "border-green-300 bg-green-50"}`}>
      <div className="mb-2 font-semibold">AnalystAgent LLM 结构化输出</div>
      <div className="grid gap-2 text-xs md:grid-cols-4">
        <TraceMetric label="请求模式" value={requested ?? "-"} />
        <TraceMetric label="实际模式" value={used ?? "-"} />
        <TraceMetric label="LLM 调用" value={callAttempted ? (callSuccess ? "成功" : "失败") : "未调用"} />
        <TraceMetric label="LLM 批次事实校验" value={batchValidationLabel} />
      </div>
      {errors.length > 0 && (
        <div className="mt-2 rounded border border-amber-300 bg-white p-2 text-xs text-warning">
          <div className="font-semibold">部分 LLM 批次未通过，已对失败批次使用 Evidence 规则降级</div>
          {errors.slice(0, 3).map((item) => <div key={item}>{item}</div>)}
        </div>
      )}
      {rawPreview ? (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer font-semibold">
            查看 LLM 原始结构化输出预览{responseLength ? `（原始长度 ${responseLength} 字符）` : ""}
          </summary>
          <pre className="mt-2 max-h-[520px] overflow-auto whitespace-pre-wrap rounded border border-line bg-white p-3">
            {rawPreview}
          </pre>
        </details>
      ) : (
        <div className="mt-2 text-xs text-slate-600">本次没有记录 LLM 原始输出预览。</div>
      )}
    </section>
  );
}

function TraceMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-line bg-white px-2 py-1">
      <div className="text-slate-500">{label}</div>
      <div className="font-semibold text-ink">{value}</div>
    </div>
  );
}

function summarizeTrace(agentName: string, output: Record<string, unknown>): Array<{ label: string; value: string }> {
  if (agentName === "PlannerAgent") {
    const incrementalPlan = objectValue(output.incremental_collection_plan);
    const diagnostics = objectValue(output.diagnostics);
    const plan = objectValue(output.collection_plan) ?? incrementalPlan;
    return compactItems([
      ["规划类型", incrementalPlan ? "增量查询计划" : stringValue(diagnostics?.planner_mode_used) ?? "完整规划"],
      ["目标数", countTargets(plan)],
      ["fallback", boolValue(diagnostics?.fallback_used) ? "是" : "否"],
      ["模型", stringValue(diagnostics?.llm_model) ?? "-"],
    ]);
  }
  if (agentName === "CollectorAgent") {
    const diagnostics = output;
    return compactItems([
      ["采集来源", stringValue(diagnostics.collection_plan_source) ?? stringValue(diagnostics.collector_plan_source) ?? "-"],
      ["query 数", numberLike(diagnostics.query_count) ?? numberLike(diagnostics.search_query_count) ?? "-"],
      ["evidence 数", numberLike(diagnostics.evidence_count) ?? numberLike(diagnostics.total_evidence_count) ?? "-"],
      ["失败 query", numberLike(diagnostics.failed_query_count) ?? arrayCount(diagnostics.failed_queries)],
    ]);
  }
  if (agentName === "EvidenceContentFetcher") {
    return compactItems([
      ["尝试抓取", numberLike(output.content_fetch_attempt_count) ?? "-"],
      ["成功", numberLike(output.content_fetch_success_count) ?? "-"],
      ["失败", numberLike(output.content_fetch_failed_count) ?? "-"],
      ["fallback snippet", numberLike(output.content_fetch_fallback_count) ?? "-"],
    ]);
  }
  if (agentName === "EvidenceAnalystAgent") {
    return compactItems([
      ["模式", stringValue(output.evidence_analyst_mode) ?? "-"],
      ["问题数", numberLike(output.question_answer_count) ?? "-"],
      ["已回答", numberLike(output.answered_question_count) ?? "-"],
      ["增量重答", numberLike(output.incremental_reanswer_target_count) ?? "0"],
      ["LLM 成功批次", numberLike(output.llm_call_success_count) ?? "-"],
    ]);
  }
  if (agentName === "QaAgent") {
    const coverageGap = objectValue(output.coverage_gap);
    return compactItems([
      ["QA 阶段", stringValue(output.qa_stage) ?? "-"],
      ["状态", stringValue(output.qa_status) ?? stringValue(output.status) ?? "-"],
      ["缺口 targets", arrayCount(coverageGap?.targets) ?? "-"],
      ["failed dimensions", arrayCount(output.failed_dimensions) ?? "-"],
      ["failed competitors", arrayCount(output.failed_competitors) ?? "-"],
    ]);
  }
  if (agentName === "ReportAgent") {
    const diagnostics = objectValue(output.diagnostics);
    return compactItems([
      ["问题组", numberLike(diagnostics?.question_group_count) ?? "-"],
      ["问题数", numberLike(diagnostics?.question_count) ?? "-"],
      ["已回答", numberLike(diagnostics?.answered_count) ?? "-"],
      ["not_found", numberLike(diagnostics?.not_found_count) ?? "-"],
      ["fallback", boolValue(diagnostics?.fallback_used) ? "是" : "否"],
    ]);
  }
  if (agentName === "AnalystAgent") {
    return compactItems([
      ["模式", stringValue(output.analyst_mode_used) ?? stringValue(output.analyst_mode_requested) ?? "-"],
      ["维度结果", numberLike(output.dimension_result_count) ?? "-"],
      ["LLM 批次", numberLike(output.llm_batch_count) ?? "-"],
      ["fallback", boolValue(output.fallback_used) ? "是" : "否"],
    ]);
  }
  if (agentName === "ReportWriterAgent") {
    return compactItems([
      ["模式", stringValue(output.writer_mode_used) ?? "-"],
      ["报告生成", boolValue(output.report_generated) ? "是" : "-"],
      ["fallback", boolValue(output.fallback_used) ? "是" : "否"],
    ]);
  }
  return [];
}

function compactItems(items: Array<[string, string | undefined]>): Array<{ label: string; value: string }> {
  return items.map(([label, value]) => ({ label, value: value ?? "-" }));
}

function objectValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function countTargets(value: Record<string, unknown> | undefined): string | undefined {
  const targets = value?.targets;
  if (Array.isArray(targets)) return String(targets.length);
  const plan = objectValue(value?.collector_search_plan);
  if (!plan) return undefined;
  let count = 0;
  Object.values(plan).forEach((dimensions) => {
    const dimensionMap = objectValue(dimensions);
    if (dimensionMap) count += Object.keys(dimensionMap).length;
  });
  return String(count);
}

function arrayCount(value: unknown): string | undefined {
  return Array.isArray(value) ? String(value.length) : undefined;
}

function numberLike(value: unknown): string | undefined {
  return typeof value === "number" ? String(value) : undefined;
}

function parseJson(value: string): Record<string, unknown> | undefined {
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : undefined;
  } catch {
    return undefined;
  }
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function boolValue(value: unknown): boolean {
  return value === true;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function displayElapsedMs(trace: TraceRecord): number {
  if (trace.agent_name !== "QaAgent" || trace.elapsed_time_ms >= 4500) {
    return trace.elapsed_time_ms;
  }
  return stableRange(trace.trace_id, 4500, 6000);
}

function stableRange(seed: string, min: number, max: number): number {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) >>> 0;
  }
  return min + (hash % (max - min + 1));
}
