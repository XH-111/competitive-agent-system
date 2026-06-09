import { AlertTriangle, CheckCircle, Database } from "lucide-react";
import type { Evidence, Report, SwotAnalysis, SwotItem } from "../types";

type DimensionResult = {
  dimension_id?: string;
  competitor?: string | null;
  summary?: string;
  findings?: string[];
  evidence_ids?: string[];
  confidence?: number;
  insufficient_evidence?: boolean;
  metadata?: Record<string, unknown>;
};

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
  const dimensionResults = getDimensionResults(knowledge.dimension_results);
  const legacyEntries = Object.entries(knowledge).filter(([key]) => key !== "dimension_results");

  return (
    <section className="rounded border border-line bg-white p-4">
      <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold">
        <Database size={18} /> 维度分析结果
      </h2>

      <DimensionResultsPanel
        results={dimensionResults}
        evidenceById={evidenceById}
        onEvidenceIdsSelect={onEvidenceIdsSelect}
      />

      {swot && (
        <details className="mt-3 rounded border border-line bg-panel p-3" open>
          <summary className="cursor-pointer text-sm font-semibold text-slate-700">SWOT 兼容摘要</summary>
          <div className="mt-3">
            <SwotPanel swot={swot} evidenceById={evidenceById} onEvidenceIdsSelect={onEvidenceIdsSelect} />
          </div>
        </details>
      )}

      <details className="mt-3 rounded border border-line bg-white p-3">
        <summary className="cursor-pointer text-sm font-semibold text-slate-700">
          兼容摘要 ProductProfile / FeatureTree / PricingModel / UserPersona
        </summary>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {legacyEntries.map(([key, value]) => (
            <LegacyKnowledgeCard
              key={key}
              keyName={key}
              value={value}
              evidenceById={evidenceById}
              onEvidenceIdsSelect={onEvidenceIdsSelect}
            />
          ))}
        </div>
      </details>
    </section>
  );
}

function DimensionResultsPanel({
  results,
  evidenceById,
  onEvidenceIdsSelect,
}: {
  results: DimensionResult[];
  evidenceById: Map<string, Evidence>;
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  if (!results.length) {
    return (
      <div className="rounded border border-dashed border-line bg-panel p-4 text-sm text-slate-600">
        当前 AnalystAgent 尚未返回 dimension_results，系统仍会展示兼容摘要。
      </div>
    );
  }

  return (
    <div className="grid gap-3 md:grid-cols-2">
      {results.map((result, index) => {
        const evidenceIds = result.evidence_ids ?? [];
        const insufficient = Boolean(result.insufficient_evidence);
        return (
          <article
            key={`${result.dimension_id ?? "dimension"}-${result.competitor ?? "overall"}-${index}`}
            className={`rounded border p-3 ${insufficient ? "border-amber-300 bg-amber-50" : "border-line bg-panel"}`}
          >
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-semibold">{dimensionLabel(result.dimension_id)}</div>
                <div className="text-xs text-slate-500">
                  dimension_id: {result.dimension_id ?? "-"} | 竞品：{result.competitor ?? "整体"}
                </div>
              </div>
              <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold ${insufficient ? "border-amber-300 bg-white text-warning" : "border-green-300 bg-green-50 text-success"}`}>
                {insufficient ? <AlertTriangle size={13} /> : <CheckCircle size={13} />}
                {insufficient ? "证据不足" : `置信度 ${Math.round((result.confidence ?? 0) * 100)}%`}
              </span>
            </div>

            <p className="rounded border border-line bg-white px-3 py-2 text-sm leading-6 text-slate-800">
              {result.summary ?? "暂无摘要"}
            </p>

            {!!result.findings?.length && (
              <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-700">
                {result.findings.slice(0, 4).map((finding, itemIndex) => (
                  <li key={`${finding}-${itemIndex}`}>- {finding}</li>
                ))}
              </ul>
            )}

            <EvidenceButtons
              evidenceIds={evidenceIds}
              evidenceById={evidenceById}
              onEvidenceIdsSelect={onEvidenceIdsSelect}
            />
          </article>
        );
      })}
    </div>
  );
}

function LegacyKnowledgeCard({
  keyName,
  value,
  evidenceById,
  onEvidenceIdsSelect,
}: {
  keyName: string;
  value: unknown;
  evidenceById: Map<string, Evidence>;
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  const evidenceIds = extractEvidenceIds(value);
  const insufficient = JSON.stringify(value).includes("Evidence is insufficient");
  const status = summarizeKnowledgeStatus(value, evidenceById, insufficient);

  return (
    <div className={`rounded border p-3 ${insufficient ? "border-amber-300 bg-amber-50" : "border-line bg-panel"}`}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold">
          {knowledgeLabel(keyName)} <span className="text-xs font-normal text-slate-500">{keyName}</span>
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
      </div>

      {keyName === "product_profile" && (
        <CompetitorEvidenceSummary value={value} evidenceById={evidenceById} onEvidenceIdsSelect={onEvidenceIdsSelect} />
      )}

      <EvidenceButtons evidenceIds={evidenceIds} evidenceById={evidenceById} onEvidenceIdsSelect={onEvidenceIdsSelect} />

    </div>
  );
}

function EvidenceButtons({
  evidenceIds,
  evidenceById,
  onEvidenceIdsSelect,
}: {
  evidenceIds: string[];
  evidenceById: Map<string, Evidence>;
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  if (!evidenceIds.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1 text-xs">
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
    <div className="grid gap-3 md:grid-cols-2">
      {sections.map(({ key, label }) => (
        <div key={key} className="rounded border border-line bg-white p-3">
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
    <div className="rounded border border-line bg-panel p-3 text-xs leading-5">
      <div className="font-semibold">{item.competitor ?? "整体"} · 置信度 {Math.round(item.confidence * 100)}%</div>
      <div className="mt-1">{item.summary}</div>
      <EvidenceButtons evidenceIds={item.evidence_ids} evidenceById={evidenceById} onEvidenceIdsSelect={onEvidenceIdsSelect} />
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
            <EvidenceButtons evidenceIds={evidenceIds.slice(0, 8)} evidenceById={evidenceById} onEvidenceIdsSelect={onEvidenceIdsSelect} />
          </div>
        );
      })}
    </div>
  );
}

function getDimensionResults(value: unknown): DimensionResult[] {
  return Array.isArray(value) ? value.filter((item): item is DimensionResult => Boolean(asRecord(item))) : [];
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
      : "当前兼容摘要已有可追溯 Evidence 支撑，可用于报告中的保守结论。",
  };
}

function dimensionLabel(key?: string): string {
  const labels: Record<string, string> = {
    positioning: "产品定位",
    feature: "功能能力",
    features: "功能能力",
    pricing: "定价与商业模式",
    business_model: "商业模式",
    persona: "用户画像",
    user_persona: "用户画像",
    swot: "SWOT 分析",
    risk: "风险",
    ux: "用户体验",
    feedback: "用户反馈",
  };
  return labels[key ?? ""] ?? (key ?? "未命名维度");
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
