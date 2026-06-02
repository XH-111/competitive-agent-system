import { ArrowDown, ArrowUp, Download, FileUp, Plus, RefreshCw, Save, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { Report, Survey, SurveyAnalysis, SurveyMetricRole, SurveyPlannerContext, SurveyQuestion, SurveyUploadResponse, Task } from "../../api/types";
import { Pill } from "../../types";
import { metricRoleLabels, questionTypeLabels } from "../../i18n/zh";

type SurveyPanelProps = {
  task?: Task;
  runId?: string;
  report?: Report;
  plannerContext?: SurveyPlannerContext["planner_context"];
};

type PanelState = "idle" | "generating" | "ready" | "saving" | "revising" | "exporting" | "uploading" | "analyzed" | "error";

const feedbackAccept = ".csv,.xlsx,.json,.txt,.md,text/csv,application/json,text/plain,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

const metricRoleOptions: SurveyMetricRole[] = [
  "background",
  "pain_existence",
  "pain_severity",
  "pain_frequency",
  "pain_priority",
  "switching_risk",
  "competitor_preference",
  "solution_preference",
  "willingness_to_pay",
  "open_feedback"
];

export function SurveyPanel({ task, runId, report, plannerContext }: SurveyPanelProps) {
  const [survey, setSurvey] = useState<Survey>();
  const [analysis, setAnalysis] = useState<SurveyAnalysis>();
  const [uploadResult, setUploadResult] = useState<SurveyUploadResponse>();
  const [requirements, setRequirements] = useState("");
  const [topic, setTopic] = useState("");
  const [topicTargetRespondents, setTopicTargetRespondents] = useState("");
  const [topicResearchGoal, setTopicResearchGoal] = useState("");
  const [topicRequirements, setTopicRequirements] = useState("");
  const [topicQuestionCount, setTopicQuestionCount] = useState(10);
  const [revisionRequest, setRevisionRequest] = useState("");
  const [revisionSummary, setRevisionSummary] = useState("");
  const [csvPreview, setCsvPreview] = useState("");
  const [exportMessage, setExportMessage] = useState("");
  const [state, setState] = useState<PanelState>("idle");
  const [error, setError] = useState("");

  useEffect(() => {
    setSurvey(undefined);
    setAnalysis(undefined);
    setUploadResult(undefined);
    setRevisionSummary("");
    setCsvPreview("");
    setExportMessage("");
    setError("");
    if (!task || !runId) return;
    api.runSurvey(task.task_id, runId)
      .then((nextSurvey) => {
        setSurvey(nextSurvey);
        setState("ready");
        return api.surveyAnalysis(nextSurvey.survey_id).then(setAnalysis).catch(() => undefined);
      })
      .catch(() => setState("idle"));
  }, [task?.task_id, runId]);

  const canUseSurveyRun = Boolean(task);
  const canGenerateSurvey = Boolean(task && runId && report);
  const questionCount = survey?.questions.length ?? 0;
  const reportContext = useMemo(() => ({
    report_markdown: report?.markdown ?? "",
    claims_json: report?.claims ?? []
  }), [report]);

  async function generate() {
    if (!task) return;
    setState("generating");
    setError("");
    try {
      const nextSurvey = report && runId
        ? await api.generateSurvey(task.task_id, runId, {
            product_name: task.product_name,
            competitors: task.competitors,
            industry: task.industry,
            region: task.region,
            report_markdown: report.markdown,
            claims_json: report.claims,
            uncertain_findings: report.claims.filter((claim) => claim.confidence < 0.7).map((claim) => claim.text),
            user_requirements: requirements,
            question_count: 10
          })
        : await api.generateTaskSurvey(task.task_id, true);
      setSurvey(nextSurvey);
      setCsvPreview("");
      setExportMessage("");
      setState("ready");
    } catch (err) {
      setError(formatSurveyError(err));
      setState("error");
    }
  }

  async function generateFromTopic() {
    if (!topic.trim()) return;
    setState("generating");
    setError("");
    try {
      const nextSurvey = await api.generateSurveyFromTopic({
        topic: topic.trim(),
        target_respondents: topicTargetRespondents,
        research_goal: topicResearchGoal,
        requirements: topicRequirements,
        question_count: topicQuestionCount
      });
      setSurvey(nextSurvey);
      setAnalysis(undefined);
      setUploadResult(undefined);
      setCsvPreview("");
      setExportMessage("");
      setRevisionSummary("");
      setState("ready");
    } catch (err) {
      setError(formatSurveyError(err));
      setState("error");
    }
  }

  async function saveManualEdits() {
    if (!survey) return;
    setState("saving");
    setError("");
    try {
      const saved = await api.updateSurvey(survey.survey_id, {
        title: survey.title,
        description: survey.description,
        target_respondents: survey.target_respondents,
        research_goal: survey.research_goal,
        expected_analysis_dimensions: survey.expected_analysis_dimensions,
        questions: survey.questions.map((question) => ({
          question_id: question.question_id,
          field_name: question.field_name,
          question_text: question.question_text,
          question_type: question.question_type,
          options: question.options,
          required: question.required,
          analysis_goal: question.analysis_goal,
          related_claim_id: question.related_claim_id,
          reason: question.reason,
          order: question.order,
          theme: question.theme,
          hypothesis: question.hypothesis,
          maps_to_pain_id: question.maps_to_pain_id,
          research_purpose: question.research_purpose,
          analysis_method: question.analysis_method,
          metric_role: question.metric_role
        }))
      });
      setSurvey(saved);
      setAnalysis(undefined);
      setUploadResult(undefined);
      setCsvPreview("");
      setExportMessage("问卷已保存，CSV 模板会按最新题目字段生成。");
      setState("ready");
    } catch (err) {
      setError(formatSurveyError(err));
      setState("error");
    }
  }

  async function addQuestion() {
    if (!survey) return;
    setState("saving");
    setError("");
    try {
      const saved = await api.addSurveyQuestion(survey.survey_id, {
        question_text: "请填写新的问卷问题",
        question_type: "single_choice",
        options: ["选项 A", "选项 B", "其他"],
        required: true,
        analysis_goal: "分析该题反馈对研究问题的影响。",
        maps_to_pain_id: survey.pain_points[0]?.pain_id ?? null,
        research_purpose: "补充验证用户侧痛点。",
        analysis_method: "按选项分布和文本反馈聚合分析。",
        metric_role: "open_feedback"
      });
      setSurvey(saved);
      setAnalysis(undefined);
      setState("ready");
    } catch (err) {
      setError(formatSurveyError(err));
      setState("error");
    }
  }

  async function deleteQuestion(questionId: string) {
    if (!survey) return;
    setState("saving");
    setError("");
    try {
      const saved = await api.deleteSurveyQuestion(survey.survey_id, questionId);
      setSurvey(saved);
      setAnalysis(undefined);
      setState("ready");
    } catch (err) {
      setError(formatSurveyError(err));
      setState("error");
    }
  }

  async function moveQuestion(questionId: string, direction: -1 | 1) {
    if (!survey) return;
    const currentIndex = survey.questions.findIndex((question) => question.question_id === questionId);
    const nextIndex = currentIndex + direction;
    if (currentIndex < 0 || nextIndex < 0 || nextIndex >= survey.questions.length) return;
    const reordered = [...survey.questions];
    const [item] = reordered.splice(currentIndex, 1);
    reordered.splice(nextIndex, 0, item);
    setSurvey({ ...survey, questions: reordered.map((question, index) => ({ ...question, order: index + 1 })) });
    setState("saving");
    setError("");
    try {
      const saved = await api.reorderSurveyQuestions(survey.survey_id, reordered.map((question) => question.question_id));
      setSurvey(saved);
      setAnalysis(undefined);
      setState("ready");
    } catch (err) {
      setError(formatSurveyError(err));
      setState("error");
    }
  }

  async function revise() {
    if (!survey || !revisionRequest.trim()) return;
    setState("revising");
    setError("");
    try {
      const result = await api.reviseSurvey(survey.survey_id, {
        revision_request: revisionRequest,
        report_context: reportContext
      });
      const refreshedSurvey = task && runId
        ? await api.runSurvey(task.task_id, runId).catch(() => result.survey)
        : result.survey;
      setSurvey(refreshedSurvey);
      setAnalysis(undefined);
      setUploadResult(undefined);
      setCsvPreview("");
      setExportMessage("");
      setRevisionSummary(result.revision_summary);
      setRevisionRequest("");
      setState("ready");
    } catch (err) {
      setError(formatSurveyError(err));
      setState("error");
    }
  }

  async function exportCsv() {
    if (!survey) return;
    setState("exporting");
    setError("");
    try {
      const csvText = task ? await api.exportTaskSurveyCsv(task.task_id) : await api.exportSurveyResponseTemplateCsv(survey.survey_id);
      setCsvPreview(csvText.replace(/^\uFEFF/, ""));
      setExportMessage("CSV 答卷模板已生成。第一行是匿名 respondent_id 和各问题字段，可填写后再上传分析。");
      triggerCsvDownload(csvText, `${survey.survey_id}.csv`);
      setState(analysis ? "analyzed" : "ready");
    } catch (err) {
      setError(formatSurveyError(err));
      setState("error");
    }
  }

  async function exportSampleResponsesCsv() {
    if (!survey) return;
    setState("exporting");
    setError("");
    try {
      const csvText = await api.exportSurveySampleResponsesCsv(survey.survey_id);
      setCsvPreview(csvText.replace(/^\uFEFF/, ""));
      setExportMessage("示例反馈 CSV 已按当前问卷字段生成，可直接用于测试上传。");
      triggerCsvDownload(csvText, `${survey.survey_id}_sample_responses.csv`);
      setState(analysis ? "analyzed" : "ready");
    } catch (err) {
      setError(formatSurveyError(err));
      setState("error");
    }
  }

  async function upload(file?: File) {
    if (!survey || !file) return;
    setState("uploading");
    setError("");
    try {
      const result = await api.uploadSurveyResponses(survey.survey_id, file);
      if (result.survey) {
        setSurvey(result.survey);
      }
      setUploadResult(result);
      setAnalysis(result.analysis);
      setState("analyzed");
    } catch (err) {
      setError(formatSurveyError(err));
      setState("error");
    }
  }

  async function uploadAny(file?: File) {
    if (!task || !file) return;
    setState("uploading");
    setError("");
    try {
      const result = await api.importTaskSurveyCsv(task.task_id, file);
      if (result.survey) {
        setSurvey(result.survey);
      }
      setUploadResult(result);
      setAnalysis(result.analysis);
      setCsvPreview("");
      setExportMessage("");
      setState("analyzed");
    } catch (err) {
      setError(formatSurveyError(err));
      setState("error");
    }
  }

  function updateSurveyDraft(patch: Partial<Survey>) {
    if (!survey) return;
    setSurvey({ ...survey, ...patch });
  }

  function updateQuestionDraft(questionId: string, patch: Partial<SurveyQuestion>) {
    if (!survey) return;
    setSurvey({
      ...survey,
      questions: survey.questions.map((question) => {
        if (question.question_id !== questionId) return question;
        const nextQuestion = { ...question, ...patch };
        if (patch.question_type === "rating" && !nextQuestion.options.length) {
          nextQuestion.options = ["1", "2", "3", "4", "5"];
        }
        if ((patch.question_type === "single_choice" || patch.question_type === "multiple_choice") && !nextQuestion.options.length) {
          nextQuestion.options = ["选项 A", "选项 B", "其他"];
        }
        if (patch.question_type === "text" || patch.question_type === "number") {
          nextQuestion.options = [];
        }
        return nextQuestion;
      })
    });
  }

  return (
    <section className="rounded border border-line bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">问卷面板 Survey Panel</h2>
          <p className="text-sm text-slate-600">竞品分析验证问卷，独立于主工作流运行。</p>
        </div>
        <div className="flex items-center gap-2">
          {survey && <Pill value={survey.status} />}
          {survey && <span className="text-xs text-slate-500">v{survey.version} · {questionCount} 题</span>}
        </div>
      </div>

      <div className="mb-4 rounded border border-line bg-panel p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="font-semibold">按话题生成问卷</div>
            <p className="mt-1 text-sm text-slate-600">不依赖任务或报告，可直接输入任意研究主题生成问卷。</p>
          </div>
          <button
            type="button"
            onClick={generateFromTopic}
            disabled={state === "generating" || !topic.trim()}
            className="inline-flex items-center gap-2 rounded bg-accent px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            <Sparkles size={16} /> {state === "generating" ? "生成中..." : "生成话题问卷"}
          </button>
        </div>
        <div className="grid gap-2 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_120px]">
          <input
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            className="rounded border border-line px-3 py-2 text-sm"
            placeholder="例如：高校学生对 AI 学习工具的使用与付费意愿"
          />
          <input
            value={topicTargetRespondents}
            onChange={(event) => setTopicTargetRespondents(event.target.value)}
            className="rounded border border-line px-3 py-2 text-sm"
            placeholder="目标受访者"
          />
          <input
            type="number"
            min={1}
            max={30}
            value={topicQuestionCount}
            onChange={(event) => setTopicQuestionCount(Number(event.target.value))}
            className="rounded border border-line px-3 py-2 text-sm"
          />
        </div>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          <input
            value={topicResearchGoal}
            onChange={(event) => setTopicResearchGoal(event.target.value)}
            className="rounded border border-line px-3 py-2 text-sm"
            placeholder="研究目标"
          />
          <input
            value={topicRequirements}
            onChange={(event) => setTopicRequirements(event.target.value)}
            className="rounded border border-line px-3 py-2 text-sm"
            placeholder="额外要求"
          />
        </div>
      </div>

      {!canUseSurveyRun && !survey && (
        <div className="rounded border border-line bg-panel px-3 py-2 text-sm text-slate-600">
          也可以选择已有任务和 run，基于报告或 Planner 生成竞品分析验证问卷。
        </div>
      )}

      {canUseSurveyRun && !survey && (
        <div className="grid gap-3">
          {plannerContext?.survey_inputs && (
            <div className="rounded border border-line bg-panel p-3 text-sm">
              <div className="font-semibold">Planner 问卷规划</div>
              <div className="mt-2 grid gap-1 text-slate-700">
                <span>建议生成：{plannerContext.survey_needed || plannerContext.survey_recommended ? "是" : "否"}</span>
                <span>目标：{plannerContext.survey_inputs.objective ?? plannerContext.survey_objective ?? "暂无明确目标"}</span>
                <span>受访者：{plannerContext.survey_inputs.respondent_type ?? "暂无明确受访者"}</span>
                <span>主题：{readStringList(plannerContext.survey_inputs.question_themes).join("、") || "暂无主题"}</span>
                <span>假设：{readStringList(plannerContext.survey_inputs.hypotheses).join("；") || "暂无假设"}</span>
              </div>
            </div>
          )}
          {canGenerateSurvey ? (
            <>
              <textarea
                value={requirements}
                onChange={(event) => setRequirements(event.target.value)}
                className="min-h-20 rounded border border-line px-3 py-2 text-sm"
                placeholder="可选：告诉系统你希望重点验证哪些用户侧问题"
              />
              <button
                type="button"
                onClick={generate}
                disabled={state === "generating"}
                className="inline-flex w-fit items-center gap-2 rounded bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                <Sparkles size={16} /> {state === "generating" ? "生成中..." : "基于报告生成问卷"}
              </button>
            </>
          ) : (
            <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-warning">
              当前没有可用报告上下文，将基于 Planner 的 survey_inputs 生成问卷；也可以直接上传任意反馈文件进行独立分析。
            </div>
          )}
          {!canGenerateSurvey && (
            <button
              type="button"
              onClick={generate}
              disabled={state === "generating"}
              className="inline-flex w-fit items-center gap-2 rounded bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              <Sparkles size={16} /> {state === "generating" ? "生成中..." : "根据 Planner 生成问卷"}
            </button>
          )}
          <label className="inline-flex w-fit cursor-pointer items-center gap-2 rounded border border-line bg-white px-4 py-2 text-sm font-semibold">
            <FileUp size={16} /> {state === "uploading" ? "分析中..." : "上传反馈文件分析"}
            <input
              type="file"
              accept={feedbackAccept}
              className="hidden"
              onChange={(event) => uploadAny(event.target.files?.[0])}
            />
          </label>
        </div>
      )}

      {survey && (
        <div className="grid gap-4">
          <div className="rounded border border-line bg-panel p-3">
            <div className="grid gap-2">
              <input
                value={survey.title}
                onChange={(event) => updateSurveyDraft({ title: event.target.value })}
                className="rounded border border-line px-3 py-2 text-base font-semibold"
              />
              <textarea
                value={survey.description}
                onChange={(event) => updateSurveyDraft({ description: event.target.value })}
                className="min-h-16 rounded border border-line px-3 py-2 text-sm"
              />
            </div>
            <div className="mt-2 grid gap-2 md:grid-cols-2">
              <input
                value={survey.target_respondents}
                onChange={(event) => updateSurveyDraft({ target_respondents: event.target.value })}
                className="rounded border border-line px-3 py-2 text-sm"
                placeholder="目标受访者"
              />
              <input
                value={survey.research_goal}
                onChange={(event) => updateSurveyDraft({ research_goal: event.target.value })}
                className="rounded border border-line px-3 py-2 text-sm"
                placeholder="研究目标"
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={saveManualEdits}
                disabled={state === "saving"}
                className="inline-flex items-center gap-2 rounded bg-accent px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                <Save size={16} /> {state === "saving" ? "保存中..." : "保存问卷"}
              </button>
              <button
                type="button"
                onClick={addQuestion}
                disabled={state === "saving"}
                className="inline-flex items-center gap-2 rounded border border-line bg-white px-3 py-2 text-sm font-semibold disabled:opacity-50"
              >
                <Plus size={16} /> 新增题目
              </button>
            </div>
          </div>

          {!!survey.pain_points.length && (
            <div className="rounded border border-line p-3">
              <div className="font-semibold">待验证痛点</div>
              <div className="mt-2 grid gap-2 md:grid-cols-2">
                {survey.pain_points.map((painPoint) => (
                  <div key={painPoint.pain_id} className="rounded bg-panel px-3 py-2 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium">{painPoint.pain_id} · {painPoint.pain_point}</span>
                      <span className="text-xs text-slate-500">{painPoint.severity_assumption ?? "severity unknown"} · {formatConfidence(painPoint.confidence)}</span>
                    </div>
                    <p className="mt-1 text-slate-700">{painPoint.source_from_report}</p>
                    <p className="mt-1 text-xs text-slate-500">{painPoint.why_need_survey}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="grid gap-2">
            {survey.questions.map((question, index) => (
              <article key={question.question_id} className="rounded border border-line p-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{question.question_id}</span>
                    <input
                      value={question.field_name}
                      onChange={(event) => updateQuestionDraft(question.question_id, { field_name: event.target.value })}
                      className="w-56 rounded border border-line px-2 py-1 text-xs"
                    />
                    <label className="inline-flex items-center gap-1 text-xs text-slate-600">
                      <input
                        type="checkbox"
                        checked={question.required}
                        onChange={(event) => updateQuestionDraft(question.question_id, { required: event.target.checked })}
                      />
                      必答
                    </label>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => moveQuestion(question.question_id, -1)}
                      disabled={index === 0 || state === "saving"}
                      className="rounded border border-line p-1.5 disabled:opacity-40"
                      title="上移"
                    >
                      <ArrowUp size={15} />
                    </button>
                    <button
                      type="button"
                      onClick={() => moveQuestion(question.question_id, 1)}
                      disabled={index === survey.questions.length - 1 || state === "saving"}
                      className="rounded border border-line p-1.5 disabled:opacity-40"
                      title="下移"
                    >
                      <ArrowDown size={15} />
                    </button>
                    <button
                      type="button"
                      onClick={() => deleteQuestion(question.question_id)}
                      disabled={state === "saving"}
                      className="rounded border border-line p-1.5 text-danger disabled:opacity-40"
                      title="删除"
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
                <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_180px]">
                  <textarea
                    value={question.question_text}
                    onChange={(event) => updateQuestionDraft(question.question_id, { question_text: event.target.value })}
                    className="min-h-16 rounded border border-line px-3 py-2 text-sm"
                  />
                  <select
                    value={question.question_type}
                    onChange={(event) => updateQuestionDraft(question.question_id, { question_type: event.target.value as SurveyQuestion["question_type"] })}
                    className="h-10 rounded border border-line px-3 py-2 text-sm"
                  >
                    <option value="single_choice">{questionTypeLabels.single_choice}</option>
                    <option value="multiple_choice">{questionTypeLabels.multiple_choice}</option>
                    <option value="rating">{questionTypeLabels.rating}</option>
                    <option value="text">{questionTypeLabels.text}</option>
                    <option value="number">{questionTypeLabels.number}</option>
                  </select>
                </div>
                {(question.question_type === "single_choice" || question.question_type === "multiple_choice" || question.question_type === "rating") && (
                  <textarea
                    value={question.options.join("\n")}
                    onChange={(event) => updateQuestionDraft(question.question_id, { options: event.target.value.split("\n").map((option) => option.trim()).filter(Boolean) })}
                    className="mt-2 min-h-20 w-full rounded border border-line px-3 py-2 text-sm"
                    placeholder="每行一个选项"
                  />
                )}
                <div className="mt-2 grid gap-2 md:grid-cols-3">
                  <select
                    value={question.maps_to_pain_id ?? ""}
                    onChange={(event) => updateQuestionDraft(question.question_id, { maps_to_pain_id: event.target.value || null })}
                    className="rounded border border-line px-3 py-2 text-sm"
                  >
                    <option value="">不绑定痛点</option>
                    {survey.pain_points.map((painPoint) => (
                      <option key={painPoint.pain_id} value={painPoint.pain_id}>
                        {painPoint.pain_id} · {painPoint.pain_point}
                      </option>
                    ))}
                  </select>
                  <select
                    value={question.metric_role ?? ""}
                    onChange={(event) => updateQuestionDraft(question.question_id, { metric_role: (event.target.value || null) as SurveyMetricRole | null })}
                    className="rounded border border-line px-3 py-2 text-sm"
                  >
                    <option value="">指标角色</option>
                    {metricRoleOptions.map((role) => (
                      <option key={role} value={role}>{metricRoleLabels[role] ?? role}</option>
                    ))}
                  </select>
                  <input
                    value={question.research_purpose ?? ""}
                    onChange={(event) => updateQuestionDraft(question.question_id, { research_purpose: event.target.value })}
                    className="rounded border border-line px-3 py-2 text-sm"
                    placeholder="研究用途"
                  />
                </div>
                <input
                  value={question.analysis_method ?? ""}
                  onChange={(event) => updateQuestionDraft(question.question_id, { analysis_method: event.target.value })}
                  className="mt-2 w-full rounded border border-line px-3 py-2 text-sm"
                  placeholder="分析方法"
                />
                <div className="mt-2 grid gap-2 md:grid-cols-3">
                  <input
                    value={question.analysis_goal}
                    onChange={(event) => updateQuestionDraft(question.question_id, { analysis_goal: event.target.value })}
                    className="rounded border border-line px-3 py-2 text-sm"
                    placeholder="分析目的"
                  />
                  <input
                    value={question.theme ?? ""}
                    onChange={(event) => updateQuestionDraft(question.question_id, { theme: event.target.value })}
                    className="rounded border border-line px-3 py-2 text-sm"
                    placeholder="主题"
                  />
                  <input
                    value={question.hypothesis ?? ""}
                    onChange={(event) => updateQuestionDraft(question.question_id, { hypothesis: event.target.value })}
                    className="rounded border border-line px-3 py-2 text-sm"
                    placeholder="假设"
                  />
                </div>
              </article>
            ))}
          </div>

          <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_auto_auto]">
            <textarea
              value={revisionRequest}
              onChange={(event) => setRevisionRequest(event.target.value)}
              className="min-h-16 rounded border border-line px-3 py-2 text-sm"
              placeholder="输入修改要求，例如：压缩到 8 题以内，并增加付费意愿问题"
            />
            <button
              type="button"
              onClick={revise}
              disabled={state === "revising" || !revisionRequest.trim()}
              className="inline-flex h-10 items-center justify-center gap-2 rounded border border-line bg-white px-3 text-sm font-semibold disabled:opacity-50"
            >
              <RefreshCw size={16} /> 修改问卷
            </button>
            <button
              type="button"
              onClick={exportCsv}
              disabled={state === "exporting"}
              className="inline-flex h-10 items-center justify-center gap-2 rounded border border-line bg-white px-3 text-sm font-semibold disabled:opacity-50"
            >
              <Download size={16} /> {state === "exporting" ? "导出中..." : "导出 CSV"}
            </button>
          </div>
          {revisionSummary && <div className="rounded border border-green-300 bg-green-50 px-3 py-2 text-sm text-success">{revisionSummary}</div>}
          {revisionSummary && !analysis && (
            <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-warning">
              问卷已更新，旧反馈分析已清空。请重新上传与新版题目匹配的反馈文件，或直接上传任意问卷反馈进行独立分析。
            </div>
          )}
          {exportMessage && <div className="rounded border border-green-300 bg-green-50 px-3 py-2 text-sm text-success">{exportMessage}</div>}
          {csvPreview && (
            <div className="rounded border border-line bg-panel p-3">
              <div className="mb-2 text-sm font-semibold">CSV 模板预览</div>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded border border-line bg-white p-3 text-xs leading-5">{csvPreview}</pre>
            </div>
          )}

          <label className="inline-flex w-fit cursor-pointer items-center gap-2 rounded border border-line bg-white px-3 py-2 text-sm font-semibold">
            <FileUp size={16} /> {state === "uploading" ? "分析中..." : "上传当前问卷反馈"}
            <input
              type="file"
              accept={feedbackAccept}
              className="hidden"
              onChange={(event) => upload(event.target.files?.[0])}
            />
          </label>
          {task && (
            <label className="inline-flex w-fit cursor-pointer items-center gap-2 rounded border border-line bg-white px-3 py-2 text-sm font-semibold">
              <FileUp size={16} /> {state === "uploading" ? "分析中..." : "上传任意反馈文件分析"}
              <input
                type="file"
                accept={feedbackAccept}
                className="hidden"
                onChange={(event) => uploadAny(event.target.files?.[0])}
              />
            </label>
          )}
          <button
            type="button"
            onClick={exportSampleResponsesCsv}
            disabled={state === "exporting"}
            className="inline-flex w-fit items-center gap-2 rounded border border-line bg-white px-3 py-2 text-sm font-semibold disabled:opacity-50"
          >
            <Download size={16} /> 导出示例反馈 CSV
          </button>
        </div>
      )}

      {uploadResult && (
        <div className="mt-4 grid gap-2 rounded border border-line bg-panel p-3 text-sm">
          <div>样本量 {uploadResult.sample_size}，有效 {uploadResult.valid_count}，无效 {uploadResult.invalid_count}</div>
          {uploadResult.evidence && (
            <div className="rounded border border-green-300 bg-green-50 px-3 py-2 text-success">
              SurveyEvidence 已转换为标准 Evidence：source_type={uploadResult.evidence.source_type}，local_ref={uploadResult.evidence.local_ref}
            </div>
          )}
        </div>
      )}

      {analysis && (
        <div className="mt-4 grid gap-3">
          <div className="grid gap-2 md:grid-cols-4">
            <MetricCard label="样本量" value={String(readNumber(analysis.sample_summary.sample_size) ?? uploadResult?.sample_size ?? "-")} />
            <MetricCard label="有效样本" value={String(readNumber(analysis.sample_summary.valid_count) ?? uploadResult?.valid_count ?? "-")} />
            <MetricCard label="无效样本" value={String(readNumber(analysis.sample_summary.invalid_count) ?? uploadResult?.invalid_count ?? "-")} />
            <MetricCard label="Evidence 置信度" value={formatConfidence(analysis.survey_evidence.confidence)} />
          </div>
          <div className="rounded border border-green-300 bg-green-50 p-3">
            <div className="font-semibold text-success">SurveyEvidence 摘要</div>
            <p className="mt-1 text-sm">{analysis.executive_summary || analysis.dashboard_summary}</p>
            <p className="mt-2 text-xs text-success">
              已生成聚合 SurveyEvidence；上传流程会同步写入 source_type=survey 的标准 Evidence，不保存原始逐行隐私数据。
            </p>
          </div>
          {!!analysis.pain_point_validation?.length && (
            <div className="rounded border border-line p-3">
              <div className="font-semibold">痛点验证</div>
              <div className="mt-2 grid gap-2 md:grid-cols-2">
                {analysis.pain_point_validation.map((item, index) => (
                  <div key={index} className="rounded bg-panel px-3 py-2 text-sm">
                    <div className="font-medium">
                      {String(item.pain_id ?? item.title ?? `痛点 ${index + 1}`)}
                      {item.validation_status ? ` · ${String(item.validation_status)}` : ""}
                    </div>
                    <div className="mt-1 text-slate-700">{String(item.summary ?? item.recommendation ?? item.evidence_summary ?? "")}</div>
                    {item.support_score !== undefined && <div className="mt-1 text-xs text-slate-500">支持度：{String(item.support_score)}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
          {!!analysis.claim_validation_matrix?.length && (
            <div className="rounded border border-line p-3">
              <div className="font-semibold">结论验证矩阵</div>
              <div className="mt-2 grid gap-2">
                {analysis.claim_validation_matrix.map((item, index) => (
                  <div key={index} className="rounded bg-panel px-3 py-2 text-sm">
                    <div className="font-medium">{String(item.claim_id ?? item.claim_text ?? `Claim ${index + 1}`)}</div>
                    <div className="mt-1 text-slate-700">{renderRecordSummary(item, ["validation_status", "support_level", "reason", "recommended_update"])}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {!!analysis.key_findings.length && (
            <div className="rounded border border-line p-3">
              <div className="font-semibold">关键发现</div>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">
                {analysis.key_findings.map((finding, index) => (
                  <li key={index}>{String(finding.finding ?? finding.explanation ?? JSON.stringify(finding))}</li>
                ))}
              </ul>
            </div>
          )}
          {!!analysis.question_level_analysis.length && (
            <div className="rounded border border-line p-3">
              <div className="font-semibold">每题分析</div>
              <div className="mt-2 grid gap-2">
                {analysis.question_level_analysis.map((item, index) => (
                  <div key={index} className="rounded bg-panel px-3 py-2 text-sm">
                    <div className="font-medium">{String(item.question_id ?? item.field_name ?? `Q${index + 1}`)} · {String(item.field_name ?? "")}</div>
                    <div className="mt-1 text-slate-700">{String(item.summary ?? "")}</div>
                    {Array.isArray(item.notable_stats) && (
                      <div className="mt-1 text-xs text-slate-500">{item.notable_stats.map(String).join("；")}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          {!!analysis.user_pain_points.length && (
            <div className="rounded border border-line p-3">
              <div className="font-semibold">用户痛点</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {analysis.user_pain_points.map((painPoint) => (
                  <span key={painPoint} className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-700">{painPoint}</span>
                ))}
              </div>
            </div>
          )}
          {(analysis.willingness_to_pay || analysis.switching_risk) && (
            <div className="grid gap-3 md:grid-cols-2">
              {analysis.willingness_to_pay && (
                <div className="rounded border border-line p-3">
                  <div className="font-semibold">付费 / 预算信号</div>
                  <p className="mt-1 text-sm text-slate-700">{analysis.willingness_to_pay}</p>
                </div>
              )}
              {analysis.switching_risk && (
                <div className="rounded border border-line p-3">
                  <div className="font-semibold">切换风险</div>
                  <p className="mt-1 text-sm text-slate-700">{analysis.switching_risk}</p>
                </div>
              )}
            </div>
          )}
          {!!analysis.recommended_report_revisions?.length && (
            <div className="rounded border border-line p-3">
              <div className="font-semibold">建议回写报告</div>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">
                {analysis.recommended_report_revisions.map((item, index) => (
                  <li key={index}>{renderRecordSummary(item, ["claim_id", "action", "reason", "suggested_text"])}</li>
                ))}
              </ul>
            </div>
          )}
          {!!analysis.competitor_switching_analysis?.length && (
            <div className="rounded border border-line p-3">
              <div className="font-semibold">竞品切换信号</div>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">
                {analysis.competitor_switching_analysis.map((item, index) => (
                  <li key={index}>{renderRecordSummary(item, ["competitor", "switching_risk", "summary", "reason"])}</li>
                ))}
              </ul>
            </div>
          )}
          {analysis.pricing_and_wtp_analysis && (
            <div className="rounded border border-line p-3">
              <div className="font-semibold">价格与付费意愿</div>
              <p className="mt-1 text-sm text-slate-700">{renderRecordSummary(analysis.pricing_and_wtp_analysis, ["summary", "willingness_to_pay", "price_sensitivity", "recommendation"])}</p>
            </div>
          )}
          {!!analysis.next_research_questions?.length && (
            <div className="rounded border border-line p-3">
              <div className="font-semibold">下一步研究问题</div>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">
                {analysis.next_research_questions.map((question) => <li key={question}>{question}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {error && <div className="mt-3 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-warning">{error}</div>}
    </section>
  );
}

function renderRecordSummary(record: Record<string, unknown>, preferredKeys: string[]): string {
  const parts = preferredKeys
    .map((key) => record[key])
    .filter((value) => value !== undefined && value !== null && String(value).trim())
    .map((value) => String(value));
  if (parts.length) {
    return parts.join("；");
  }
  return JSON.stringify(record);
}

function readStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-line bg-panel px-3 py-2">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}

function readNumber(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function formatConfidence(value: unknown): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "-";
}

function triggerCsvDownload(csvText: string, filename: string) {
  const blob = new Blob([csvText], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

function formatSurveyError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  try {
    const parsed = JSON.parse(raw);
    const detail = parsed.detail;
    if (detail?.missing_fields) {
      return `上传的反馈文件缺少当前问卷字段：${detail.missing_fields.join(", ")}。可以改用“上传任意反馈文件分析”。`;
    }
    if (detail?.error === "SURVEY_LLM_API_KEY is not configured") {
      return "问卷模块暂未配置大模型 API Key。请在后端 .env 文件中配置 SURVEY_LLM_API_KEY 后重试。";
    }
    return detail?.message ?? detail?.error ?? raw;
  } catch {
    return raw;
  }
}
