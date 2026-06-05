import type { CollectorDiagnostics, WorkflowSummary } from "../types";

const DIMENSION_LABELS: Record<string, string> = {
  pricing: "价格",
  feature: "功能",
  persona: "用户画像",
  strength: "优势",
  weakness: "劣势",
  opportunity: "机会",
  threat: "威胁",
};

export function PlannerSummaryCard({
  workflowSummary,
  collectorDiagnostics,
}: {
  workflowSummary?: WorkflowSummary;
  collectorDiagnostics?: CollectorDiagnostics;
}) {
  const hasPlannerSummary = Boolean(
    workflowSummary?.selected_dimensions?.length ||
    workflowSummary?.intent_classification ||
    workflowSummary?.ambiguity_level ||
    workflowSummary?.scope_type ||
    workflowSummary?.scope_size ||
    workflowSummary?.candidate_competitors?.length ||
    typeof workflowSummary?.survey_needed === "boolean" ||
    workflowSummary?.recommended_next_constraints?.length,
  );
  const hasCollectorGuidance = Boolean(
    collectorDiagnostics?.planner_query_hints_used ||
    collectorDiagnostics?.targeted_recollection_used ||
    collectorDiagnostics?.effective_query_count_by_competitor ||
    collectorDiagnostics?.effective_queries_preview_by_competitor,
  );

  if (!hasPlannerSummary && !hasCollectorGuidance) return null;

  return (
    <section className="mb-4 grid gap-3 lg:grid-cols-2">
      {hasPlannerSummary && (
        <div className="rounded border border-line bg-white p-4">
          <h2 className="mb-3 text-base font-semibold">规划摘要</h2>
          <div className="grid gap-2 text-sm md:grid-cols-2">
            <SummaryItem label="意图" value={workflowSummary?.intent_classification ?? "-"} />
            <SummaryItem label="歧义程度" value={workflowSummary?.ambiguity_level ?? "-"} />
            <SummaryItem label="范围类型" value={workflowSummary?.scope_type ?? "-"} />
            <SummaryItem label="范围大小" value={workflowSummary?.scope_size ?? "-"} />
            <SummaryItem label="需要问卷" value={formatBoolean(workflowSummary?.survey_needed)} />
            <SummaryItem label="建议问卷" value={formatBoolean(workflowSummary?.survey_recommended)} />
            <SummaryItem label="run_id" value={workflowSummary?.run_id ?? "-"} />
          </div>
          <div className="mt-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">固定分析维度</div>
            <div className="flex flex-wrap gap-2">
              {(workflowSummary?.selected_dimensions?.length
                ? workflowSummary.selected_dimensions
                : ["No planner-selected dimensions returned"]).map((dimension) => (
                <span
                  key={dimension}
                  className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-semibold text-accent"
                >
                  {dimensionLabel(dimension)}
                </span>
              ))}
            </div>
          </div>
          {!!workflowSummary?.candidate_competitors?.length && (
            <div className="mt-3">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">候选竞品</div>
              <div className="flex flex-wrap gap-2">
                {workflowSummary.candidate_competitors.slice(0, 4).map((item, index) => (
                  <span
                    key={`${item.name ?? "candidate"}-${index}`}
                    className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-700"
                  >
                    {item.name ?? "unknown"}
                    {typeof item.confidence === "number" ? ` (${Math.round(item.confidence * 100)}%)` : ""}
                  </span>
                ))}
              </div>
            </div>
          )}
          {!!workflowSummary?.recommended_next_constraints?.length && (
            <div className="mt-3 rounded border border-line bg-panel p-3 text-xs leading-5 text-slate-700">
              <div className="mb-1 font-semibold">Planner 约束</div>
              {workflowSummary.recommended_next_constraints.slice(0, 3).map((item) => (
                <div key={item}>- {item}</div>
              ))}
            </div>
          )}
          {!!workflowSummary?.downstream_guidance?.writer?.length && (
            <div className="mt-3 rounded border border-line bg-panel p-3 text-xs leading-5 text-slate-700">
              <div className="mb-1 font-semibold">报告撰写提示</div>
              {workflowSummary.downstream_guidance.writer.slice(0, 3).map((item) => (
                <div key={item}>- {item}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {hasCollectorGuidance && (
        <div className="rounded border border-line bg-white p-4">
          <h2 className="mb-3 text-base font-semibold">搜索关键词与采集计划</h2>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <SummaryItem label="使用 Planner 提示" value={formatBoolean(collectorDiagnostics?.planner_query_hints_used)} />
            <SummaryItem label="定向返工采集" value={formatBoolean(collectorDiagnostics?.targeted_recollection_used)} />
            <SummaryItem label="使用竞品别名" value={formatBoolean(collectorDiagnostics?.entity_aliases_used)} />
            <SummaryItem
              label="采集模式"
              value={collectorDiagnostics?.collector_mode_used ?? collectorDiagnostics?.collector_mode_requested ?? "-"}
            />
          </div>
          {!!collectorDiagnostics?.effective_query_count_by_competitor && (
            <div className="mt-3 space-y-2">
              {Object.entries(collectorDiagnostics.effective_query_count_by_competitor).map(([competitor, count]) => (
                <div key={competitor} className="rounded border border-line bg-panel p-3 text-sm">
                  <div className="font-semibold">{competitor}</div>
                  <div className="mt-1 text-xs text-slate-600">
                    有效搜索词：{count}
                    {typeof collectorDiagnostics?.planner_hint_query_count_by_competitor?.[competitor] === "number" && (
                      <span> | Planner 搜索词：{collectorDiagnostics.planner_hint_query_count_by_competitor[competitor]}</span>
                    )}
                    {typeof collectorDiagnostics?.alias_query_count_by_competitor?.[competitor] === "number" && (
                      <span> | 别名搜索词：{collectorDiagnostics.alias_query_count_by_competitor[competitor]}</span>
                    )}
                    {typeof collectorDiagnostics?.targeted_query_count_by_competitor?.[competitor] === "number" && (
                      <span> | 返工搜索词：{collectorDiagnostics.targeted_query_count_by_competitor[competitor]}</span>
                    )}
                  </div>

                  <TagGroup title="竞品别名" tone="blue" values={collectorDiagnostics.competitor_aliases_by_competitor?.[competitor]} limit={12} />
                  <TagGroup title="别名扩展搜索词" tone="blue" values={collectorDiagnostics.alias_queries_preview_by_competitor?.[competitor]} limit={9} />
                  <TagGroup title="返工定向搜索词" tone="amber" values={collectorDiagnostics.targeted_queries_preview_by_competitor?.[competitor]} limit={9} />
                  <TagGroup title="实际使用搜索词" tone="slate" values={collectorDiagnostics.effective_queries_preview_by_competitor?.[competitor]} limit={14} />

                  {!!collectorDiagnostics?.query_dimensions_by_competitor?.[competitor]?.length && (
                    <details className="mt-2 text-xs text-slate-600">
                      <summary className="cursor-pointer font-semibold">查看搜索词对应维度</summary>
                      <div className="mt-2 space-y-1">
                        {collectorDiagnostics.query_dimensions_by_competitor[competitor].slice(0, 24).map((item, index) => (
                          <div key={`${item.query ?? "query"}-${index}`} className="rounded border border-line bg-white px-2 py-1">
                            <span className="font-semibold">{dimensionLabel(item.dimension_id ?? "-")}</span>
                            <span className="ml-2">{item.query ?? "-"}</span>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function TagGroup({ title, values, tone, limit }: { title: string; values?: string[]; tone: "blue" | "amber" | "slate"; limit: number }) {
  if (!values?.length) return null;
  const cls =
    tone === "blue"
      ? "border-blue-200 bg-blue-50 text-accent"
      : tone === "amber"
        ? "border-amber-200 bg-amber-50 text-amber-800"
        : "border-line bg-white text-slate-700";
  return (
    <div className="mt-2">
      <div className="mb-1 text-xs font-semibold text-slate-500">{title}</div>
      <div className="flex flex-wrap gap-2">
        {values.slice(0, limit).map((value) => (
          <span key={value} className={`rounded border px-2 py-1 text-xs ${cls}`}>
            {value}
          </span>
        ))}
      </div>
    </div>
  );
}

function dimensionLabel(dimension: string): string {
  const label = DIMENSION_LABELS[dimension];
  return label ? `${label} ${dimension}` : dimension;
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-line bg-panel px-3 py-2">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 font-semibold text-ink">{value}</div>
    </div>
  );
}

function formatBoolean(value: boolean | undefined): string {
  if (value === undefined) return "-";
  return value ? "是" : "否";
}
