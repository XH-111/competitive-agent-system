import { useEffect, useMemo, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { api } from "../api/client";
import type { Survey, SurveyResponse } from "../types";

type PublicSurveyPageProps = {
  inviteCode: string;
};

export function PublicSurveyPage({ inviteCode }: PublicSurveyPageProps) {
  const [survey, setSurvey] = useState<Survey>();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submittedResponse, setSubmittedResponse] = useState<SurveyResponse>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    setError(undefined);
    setSubmittedResponse(undefined);
    api.surveyInvite(inviteCode)
      .then((nextSurvey) => {
        setSurvey(nextSurvey);
        setAnswers(Object.fromEntries((nextSurvey.questions ?? []).map((question) => [question.question_id, ""])));
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [inviteCode]);

  const missingRequired = useMemo(
    () => (survey?.questions ?? []).some((question) => question.required && !answers[question.question_id]?.trim()),
    [answers, survey?.questions],
  );

  async function submit() {
    if (!survey || missingRequired) return;
    setBusy(true);
    setError(undefined);
    try {
      const response = await api.submitSurveyInvite(
        inviteCode,
        (survey.questions ?? []).map((question) => ({
          question_id: question.question_id,
          answer: answers[question.question_id] ?? "",
        })),
        { user_agent: navigator.userAgent },
      );
      setSubmittedResponse(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen bg-panel p-4">
      <section className="mx-auto max-w-3xl rounded border border-line bg-white p-5">
        {error && <div className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-danger">{error}</div>}
        {!survey && !error && <div className="text-sm text-slate-600">正在加载问卷...</div>}
        {survey && submittedResponse && <SubmitResult response={submittedResponse} />}
        {survey && !submittedResponse && (
          <>
            <div className="border-b border-line pb-4">
              <div className="text-xs font-semibold text-accent">邀请码：{inviteCode}</div>
              <h1 className="mt-1 text-xl font-semibold">{survey.title}</h1>
              <p className="mt-2 text-sm text-slate-600">{survey.description}</p>
            </div>
            <div className="mt-4 space-y-4">
              {(survey.questions ?? []).map((question, index) => (
                <label key={question.question_id} className="block rounded border border-line bg-panel p-3">
                  <div className="text-xs font-semibold text-slate-500">
                    Q{index + 1} · {question.competitor ?? "-"} · {question.dimension_id ?? "-"}
                    {question.required ? " · 必填" : ""}
                  </div>
                  <div className="mt-1 text-sm font-semibold">{question.question_text}</div>
                  <textarea
                    className="mt-2 min-h-28 w-full rounded border border-line bg-white p-2 text-sm"
                    value={answers[question.question_id] ?? ""}
                    disabled={busy}
                    onChange={(event) => setAnswers((current) => ({ ...current, [question.question_id]: event.target.value }))}
                    placeholder="请填写你了解的事实、使用经历、时间、依据或可公开来源。"
                  />
                </label>
              ))}
            </div>
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                disabled={busy || missingRequired}
                onClick={submit}
                className="inline-flex items-center gap-2 rounded bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                {busy && <LoaderCircle className="animate-spin" size={16} />}
                {busy ? "正在提交并审核..." : "提交问卷"}
              </button>
            </div>
          </>
        )}
      </section>
    </main>
  );
}

function SubmitResult({ response }: { response: SurveyResponse }) {
  const qaStatus = response.qa_result?.status;
  const acceptedCount = response.qa_result?.accepted_answer_ids?.length ?? 0;
  const rejectedCount = response.qa_result?.rejected_answer_ids?.length ?? 0;
  const needsReviewCount = response.qa_result?.needs_review_answer_ids?.length ?? 0;
  const reasons = response.qa_result?.reasons ?? [];

  if (qaStatus === "accepted" && acceptedCount > 0) {
    return (
      <div className="rounded border border-green-200 bg-green-50 p-4">
        <h1 className="text-xl font-semibold text-success">感谢填写，回答已通过审核并入库</h1>
        <p className="mt-2 text-sm text-slate-700">
          你的回答已保存，并会作为需要验证的问卷知识进入后续分析。
        </p>
      </div>
    );
  }

  return (
    <div className="rounded border border-amber-200 bg-amber-50 p-4">
      <h1 className="text-xl font-semibold text-warning">感谢填写，回答已保存但暂未入库</h1>
      <p className="mt-2 text-sm text-slate-700">
        系统审核认为当前回答暂时不足以进入长期知识库。你可以补充更具体的事实、使用场景、时间、依据或可公开来源后重新提交。
      </p>
      <div className="mt-3 text-xs text-slate-600">
        审核状态：{qaStatus ?? "unknown"}；通过 {acceptedCount} 条，待复核 {needsReviewCount} 条，未通过 {rejectedCount} 条。
      </div>
      {!!reasons.length && (
        <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-slate-600">
          {reasons.slice(0, 5).map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
