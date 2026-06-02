import { Activity } from "lucide-react";
import type { Dag, TraceRecord } from "../types";
import { Pill } from "../types";
import { dagDescriptions } from "../i18n/zh";

const schemaByAgent: Record<string, { input: string; output: string }> = {
  PlannerAgent: { input: "PlannerInput", output: "PlannerOutput" },
  CollectorAgent: { input: "CollectorInput", output: "CollectorOutput" },
  EvidenceGate: { input: "EvidenceGateInput", output: "EvidenceGateOutput" },
  PageFetcher: { input: "PageFetchInput", output: "PageFetchOutput" },
  AnalystAgent: { input: "AnalystInput", output: "AnalystOutput" },
  ReportWriterAgent: { input: "ReportWriterInput", output: "ReportWriterOutput" },
  QaAgent: { input: "QaInput", output: "QaOutput" },
  FinalReport: { input: "FinalReportInput", output: "FinalReportOutput" },
  FinalReportAgent: { input: "FinalReportInput", output: "FinalReportOutput" }
};

const orderedAgents = ["PlannerAgent", "CollectorAgent", "EvidenceGate", "PageFetcher", "AnalystAgent", "ReportWriterAgent", "QaAgent", "FinalReport"];

export function DagView({ dag, traces, qaRouteTo }: { dag?: Dag; traces: TraceRecord[]; qaRouteTo?: string }) {
  const nodes = dag?.nodes?.length
    ? dag.nodes
    : orderedAgents.map((agent) => ({
        id: agent,
        label: agent,
        status: "pending"
      }));
  return (
    <section className="bg-white p-4">
      <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold"><Activity size={18} /> DAG 执行状态</h2>
      <div className="grid gap-3 lg:grid-cols-8">
        {nodes.map((node, index) => {
          const agentTraces = traces.filter((trace) => trace.agent_name === node.id);
          const elapsed = agentTraces.reduce((sum, trace) => sum + trace.elapsed_time_ms, 0);
          const schemas = schemaByAgent[node.id] ?? { input: "-", output: "-" };
          const inferredStatus = agentTraces.some((trace) => trace.schema_validation_result === "failed")
            ? "failed"
            : agentTraces.length
              ? "completed"
              : node.status;
          return (
            <div key={node.id} className="relative min-h-44 rounded border border-line bg-panel p-3">
              <div className="text-sm font-semibold">{node.id === "FinalReport" ? "FinalReportAgent" : node.id}</div>
              <p className="mt-1 min-h-8 text-xs text-slate-600">{dagDescriptions[node.id] ?? node.label}</p>
              <div className="mt-2"><Pill value={inferredStatus} /></div>
              <div className="mt-3 space-y-1 text-xs text-slate-600">
                <div>输入：{schemas.input}</div>
                <div>输出：{schemas.output}</div>
                <div>Trace：{agentTraces.length}</div>
                <div>耗时：{elapsed}ms</div>
              </div>
              {index < nodes.length - 1 && <div className="absolute -right-3 top-1/2 hidden text-slate-400 lg:block">-&gt;</div>}
            </div>
          );
        })}
      </div>
      {qaRouteTo && (
        <div className="mt-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-danger">
          QA 打回边：QaAgent -&gt; {qaRouteTo}
        </div>
      )}
    </section>
  );
}
