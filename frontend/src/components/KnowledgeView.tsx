import { AlertTriangle, CheckCircle, Database } from "lucide-react";
import type { Evidence, Report, SwotAnalysis, SwotItem } from "../types";

export function KnowledgeView({
  report,
  evidence,
  onEvidenceIdsSelect,
}: {
  report?: Report;
  evidence?: Evidence[];
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  const knowledge = report?.json_report.knowledge;
  if (!knowledge) return null;

  const swot = report?.json_report.swot;
  const evidenceById = new Map((evidence ?? []).map((item) => [item.evidence_id, item]));

  return (
    <section className="rounded border border-line bg-white p-4">
      <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold">
        <Database size={18} /> 结构化知识
      </h2>

      {swot && (
        <div className="mb-3">
          <div className="mb-2 text-sm font-semibold text-slate-700">SWOT 结构化摘要</div>
          <SwotPanel swot={swot} evidenceById={evidenceById} onEvidenceIdsSelect={onEvidenceIdsSelect} />
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {Object.entries(knowledge).map(([key, value]) => {
          const evidenceIds = extractEvidenceIds(value);
          const insufficient = JSON.stringify(value).includes("Evidence is insufficient");
          const status = summarizeKnowledgeStatus(value, evidenceById, insufficient);

          return (
            <div key={key} className={`rounded border p-3 ${insufficient ? "border-amber-300 bg-amber-50" : "border-line bg-panel"}`}>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <div className="text-sm font-semibold">
                  {knowledgeLabel(key)} <span className="text-xs font-normal text-slate-500">{key}</span>
                </div>
                <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold ${insufficient ? "border-amber-300 bg-white text-warning" : "border-green-300 bg-green-50 text-success"}`}>
                  {insufficient ? <AlertTriangle size={13} /> : <CheckCircle size={13} />}
                  {insufficient ? "暂不做强结论" : "可形成初步结论"}
                </span>
              </div>

              <div className="mb-2 rounded border border-line bg-white px-3 py-2 text-xs leading-5 text-slate-700">
                <div className="font-semibold text-slate-900">{status.summary}</div>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                  <span>绑定 Evidence：{evidenceIds.length} 条</span>
                  <span>high/medium：{status.relevantEvidenceCount} 条</span>
                  <span>来源域名：{status.sourceDomains.length ? status.sourceDomains.slice(0, 4).join(", ") : "-"}</span>
                </div>
                {insufficient && (
                  <div className="mt-1 text-warning">
                    原因：当前证据虽存在，但还没有从竞品专属 high/medium Evidence 中抽取到足够的功能、定价或用户画像信号，因此只能作为弱支撑。
                  </div>
                )}
              </div>

              {key === "product_profile" && (
                <CompetitorEvidenceSummary value={value} evidenceById={evidenceById} onEvidenceIdsSelect={onEvidenceIdsSelect} />
              )}

              {evidenceIds.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1 text-xs">
                  {evidenceIds.map((id) => (
                    <button
                      key={id}
                      className="rounded border border-line bg-white px-2 py-0.5 text-slate-700 hover:border-accent"
                      onClick={() => onEvidenceIdsSelect?.([id])}
                      title={evidenceById.get(id)?.source_domain ?? id}
                    >
                      {id}
                    </button>
                  ))}
                </div>
              )}

              <details className="mt-2 rounded border border-line bg-white">
                <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-600">查看原始 JSON</summary>
                <pre className="max-h-56 overflow-auto whitespace-pre-wrap border-t border-line p-3 text-xs leading-5">
                  {JSON.stringify(value, null, 2)}
                </pre>
              </details>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function SwotPanel({
  swot,
  evidenceById,
  onEvidenceIdsSelect,
}: {
  swot: SwotAnalysis;
  evidenceById: Map<string, Evidence>;
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  const sections: Array<{ key: keyof SwotAnalysis; label: string }> = [
    { key: "strengths", label: "优势" },
    { key: "weaknesses", label: "劣势" },
    { key: "opportunities", label: "机会" },
    { key: "threats", label: "威胁" },
  ];

  return (
    <div className="mb-3 grid gap-3 md:grid-cols-2">
      {sections.map(({ key, label }) => (
        <div key={key} className="rounded border border-line bg-panel p-3">
          <div className="mb-2 text-sm font-semibold">{label}</div>
          <div className="space-y-2">
            {swot[key].map((item, index) => (
              <SwotCard
                key={`${key}-${item.competitor ?? "overall"}-${index}`}
                item={item}
                evidenceById={evidenceById}
                onEvidenceIdsSelect={onEvidenceIdsSelect}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function SwotCard({
  item,
  evidenceById,
  onEvidenceIdsSelect,
}: {
  item: SwotItem;
  evidenceById: Map<string, Evidence>;
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  return (
    <div className="rounded border border-line bg-white p-3 text-xs leading-5">
      <div className="font-semibold">{item.competitor ?? "整体"} · 置信度 {Math.round(item.confidence * 100)}%</div>
      <div className="mt-1">{item.summary}</div>
      <div className="mt-2 flex flex-wrap gap-1">
        {item.evidence_ids.map((id) => (
          <button
            key={id}
            className="rounded border border-line bg-panel px-2 py-0.5 text-slate-700 hover:border-accent"
            onClick={() => onEvidenceIdsSelect?.([id])}
            title={evidenceById.get(id)?.source_domain ?? id}
          >
            {id}
          </button>
        ))}
      </div>
    </div>
  );
}

function CompetitorEvidenceSummary({
  value,
  evidenceById,
  onEvidenceIdsSelect,
}: {
  value: unknown;
  evidenceById: Map<string, Evidence>;
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  const competitorAnalysis = getCompetitorAnalysis(value);
  if (!competitorAnalysis.length) return null;

  return (
    <div className="mb-2 space-y-2">
      {competitorAnalysis.map((item) => {
        const missing = missingSignals(item.details);
        const evidenceIds = getStringArray(item.details.evidence_ids);
        return (
          <div key={item.competitor} className="rounded border border-line bg-white p-3 text-xs leading-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-semibold text-slate-900">{item.competitor}</div>
              <span className={`rounded-full border px-2 py-0.5 font-semibold ${item.details.insufficient_evidence ? "border-amber-300 bg-amber-50 text-warning" : "border-green-300 bg-green-50 text-success"}`}>
                {item.details.insufficient_evidence ? "证据信号不足" : "信号可用"}
              </span>
            </div>
            <div className="mt-1 text-slate-600">
              已用 Evidence：{evidenceIds.length} 条
              {missing.length > 0 && <span> · 缺少信号：{missing.join("、")}</span>}
            </div>
            {evidenceIds.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {evidenceIds.slice(0, 8).map((id) => (
                  <button
                    key={id}
                    className="rounded border border-line bg-panel px-2 py-0.5 text-slate-700 hover:border-accent"
                    onClick={() => onEvidenceIdsSelect?.([id])}
                    title={evidenceById.get(id)?.source_domain ?? id}
                  >
                    {id}
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function extractEvidenceIds(value: unknown): string[] {
  const ids = new Set<string>();

  function walk(item: unknown) {
    if (Array.isArray(item)) {
      item.forEach(walk);
      return;
    }
    if (item && typeof item === "object") {
      for (const [key, nested] of Object.entries(item)) {
        if (key === "evidence_ids" && Array.isArray(nested)) {
          nested.forEach((id) => {
            if (typeof id === "string") ids.add(id);
          });
        } else {
          walk(nested);
        }
      }
    }
  }

  walk(value);
  return Array.from(ids);
}

function summarizeKnowledgeStatus(value: unknown, evidenceById: Map<string, Evidence>, insufficient: boolean) {
  const ids = extractEvidenceIds(value);
  const records = ids.map((id) => evidenceById.get(id)).filter((item): item is Evidence => Boolean(item));
  const relevantEvidenceCount = records.filter((item) => item.relevance_level === "high" || item.relevance_level === "medium").length;
  const sourceDomains = Array.from(new Set(records.map((item) => item.source_domain).filter((item): item is string => Boolean(item))));
  return {
    relevantEvidenceCount,
    sourceDomains,
    summary: insufficient
      ? "已有证据，但当前只适合作为弱支撑，不能直接作为强结论。"
      : "当前结构化知识已有可追溯 Evidence 支撑，可用于报告中的保守结论。",
  };
}

function knowledgeLabel(key: string): string {
  const labels: Record<string, string> = {
    product_profile: "产品画像",
    feature_tree: "功能能力",
    pricing_model: "定价模式",
    user_persona: "用户画像",
    diagnostics: "分析诊断",
  };
  return labels[key] ?? key;
}

function getCompetitorAnalysis(value: unknown): Array<{ competitor: string; details: Record<string, unknown> }> {
  const root = asRecord(value);
  const customDimensions = asRecord(root?.custom_dimensions);
  const competitorAnalysis = asRecord(customDimensions?.competitor_analysis);
  if (!competitorAnalysis) return [];
  return Object.entries(competitorAnalysis)
    .map(([competitor, details]) => ({ competitor, details: asRecord(details) ?? {} }))
    .filter((item) => item.competitor);
}

function missingSignals(details: Record<string, unknown>): string[] {
  const missing: string[] = [];
  const features = getStringArray(details.features);
  const pricing = getStringArray(details.pricing);
  const persona = getStringArray(details.persona);
  if (!features.length || features.some((item) => item.toLowerCase().includes("insufficient"))) missing.push("功能");
  if (!pricing.length || pricing.some((item) => item.toLowerCase().includes("insufficient"))) missing.push("定价");
  if (!persona.length || persona.some((item) => item.toLowerCase().includes("insufficient"))) missing.push("用户画像");
  return missing;
}

function getStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}
