import { useState } from "react";
import type { CollectorConfig, PlannerAttempt, WorkflowSummary } from "../types";

type JsonRecord = Record<string, unknown>;

type DisplayAttempt = {
  attemptNo: number;
  status: PlannerAttempt["status"];
  plannerOutput: JsonRecord;
  diagnostics: JsonRecord;
  reworkContext?: JsonRecord | null;
  createdAt?: string;
};

type CollectionPlanItem = {
  dimensionId: string;
  label: string;
  queries: string[];
  researchGoals: string[];
};

type IncrementalTarget = {
  competitor: string;
  dimensionId: string;
  queries: string[];
  reason: string;
  questionId?: string;
  question?: string;
  suggestions: string[];
  maxEvidence?: number;
  contentFetchPriority?: string;
};

const DIMENSION_LABELS: Record<string, string> = {
  pricing: "定价与商业模式",
  feature: "功能能力",
  persona: "用户画像",
  strength: "优势",
  weakness: "不足",
  opportunity: "机会",
  threat: "威胁",
  commercialization: "商业化",
  capability: "产品能力",
  content_ecosystem: "内容生态",
  user_operation_strategy: "用户运营策略",
};

export function PlannerSummaryCard({
  workflowSummary,
  plannerAttempts = [],
}: {
  workflowSummary?: WorkflowSummary;
  plannerAttempts?: PlannerAttempt[];
}) {
  const attempts = buildDisplayAttempts(workflowSummary, plannerAttempts);
  if (!attempts.length) return null;

  const latest = attempts[0];
  const latestKind = isIncrementalOutput(latest.plannerOutput) ? "增量补采" : "完整规划";

  return (
    <details className="mb-4 border border-line bg-white">
      <summary className="cursor-pointer list-none p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Planner 规划记录</h2>
          <p className="mt-1 text-xs text-slate-500">点击展开各轮规划结果</p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <Metric label="规划轮次" value={`${attempts.length}`} />
          <Metric label="最新类型" value={latestKind} />
          <Metric label="最新状态" value={attemptStatusLabel(latest)} tone={statusTone(latest.status)} />
        </div>
      </div>
      </summary>

      <div className="space-y-3 border-t border-line p-4">
        {attempts.map((attempt, index) => {
          const incremental = isIncrementalOutput(attempt.plannerOutput);
          const source = attemptSource(attempt, incremental);
          return (
            <details
              key={`${attempt.attemptNo}-${attempt.createdAt ?? index}`}
              className="border border-line bg-white"
            >
              <summary className="cursor-pointer list-none bg-panel px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">第 {attempt.attemptNo} 轮</span>
                    <Badge text={incremental ? "增量补采规划" : "完整任务规划"} tone={incremental ? "amber" : "blue"} />
                    <Badge text={attemptStatusLabel(attempt)} tone={statusTone(attempt.status)} />
                    {source ? <span className="text-xs text-slate-500">触发来源：{source}</span> : null}
                  </div>
                  {attempt.createdAt ? (
                    <span className="text-xs text-slate-500">{formatTime(attempt.createdAt)}</span>
                  ) : null}
                </div>
              </summary>

              <div className="p-3">
                {incremental ? (
                  <IncrementalAttempt output={attempt.plannerOutput} reworkContext={attempt.reworkContext} />
                ) : (
                  <FullAttempt output={attempt.plannerOutput} workflowSummary={workflowSummary} />
                )}
              </div>
            </details>
          );
        })}
      </div>
    </details>
  );
}

