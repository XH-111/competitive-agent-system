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
  if (!report) return null;
  const structured = parseStructuredReport(report);
  if (structured) {
    return <StructuredReportView structured={structured} />;
  }

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
        </div>
        <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap rounded border border-line bg-panel p-4 text-sm leading-6">
          {report.markdown}
        </pre>
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

type StructuredReport = {
  report_title: string;
  sections: StructuredSection[];
  evidence_refs: Record<string, StructuredEvidenceRef>;
};

type StructuredSection = {
  section_id: string;
  section_no: string;
  title: string;
  summary: string;
  competitor_analyses: Array<Record<string, unknown>>;
  comparison?: string | null;
  limitations: string[];
  evidence_ids: string[];
  confidence: string;
};

type StructuredEvidenceRef = {
  evidence_id: string;
  title?: string | null;
  url?: string | null;
  source_domain?: string | null;
  source_quality?: string | null;
  snippet?: string | null;
};

function StructuredReportView({ structured }: { structured: StructuredReport }) {
  const [activeSectionId, setActiveSectionId] = useState(structured.sections[0]?.section_id);
  const activeSection = structured.sections.find((section) => section.section_id === activeSectionId) ?? structured.sections[0];
  const activeEvidence = activeSection
    ? activeSection.evidence_ids.map((id) => structured.evidence_refs[id]).filter(Boolean)
    : [];

  return (
    <section className="bg-white">
      <header className="border-b border-line px-5 py-4">
        <h2 className="text-lg font-semibold text-ink">{structured.report_title}</h2>
      </header>
      <div className="grid min-h-[720px] grid-cols-1 lg:grid-cols-[220px_minmax(0,1fr)_360px]">
        <aside className="border-b border-line bg-panel p-4 lg:border-b-0 lg:border-r">
          <div className="mb-3 text-sm font-semibold">章节目录</div>
          <nav className="space-y-1">
            {structured.sections.map((section) => (
              <button
                key={section.section_id}
                className={`block w-full rounded px-3 py-2 text-left text-sm ${
                  activeSection?.section_id === section.section_id
                    ? "bg-blue-50 font-semibold text-accent"
                    : "text-slate-700 hover:bg-white"
                }`}
                onClick={() => setActiveSectionId(section.section_id)}
              >
                {section.section_no} {section.title}
              </button>
            ))}
          </nav>
        </aside>

        <main className="space-y-4 p-5">
          {structured.sections.map((section) => (
            <article
              key={section.section_id}
              className={`rounded border p-5 transition-colors ${
                activeSection?.section_id === section.section_id
                  ? "border-blue-300 bg-blue-50/40"
                  : "border-line bg-white"
              }`}
              onClick={() => setActiveSectionId(section.section_id)}
            >
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-lg font-semibold">
                  {section.section_no} {section.title}
                </h3>
                <span className="rounded border border-line bg-white px-2 py-1 text-xs text-slate-600">
                  confidence: {section.confidence}
                </span>
              </div>
              <p className="leading-7 text-slate-800">{section.summary}</p>

              <div className="mt-4 space-y-3">
                {section.competitor_analyses.map((item, index) => (
                  <section key={`${section.section_id}-${index}`} className="rounded border border-line bg-white p-4">
                    <h4 className="mb-2 font-semibold">{stringValue(item.competitor) || `竞品 ${index + 1}`}</h4>
                    <p className="leading-7 text-slate-800">{stringValue(item.analysis) || stringValue(item.summary) || "暂无分析。"}</p>
                    <ListBlock title="优势" items={stringList(item.strengths)} />
                    <ListBlock title="不足或限制" items={stringList(item.weaknesses) || stringList(item.weaknesses_or_gaps)} />
                  </section>
                ))}
              </div>

              {section.comparison && (
                <section className="mt-4 rounded border border-line bg-panel p-4">
                  <h4 className="mb-2 font-semibold">优劣对比</h4>
                  <p className="leading-7">{section.comparison}</p>
                </section>
              )}

              {!!section.limitations.length && (
                <section className="mt-4 rounded border border-amber-300 bg-amber-50 p-4 text-warning">
                  <h4 className="mb-2 font-semibold">证据不足</h4>
                  <ul className="list-disc space-y-1 pl-5 text-sm">
                    {section.limitations.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </section>
              )}
            </article>
          ))}
        </main>

        <aside className="border-t border-line bg-panel p-4 lg:border-l lg:border-t-0">
          <div className="mb-1 text-sm font-semibold">Evidence 面板</div>
          <div className="mb-3 text-xs text-slate-500">
            当前章节：{activeSection ? `${activeSection.section_no} ${activeSection.title}` : "-"}
          </div>
          <div className="space-y-3">
            {activeEvidence.map((item) => (
              <section key={item.evidence_id} className="rounded border border-line bg-white p-3 text-sm">
                <div className="font-semibold">{item.title || item.evidence_id}</div>
                <div className="mt-1 text-xs text-slate-500">
                  {item.source_domain || "-"} · {item.source_quality || "unknown"}
                </div>
                {item.snippet && <p className="mt-2 leading-6 text-slate-700">{item.snippet}</p>}
                {item.url && (
                  <button
                    className="mt-3 rounded border border-line px-3 py-1.5 text-xs font-semibold text-accent"
                    onClick={() => window.open(item.url || "", "_blank", "noopener,noreferrer")}
                  >
                    打开来源
                  </button>
                )}
              </section>
            ))}
            {!activeEvidence.length && (
              <div className="rounded border border-line bg-white p-3 text-sm text-slate-500">
                当前章节没有可展示的 evidence。
              </div>
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}

function ListBlock({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="mt-3">
      <div className="mb-1 text-sm font-semibold text-slate-700">{title}</div>
      <ul className="list-disc space-y-1 pl-5 text-sm leading-6 text-slate-700">
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </div>
  );
}

function parseStructuredReport(report: Report): StructuredReport | undefined {
  const payload = report.json_report.report_agent as Record<string, unknown> | undefined;
  if (!payload || !Array.isArray(payload.sections)) return undefined;
  const sections = payload.sections.filter(isStructuredSection);
  if (!sections.length) return undefined;
  const evidenceRefsRaw = payload.evidence_refs;
  const evidence_refs = isRecord(evidenceRefsRaw)
    ? Object.fromEntries(
        Object.entries(evidenceRefsRaw)
          .filter(([, value]) => isRecord(value))
          .map(([key, value]) => [key, value as StructuredEvidenceRef]),
      )
    : {};
  return {
    report_title: stringValue(payload.report_title) || "竞品分析报告",
    sections,
    evidence_refs,
  };
}

function isStructuredSection(value: unknown): value is StructuredSection {
  if (!isRecord(value)) return false;
  return typeof value.section_id === "string"
    && typeof value.section_no === "string"
    && typeof value.title === "string"
    && typeof value.summary === "string"
    && Array.isArray(value.competitor_analyses)
    && Array.isArray(value.limitations)
    && Array.isArray(value.evidence_ids);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function stringList(value: unknown): string[] | undefined {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : undefined;
}
