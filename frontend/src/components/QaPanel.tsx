import { AlertTriangle, SearchCheck } from "lucide-react";
import type { QaResult, WorkflowSummary } from "../types";
import { Pill } from "../types";

type ReworkItem = {
  from: string;
  to: string;
  errorType?: string;
  reason?: string | null;
  action?: string;
  failedSchema?: string | null;
  metadata?: Record<string, unknown>;
  resultStatus?: string;
};

export function QaPanel({ qa, workflowSummary }: { qa?: QaResult; workflowSummary?: WorkflowSummary }) {
  if (!qa) return null;

  const history = buildReworkHistory(qa, workflowSummary);
  const statusClass = qa.status === "passed"
    ? "border-green-200 bg-green-50"
    : qa.status === "warning"
      ? "border-amber-300 bg-amber-50"
    : qa.status === "manual_review"
      ? "border-amber-300 bg-amber-50"
      : "border-red-200 bg-red-50";

  return (
    <section className={`rounded border p-4 ${statusClass}`}>
      <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold">
        <SearchCheck size={18} /> 质检结果
      </h2>

      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        {qa.qa_stage === "evidence" && <span>检查阶段：EvidenceQA</span>}
        <span>最终状态：</span>
        <Pill value={qa.status} />
        <span>返工次数：{qa.rework_count}</span>
        <span>最大返工：3</span>
      </div>

      <PrimaryReason qa={qa} />
      <EvidenceGateDetails qa={qa} workflowSummary={workflowSummary} />

      {qa.qa_stage === "evidence" && (
        <div className="mb-3 grid gap-3 md:grid-cols-3">
          <InfoBlock title="失败查询 failed_queries" items={qa.failed_queries ?? []} emptyText="无" />
          <InfoBlock title="薄弱维度 failed_dimensions" items={qa.failed_dimensions ?? []} emptyText="无" />
          <InfoBlock title="失败竞品 failed_competitors" items={qa.failed_competitors ?? []} emptyText="无" />
          <div className="rounded border border-line bg-white p-3 text-sm md:col-span-3">
            <div className="font-semibold">建议动作 suggested_action</div>
            <div className="mt-1">{qa.suggested_action || "无"}</div>
            <div className="mt-1 text-slate-600">建议路由：{qa.route_to ?? "无需返工"}</div>
          </div>
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-3">
        <InfoBlock title="严重问题 hard_errors" items={qa.hard_errors} emptyText="无" />
        <InfoBlock title="优化建议 soft_suggestions" items={qa.soft_suggestions} emptyText="无" />
        <InfoBlock
          title="返工指令 rework_instructions"
          items={(qa.rework_instructions ?? []).map((item) => {
            const metadata = item.metadata ?? {};
            return [
              `问题类型：${qaReasonLabel(item.error_type)} (${item.error_type})`,
              `打回节点：${item.target_agent ?? qa.route_to ?? "-"}`,
              item.failed_schema ? `失败 Schema：${item.failed_schema}` : undefined,
              typeof metadata.competitor === "string" ? `竞品：${metadata.competitor}` : undefined,
              typeof metadata.dimension_id === "string" ? `维度：${metadata.dimension_id}` : undefined,
              typeof metadata.dimension_result_id === "string"
                ? `结构化事实：${metadata.dimension_result_id}`
                : undefined,
              typeof metadata.evidence_id === "string" ? `Evidence：${metadata.evidence_id}` : undefined,
              `原因：${item.reason}`,
              `修复要求：${item.suggested_action}`,
            ].filter(Boolean).join(" | ");
          })}
          emptyText="无"
        />
      </div>

      <div className="mt-3 rounded border border-line bg-white p-3 text-sm">
        <h3 className="mb-2 font-semibold">返工历史 rework_history</h3>
        {history.length ? (
          <div className="space-y-2">
            {history.map((item, index) => (
              <div key={`${item.from}-${item.to}-${index}`} className="rounded border border-line bg-panel px-3 py-2">
                <div>
                  第 {index + 1} 轮：{item.from} -&gt; {item.to}
                  {item.errorType ? `，问题：${qaReasonLabel(item.errorType)}` : ""}
                  {item.resultStatus ? `，结果：${item.resultStatus}` : ""}
                </div>
                {item.reason && <div className="mt-1 text-xs text-slate-700">直接原因：{item.reason}</div>}
                {item.failedSchema && <div className="mt-1 text-xs text-slate-600">失败 Schema：{item.failedSchema}</div>}
                <MetadataLine metadata={item.metadata} />
                {item.action && <div className="mt-1 text-xs text-slate-600">返工动作：{item.action}</div>}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-slate-500">暂无自动返工历史。</p>
        )}
      </div>
    </section>
  );
}

function PrimaryReason({ qa }: { qa: QaResult }) {
  if (qa.status === "passed" || !qa.rework_instructions?.length) return null;
  const primary = qa.rework_instructions[0];
  const metadata = primary.metadata ?? {};
  return (
    <div className="mb-3 rounded border border-red-200 bg-white p-3 text-sm">
      <div className="mb-2 flex items-center gap-2 font-semibold text-danger">
        <AlertTriangle size={16} /> 为什么被打回
      </div>
      <div className="grid gap-2 lg:grid-cols-3">
        <ReasonItem label="打回节点" value={primary.target_agent ?? qa.route_to ?? "-"} />
        <ReasonItem label="问题类型" value={qaReasonLabel(primary.error_type)} />
        <ReasonItem label="失败 Schema" value={primary.failed_schema ?? "-"} />
      </div>
      <div className="mt-2 rounded border border-line bg-panel px-3 py-2 leading-6">
        <div><span className="font-semibold">直接原因：</span>{primary.reason}</div>
        <div><span className="font-semibold">修复要求：</span>{primary.suggested_action}</div>
      </div>
      <MetadataLine metadata={metadata} prominent />
    </div>
  );
}

function EvidenceGateDetails({ qa, workflowSummary }: { qa: QaResult; workflowSummary?: WorkflowSummary }) {
  const metadata = qa.metadata as Record<string, unknown> | undefined;
  const details = (metadata?.evidence_gate_details ?? workflowSummary?.evidence_gate_output) as
    | Record<string, unknown>
    | undefined;
  const diagnostics = (details?.by_competitor ?? details?.evidence_diagnostics_by_competitor) as
    | Record<string, Record<string, unknown>>
    | undefined;
  if (!diagnostics || !Object.keys(diagnostics).length) return null;

  return (
    <div className="mb-3 rounded border border-amber-200 bg-white p-3 text-sm">
      <div className="mb-2 flex items-center gap-2 font-semibold text-warning">
        <AlertTriangle size={16} /> EvidenceGate 证据门禁详情
      </div>
      {typeof details?.failure_explanation === "string" && (
        <div className="mb-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs">
          {details.failure_explanation}
        </div>
      )}
      <div className="space-y-2">
        {Object.entries(diagnostics).map(([competitor, item]) => (
          <div key={competitor} className="rounded border border-line bg-panel p-3">
            <div className="font-semibold">
              {competitor} | {String(item.status ?? "-")} | {String(item.reason_code ?? "-")}
            </div>
            {typeof item.explanation === "string" && <div className="mt-1 text-xs">{item.explanation}</div>}
            <div className="mt-2 grid gap-2 text-xs md:grid-cols-6">
              <ReasonItem label="总数" value={String(item.total_evidence_count ?? 0)} />
              <ReasonItem label="high" value={String(item.high_count ?? 0)} />
              <ReasonItem label="medium" value={String(item.medium_count ?? 0)} />
              <ReasonItem label="low" value={String(item.low_count ?? 0)} />
              <ReasonItem label="unrelated" value={String(item.unrelated_count ?? 0)} />
              <ReasonItem label="别名未命中" value={String(item.alias_miss_count ?? 0)} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MetadataLine({ metadata, prominent = false }: { metadata?: Record<string, unknown>; prominent?: boolean }) {
  if (!metadata) return null;
  const parts = [
    typeof metadata.competitor === "string" ? `竞品：${metadata.competitor}` : undefined,
    typeof metadata.dimension_id === "string" ? `维度：${metadata.dimension_id}` : undefined,
    typeof metadata.dimension_result_id === "string" ? `结构化事实：${metadata.dimension_result_id}` : undefined,
    typeof metadata.evidence_id === "string" ? `Evidence：${metadata.evidence_id}` : undefined,
    Array.isArray(metadata.missing_competitors) && metadata.missing_competitors.length
      ? `缺失竞品：${metadata.missing_competitors.join(", ")}`
      : undefined,
  ].filter(Boolean);
  if (!parts.length) return null;
  return (
    <div className={`${prominent ? "mt-2 rounded border border-amber-200 bg-amber-50 px-3 py-2" : "mt-1"} text-xs text-slate-700`}>
      {parts.join(" | ")}
    </div>
  );
}

function ReasonItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-line bg-panel px-3 py-2">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 font-semibold text-ink">{value}</div>
    </div>
  );
}

function InfoBlock({ title, items, emptyText }: { title: string; items?: string[]; emptyText: string }) {
  const cleanItems = (items ?? []).filter(Boolean);
  return (
    <div className="rounded border border-line bg-white p-3 text-sm">
      <h3 className="mb-2 font-semibold">{title}</h3>
      {cleanItems.length
        ? <div className="space-y-2">{cleanItems.map((item, index) => <p key={`${title}-${index}`}>{item}</p>)}</div>
        : <p className="text-slate-500">{emptyText}</p>}
    </div>
  );
}

function buildReworkHistory(qa: QaResult, workflowSummary?: WorkflowSummary): ReworkItem[] {
  const history = (qa.rework_history ?? [])
    .map((item) => ({
      from: typeof item.metadata?.source_node === "string" ? item.metadata.source_node : "QaAgent",
      to: item.route_to ?? "-",
      errorType: item.error_type,
      reason: item.reason,
      action: item.action,
      failedSchema: item.failed_schema,
      metadata: item.metadata,
      resultStatus: item.result_status,
    }))
    .filter((item) => item.to !== "-");
  if (history.length) return history;

  return (workflowSummary?.conditional_routes_taken ?? [])
    .filter((item) => item.to_node !== "final_report")
    .map((item) => ({
      from: normalizeNodeName(item.from_node ?? "qa"),
      to: normalizeNodeName(item.to_node ?? "-"),
      errorType: item.reason,
      resultStatus: item.final_status,
    }));
}

function qaReasonLabel(reason: string) {
  const labels: Record<string, string> = {
    invalid_planner_output: "Planner 固定维度缺失",
    missing_dimension_search_plan: "Planner 搜索计划不完整",
    missing_evidence: "缺少 Evidence",
    missing_relevant_evidence: "缺少相关 Evidence",
    missing_dimension_evidence: "维度证据不足",
    invalid_extraction: "结构化抽取异常",
    dimension_coverage_gap: "结构化事实维度缺失",
    fact_missing_evidence: "结构化事实未绑定证据",
    fact_evidence_not_found: "结构化事实引用了无效证据",
    fact_competitor_mismatch: "结构化事实与证据竞品不一致",
    fact_dimension_mismatch: "结构化事实与证据维度不一致",
    fact_confidence_overstated: "结构化事实置信度过高",
    invalid_insufficient_evidence_state: "证据不足状态不合法",
    bad_report_format: "报告格式问题",
    report_competitor_gap: "报告缺少竞品",
    report_fact_mismatch: "报告结构化事实被改写",
    report_missing_citations: "报告缺少事实或证据引用",
    max_rework_reached: "达到最大返工次数",
  };
  return labels[reason] ?? reason;
}

function normalizeNodeName(node: string) {
  const names: Record<string, string> = {
    planner: "PlannerAgent",
    qa: "QaAgent",
    collector: "CollectorAgent",
    analyst: "AnalystAgent",
    report_writer: "ReportWriterAgent",
    final_report: "FinalReportAgent",
  };
  return names[node] ?? node;
}