function FullAttempt({
  output,
  workflowSummary,
}: {
  output: JsonRecord;
  workflowSummary?: WorkflowSummary;
}) {
  const summary = objectValue(output.planner_summary);
  const dimensionPlan = objectValue(output.analysis_dimension_plan);
  const dimensions = stringList(output.selected_dimensions);
  const dimensionDefinitions = arrayOfRecords(dimensionPlan.dimension_plans);
  const collectionPlan = normalizeCollectionPlan(objectValue(output.collection_plan).collector_search_plan);
  const notes = stringList(output.planner_notes);
  const taskGoal =
    stringValue(summary.task_goal) ||
    workflowSummary?.planner_summary?.task_goal ||
    workflowSummary?.intent_summary ||
    "";
  const competitors =
    stringList(summary.competitors).length > 0
      ? stringList(summary.competitors)
      : workflowSummary?.planner_summary?.competitors ?? [];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(16rem,1fr)]">
        <div>
          <SectionTitle>任务目标</SectionTitle>
          <p className="text-sm leading-5 text-slate-800">{taskGoal || "本轮未返回任务目标。"}</p>
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <Field label="竞品" value={competitors.join("、") || "-"} wide />
          <Field label="行业" value={stringValue(summary.industry) || workflowSummary?.planner_summary?.industry || "-"} />
          <Field label="地区" value={stringValue(summary.region) || workflowSummary?.planner_summary?.region || "-"} />
          <Field label="产品类型" value={stringValue(summary.product_type) || "-"} />
          <Field
            label="计划来源"
            value={workflowSummary?.manual_collection_plan_override_used ? "人工覆盖" : "Planner 生成"}
          />
        </dl>
      </div>

      <div>
        <SectionTitle>分析维度</SectionTitle>
        <div className="flex flex-wrap gap-2">
          {dimensions.map((dimension) => (
            <Badge key={dimension} text={dimensionLabel(dimension)} tone="blue" />
          ))}
          {!dimensions.length ? <span className="text-xs text-slate-500">未返回维度。</span> : null}
        </div>
      </div>

      <div>
        <SectionTitle>各竞品采集计划</SectionTitle>
        <div className="space-y-3">
          {Object.entries(collectionPlan).map(([competitor, plans]) => (
            <CompetitorPlan
              key={competitor}
              competitor={competitor}
              plans={plans}
              dimensionDefinitions={dimensionDefinitions}
              collectorConfig={workflowSummary?.collector_config}
            />
          ))}
          {!Object.keys(collectionPlan).length ? (
            <div className="text-sm text-slate-500">本轮未返回可执行的采集计划。</div>
          ) : null}
        </div>
      </div>

      {notes.length ? (
        <div>
          <SectionTitle>规划备注</SectionTitle>
          <TextList values={notes} emptyText="" />
        </div>
      ) : null}
    </div>
  );
}

