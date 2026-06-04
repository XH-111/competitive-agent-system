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
            <SummaryItem label="intent" value={workflowSummary?.intent_classification ?? "-"} />
            <SummaryItem label="ambiguity" value={workflowSummary?.ambiguity_level ?? "-"} />
            <SummaryItem label="scope type" value={workflowSummary?.scope_type ?? "-"} />
            <SummaryItem label="scope size" value={workflowSummary?.scope_size ?? "-"} />
            <SummaryItem label="survey needed" value={formatBoolean(workflowSummary?.survey_needed)} />
            <SummaryItem label="survey recommended" value={formatBoolean(workflowSummary?.survey_recommended)} />
            <SummaryItem label="run id" value={workflowSummary?.run_id ?? "-"} />
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
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Candidate Competitors</div>
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
              <div className="mb-1 font-semibold">Planner constraints</div>
              {workflowSummary.recommended_next_constraints.slice(0, 3).map((item) => (
                <div key={item}>- {item}</div>
              ))}
            </div>
          )}
          {!!workflowSummary?.downstream_guidance?.writer?.length && (
            <div className="mt-3 rounded border border-line bg-panel p-3 text-xs leading-5 text-slate-700">
              <div className="mb-1 font-semibold">Writer guidance</div>
              {workflowSummary.downstream_guidance.writer.slice(0, 3).map((item) => (
                <div key={item}>- {item}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {hasCollectorGuidance && (
        <div className="rounded border border-line bg-white p-4">
          <h2 className="mb-3 text-base font-semibold">采集查询规划</h2>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <SummaryItem
              label="planner hints used"
              value={collectorDiagnostics?.planner_query_hints_used ? "yes" : "no"}
            />
            <SummaryItem
              label="targeted recollection"
              value={collectorDiagnostics?.targeted_recollection_used ? "yes" : "no"}
            />
            <SummaryItem
              label="collector mode"
              value={collectorDiagnostics?.collector_mode_used ?? collectorDiagnostics?.collector_mode_requested ?? "-"}
            />
          </div>
          {!!collectorDiagnostics?.effective_query_count_by_competitor && (
            <div className="mt-3 space-y-2">
              {Object.entries(collectorDiagnostics.effective_query_count_by_competitor).map(([competitor, count]) => (
                <div key={competitor} className="rounded border border-line bg-panel p-3 text-sm">
                  <div className="font-semibold">{competitor}</div>
                  <div className="mt-1 text-xs text-slate-600">
                    effective query count: {count}
                    {typeof collectorDiagnostics?.planner_hint_query_count_by_competitor?.[competitor] === "number" && (
                      <span> | planner hint queries: {collectorDiagnostics.planner_hint_query_count_by_competitor[competitor]}</span>
                    )}
                    {typeof collectorDiagnostics?.targeted_query_count_by_competitor?.[competitor] === "number" && (
                      <span> | targeted queries: {collectorDiagnostics.targeted_query_count_by_competitor[competitor]}</span>
                    )}
                  </div>
                  {!!collectorDiagnostics?.targeted_queries_preview_by_competitor?.[competitor]?.length && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {collectorDiagnostics.targeted_queries_preview_by_competitor[competitor].slice(0, 7).map((query) => (
                        <span key={query} className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800">
                          {query}
                        </span>
                      ))}
                    </div>
                  )}
                  {!!collectorDiagnostics?.effective_queries_preview_by_competitor?.[competitor]?.length && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {collectorDiagnostics.effective_queries_preview_by_competitor[competitor].slice(0, 7).map((query) => (
                        <span key={query} className="rounded border border-line bg-white px-2 py-1 text-xs text-slate-700">
                          {query}
                        </span>
                      ))}
                    </div>
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

function dimensionLabel(dimension: string): string {
  const label = DIMENSION_LABELS[dimension];
  return label ? `${label} ${dimension}` : dimension;
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-line bg-panel px-3 py-2">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 font-semibold text-ink">{value}</div>
    </div>
  );
}

function formatBoolean(value: boolean | undefined): string {
  if (value === undefined) return "-";
  return value ? "yes" : "no";
}
