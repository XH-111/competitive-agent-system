import type { ReactNode } from "react";
import type { CollectorDiagnostics, PlannerAttempt, WorkflowSummary } from "../types";

const DIMENSION_LABELS: Record<string, string> = {
  pricing: "价格",
  feature: "功能",
  persona: "用户画像",
  strength: "优势",
  weakness: "劣势",
  opportunity: "机会",
  threat: "威胁",
  hardware_specs: "硬件参数",
  camera_capability: "影像能力",
  chip_performance: "芯片性能",
  os_ecosystem: "系统生态",
  channel_strategy: "渠道策略",
  ai_capability: "AI 能力",
  battery_range: "续航与电池",
  battery_life: "续航表现",
  health_monitoring: "健康监测",
  ecosystem_compatibility: "生态兼容",
  appearance_design: "外观设计",
  positioning_accuracy: "定位精度",
  sport_mode: "运动模式",
  sensor_config: "传感器配置",
  sports_mode_support: "运动模式支持",
};

type PlanQuery = {
  dimension_id?: string;
  dimension_label?: string;
  queries?: string[];
  intent?: string;
  preferred_sources?: string[];
  query_strategy?: string;
};

export function PlannerSummaryCard({
  workflowSummary,
  collectorDiagnostics,
  plannerAttempts = [],
}: {
  workflowSummary?: WorkflowSummary;
  collectorDiagnostics?: CollectorDiagnostics;
  plannerAttempts?: PlannerAttempt[];
}) {
  const dimensions = workflowSummary?.selected_dimensions ?? [];
  const dimensionPlans = workflowSummary?.analysis_dimension_plan?.dimension_plans ?? [];
  const researchGoals = dimensionPlans.flatMap((dimension) => dimension.research_goals ?? []);
  const collectorPlan = normalizeCollectorPlan(
    workflowSummary?.collection_plan ??
      workflowSummary?.analysis_dimension_plan?.metadata?.collector_search_plan,
  );
  const queryHints = Object.fromEntries(
    Object.entries(collectorPlan).map(([competitor, byDimension]) => [
      competitor,
      Object.values(byDimension).flatMap((item) => item.queries ?? []),
    ]),
  );
  const guidance = workflowSummary?.downstream_guidance;
  const candidates = workflowSummary?.candidate_competitors ?? [];
  const hasContent = Boolean(
    workflowSummary?.planner_summary?.task_goal ||
      workflowSummary?.intent_summary ||
      dimensions.length ||
      Object.keys(queryHints).length ||
      Object.keys(collectorPlan).length ||
      collectorDiagnostics?.effective_queries_preview_by_competitor ||
      guidance,
  );

  if (!hasContent) return null;

  return (
    <section className="mb-4 rounded border border-line bg-white p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">PlannerAgent 任务规划</h2>
          <p className="mt-1 text-xs text-slate-500">展示 Planner 如何理解任务、选择分析维度、为每个维度生成搜索词，并指导下游 Agent。</p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <StatusPill label="搜索计划" value={collectorDiagnostics?.collector_search_plan_used ? "已使用" : "未使用"} />
          <StatusPill label="采集模式" value={collectorDiagnostics?.collector_mode_used ?? collectorDiagnostics?.collector_mode_requested ?? "-"} />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="任务理解摘要">
          <div className="rounded border border-line bg-panel p-3 text-sm leading-6 text-slate-800">
            {workflowSummary?.planner_summary?.task_goal ||
              workflowSummary?.intent_summary ||
              "暂无 Planner 任务理解摘要。"}
          </div>
          {workflowSummary?.planner_summary ? (
            <div className="mt-3 grid gap-2 text-xs text-slate-700 sm:grid-cols-2">
              <div>产品：{workflowSummary.planner_summary.product_name ?? "-"}</div>
              <div>产品类型：{workflowSummary.planner_summary.product_type ?? "-"}</div>
              <div>行业：{workflowSummary.planner_summary.industry ?? "-"}</div>
              <div>地区：{workflowSummary.planner_summary.region ?? "-"}</div>
              <div className="sm:col-span-2">
                竞品：{workflowSummary.planner_summary.competitors?.join("、") || "-"}
              </div>
            </div>
          ) : null}
          {!!candidates.length && (
            <div className="mt-3">
              <SubTitle>候选竞品</SubTitle>
              <div className="flex flex-wrap gap-2">
                {candidates.slice(0, 8).map((item, index) => (
                  <span key={`${item.name ?? "candidate"}-${index}`} className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-700">
                    {item.name ?? "unknown"}
                    {typeof item.confidence === "number" ? ` (${Math.round(item.confidence * 100)}%)` : ""}
                  </span>
                ))}
              </div>
            </div>
          )}
          {!!researchGoals.length && (
            <details className="mt-3 rounded border border-line bg-white p-3 text-xs leading-5 text-slate-700">
              <summary className="cursor-pointer font-semibold">研究目标</summary>
              <div className="mt-2 space-y-1">
                {researchGoals.slice(0, 5).map((goal) => (
                  <div key={goal}>- {goal}</div>
                ))}
              </div>
            </details>
          )}
        </Panel>

        <Panel title="分析维度规划">
          <div className="flex flex-wrap gap-2">
            {(dimensions.length ? dimensions : ["No planner-selected dimensions returned"]).map((dimension) => (
              <span key={dimension} className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-semibold text-accent">
                {dimensionLabel(dimension)}
              </span>
            ))}
          </div>
          {!!dimensionPlans.length && (
            <details className="mt-3 rounded border border-line bg-panel p-3 text-xs leading-5 text-slate-700">
              <summary className="cursor-pointer font-semibold">查看维度说明</summary>
              <div className="mt-2 grid gap-2 md:grid-cols-2">
                {dimensionPlans.slice(0, 20).map((dimension) => (
                  <div key={dimension.dimension_id} className="rounded border border-line bg-white p-2">
                    <div className="font-semibold">{dimensionLabel(dimension.dimension_id ?? "-")}</div>
                    {dimension.description ? <div className="mt-1 text-slate-600">{dimension.description}</div> : null}
                  </div>
                ))}
              </div>
            </details>
          )}
        </Panel>

        <Panel title="搜索关键词与采集计划" className="xl:col-span-2">
          <div className="mb-3 flex flex-wrap gap-2 text-xs">
            <StatusPill label="Planner 计划搜索词" value={sumRecord(collectorDiagnostics?.planned_query_count_by_competitor).toString()} />
            <StatusPill label="实际执行搜索词" value={sumRecord(collectorDiagnostics?.effective_query_count_by_competitor).toString()} />
            <StatusPill label="返工搜索词" value={sumRecord(collectorDiagnostics?.targeted_query_count_by_competitor).toString()} />
          </div>

          <div className="space-y-4">
            {Object.entries(collectorPlan).map(([competitor, byDimension]) => (
              <div key={competitor} className="rounded border border-line bg-panel p-3">
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{competitor}</span>
                  <span className="text-xs text-slate-500">
                    {Object.keys(byDimension).length} 个维度
                    {typeof collectorDiagnostics?.planned_query_count_by_competitor?.[competitor] === "number"
                      ? ` / Planner 计划 ${collectorDiagnostics.planned_query_count_by_competitor[competitor]} 条`
                      : ""}
                    {typeof collectorDiagnostics?.effective_query_count_by_competitor?.[competitor] === "number"
                      ? ` / 实际执行 ${collectorDiagnostics.effective_query_count_by_competitor[competitor]} 条`
                      : ""}
                  </span>
                </div>
                <div className="grid gap-2 lg:grid-cols-2">
                  {dimensionsForCompetitor(dimensions, byDimension).map((dimensionId) => {
                    const plan = byDimension[dimensionId];
                    return (
                      <div key={dimensionId} className="rounded border border-line bg-white p-2">
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                          <span className="font-semibold">{dimensionLabel(plan?.dimension_id ?? dimensionId)}</span>
                          {plan?.query_strategy ? <span className="text-xs text-slate-400">{plan.query_strategy}</span> : null}
                        </div>
                        <TagGroup values={plan?.queries ?? []} limit={6} />
                        {plan?.intent ? <div className="mt-2 text-xs leading-5 text-slate-500">{plan.intent}</div> : null}
                      </div>
                    );
                  })}
                </div>

                {!!collectorDiagnostics?.skipped_queries_by_competitor?.[competitor]?.length && (
                  <details className="mt-3 text-xs text-slate-600">
                    <summary className="cursor-pointer font-semibold text-amber-700">查看未执行搜索词</summary>
                    <div className="mt-2 space-y-1">
                      {collectorDiagnostics.skipped_queries_by_competitor[competitor].slice(0, 30).map((item, index) => (
                        <div key={`${item.query ?? "query"}-${index}`} className="rounded border border-amber-200 bg-amber-50 px-2 py-1">
                          <span className="font-semibold">{dimensionLabel(item.dimension_id ?? "-")}</span>
                          <span className="ml-2">{item.query ?? "-"}</span>
                          <span className="ml-2 text-amber-700">原因：{skipReasonLabel(item.reason)}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            ))}
            {!Object.keys(collectorPlan).length && Object.entries(queryHints).map(([competitor, queries]) => (
              <div key={competitor} className="rounded border border-line bg-panel p-3">
                <div className="mb-2 font-semibold">{competitor}</div>
                <TagGroup values={queries} limit={12} />
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="下游 Agent 指导" className="xl:col-span-2">
          <div className="grid gap-3 md:grid-cols-3">
            <GuidanceBlock title="CollectorAgent" items={guidance?.collector} />
            <GuidanceBlock title="AnalystAgent" items={guidance?.analyst} />
            <GuidanceBlock title="ReportWriterAgent" items={guidance?.writer} />
          </div>
        </Panel>

        <Panel title="Planner 运行诊断" className="xl:col-span-2">
          {!!plannerAttempts.length && (
            <div className="mb-3 space-y-2">
              {plannerAttempts.map((attempt) => (
                <details key={`${attempt.run_id}-${attempt.attempt_no}`} className="rounded border border-line bg-panel p-3">
                  <summary className="cursor-pointer list-none">
                    <div className="flex flex-wrap items-center gap-3 text-xs">
                      <span className="font-semibold">第 {attempt.attempt_no} 次规划</span>
                      <span className={attempt.status === "generated" ? "text-success" : attempt.status === "fallback" ? "text-warning" : "text-danger"}>
                        {attemptStatusLabel(attempt.status)}
                      </span>
                      <span>模式：{String(attempt.diagnostics.planner_mode_used ?? "-")}</span>
                      <span>fallback：{attempt.diagnostics.fallback_used ? "是" : "否"}</span>
                      <span>维度：{attemptDimensions(attempt).join("、") || "-"}</span>
                      <span className="text-slate-500">{formatAttemptTime(attempt.created_at)}</span>
                    </div>
                  </summary>
                  <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded border border-line bg-white p-3 text-xs leading-5 text-slate-700">
                    {JSON.stringify(attempt.planner_output, null, 2)}
                  </pre>
                </details>
              ))}
            </div>
          )}
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded border border-line bg-panel p-3 text-xs leading-5 text-slate-700">
            {JSON.stringify(workflowSummary?.diagnostics ?? {}, null, 2)}
          </pre>
          {!!workflowSummary?.planner_notes?.length && (
            <div className="mt-3 space-y-1 text-xs leading-5 text-slate-700">
              {workflowSummary.planner_notes.map((note) => <div key={note}>- {note}</div>)}
            </div>
          )}
        </Panel>

        <details className="xl:col-span-2 rounded border border-line bg-white p-3">
          <summary className="cursor-pointer text-sm font-semibold">原始 PlannerOutput JSON</summary>
          <pre className="mt-3 max-h-[32rem] overflow-auto whitespace-pre-wrap rounded border border-line bg-panel p-3 text-xs leading-5 text-slate-700">
            {JSON.stringify(workflowSummary?.planner_output ?? {}, null, 2)}
          </pre>
        </details>
      </div>
    </section>
  );
}

function attemptStatusLabel(status: PlannerAttempt["status"]) {
  if (status === "generated") return "LLM 生成";
  if (status === "fallback") return "确定性兜底";
  return "失败";
}

function attemptDimensions(attempt: PlannerAttempt): string[] {
  const dimensions = attempt.planner_output.selected_dimensions;
  return Array.isArray(dimensions)
    ? dimensions.filter((item): item is string => typeof item === "string")
    : [];
}

function formatAttemptTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function Panel({ title, className = "", children }: { title: string; className?: string; children: ReactNode }) {
  return (
    <div className={`rounded border border-line bg-white p-3 ${className}`}>
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      {children}
    </div>
  );
}

function GuidanceBlock({ title, items }: { title: string; items?: string[] }) {
  return (
    <div className="rounded border border-line bg-panel p-3 text-xs leading-5 text-slate-700">
      <div className="mb-2 font-semibold text-ink">{title}</div>
      {items?.length ? items.slice(0, 5).map((item) => <div key={item}>- {item}</div>) : <div className="text-slate-500">暂无指导</div>}
    </div>
  );
}

function SubTitle({ children }: { children: ReactNode }) {
  return <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{children}</div>;
}

function StatusPill({ label, value }: { label: string; value: string }) {
  return (
    <span className="rounded border border-line bg-panel px-3 py-1">
      <span className="text-slate-500">{label}：</span>
      <span className="font-semibold text-ink">{value}</span>
    </span>
  );
}

function TagGroup({ values, limit }: { values?: string[]; limit: number }) {
  if (!values?.length) return <div className="text-xs text-slate-500">暂无搜索词</div>;
  return (
    <div className="flex flex-wrap gap-2">
      {values.slice(0, limit).map((value) => (
        <span key={value} className="rounded border border-line bg-white px-2 py-1 text-xs text-slate-700">
          {value}
        </span>
      ))}
    </div>
  );
}

function dimensionLabel(dimension: string): string {
  const label = DIMENSION_LABELS[dimension];
  return label ? `${label} ${dimension}` : dimension;
}

function sumRecord(record?: Record<string, number>): number {
  return Object.values(record ?? {}).reduce((sum, value) => sum + value, 0);
}

function normalizeCollectorPlan(value: unknown): Record<string, Record<string, PlanQuery>> {
  if (!value || typeof value !== "object") return {};
  const output: Record<string, Record<string, PlanQuery>> = {};
  for (const [competitor, byDimension] of Object.entries(value as Record<string, unknown>)) {
    if (!byDimension || typeof byDimension !== "object") continue;
    output[competitor] = {};
    for (const [dimensionId, item] of Object.entries(byDimension as Record<string, unknown>)) {
      if (!item || typeof item !== "object") continue;
      const plan = item as PlanQuery;
      output[competitor][dimensionId] = {
        ...plan,
        dimension_id: plan.dimension_id ?? dimensionId,
        queries: Array.isArray(plan.queries) ? plan.queries : [],
      };
    }
  }
  return output;
}

function dimensionsForCompetitor(allDimensions: string[], byDimension: Record<string, PlanQuery>): string[] {
  const ordered = [...allDimensions.filter((dimension) => byDimension[dimension]), ...Object.keys(byDimension).filter((dimension) => !allDimensions.includes(dimension))];
  return Array.from(new Set(ordered));
}

function skipReasonLabel(reason?: string | null): string {
  const labels: Record<string, string> = {
    exceeded_max_query_count_per_competitor: "超过每个竞品最大搜索词数量",
    max_evidence_per_competitor_reached: "该竞品 Evidence 数量已达上限",
    max_evidence_per_dimension_reached: "该维度 Evidence 数量已达上限",
  };
  return reason ? labels[reason] ?? reason : "-";
}
