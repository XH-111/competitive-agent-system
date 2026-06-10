export type {
  Claim,
  CollectionPlan,
  CollectorConfig,
  DimensionResult,
  CollectorDiagnostics,
  CollectorStatus,
  Dag,
  Evidence,
  QaResult,
  Report,
  SearchTestResult,
  Survey,
  SurveyQuestion,
  SurveyResponse,
  SwotAnalysis,
  SwotItem,
  Task,
  TaskRun,
  TraceRecord,
  LlmStatus,
  KnowledgeHit,
  KnowledgeRetrievalStrategy,
  PlannerAttempt,
  PlannerRunResult,
  RunTaskOverrides,
  WorkflowProgress,
  WriterDiagnostics,
  WorkflowSummary
} from "./api/types";

export type DemoMode = "normal" | "qa_missing_evidence" | "qa_invalid_extraction" | "qa_bad_report";

export type TaskFormValues = {
  taskName: string;
  competitors: string;
  region: string;
  industry: string;
  collectionStrategyMode: "simple" | "balanced" | "expert";
};

export const statusClass: Record<string, string> = {
  completed: "bg-green-100 text-success border-green-300",
  passed: "bg-green-100 text-success border-green-300",
  failed: "bg-red-100 text-danger border-red-300",
  qa_failed: "bg-red-100 text-danger border-red-300",
  cancel_requested: "bg-amber-100 text-warning border-amber-300",
  cancelled: "bg-slate-100 text-slate-600 border-slate-300",
  manual_review: "bg-amber-100 text-warning border-amber-300",
  running: "bg-blue-100 text-accent border-blue-300",
  pending: "bg-white text-slate-500 border-line",
  skipped: "bg-slate-100 text-slate-500 border-slate-300",
  created: "bg-white text-slate-500 border-line"
};

export const statusLabel: Record<string, string> = {
  completed: "已完成",
  passed: "通过",
  failed: "失败",
  qa_failed: "QA 未通过",
  cancel_requested: "取消中",
  cancelled: "已取消",
  manual_review: "需要人工复核",
  running: "运行中",
  pending: "待执行",
  skipped: "已冻结",
  created: "已创建"
};

export const schemaStatusLabel: Record<string, string> = {
  passed: "Schema 通过",
  failed: "Schema 失败"
};

export const categoryLabel: Record<string, string> = {
  positioning: "定位",
  feature: "功能",
  pricing: "定价",
  persona: "用户画像",
  risk: "风险",
  recommendation: "建议"
};

export function Pill({ value, schema = false }: { value: string; schema?: boolean }) {
  return (
    <span className={`rounded border px-2 py-0.5 text-xs font-semibold ${statusClass[value] ?? statusClass.pending}`}>
      {(schema ? schemaStatusLabel[value] : statusLabel[value]) ?? value}
    </span>
  );
}
