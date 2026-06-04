import { AlertTriangle, SearchCheck } from "lucide-react";
import type { QaResult, WorkflowSummary } from "../types";
import { Pill } from "../types";

type RouteHistoryItem = {
  from: string;
  to: string;
  errorType?: string;
  reason?: string | null;
  action?: string;
  failedSchema?: string | null;
  claimId?: string | null;
  failedClaim?: string | null;
  metadata?: Record<string, unknown>;
  resultStatus?: string;
};

export function QaPanel({ qa, workflowSummary }: { qa?: QaResult; workflowSummary?: WorkflowSummary }) {
  if (!qa) return null;

  const reworkHistory = buildReworkHistory(qa, workflowSummary);
  const swotValidation = qa.metadata?.swot_validation;
  const swotIssues = swotValidation?.issues ?? [];
  const statusClass = qa.status === "passed"
    ? "border-green-200 bg-green-50"
    : qa.status === "manual_review"
      ? "border-amber-300 bg-amber-50"
      : "border-red-200 bg-red-50";

  return (
    <section className={`rounded border p-4 ${statusClass}`}>
      <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold">
        <SearchCheck size={18} /> 质检结果
      </h2>

      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <span>最终状态：</span>
        <Pill value={qa.status} />
        <span>返工次数：{qa.rework_count}</span>
        <span>最大返工：3</span>
        {swotValidation && <span>swot_validation: {swotValidation.status ?? "-"}</span>}
      </div>

      <ReworkReasonCard qa={qa} />
      <EvidenceGateDetailsCard qa={qa} workflowSummary={workflowSummary} />

      <div className="grid gap-3 lg:grid-cols-3">
        <InfoBlock title="严重问题 hard_errors" items={qa.hard_errors} emptyText="无" />
        <InfoBlock title="优化建议 soft_suggestions" items={qa.soft_suggestions} emptyText="无" />
        <InfoBlock
          title="返工指令 rework_instructions"
          items={(qa.rework_instructions ?? []).map((item) => [
            `error_type: ${item.error_type}`,
            item.failed_claim ? `failed_claim: ${item.failed_claim}` : undefined,
            item.failed_schema ? `failed_schema: ${item.failed_schema}` : undefined,
            typeof item.metadata?.competitor === "string" ? `competitor: ${item.metadata.competitor}` : undefined,
            typeof item.metadata?.quadrant === "string" ? `quadrant: ${item.metadata.quadrant}` : undefined,
            typeof item.metadata?.fix_type === "string" ? `fix_type: ${item.metadata.fix_type}` : undefined,
            `reason: ${item.reason}`,
            `route_to: ${item.target_agent ?? qa.route_to ?? "-"}`,
            `suggested_action: ${item.suggested_action}`,
            Array.isArray(item.metadata?.query_focus) && item.metadata.query_focus.length
              ? `query_focus: ${item.metadata.query_focus.join(", ")}`
              : undefined,
            `rework_count: ${qa.rework_count}`,
            `final_status: ${qa.status}`,
          ].filter(Boolean).join(" | "))}
          emptyText="无"
        />
      </div>

      {swotIssues.length > 0 && (
        <div className="mt-3 rounded border border-line bg-white p-3 text-sm">
          <h3 className="mb-2 font-semibold">SWOT 质检问题</h3>
          <div className="space-y-2">
            {swotIssues.map((issue, index) => (
              <div key={`${issue.error_type ?? "swot"}-${index}`} className="rounded border border-line bg-panel p-3">
                <div className="font-semibold">
                  {issue.error_type ?? "swot_issue"}
                  {issue.competitor ? ` | ${issue.competitor}` : ""}
                  {issue.quadrant ? ` | ${issue.quadrant}` : ""}
                </div>
                {issue.reason && <div className="mt-1">{issue.reason}</div>}
                <div className="mt-1 text-xs text-slate-600">
                  {issue.fix_type ? `fix_type: ${issue.fix_type}` : ""}
                  {issue.target_agent ? ` | route_to: ${issue.target_agent}` : ""}
                </div>
                {Array.isArray(issue.query_focus) && issue.query_focus.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {issue.query_focus.map((item) => (
                      <span key={item} className="rounded border border-line bg-white px-2 py-1 text-xs text-slate-700">
                        {item}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mt-3 rounded border border-line bg-white p-3 text-sm">
        <h3 className="mb-2 font-semibold">返工历史 rework_history</h3>
        {reworkHistory.length ? (
          <div className="space-y-2">
            {reworkHistory.map((item, index) => (
              <div key={`${item.from}-${item.to}-${index}`} className="rounded border border-line bg-panel px-3 py-2">
                <div>
                  第 {index + 1} 轮：{item.from} -&gt; {item.to}
                  {item.errorType ? `，问题：${qaReasonLabel(item.errorType)}` : ""}
                  {item.resultStatus ? `，结果：${item.resultStatus}` : ""}
                </div>
                {item.reason && <div className="mt-1 text-xs text-slate-700">直接原因：{item.reason}</div>}
                {(item.failedSchema || item.claimId) && (
                  <div className="mt-1 text-xs text-slate-600">
                    {item.failedSchema ? `失败 Schema：${item.failedSchema}` : ""}
                    {item.claimId ? ` | Claim：${item.claimId}` : ""}
                  </div>
                )}
                <ReworkMetadataLine metadata={item.metadata} />
                {item.failedClaim && <div className="mt-1 text-xs text-slate-600">被打回结论：{item.failedClaim}</div>}
                {item.action && <div className="mt-1 text-xs text-slate-600">返工动作：{item.action}</div>}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-slate-500">暂无自动返工历史。</p>
        )}
        {(qa.hard_errors.length > 0 || qa.rework_instructions.length > 0) && (
          <div className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-slate-700">
            <div className="font-semibold text-slate-900">当前失败详情</div>
            {qa.hard_errors.map((item, index) => (
              <div key={`hard-${index}`}>严重问题：{item}</div>
            ))}
            {qa.rework_instructions.slice(0, 1).map((item, index) => (
              <div key={`instruction-${index}`}>
                直接原因：{item.reason}；建议：{item.suggested_action}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function EvidenceGateDetailsCard({ qa, workflowSummary }: { qa: QaResult; workflowSummary?: WorkflowSummary }) {
  const details = (qa.metadata?.evidence_gate_details ?? workflowSummary?.evidence_gate_output) as Record<string, unknown> | undefined;
  const diagnostics = (details?.by_competitor ?? details?.evidence_diagnostics_by_competitor) as Record<string, Record<string, unknown>> | undefined;
  if (!details || !diagnostics) return null;

  const competitors = Object.entries(diagnostics);
  if (!competitors.length) return null;

  return (
    <div className="mb-3 rounded border border-amber-200 bg-white p-3 text-sm">
      <div className="mb-2 flex items-center gap-2 font-semibold text-warning">
        <AlertTriangle size={16} /> {"EvidenceGate \u8bc1\u636e\u95e8\u7981\u8be6\u60c5"}
      </div>
      {typeof details.failure_explanation === "string" && (
        <div className="mb-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-slate-700">
          {details.failure_explanation}
        </div>
      )}
      <div className="space-y-2">
        {competitors.map(([competitor, raw]) => {
          const item = raw as Record<string, unknown>;
          const topEvidence = Array.isArray(item.top_evidence) ? item.top_evidence as Array<Record<string, unknown>> : [];
          return (
            <div key={competitor} className="rounded border border-line bg-panel p-3">
              <div className="font-semibold">
                {competitor} | {String(item.status ?? "-")} | {String(item.reason_code ?? "-")}
              </div>
              {typeof item.explanation === "string" && <div className="mt-1 text-xs text-slate-700">{item.explanation}</div>}
              <div className="mt-2 grid gap-2 text-xs md:grid-cols-6">
                <ReasonItem label="total" value={String(item.total_evidence_count ?? 0)} />
                <ReasonItem label="high" value={String(item.high_count ?? 0)} />
                <ReasonItem label="medium" value={String(item.medium_count ?? 0)} />
                <ReasonItem label="low" value={String(item.low_count ?? 0)} />
                <ReasonItem label="unrelated" value={String(item.unrelated_count ?? 0)} />
                <ReasonItem label="alias miss" value={String(item.alias_miss_count ?? 0)} />
              </div>
              {!!topEvidence.length && (
                <div className="mt-2 space-y-1 text-xs text-slate-700">
                  <div className="font-semibold">Top Evidence</div>
                  {topEvidence.map((evidence, index) => (
                    <div key={`${String(evidence.evidence_id ?? index)}-${index}`} className="rounded border border-line bg-white px-2 py-1">
                      {String(evidence.evidence_id ?? "-")} | {String(evidence.source_domain ?? "-")} | {String(evidence.source_quality ?? "-")}
                      {" | relevance="}{String(evidence.relevance_level ?? "-")} ({String(evidence.relevance_score ?? "-")})
                      {" | confidence="}{String(evidence.confidence ?? "-")}
                      {evidence.collector_dimension ? ` | dimension=${String(evidence.collector_dimension)}` : ""}
                      {typeof evidence.relevance_reason === "string" && <div className="mt-1 text-slate-500">{evidence.relevance_reason}</div>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ReworkReasonCard({ qa }: { qa: QaResult }) {
  if (qa.status === "passed" || !qa.rework_instructions?.length) return null;
  const primary = qa.rework_instructions[0];
  const metadata = primary.metadata ?? {};
  const isClaimMismatch = metadata.kind === "claim_evidence_competitor_mismatch";

  return (
    <div className="mb-3 rounded border border-red-200 bg-white p-3 text-sm">
      <div className="mb-2 flex items-center gap-2 font-semibold text-danger">
        <AlertTriangle size={16} /> 为什么被打回
      </div>
      <div className="grid gap-2 lg:grid-cols-3">
        <ReasonItem label="打回节点" value={primary.target_agent ?? qa.route_to ?? "-"} />
        <ReasonItem label="问题类型" value={primary.error_type} />
        <ReasonItem label="失败 Schema" value={primary.failed_schema ?? "-"} />
      </div>

      <div className="mt-2 rounded border border-line bg-panel px-3 py-2 leading-6">
        <div><span className="font-semibold">直接原因：</span>{primary.reason}</div>
        <div><span className="font-semibold">修复要求：</span>{primary.suggested_action}</div>
      </div>

      {isClaimMismatch && (
        <div className="mt-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-slate-700">
          <div className="font-semibold text-slate-900">Claim 与 Evidence 竞品不一致</div>
          <div className="mt-1 grid gap-x-4 gap-y-1 md:grid-cols-2">
            <span>Claim：{String(metadata.claim_id ?? primary.claim_id ?? "-")}</span>
            <span>Claim 竞品：{String(metadata.claim_competitor ?? "-")}</span>
            <span>Evidence：{String(metadata.evidence_id ?? "-")}</span>
            <span>Evidence 竞品：{String(metadata.evidence_competitor ?? "-")}</span>
            <span>来源域名：{String(metadata.evidence_source_domain ?? "-")}</span>
            <span>相关性：{String(metadata.evidence_relevance_level ?? "-")}</span>
          </div>
          {primary.failed_claim && <div className="mt-1">被打回的结论：{primary.failed_claim}</div>}
        </div>
      )}
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
      {cleanItems.length ? (
        <div className="space-y-2">
          {cleanItems.map((item, index) => <p key={`${title}-${index}`}>{item}</p>)}
        </div>
      ) : (
        <p className="text-slate-500">{emptyText}</p>
      )}
    </div>
  );
}

function buildReworkHistory(qa: QaResult, workflowSummary?: WorkflowSummary): RouteHistoryItem[] {
  const qaHistory = (qa.rework_history ?? [])
    .map((item) => ({
      from: typeof item.metadata?.source_node === "string" ? item.metadata.source_node : "QaAgent",
      to: item.route_to ?? "-",
      errorType: item.error_type,
      reason: item.reason,
      action: item.action,
      failedSchema: item.failed_schema,
      claimId: item.claim_id,
      failedClaim: item.failed_claim,
      metadata: item.metadata,
      resultStatus: item.result_status,
    }))
    .filter((item) => item.to !== "-");

  if (qaHistory.length) return qaHistory;

  return (workflowSummary?.conditional_routes_taken ?? [])
    .filter((item) => item.to_node !== "final_report")
    .map((item) => ({
      from: normalizeNodeName(item.from_node ?? "qa"),
      to: normalizeNodeName(item.to_node ?? "-"),
      errorType: item.reason,
      resultStatus: item.final_status,
    }));
}

function ReworkMetadataLine({ metadata }: { metadata?: Record<string, unknown> }) {
  if (!metadata) return null;
  const parts = [
    Array.isArray(metadata.missing_competitors) && metadata.missing_competitors.length
      ? `缺失竞品：${metadata.missing_competitors.join(", ")}`
      : undefined,
    typeof metadata.claim_competitor === "string" ? `Claim 竞品：${metadata.claim_competitor}` : undefined,
    typeof metadata.evidence_id === "string" ? `Evidence：${metadata.evidence_id}` : undefined,
    typeof metadata.evidence_competitor === "string" ? `Evidence 竞品：${metadata.evidence_competitor}` : undefined,
    typeof metadata.evidence_source_domain === "string" ? `来源域名：${metadata.evidence_source_domain}` : undefined,
    typeof metadata.evidence_relevance_level === "string" ? `相关性：${metadata.evidence_relevance_level}` : undefined,
  ].filter(Boolean);
  if (!parts.length) return null;
  return <div className="mt-1 text-xs text-slate-600">{parts.join(" | ")}</div>;
}

function qaReasonLabel(reason: string) {
  const labels: Record<string, string> = {
    bad_report_format: "报告格式或 Claim 证据绑定问题",
    missing_evidence: "缺少 Evidence",
    missing_relevant_evidence: "缺少相关 Evidence",
    invalid_extraction: "结构化抽取异常",
    claim_evidence_support_mismatch: "结论与证据支撑不匹配",
    max_rework_reached: "达到最大返工次数",
  };
  return labels[reason] ?? reason;
}

function normalizeNodeName(node: string) {
  const names: Record<string, string> = {
    qa: "QaAgent",
    collector: "CollectorAgent",
    analyst: "AnalystAgent",
    report_writer: "ReportWriterAgent",
    final_report: "FinalReportAgent",
  };
  return names[node] ?? node;
}
