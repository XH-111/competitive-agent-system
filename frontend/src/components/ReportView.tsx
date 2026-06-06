import { FileText } from "lucide-react";
import { useState } from "react";
import type { DimensionResult, Evidence, Report } from "../types";
import { EvidencePanel } from "./EvidencePanel";

export function ReportView({
  report,
  evidence,
  competitors,
  selectedFact,
  selectedEvidenceIds,
  onSelect,
}: {
  report?: Report;
  evidence: Evidence[];
  competitors?: string[];
  selectedFact?: DimensionResult;
  selectedEvidenceIds?: string[];
  onSelect: (fact: DimensionResult) => void;
}) {
  const [showJson, setShowJson] = useState(false);
  if (!report) return null;

  const legacyKnowledge = report.json_report.knowledge as { dimension_results?: DimensionResult[] } | undefined;
  const facts = report.dimension_results?.length
    ? report.dimension_results
    : report.json_report.dimension_results?.length
      ? report.json_report.dimension_results
      : legacyKnowledge?.dimension_results ?? [];
  const coverage = (competitors ?? Array.from(new Set(facts.map((item) => item.competitor).filter(Boolean))) as string[])
    .map((competitor) => ({
      competitor,
      evidenceCount: evidence.filter((item) => item.competitor === competitor).length,
      relevantEvidenceCount: evidence.filter(
        (item) => item.competitor === competitor && ["high", "medium"].includes(item.relevance_level ?? "high"),
      ).length,
      factCount: facts.filter((item) => item.competitor === competitor && !item.insufficient_evidence).length,
      insufficientCount: facts.filter((item) => item.competitor === competitor && item.insufficient_evidence).length,
    }));

  return (
    <section className="grid gap-4 bg-white p-4 lg:grid-cols-[minmax(0,1fr)_420px]">
      <div>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <FileText size={18} /> 分析报告
          </h2>
          <button
            className="rounded border border-line px-3 py-1.5 text-sm font-semibold"
            onClick={() => setShowJson((value) => !value)}
          >
            {showJson ? "收起 JSON 报告" : "展开 JSON 报告"}
          </button>
        </div>
        <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap rounded border border-line bg-panel p-4 text-sm leading-6">
          {report.markdown}
        </pre>
        {showJson && (
          <pre className="mt-3 max-h-80 overflow-auto rounded border border-line bg-panel p-4 text-xs leading-5">
            {JSON.stringify(report.json_report, null, 2)}
          </pre>
        )}
      </div>

      <div className="space-y-3">
        {coverage.length > 0 && (
          <section className="rounded border border-line bg-white p-4">
            <h3 className="mb-3 text-sm font-semibold">竞品事实覆盖</h3>
            <div className="space-y-2 text-sm">
              {coverage.map((item) => (
                <div
                  key={item.competitor}
                  className={`rounded border px-3 py-2 ${
                    item.relevantEvidenceCount === 0 || item.factCount === 0
                      ? "border-amber-300 bg-amber-50 text-warning"
                      : "border-green-300 bg-green-50 text-success"
                  }`}
                >
                  <span className="font-semibold">{item.competitor}</span>
                  <span className="ml-2">
                    Evidence {item.evidenceCount} / 相关证据 {item.relevantEvidenceCount} / 已支持事实 {item.factCount} / 证据不足 {item.insufficientCount}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="rounded border border-line bg-white p-4">
          <h3 className="mb-3 text-sm font-semibold">结构化事实</h3>
          <div className="max-h-[520px] space-y-2 overflow-auto pr-1">
            {facts.map((fact) => (
              <button
                key={fact.dimension_result_id}
                onClick={() => onSelect(fact)}
                className={`block w-full rounded border p-3 text-left text-sm ${
                  selectedFact?.dimension_result_id === fact.dimension_result_id
                    ? "border-accent bg-blue-50"
                    : fact.insufficient_evidence
                      ? "border-amber-300 bg-amber-50"
                      : "border-line bg-panel"
                }`}
              >
                <div className="font-semibold">
                  {fact.competitor ?? "综合"} · {fact.dimension_id} · 置信度 {Math.round(fact.confidence * 100)}%
                </div>
                <div className="mt-1">{fact.summary}</div>
                {!!fact.findings.length && (
                  <div className="mt-2 text-xs text-slate-600">{fact.findings.slice(0, 3).join("；")}</div>
                )}
                <div className="mt-2 text-xs text-slate-500">
                  {fact.dimension_result_id} · {fact.evidence_ids.length ? `证据：${fact.evidence_ids.join(", ")}` : "当前公开证据不足"}
                </div>
              </button>
            ))}
            {!facts.length && <div className="text-sm text-slate-500">暂无结构化事实。</div>}
          </div>
        </section>

        <EvidencePanel
          evidence={evidence}
          evidenceIds={selectedEvidenceIds}
          selectedFactId={selectedFact?.dimension_result_id}
        />
      </div>
    </section>
  );
}
