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
        <td className="p-2">{trace.elapsed_time_ms}ms</td>
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
        <details className="mt-2 text-xs" open={schemaSuccess === false}>
          <summary className="cursor-pointer font-semibold">
            查看 LLM 原始结构化输出预览{responseLength ? `（原始长度 ${responseLength} 字符）` : ""}
          </summary>
          <pre className="mt-2 max-h-[520px] overflow-auto whitespace-pre-wrap rounded border border-line bg-white p-3">{rawPreview}</pre>
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
