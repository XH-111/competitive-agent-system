import { SearchCheck } from "lucide-react";
import type { QaResult, WorkflowSummary } from "../types";
import { Pill } from "../types";
import { errorTypeLabel, labelFor } from "../i18n/zh";

type RouteHistoryItem = {
  from: string;
  to: string;
  reason?: string;
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
        <SearchCheck size={18} /> 质检结果 QA Result
      </h2>

      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <span>最终状态 final_status：</span>
        <Pill value={qa.status} />
        <span>返工次数 rework_count：{qa.rework_count}</span>
        <span>最大返工次数 max_rework：3</span>
        {swotValidation && <span>SWOT 校验 swot_validation：{swotValidation.status ?? "-"}</span>}
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <InfoBlock title={labelFor("hard_errors")} items={qa.hard_errors} emptyText="暂无严重问题" />
        <InfoBlock title={labelFor("soft_suggestions")} items={qa.soft_suggestions} emptyText="暂无优化建议" />
        <InfoBlock
          title={labelFor("rework_instructions")}
          items={(qa.rework_instructions ?? []).map((item) => [
            `问题类型 error_type: ${errorTypeLabel(item.error_type)}`,
            item.failed_claim ? `失败结论 failed_claim: ${item.failed_claim}` : undefined,
            item.failed_schema ? `失败 Schema failed_schema: ${item.failed_schema}` : undefined,
            typeof item.metadata?.competitor === "string" ? `竞品 competitor: ${item.metadata.competitor}` : undefined,
            typeof item.metadata?.quadrant === "string" ? `SWOT 象限 quadrant: ${item.metadata.quadrant}` : undefined,
            typeof item.metadata?.fix_type === "string" ? `修复类型 fix_type: ${item.metadata.fix_type}` : undefined,
            `原因 reason: ${item.reason}`,
            `返工目标 route_to: ${item.target_agent ?? qa.route_to ?? "-"}`,
            `建议动作 suggested_action: ${item.suggested_action}`,
            Array.isArray(item.metadata?.query_focus) && item.metadata.query_focus.length
              ? `搜索重点 query_focus: ${item.metadata.query_focus.join(", ")}`
              : undefined,
            `返工次数 rework_count: ${qa.rework_count}`,
            `最终状态 final_status: ${qa.status}`,
          ].filter(Boolean).join(" | "))}
          emptyText="暂无返工指令"
        />
      </div>

      {swotIssues.length > 0 && (
        <div className="mt-3 rounded border border-line bg-white p-3 text-sm">
          <h3 className="mb-2 font-semibold">SWOT 质检问题</h3>
          <div className="space-y-2">
            {swotIssues.map((issue, index) => (
              <div key={`${issue.error_type ?? "swot"}-${index}`} className="rounded border border-line bg-panel p-3">
                <div className="font-semibold">
                  {errorTypeLabel(issue.error_type ?? "swot_issue")}
                  {issue.competitor ? ` | ${issue.competitor}` : ""}
                  {issue.quadrant ? ` | ${issue.quadrant}` : ""}
                </div>
                {issue.reason && <div className="mt-1">{issue.reason}</div>}
                <div className="mt-1 text-xs text-slate-600">
                  {issue.fix_type ? `修复类型 fix_type: ${issue.fix_type}` : ""}
                  {issue.target_agent ? ` | 返工目标 route_to: ${issue.target_agent}` : ""}
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
        <h3 className="mb-2 font-semibold">{labelFor("rework_history")}</h3>
        {reworkHistory.length ? (
          <div className="space-y-1">
            {reworkHistory.map((item, index) => (
              <div key={`${item.from}-${item.to}-${index}`}>
                第 {index + 1} 轮：{item.from} -&gt; {item.to}
                {item.reason ? `，原因 reason: ${errorTypeLabel(item.reason)}` : ""}
                {item.resultStatus ? `，结果 result: ${item.resultStatus}` : ""}
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
      from: "QaAgent",
      to: item.route_to ?? "-",
      reason: item.error_type ?? item.action,
      resultStatus: item.result_status,
    }))
    .filter((item) => item.to !== "-");

  if (qaHistory.length) return qaHistory;

  return (workflowSummary?.conditional_routes_taken ?? [])
    .filter((item) => item.to_node !== "final_report")
    .map((item) => ({
      from: normalizeNodeName(item.from_node ?? "qa"),
      to: normalizeNodeName(item.to_node ?? "-"),
      reason: item.reason,
      resultStatus: item.final_status,
    }));
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
