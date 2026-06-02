import { FileText } from "lucide-react";
import { useState } from "react";
import type { Claim, Evidence, Report } from "../types";
import { reportContractSource } from "../lib/contractSelectors";
import { categoryLabel } from "../types";
import { EvidencePanel } from "./EvidencePanel";
import { labelFor } from "../i18n/zh";

export function ReportView({
  report,
  evidence,
  competitors,
  selectedClaim,
  selectedEvidenceIds,
  onSelect
}: {
  report?: Report;
  evidence: Evidence[];
  competitors?: string[];
  selectedClaim?: Claim;
  selectedEvidenceIds?: string[];
  onSelect: (claim: Claim) => void;
}) {
  const [showJson, setShowJson] = useState(false);
  if (!report) return null;
  const core = report.json_report.core;
  const extensions = report.json_report.extensions;
  const planner = core?.planner ?? report.json_report.planner;
  const survey = extensions?.survey;
  const questionnaireFollowUp = extensions?.questionnaire_follow_up;
  const domainPack = planner?.domain_pack ?? extensions?.domain?.domain_pack;
  const reportContract = reportContractSource(report);

  const coverage = (competitors ?? Array.from(new Set([...evidence.map((item) => item.competitor).filter(Boolean), ...report.claims.map((item) => item.competitor).filter(Boolean)])) as string[])
    .map((competitor) => ({
      competitor,
      evidenceCount: evidence.filter((item) => item.competitor === competitor).length,
      relevantEvidenceCount: evidence.filter((item) => item.competitor === competitor && ["high", "medium"].includes(item.relevance_level ?? "high")).length,
      unrelatedEvidenceCount: evidence.filter((item) => item.competitor === competitor && item.relevance_level === "unrelated").length,
      claimCount: report.claims.filter((item) => item.competitor === competitor).length,
    }));

  return (
    <section className="grid gap-4 bg-white p-4 lg:grid-cols-[minmax(0,1fr)_420px]">
      <div>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold"><FileText size={18} /> 分析报告</h2>
          <button className="rounded border border-line px-3 py-1.5 text-sm font-semibold" onClick={() => setShowJson((value) => !value)}>
            {showJson ? "收起 JSON 报告" : "展开 JSON 报告"}
          </button>
        </div>
        <pre className="max-h-[420px] overflow-auto rounded border border-line bg-panel p-4 whitespace-pre-wrap text-sm leading-6">{report.markdown}</pre>
        {showJson && (
          <pre className="mt-3 max-h-80 overflow-auto rounded border border-line bg-panel p-4 text-xs leading-5">{JSON.stringify(report.json_report, null, 2)}</pre>
        )}
      </div>

      <div className="space-y-3">
        {planner && (
          <section className="rounded border border-line bg-white p-4">
            <h3 className="mb-3 text-sm font-semibold">Planner 分析框架</h3>
            <div className="space-y-2 text-sm">
              <div>意图 intent：{planner.intent_classification ?? "-"}</div>
              <div>已选分析维度 selected_dimensions：{(planner.selected_dimensions ?? []).join(", ") || "-"}</div>
              <div>前端契约 contract：{reportContract}</div>
              <div>领域配置 domain pack：{domainPack?.display_name ?? "-"}</div>
              {!!planner.writer_guidance?.length && (
                <div className="rounded border border-line bg-panel p-3 text-xs leading-5">
                  {planner.writer_guidance.slice(0, 4).map((item) => (
                    <div key={item}>- {item}</div>
                  ))}
                </div>
              )}
              {survey && (
                <div className="rounded border border-line bg-panel p-3 text-xs leading-5">
                  <div>是否建议问卷 survey_recommended：{survey.survey_recommended ? "是" : "否"}</div>
                  <div>问卷目标 survey_objective：{survey.survey_objective ?? "-"}</div>
                </div>
              )}
              {questionnaireFollowUp && (
                <div className="rounded border border-line bg-panel p-3 text-xs leading-5">
                  <div>问卷启动方式 launch_mode：{questionnaireFollowUp.launch_mode ?? "follow_up_sidecar"}</div>
                  <div>是否必需 required：{questionnaireFollowUp.required ? "是" : "否"}</div>
                  <div>受访者 respondents：{questionnaireFollowUp.respondent_type ?? "-"}</div>
                </div>
              )}
            </div>
          </section>
        )}

        {coverage.length > 0 && (
          <section className="rounded border border-line bg-white p-4">
            <h3 className="mb-3 text-sm font-semibold">竞品覆盖情况</h3>
            <div className="space-y-2 text-sm">
              {coverage.map((item) => (
                <div
                  key={item.competitor}
                  className={`rounded border px-3 py-2 ${
                    item.relevantEvidenceCount === 0 || item.claimCount === 0
                      ? "border-amber-300 bg-amber-50 text-warning"
                      : "border-green-300 bg-green-50 text-success"
                  }`}
                >
                  <span className="font-semibold">{item.competitor}</span>
                  <span className="ml-2">
                    证据 Evidence {item.evidenceCount} / 相关证据 Relevant {item.relevantEvidenceCount} / 无关证据 Unrelated {item.unrelatedEvidenceCount} / 结论 Claim {item.claimCount}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="rounded border border-line bg-white p-4">
          <h3 className="mb-3 text-sm font-semibold">结论列表 Claim</h3>
          <div className="space-y-2">
            {report.claims.map((claim) => (
              <button
                key={claim.claim_id}
                onClick={() => onSelect(claim)}
                className={`block w-full rounded border p-3 text-left text-sm ${selectedClaim?.claim_id === claim.claim_id ? "border-accent bg-blue-50" : "border-line bg-panel"} ${claim.evidence_ids.length ? "" : "border-danger bg-red-50"}`}
              >
                <div className="font-semibold">{claim.competitor ? `${claim.competitor} · ` : ""}{categoryLabel[claim.category] ?? claim.category} · 置信度 {Math.round(claim.confidence * 100)}%</div>
                <div className="mt-1">{claim.text}</div>
                <div className={`mt-2 text-xs ${claim.evidence_ids.length ? "text-slate-600" : "font-semibold text-danger"}`}>
                  {claim.evidence_ids.length ? `${labelFor("evidence_ids")}：${claim.evidence_ids.join(", ")}` : "缺少 evidence_ids"}
                </div>
              </button>
            ))}
          </div>
        </section>
        <EvidencePanel claim={selectedClaim} evidence={evidence} evidenceIds={selectedEvidenceIds} />
      </div>
    </section>
  );
}
