import { ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import type { Claim, Evidence } from "../types";

export function EvidencePanel({
  claim,
  evidence,
  evidenceIds,
}: {
  claim?: Claim;
  evidence: Evidence[];
  evidenceIds?: string[];
}) {
  const [competitorFilter, setCompetitorFilter] = useState("all");
  const [relevanceFilter, setRelevanceFilter] = useState("all");
  const [onlyRelevant, setOnlyRelevant] = useState(false);
  const selectedIds = evidenceIds?.length ? evidenceIds : undefined;
  const competitors = useMemo(
    () => Array.from(new Set(evidence.map((item) => item.competitor).filter(Boolean))) as string[],
    [evidence],
  );

  const related = (claim
    ? evidence.filter((item) => claim.evidence_ids.includes(item.evidence_id))
    : selectedIds
      ? evidence.filter((item) => selectedIds.includes(item.evidence_id))
      : evidence
  )
    .filter((item) => competitorFilter === "all" || item.competitor === competitorFilter)
    .filter((item) => relevanceFilter === "all" || item.relevance_level === relevanceFilter)
    .filter((item) => !onlyRelevant || ["high", "medium"].includes(item.relevance_level ?? "high"))
    .sort((a, b) => {
      const relevanceRank = { high: 4, medium: 3, low: 2, unrelated: 1 };
      return (
        (relevanceRank[b.relevance_level ?? "high"] ?? 0) - (relevanceRank[a.relevance_level ?? "high"] ?? 0) ||
        b.confidence - a.confidence
      );
    });

  return (
    <section className="rounded border border-line bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">采集证据结果</h2>
          <p className="mt-1 text-xs text-slate-500">
            共 {evidence.length} 条 Evidence，当前显示 {related.length} 条
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <select className="rounded border border-line bg-white px-2 py-1" value={competitorFilter} onChange={(event) => setCompetitorFilter(event.target.value)}>
            <option value="all">全部竞品</option>
            {competitors.map((competitor) => (
              <option key={competitor} value={competitor}>{competitor}</option>
            ))}
          </select>
          <select className="rounded border border-line bg-white px-2 py-1" value={relevanceFilter} onChange={(event) => setRelevanceFilter(event.target.value)}>
            <option value="all">全部相关性</option>
            <option value="high">high</option>
            <option value="medium">medium</option>
            <option value="low">low</option>
            <option value="unrelated">unrelated</option>
          </select>
          <label className="flex items-center gap-1 rounded border border-line bg-panel px-2 py-1">
            <input type="checkbox" checked={onlyRelevant} onChange={(event) => setOnlyRelevant(event.target.checked)} />
            只看 high/medium
          </label>
        </div>
      </div>

      {claim && !claim.evidence_ids.length && (
        <div className="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm font-semibold text-danger">
          当前 Claim 缺少 evidence_ids，无法完成证据溯源。
        </div>
      )}

      <div className="space-y-3">
        {related.map((item) => (
          <div
            key={item.evidence_id}
            className={`rounded border p-3 text-sm ${
              item.relevance_level === "unrelated" || item.confidence < 0.5 ? "border-amber-300 bg-amber-50" : "border-line bg-panel"
            }`}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="font-semibold">{item.evidence_id} · {item.source_type}</div>
                <div className="mt-1 text-xs text-slate-600">
                  竞品：{item.competitor ?? "-"} · 来源：{item.source_domain ?? "unknown"} · 质量：{item.source_quality ?? "unknown"}
                </div>
              </div>
              {item.url && (
                <a
                  className="inline-flex items-center gap-1 rounded border border-line bg-white px-2 py-1 text-xs font-semibold text-accent"
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开来源 <ExternalLink size={12} />
                </a>
              )}
            </div>
            <div className="mt-2 text-slate-700">{item.snippet}</div>
            <div className="mt-2 break-all text-xs text-slate-500">{item.url ?? item.local_ref}</div>
            <EvidenceQuerySummary evidence={item} />
            <div className="mt-2 grid gap-1 text-xs text-slate-600 md:grid-cols-2">
              <span>内容模式：{item.content_mode === "page" ? "正文摘要" : "搜索摘要"}</span>
              <span>正文抓取：{item.page_fetch_success ? "成功" : "未抓取或失败"}</span>
              {item.page_title && <span>网页标题：{item.page_title}</span>}
              {typeof item.content_chars === "number" && <span>正文字符数：{item.content_chars}</span>}
              {typeof item.fetch_status_code === "number" && <span>HTTP 状态：{item.fetch_status_code}</span>}
              {item.page_fetch_error && <span>抓取错误：{item.page_fetch_error}</span>}
              <span>置信度：{Math.round(item.confidence * 100)}% · {confidenceLabel(item.confidence)}</span>
              <span>相关性：{Math.round((item.relevance_score ?? 1) * 100)}% · {item.relevance_level ?? "high"}</span>
              <span className="md:col-span-2">相关性原因：{item.relevance_reason ?? "-"}</span>
              <span>采集时间：{item.collected_at ? new Date(item.collected_at).toLocaleString() : "-"}</span>
            </div>
            <ScoringExplanation evidence={item} />
            {item.content_excerpt && (
              <details className="mt-2 text-xs text-slate-600">
                <summary className="cursor-pointer font-semibold">查看正文摘要</summary>
                <div className="mt-1 rounded border border-line bg-white p-2">
                  <p className="whitespace-pre-wrap">{item.content_excerpt.slice(0, 500)}{item.content_excerpt.length > 500 ? "..." : ""}</p>
                  {item.content_excerpt.length > 500 && (
                    <details className="mt-2">
                      <summary className="cursor-pointer font-semibold">展开完整摘要</summary>
                      <p className="mt-1 whitespace-pre-wrap">{item.content_excerpt}</p>
                    </details>
                  )}
                </div>
              </details>
            )}
            {item.entity_match_signals && (
              <details className="mt-2 text-xs text-slate-600">
                <summary className="cursor-pointer font-semibold">开发者详情：entity_match_signals</summary>
                <pre className="mt-1 whitespace-pre-wrap rounded border border-line bg-white p-2">{JSON.stringify(item.entity_match_signals, null, 2)}</pre>
              </details>
            )}
          </div>
        ))}
        {!related.length && <p className="text-sm text-slate-500">暂无可展示证据。</p>}
      </div>
    </section>
  );
}

function EvidenceQuerySummary({ evidence }: { evidence: Evidence }) {
  const signals = evidence.entity_match_signals ?? {};
  const collectorQuery = typeof signals.collector_query === "string" ? signals.collector_query : undefined;
  const collectorDimension = typeof signals.collector_dimension === "string" ? signals.collector_dimension : undefined;
  const matched = getMatchedAliasEntries(signals.matched_aliases_by_field);
  if (!collectorQuery && !matched.length) return null;

  return (
    <div className="mt-2 rounded border border-blue-100 bg-blue-50 p-2 text-xs text-slate-700">
      {collectorQuery && (
        <div>
          <span className="font-semibold text-accent">搜索关键词：</span>
          <span>{collectorQuery}</span>
          {collectorDimension && <span className="ml-2 text-slate-500">维度：{collectorDimension}</span>}
        </div>
      )}
      {!!matched.length && (
        <div className="mt-1">
          <span className="font-semibold text-accent">命中的竞品名/别名：</span>
          <div className="mt-1 flex flex-wrap gap-1">
            {matched.map((item) => (
              <span key={`${item.field}-${item.alias}`} className="rounded border border-blue-200 bg-white px-2 py-0.5">
                {fieldLabel(item.field)}：{item.alias}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ScoringExplanation({ evidence }: { evidence: Evidence }) {
  const signals = evidence.entity_match_signals ?? {};
  const confidenceBreakdown = getRecord(signals.confidence_breakdown);
  const matchedAliasesByField = getRecord(signals.matched_aliases_by_field);
  const relevanceRows = [
    {
      label: "标题命中",
      matched: Boolean(signals.competitor_in_title),
      weight: 0.35,
      note: "title 中出现竞品名或别名",
    },
    {
      label: "摘要命中",
      matched: Boolean(signals.competitor_in_snippet),
      weight: 0.35,
      note: "snippet 中出现竞品名或别名",
    },
    {
      label: "URL 命中",
      matched: Boolean(signals.competitor_in_url),
      weight: 0.2,
      note: "url 中出现竞品名或别名",
    },
    {
      label: "域名命中",
      matched: Boolean(signals.competitor_in_domain),
      weight: 0.2,
      note: "domain 中出现竞品名或别名",
    },
    {
      label: "域名相似",
      matched: Number(signals.domain_similarity_score ?? 0) >= 0.75,
      weight: 0.1,
      note: `domain_similarity_score=${formatNumber(signals.domain_similarity_score)}`,
    },
  ];

  return (
    <details className="mt-2 rounded border border-line bg-white p-2 text-xs text-slate-700">
      <summary className="cursor-pointer font-semibold">查看相关性与置信度计算过程</summary>
      <div className="mt-2 grid gap-3 lg:grid-cols-2">
        <div>
          <div className="mb-1 font-semibold">相关性计算</div>
          <div className="space-y-1">
            {relevanceRows.map((row) => (
              <div key={row.label} className="flex items-start justify-between gap-2 rounded border border-line bg-panel px-2 py-1">
                <div>
                  <span className={row.matched ? "font-semibold text-success" : "text-slate-500"}>
                    {row.matched ? "命中" : "未命中"} · {row.label}
                  </span>
                  <div className="text-slate-500">{row.note}</div>
                  <MatchedAliasLine field={fieldKey(row.label)} matchedAliasesByField={matchedAliasesByField} />
                </div>
                <span className="font-semibold">{row.matched ? `+${row.weight}` : "+0"}</span>
              </div>
            ))}
          </div>
          <div className="mt-2 rounded border border-line bg-panel px-2 py-1">
            最终相关性：{formatPercent(evidence.relevance_score)} · {evidence.relevance_level ?? "-"}
          </div>
          {Array.isArray(signals.aliases_used) && (
            <div className="mt-2">
              <div className="mb-1 font-semibold">参与匹配的别名</div>
              <div className="flex flex-wrap gap-1">
                {signals.aliases_used.slice(0, 12).map((alias) => (
                  <span key={String(alias)} className="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-accent">
                    {String(alias)}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        <div>
          <div className="mb-1 font-semibold">置信度计算</div>
          {confidenceBreakdown ? (
            <div className="space-y-1 rounded border border-line bg-panel p-2">
              <div>来源质量：{String(confidenceBreakdown.source_quality ?? evidence.source_quality ?? "unknown")}</div>
              <div>来源质量基础分：{formatNumber(confidenceBreakdown.source_quality_base)}</div>
              <div>搜索结果分：{formatNumber(confidenceBreakdown.search_score)}</div>
              <div>搜索分换算置信度：{formatNumber(confidenceBreakdown.search_score_confidence)}</div>
              <div>公式：{String(confidenceBreakdown.formula ?? "-")}</div>
              <div className="font-semibold">最终置信度：{formatPercent(confidenceBreakdown.final_confidence ?? evidence.confidence)}</div>
              <div className="text-slate-500">{String(confidenceBreakdown.reason ?? "")}</div>
            </div>
          ) : (
            <div className="rounded border border-line bg-panel p-2">
              当前 Evidence 来自旧 run，未记录置信度拆解；只能看到最终置信度 {formatPercent(evidence.confidence)}。
            </div>
          )}
        </div>
      </div>
    </details>
  );
}

function getRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function MatchedAliasLine({ field, matchedAliasesByField }: { field?: string; matchedAliasesByField?: Record<string, unknown> }) {
  if (!field || !matchedAliasesByField) return null;
  const aliases = getStringArray(matchedAliasesByField[field]);
  if (!aliases.length) return null;
  return <div className="text-accent">命中别名：{aliases.join(", ")}</div>;
}

function getMatchedAliasEntries(value: unknown): Array<{ field: string; alias: string }> {
  const record = getRecord(value);
  if (!record) return [];
  return Object.entries(record).flatMap(([field, aliases]) =>
    getStringArray(aliases).map((alias) => ({ field, alias })),
  );
}

function getStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0) : [];
}

function fieldKey(label: string): string | undefined {
  if (label === "标题命中") return "title";
  if (label === "摘要命中") return "snippet";
  if (label === "URL 命中") return "url";
  if (label === "域名命中") return "domain";
  return undefined;
}

function fieldLabel(field: string): string {
  if (field === "title") return "标题";
  if (field === "snippet") return "摘要";
  if (field === "url") return "URL";
  if (field === "domain") return "域名";
  return field;
}

function formatNumber(value: unknown): string {
  if (typeof value !== "number") return "-";
  return String(Math.round(value * 100) / 100);
}

function formatPercent(value: unknown): string {
  if (typeof value !== "number") return "-";
  return `${Math.round(value * 100)}%`;
}

function confidenceLabel(confidence: number) {
  if (confidence >= 0.8) return "高可信";
  if (confidence >= 0.5) return "中可信";
  return "低可信";
}
