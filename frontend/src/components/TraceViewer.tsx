import { useMemo, useState } from "react";
import type { TraceRecord } from "../types";
import { Pill } from "../types";
import { labelFor } from "../i18n/zh";

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
        <h2 className="text-lg font-semibold">执行追踪 Trace Viewer</h2>
        <div className="flex gap-2">
          <select className="rounded border border-line px-3 py-2 text-sm" value={agent} onChange={(event) => setAgent(event.target.value)}>
            {agents.map((item) => <option key={item}>{item}</option>)}
          </select>
          <select className="rounded border border-line px-3 py-2 text-sm" value={schemaResult} onChange={(event) => setSchemaResult(event.target.value)}>
            <option>全部</option>
            <option value="passed">通过 passed</option>
            <option value="failed">失败 failed</option>
          </select>
        </div>
      </div>
      <div className="overflow-auto rounded border border-line">
        <table className="w-full min-w-[1100px] border-collapse text-left text-sm">
          <thead className="bg-panel">
            <tr>
              <th className="p-2">Agent agent_name</th>
              <th className="p-2">Trace ID trace_id</th>
              <th className="p-2">任务 ID task_id</th>
              <th className="p-2">Schema 校验</th>
              <th className="p-2">{labelFor("elapsed_time_ms")}</th>
              <th className="p-2">{labelFor("retry_count")}</th>
              <th className="p-2">{labelFor("model_name")}</th>
              <th className="p-2">{labelFor("token_usage")}</th>
              <th className="p-2">{labelFor("error_message")}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((trace) => (
              <>
                <tr
                  key={trace.trace_id}
                  onClick={() => setExpanded(expanded === trace.trace_id ? undefined : trace.trace_id)}
                  className={`cursor-pointer border-t border-line ${trace.schema_validation_result === "failed" || trace.error_message ? "bg-red-50" : "hover:bg-blue-50"}`}
                >
                  <td className="p-2 font-semibold">{trace.agent_name}</td>
                  <td className="p-2 text-xs">{trace.trace_id}</td>
                  <td className="p-2 text-xs">{trace.task_id}</td>
                  <td className="p-2"><Pill value={trace.schema_validation_result} schema /></td>
                  <td className="p-2">{trace.elapsed_time_ms}</td>
                  <td className="p-2">{trace.retry_count}</td>
                  <td className="p-2">{trace.model_name ?? "-"}</td>
                  <td className="p-2">{trace.token_usage ?? "-"}</td>
                  <td className="p-2 text-danger">{trace.error_message ?? "-"}</td>
                </tr>
                {expanded === trace.trace_id && (
                  <tr className="border-t border-line bg-panel">
                    <td className="p-3 text-sm" colSpan={9}>
                      <div className="grid gap-3 md:grid-cols-2">
                        <div>
                          <div className="mb-1 font-semibold">{labelFor("input_summary")}</div>
                          <pre className="whitespace-pre-wrap rounded border border-line bg-white p-3">{trace.input_summary}</pre>
                        </div>
                        <div>
                          <div className="mb-1 font-semibold">{labelFor("output_summary")}</div>
                          <pre className="whitespace-pre-wrap rounded border border-line bg-white p-3">{trace.output_summary}</pre>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