function CompetitorPlan({
  competitor,
  plans,
  dimensionDefinitions,
  collectorConfig,
}: {
  competitor: string;
  plans: CollectionPlanItem[];
  dimensionDefinitions: JsonRecord[];
  collectorConfig?: CollectorConfig | null;
}) {
  const [selectedDimension, setSelectedDimension] = useState(plans[0]?.dimensionId ?? "");
  const selectedPlan = plans.find((plan) => plan.dimensionId === selectedDimension) ?? plans[0];
  if (!selectedPlan) return null;

  const definition = dimensionDefinitions.find(
    (item) => stringValue(item.dimension_id) === selectedPlan.dimensionId,
  );
  const goals = selectedPlan.researchGoals.length
    ? selectedPlan.researchGoals
    : stringList(definition?.research_goals);
  const config = resolveCollectorConfig(collectorConfig, competitor, selectedPlan.dimensionId);

  return (
    <div className="border-t border-line pt-3 first:border-t-0 first:pt-0">
      <div className="mb-2 font-semibold">{competitor}</div>
      <div className="mb-2 flex flex-wrap gap-1">
        {plans.map((plan) => {
          const active = plan.dimensionId === selectedPlan.dimensionId;
          return (
            <button
              key={plan.dimensionId}
              type="button"
              onClick={() => setSelectedDimension(plan.dimensionId)}
              className={`border px-2.5 py-1 text-xs font-medium ${
                active
                  ? "border-blue-500 bg-blue-50 text-blue-700"
                  : "border-line bg-white text-slate-600 hover:border-blue-300 hover:text-blue-700"
              }`}
            >
              {plan.label || dimensionLabel(plan.dimensionId)}
            </button>
          );
        })}
      </div>

      <div className="border border-line bg-panel/40 p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-baseline gap-2">
            <span className="font-semibold">
              {selectedPlan.label || dimensionLabel(selectedPlan.dimensionId)}
            </span>
            <span className="text-xs text-slate-400">{selectedPlan.dimensionId}</span>
          </div>
          <span className="text-xs text-slate-500">{selectedPlan.queries.length} 条查询词</span>
        </div>

        <div className="grid gap-3 lg:grid-cols-2">
          <TextList title="调研目标" values={goals} emptyText="未设置调研目标" />
          <TextList title="搜索词" values={selectedPlan.queries} emptyText="未设置搜索词" numbered />
        </div>

        {config ? (
          <div className="mt-2 border-t border-line pt-2">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
              <span>每条 query：{config.max_results_per_query ?? "-"}</span>
              <span>最多 evidence：{config.max_evidence_per_dimension ?? "-"}</span>
              <span>QA 最少有效：{config.min_valid_evidence_required ?? "-"}</span>
              {config.include_domains?.length ? (
                <span>包含域名：{config.include_domains.join("、")}</span>
              ) : null}
              {config.exclude_domains?.length ? (
                <span>排除域名：{config.exclude_domains.join("、")}</span>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function IncrementalAttempt({
  output,
  reworkContext,
}: {
  output: JsonRecord;
  reworkContext?: JsonRecord | null;
}) {
  const targets = normalizeIncrementalTargets(output.targets);
  const coverageGap = objectValue(reworkContext?.coverage_gap);
  const gapTargets = arrayOfRecords(coverageGap.targets);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2 text-xs">
        <Metric label="补采目标" value={`${targets.length}`} />
        <Metric label="基础规划轮次" value={numberOrText(output.base_attempt_no) || "-"} />
        <Metric
          label="正文要求"
          value={targets.some((target) => target.contentFetchPriority === "required") ? "需要正文" : "常规"}
        />
      </div>

      {targets.map((target, index) => {
        const gap = gapTargets.find(
          (item) =>
            stringValue(item.competitor) === target.competitor &&
            stringValue(item.dimension_id) === target.dimensionId &&
            (!target.questionId || stringValue(item.question_id) === target.questionId),
        );
        const originalQueries = stringList(gap?.original_queries);
        return (
          <div key={`${target.competitor}-${target.dimensionId}-${target.questionId ?? index}`} className="border border-line p-4">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="font-semibold">{target.competitor}</span>
              <Badge text={dimensionLabel(target.dimensionId)} tone="blue" />
              {target.questionId ? <span className="text-xs text-slate-500">{target.questionId}</span> : null}
              {target.maxEvidence ? <span className="text-xs text-slate-500">最多补采 {target.maxEvidence} 条</span> : null}
              {target.contentFetchPriority === "required" ? <Badge text="补采后抓取正文" tone="amber" /> : null}
            </div>

            {target.question ? (
              <div className="mb-3">
                <SectionTitle>待补充问题</SectionTitle>
                <p className="text-sm leading-6 text-slate-800">{target.question}</p>
              </div>
            ) : null}

            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <SectionTitle>返工原因</SectionTitle>
                <p className="text-sm leading-6 text-slate-700">{target.reason || "证据覆盖不足"}</p>
                {target.suggestions.length ? (
                  <div className="mt-3">
                    <TextList title="补采建议" values={target.suggestions} emptyText="" />
                  </div>
                ) : null}
              </div>
              <div>
                {originalQueries.length ? (
                  <div className="mb-3">
                    <TextList title="原查询词" values={originalQueries} emptyText="" />
                  </div>
                ) : null}
                <TextList title="新查询词" values={target.queries} emptyText="未生成新查询词" numbered />
              </div>
            </div>
          </div>
        );
      })}

      {!targets.length ? <div className="text-sm text-slate-500">本轮未生成有效的补采目标。</div> : null}
    </div>
  );
}

function buildDisplayAttempts(
  workflowSummary: WorkflowSummary | undefined,
  plannerAttempts: PlannerAttempt[],
): DisplayAttempt[] {
  if (plannerAttempts.length) {
    return [...plannerAttempts]
      .sort((left, right) => right.attempt_no - left.attempt_no)
      .map((attempt) => ({
        attemptNo: attempt.attempt_no,
        status: attempt.status,
        plannerOutput: attempt.planner_output,
        diagnostics: attempt.diagnostics,
        reworkContext: attempt.rework_context,
        createdAt: attempt.created_at,
      }));
  }

  const plannerOutput = objectValue(workflowSummary?.planner_output);
  if (!Object.keys(plannerOutput).length && !workflowSummary?.collection_plan) return [];

  return [
    {
      attemptNo: 1,
      status: "generated",
      plannerOutput: Object.keys(plannerOutput).length
        ? plannerOutput
        : {
            planner_summary: workflowSummary?.planner_summary,
            selected_dimensions: workflowSummary?.selected_dimensions,
            analysis_dimension_plan: workflowSummary?.analysis_dimension_plan,
            collection_plan: workflowSummary?.collection_plan,
            planner_notes: workflowSummary?.planner_notes,
          },
      diagnostics: {},
    },
  ];
}

function normalizeCollectionPlan(value: unknown): Record<string, CollectionPlanItem[]> {
  const result: Record<string, CollectionPlanItem[]> = {};
  for (const [competitor, dimensions] of Object.entries(objectValue(value))) {
    const items: CollectionPlanItem[] = [];
    for (const [dimensionKey, rawItem] of Object.entries(objectValue(dimensions))) {
      const item = objectValue(rawItem);
      items.push({
        dimensionId: stringValue(item.dimension_id) || dimensionKey,
        label: stringValue(item.label) || dimensionLabel(dimensionKey),
        queries: stringList(item.queries),
        researchGoals: stringList(item.research_goals),
      });
    }
    result[competitor] = items;
  }
  return result;
}

function normalizeIncrementalTargets(value: unknown): IncrementalTarget[] {
  return arrayOfRecords(value).map((target) => ({
    competitor: stringValue(target.competitor) || "未知竞品",
    dimensionId: stringValue(target.dimension_id) || "unknown",
    queries: stringList(target.queries),
    reason: stringValue(target.reason),
    questionId: stringValue(target.question_id) || undefined,
    question: stringValue(target.question) || undefined,
    suggestions: stringList(target.suggestions),
    maxEvidence: typeof target.max_evidence === "number" ? target.max_evidence : undefined,
    contentFetchPriority: stringValue(target.content_fetch_priority) || undefined,
  }));
}

function resolveCollectorConfig(
  config: CollectorConfig | null | undefined,
  competitor: string,
  dimensionId: string,
): CollectorConfig["default"] | undefined {
  if (!config) return undefined;
  const override = config.overrides?.find(
    (item) => item.competitor === competitor && item.dimension_id === dimensionId,
  );
  return {
    ...config.default,
    ...override,
    include_domains: override?.include_domains?.length
      ? override.include_domains
      : config.default?.include_domains,
    exclude_domains: override?.exclude_domains?.length
      ? override.exclude_domains
      : config.default?.exclude_domains,
  };
}

function isIncrementalOutput(output: JsonRecord): boolean {
  return output.mode === "incremental_collection_plan" || Array.isArray(output.targets);
}

function attemptSource(attempt: DisplayAttempt, incremental: boolean): string | null {
  const explicit = stringValue(attempt.reworkContext?.source);
  if (explicit) return explicit;
  if (!incremental) return null;
  const targets = normalizeIncrementalTargets(attempt.plannerOutput.targets);
  return targets.some((target) => target.questionId || target.contentFetchPriority === "required")
    ? "AnalystQA"
    : "EvidenceQA";
}

function statusLabel(status: PlannerAttempt["status"]): string {
  if (status === "generated") return "LLM 生成";
  if (status === "fallback") return "规则兜底";
  return "生成失败";
}

function attemptStatusLabel(attempt: DisplayAttempt): string {
  if (attempt.diagnostics.planner_mode_used === "manual") return "人工规划";
  return statusLabel(attempt.status);
}

function statusTone(status: PlannerAttempt["status"]): "green" | "amber" | "red" {
  if (status === "generated") return "green";
  if (status === "fallback") return "amber";
  return "red";
}

function dimensionLabel(dimensionId: string): string {
  const label = DIMENSION_LABELS[dimensionId];
  return label ? `${label}（${dimensionId}）` : dimensionId;
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function objectValue(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as JsonRecord) : {};
}

function arrayOfRecords(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.map(objectValue).filter((item) => Object.keys(item).length > 0) : [];
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberOrText(value: unknown): string {
  return typeof value === "number" || typeof value === "string" ? String(value) : "";
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <div className="mb-1 text-xs font-semibold text-slate-500">{children}</div>;
}

function Field({ label, value, wide = false }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={wide ? "col-span-2" : ""}>
      <dt className="text-slate-500">{label}</dt>
      <dd className="font-medium text-slate-800">{value}</dd>
    </div>
  );
}

function TextList({
  title,
  values,
  emptyText,
  numbered = false,
}: {
  title?: string;
  values: string[];
  emptyText: string;
  numbered?: boolean;
}) {
  return (
    <div>
      {title ? <SectionTitle>{title}</SectionTitle> : null}
      {values.length ? (
        <div className="space-y-0.5 text-sm leading-5 text-slate-700">
          {values.map((value, index) => (
            <div key={`${value}-${index}`} className="flex gap-2">
              <span className="shrink-0 text-slate-400">{numbered ? `${index + 1}.` : "·"}</span>
              <span>{value}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-xs text-slate-500">{emptyText}</div>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  tone = "slate",
}: {
  label: string;
  value: string;
  tone?: "slate" | "green" | "amber" | "red";
}) {
  const toneClass = {
    slate: "text-slate-800",
    green: "text-success",
    amber: "text-warning",
    red: "text-danger",
  }[tone];
  return (
    <span className="border border-line bg-panel px-3 py-1.5">
      <span className="text-slate-500">{label}：</span>
      <span className={`font-semibold ${toneClass}`}>{value}</span>
    </span>
  );
}

function Badge({
  text,
  tone,
}: {
  text: string;
  tone: "blue" | "green" | "amber" | "red";
}) {
  const className = {
    blue: "border-blue-200 bg-blue-50 text-blue-700",
    green: "border-green-200 bg-green-50 text-green-700",
    amber: "border-amber-200 bg-amber-50 text-amber-700",
    red: "border-red-200 bg-red-50 text-red-700",
  }[tone];
  return <span className={`border px-2 py-0.5 text-xs font-medium ${className}`}>{text}</span>;
}
