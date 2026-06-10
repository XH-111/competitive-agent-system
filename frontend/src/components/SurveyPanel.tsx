import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Survey, SurveyResponse } from "../types";

type SurveyPanelProps = {
  taskId?: string;
  runId?: string;
};

export function SurveyPanel({ taskId, runId }: SurveyPanelProps) {
  const [surveys, setSurveys] = useState<Survey[]>([]);
  const [selectedSurvey, setSelectedSurvey] = useState<Survey>();
  const [responses, setResponses] = useState<SurveyResponse[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!taskId || !runId) {
      setSurveys([]);
      setSelectedSurvey(undefined);
      setResponses([]);
      return;
    }
    void loadSurveys();
  }, [taskId, runId]);

  const inviteUrl = useMemo(() => {
    if (!selectedSurvey?.invite_code) return "";
    return `${window.location.origin}/survey/${selectedSurvey.invite_code}`;
  }, [selectedSurvey?.invite_code]);

  async function loadSurveys() {
    if (!taskId || !runId) return;
    try {
      const list = await api.runSurveys(taskId, runId);
      setSurveys(list);
      if (list[0]) {
        await selectSurvey(list[0].survey_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function selectSurvey(surveyId: string) {
    const survey = await api.survey(surveyId);
    setSelectedSurvey(survey);
    setResponses(await api.surveyResponses(surveyId));
  }

  async function generateSurvey() {
    if (!taskId || !runId) return;
    setBusy(true);
    setError(undefined);
    try {
      const survey = await api.generateSurvey(taskId, runId);
      setSelectedSurvey(survey);
      setSurveys((current) => [survey, ...current.filter((item) => item.survey_id !== survey.survey_id)]);
      setResponses(await api.surveyResponses(survey.survey_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded border border-line bg-white p-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">问卷补充采集</h2>
          <p className="mt-1 text-xs text-slate-500">
            根据 EvidenceAnalyst 的 not_found / partial 问题生成问卷，外部回答会保存到后端并写入知识库。
          </p>
        </div>
        <button
          type="button"
          disabled={!taskId || !runId || busy}
          onClick={generateSurvey}
          className="rounded bg-accent px-3 py-2 font-semibold text-white disabled:opacity-50"
        >
          {busy ? "生成中..." : "根据缺口生成问卷"}
        </button>
      </div>

      {error && <div className="mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-danger">{error}</div>}

      {!!surveys.length && (
        <div className="mt-4 flex flex-wrap gap-2">
          {surveys.map((survey) => (
            <button
              key={survey.survey_id}
              type="button"
              onClick={() => void selectSurvey(survey.survey_id)}
              className={`rounded border px-3 py-1.5 text-xs font-semibold ${
                selectedSurvey?.survey_id === survey.survey_id ? "border-accent bg-blue-50 text-accent" : "border-line bg-panel"
              }`}
            >
              {survey.invite_code ?? survey.survey_id} · {survey.response_count} responses
            </button>
          ))}
        </div>
      )}

      {selectedSurvey && (
        <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_360px]">
          <div className="rounded border border-line bg-panel p-3">
            <div className="font-semibold">{selectedSurvey.title}</div>
            <div className="mt-1 text-xs text-slate-600">{selectedSurvey.description}</div>
            <div className="mt-3 grid gap-2">
              {(selectedSurvey.questions ?? []).map((question, index) => (
                <div key={question.question_id} className="rounded border border-line bg-white p-3">
                  <div className="text-xs font-semibold text-slate-500">
                    Q{index + 1} · {question.competitor ?? "-"} · {question.dimension_id ?? "-"} · {question.source_gap_type}
                  </div>
                  <div className="mt-1">{question.question_text}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded border border-line bg-panel p-3">
            <div className="font-semibold">邀请信息</div>
            <div className="mt-2 text-xs text-slate-500">邀请码</div>
            <div className="font-mono text-lg font-semibold">{selectedSurvey.invite_code}</div>
            <div className="mt-2 text-xs text-slate-500">填写链接</div>
            <div className="break-all rounded border border-line bg-white p-2 text-xs">{inviteUrl}</div>
            <button
              type="button"
              className="mt-2 rounded border border-line bg-white px-3 py-1.5 text-xs font-semibold"
              onClick={() => void navigator.clipboard?.writeText(inviteUrl)}
            >
              复制链接
            </button>

            <div className="mt-4 font-semibold">回答</div>
            {responses.length ? (
              <div className="mt-2 max-h-80 space-y-2 overflow-auto">
                {responses.map((response) => (
                  <div key={response.response_id} className="rounded border border-line bg-white p-2 text-xs">
                    <div className="font-semibold">
                      {new Date(response.submitted_at).toLocaleString()} · KB {response.kb_ingested ? "已入库" : "未入库"}
                    </div>
                    {response.answers.map((answer) => (
                      <div key={answer.answer_id} className="mt-1 text-slate-600">{answer.answer_text}</div>
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <div className="mt-2 text-xs text-slate-500">暂无回答。</div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
