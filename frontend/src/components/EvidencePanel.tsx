import { ExternalLink } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Evidence } from "../types";

export function EvidencePanel({
  evidence,
  evidenceIds,
  selectedFactId,
  selectable = false,
  selectedManualEvidenceIds = [],
  onManualEvidenceSelectionChange,
}: {
  evidence: Evidence[];
  evidenceIds?: string[];
  selectedFactId?: string;
  selectable?: boolean;
  selectedManualEvidenceIds?: string[];
  onManualEvidenceSelectionChange?: (ids: string[]) => void;
}) {
  const [competitorFilter, setCompetitorFilter] = useState("all");
  const [relevanceFilter, setRelevanceFilter] = useState("all");
  const [onlyRelevant, setOnlyRelevant] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const selectedIds = evidenceIds?.length ? evidenceIds : undefined;
  const selectedIdSet = useMemo(() => new Set(selectedIds ?? []), [selectedIds]);
  const manualSelection = new Set(selectedManualEvidenceIds);
  const competitors = useMemo(
    () => Array.from(new Set(evidence.map((item) => item.competitor).filter(Boolean))) as string[],
    [evidence],
  );

  const related = evidence
    .filter((item) => competitorFilter === "all" || item.competitor === competitorFilter)
    .filter((item) => relevanceFilter === "all" || item.relevance_level === relevanceFilter)
    .filter((item) => !onlyRelevant || ["high", "medium"].includes(item.relevance_level ?? "high"))
    .sort((a, b) => {
      const relevanceRank = { high: 4, medium: 3, low: 2, unrelated: 1 };
      return (
        Number(selectedIdSet.has(b.evidence_id)) - Number(selectedIdSet.has(a.evidence_id)) ||
        (relevanceRank[b.relevance_level ?? "high"] ?? 0) - (relevanceRank[a.relevance_level ?? "high"] ?? 0) ||
        b.confidence - a.confidence
      );
    });
  const totalPages = Math.max(1, Math.ceil(related.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pagedEvidence = related.slice((safePage - 1) * pageSize, safePage * pageSize);

  useEffect(() => {
    setPage(1);
  }, [competitorFilter, relevanceFilter, onlyRelevant, pageSize, evidence.length, selectedIds?.join("|")]);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  function toggleManualEvidence(evidenceId: string, checked: boolean) {
    if (!onManualEvidenceSelectionChange) return;
    const next = new Set(selectedManualEvidenceIds);
    if (checked) {
      next.add(evidenceId);
    } else {
      next.delete(evidenceId);
    }
    onManualEvidenceSelectionChange(Array.from(next));
  }

  function selectVisibleEvidence() {
    if (!onManualEvidenceSelectionChange) return;
    onManualEvidenceSelectionChange(Array.from(new Set([
      ...selectedManualEvidenceIds,
      ...pagedEvidence.map((item) => item.evidence_id),
    ])));
  }

  function clearVisibleEvidence() {
    if (!onManualEvidenceSelectionChange) return;
    const visible = new Set(pagedEvidence.map((item) => item.evidence_id));
    onManualEvidenceSelectionChange(selectedManualEvidenceIds.filter((id) => !visible.has(id)));
  }

  return (
    <section className="rounded border border-line bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">采集证据结果</h2>
          <p className="mt-1 text-xs text-slate-500">
            共 {evidence.length} 条 Evidence，当前显示 {related.length} 条
          </p>
          {selectable && (
            <p className="mt-1 text-xs text-slate-500">
              已选择 {selectedManualEvidenceIds.length} 条 Evidence
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {selectable && (
            <>
              <button
                type="button"
                className="rounded border border-line bg-white px-2 py-1 font-semibold"
                onClick={selectVisibleEvidence}
              >
                全选当前显示
              </button>
              <button
                type="button"
                className="rounded border border-line bg-white px-2 py-1 font-semibold"
                onClick={clearVisibleEvidence}
              >
                清除当前显示
              </button>
            </>
          )}
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
          <select
            className="rounded border border-line bg-white px-2 py-1"
            value={pageSize}
            onChange={(event) => setPageSize(Number(event.target.value))}
          >
            <option value={10}>10 / page</option>
            <option value={20}>20 / page</option>
            <option value={50}>50 / page</option>
          </select>
        </div>
      </div>

      {selectedFactId && !selectedIds?.length && (
        <div className="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm font-semibold text-danger">
          当前结构化事实缺少 evidence_ids，无法完成证据溯源。
        </div>
      )}

      <div className="space-y-3">
        {pagedEvidence.map((item) => (
          <div
            key={item.evidence_id}
            className={`rounded border p-3 text-sm ${
              selectedIdSet.has(item.evidence_id)
                ? "border-blue-400 bg-blue-50"
                : item.relevance_level === "unrelated" || item.confidence < 0.5 ? "border-amber-300 bg-amber-50" : "border-line bg-panel"
            }`}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="flex min-w-0 items-start gap-2">
                {selectable && (
                  <input
                    type="checkbox"
                    className="mt-1 h-4 w-4"
                    checked={manualSelection.has(item.evidence_id)}
                    onChange={(event) => toggleManualEvidence(item.evidence_id, event.target.checked)}
                    aria-label={`选择 Evidence ${item.evidence_id}`}
                  />
                )}
                <div>
                <div className="font-semibold">
                  {item.evidence_id} · {item.source_type}
                  {selectedIdSet.has(item.evidence_id) && (
                    <span className="ml-2 rounded border border-blue-300 bg-white px-2 py-0.5 text-xs text-blue-600">
                      referenced
                    </span>
                  )}
                </div>
                <div className="mt-1 text-xs text-slate-600">
                  竞品：{item.competitor ?? "-"} · 来源：{item.source_domain ?? "unknown"} · 质量：{item.source_quality ?? "unknown"}
                </div>
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
      {!!related.length && (
        <PaginationControls
          page={safePage}
          totalPages={totalPages}
          totalItems={related.length}
          pageSize={pageSize}
          onPageChange={setPage}
        />
      )}
    </section>
  );
}

function PaginationControls({
  page,
  totalPages,
  totalItems,
  pageSize,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  totalItems: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}) {
  const start = totalItems ? (page - 1) * pageSize + 1 : 0;
  const end = Math.min(totalItems, page * pageSize);
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3 text-xs text-slate-600">
      <span>
        显示 {start}-{end} / {totalItems}
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded border border-line bg-white px-2 py-1 font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          disabled={page <= 1}
          onClick={() => onPageChange(1)}
        >
          首页
        </button>
        <button
          type="button"
          className="rounded border border-line bg-white px-2 py-1 font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          上一页
        </button>
        <span className="rounded border border-line bg-panel px-2 py-1">
          {page} / {totalPages}
        </span>
        <button
          type="button"
          className="rounded border border-line bg-white px-2 py-1 font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </button>
        <button
          type="button"
          className="rounded border border-line bg-white px-2 py-1 font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          disabled={page >= totalPages}
          onClick={() => onPageChange(totalPages)}
        >
          末页
        </button>
      </div>
    </div>
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
