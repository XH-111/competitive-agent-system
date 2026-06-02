import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { SurveyPanel } from "../components/survey/SurveyPanel";
import { TaskList } from "../components/TaskList";
import type { QuestionnaireFollowUpRecommendation, Report, SurveyPlannerContext, Task, TaskRun } from "../types";
import { Pill } from "../types";

export function SurveyWorkspacePage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [task, setTask] = useState<Task>();
  const [runs, setRuns] = useState<TaskRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>();
  const [report, setReport] = useState<Report>();
  const [surveyContext, setSurveyContext] = useState<SurveyPlannerContext>();
  const [plannerContext, setPlannerContext] = useState<SurveyPlannerContext["planner_context"]>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    loadTasks();
  }, []);

  async function loadTasks(selectTaskId?: string) {
    setBusy(true);
    try {
      const nextTasks = await api.listTasks();
      setTasks(nextTasks);
      const nextTask = nextTasks.find((item) => item.task_id === selectTaskId) ?? nextTasks[0];
      if (nextTask) {
        await selectTask(nextTask);
      }
    } finally {
      setBusy(false);
    }
  }

  async function selectTask(nextTask: Task) {
    setTask(nextTask);
    setReport(undefined);
    await api.surveyPlannerContext(nextTask.task_id)
      .then((context) => {
        setSurveyContext(context);
        setPlannerContext(context.planner_context);
      })
      .catch(() => {
        setSurveyContext(undefined);
        setPlannerContext(undefined);
      });
    const nextRuns = await api.runs(nextTask.task_id).catch(() => []);
    setRuns(nextRuns);
    const nextRunId = nextRuns[0]?.run_id;
    setSelectedRunId(nextRunId);
    if (nextRunId) {
      await loadReport(nextTask.task_id, nextRunId);
    }
  }

  async function selectRun(runId: string) {
    if (!task) return;
    setSelectedRunId(runId);
    await loadReport(task.task_id, runId);
  }

  async function loadReport(taskId: string, runId: string) {
    await api.runReport(taskId, runId).then(setReport).catch(() => setReport(undefined));
  }

  return (
    <div className="p-4">
      <section className="mb-4 rounded border border-line bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">问卷分析工作台</h2>
            <p className="mt-1 text-sm text-slate-600">
              这是主竞品分析后的后续侧边流程，用于生成问卷、上传反馈文件并分析用户侧证据。它不是主 DAG 的常驻节点，也不会自动改写主报告或 QA 结果。
            </p>
          </div>
          <button
            type="button"
            onClick={() => loadTasks(task?.task_id)}
            disabled={busy}
            className="inline-flex items-center gap-2 rounded border border-line bg-white px-3 py-2 text-sm font-semibold disabled:opacity-50"
          >
            <RefreshCw size={16} /> {busy ? "刷新中..." : "刷新任务"}
          </button>
        </div>
      </section>

      <div className="mb-4">
        <TaskList tasks={tasks} currentTaskId={task?.task_id} onSelect={selectTask} />
      </div>

      {task && (
        <section className="mb-4 rounded border border-line bg-white p-4">
          <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
            <span className="font-semibold">当前任务：{task.task_id}</span>
            <Pill value={task.status} />
            <span className="text-slate-600">名称：{task.product_name}</span>
            <span className="text-slate-600">竞品：{task.competitors.join("、")}</span>
          </div>

          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {runs.map((run) => (
              <button
                key={run.run_id}
                type="button"
                onClick={() => selectRun(run.run_id)}
                className={`rounded border px-3 py-2 text-left text-xs ${selectedRunId === run.run_id ? "border-accent bg-blue-50" : "border-line bg-panel"}`}
              >
                <div className="font-semibold">{run.run_id.slice(0, 14)}</div>
                <div className="mt-1 text-slate-600">{new Date(run.started_at).toLocaleString()}</div>
                <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1">
                  <span>{run.workflow_engine}</span>
                  <span>{run.collector_mode}</span>
                  <span>{run.analyst_mode}</span>
                  <span>{run.writer_mode}</span>
                </div>
                <div className="mt-1">最终状态 final_status: {run.final_status ?? run.status}</div>
              </button>
            ))}
            {!runs.length && (
              <div className="rounded border border-line bg-panel px-3 py-2 text-sm text-slate-600">
                该任务还没有 run。你仍可以回到竞品分析工作台先运行一次流程。
              </div>
            )}
          </div>
        </section>
      )}

      {plannerContext?.survey_inputs && (
        <section className="mb-4 rounded border border-line bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">Planner 问卷规划</h2>
              <p className="mt-1 text-sm text-slate-600">
                Survey 模块优先消费 Planner 输出的 survey_needed、survey_objective、survey_inputs 和 downstream_guidance.survey。
              </p>
            </div>
            <Pill value={plannerContext.survey_needed || plannerContext.survey_recommended ? "passed" : "pending"} />
          </div>
          {surveyContext?.launch_contract?.recommendation && (
            <div className="mt-3 rounded border border-line bg-panel p-3 text-xs leading-5 text-slate-700">
              <div>启动方式 launch mode: {surveyContext.launch_contract.launch_mode ?? "follow_up_sidecar"}</div>
              <div>建议 recommendation: {formatRecommendation(surveyContext.launch_contract.recommendation)}</div>
              <div>契约来源 contract source: {String(surveyContext.launch_contract.diagnostics?.source ?? plannerContext.diagnostics?.source ?? "-")}</div>
              <div>加载路径 load path: {readStringList(surveyContext.launch_contract.diagnostics?.load_path).join(" -> ") || "-"}</div>
            </div>
          )}
          <div className="mt-3 grid gap-2 text-sm text-slate-700">
            <div><span className="font-semibold">目标 Objective：</span>{plannerContext.survey_inputs.objective ?? plannerContext.survey_objective ?? "暂无明确目标"}</div>
            <div><span className="font-semibold">受访者类型 Respondent type：</span>{plannerContext.survey_inputs.respondent_type ?? "暂无明确受访者"}</div>
            <div><span className="font-semibold">问题主题 Question themes：</span>{readStringList(plannerContext.survey_inputs.question_themes).join("、") || "暂无主题"}</div>
            <div><span className="font-semibold">待验证假设 Hypotheses：</span>{readStringList(plannerContext.survey_inputs.hypotheses).join("；") || "暂无假设"}</div>
            {plannerContext.downstream_guidance?.survey && (
              <div><span className="font-semibold">执行建议 Guidance：</span>{plannerContext.downstream_guidance.survey.join("；")}</div>
            )}
          </div>
        </section>
      )}

      <SurveyPanel task={task} runId={selectedRunId} report={report} plannerContext={plannerContext} />
    </div>
  );
}

function readStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

function formatRecommendation(recommendation: QuestionnaireFollowUpRecommendation): string {
  if (recommendation.required) return "必需后续跟进 required follow-up";
  if (recommendation.recommended) return "建议后续跟进 recommended follow-up";
  return "可选 optional";
}
