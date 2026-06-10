import type { CollectorStatus, Dag, Evidence, LlmStatus, PlannerAttempt, PlannerRunResult, QaResult, Report, RunTaskOverrides, SearchTestResult, Survey, SurveyResponse, Task, TaskRun, TraceRecord, WorkflowProgress } from "./types";
import { apiRecorder } from "./recorder";

const baseUrl = "";

type RequestOptions = RequestInit & {
  label: string;
};

function parseQuery(url: string): Record<string, string> {
  const queryIndex = url.indexOf("?");
  if (queryIndex < 0) return {};
  return Object.fromEntries(new URLSearchParams(url.slice(queryIndex + 1)).entries());
}

async function request<T>(url: string, options: RequestOptions): Promise<T> {
  const { label, ...fetchOptions } = options;
  const requestBody = typeof fetchOptions.body === "string"
    ? (() => {
        try {
          return JSON.parse(fetchOptions.body);
        } catch {
          return fetchOptions.body;
        }
      })()
    : fetchOptions.body;
  const complete = apiRecorder.start({
    label,
    url,
    method: fetchOptions.method ?? "GET",
    query: parseQuery(url),
    requestBody,
  });
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${url}`, {
      headers: { "Content-Type": "application/json" },
      ...fetchOptions
    });
  } catch (error) {
    complete({
      errorMessage: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }
  const responseText = await response.text();
  const responseBody = responseText
    ? (() => {
        try {
          return JSON.parse(responseText);
        } catch {
          return responseText;
        }
      })()
    : undefined;
  if (!response.ok) {
    complete({
      responseStatus: response.status,
      responseOk: response.ok,
      responseBody,
      errorMessage: typeof responseBody === "string" ? responseBody : JSON.stringify(responseBody),
    });
    throw new Error(typeof responseBody === "string" ? responseBody : JSON.stringify(responseBody));
  }
  complete({
    responseStatus: response.status,
    responseOk: response.ok,
    responseBody,
  });
  return responseBody as T;
}

export const api = {
  createTask: (payload: { product_name: string; competitors: string[]; region: string; industry: string; collection_strategy_mode?: "simple" | "balanced" | "expert" }) =>
    request<Task>("/api/tasks", { label: "createTask", method: "POST", body: JSON.stringify(payload) }),
  listTasks: () => request<Task[]>("/api/tasks", { label: "listTasks", method: "GET" }),
  runTask: (taskId: string, demoMode = "normal", autoRework = false, writerMode = "mock", collectorMode = "mock", analystMode = "evidence", workflowEngine = "custom", debugStage?: "planner_only" | "main_flow" | "collector_only", overrides?: RunTaskOverrides) => {
    const debugQuery = debugStage ? `&debug_stage=${encodeURIComponent(debugStage)}` : "";
    return request<PlannerRunResult>(`/api/tasks/${taskId}/run?demo_mode=${encodeURIComponent(demoMode)}&auto_rework=${autoRework}&writer_mode=${encodeURIComponent(writerMode)}&collector_mode=${encodeURIComponent(collectorMode)}&analyst_mode=${encodeURIComponent(analystMode)}&workflow_engine=${encodeURIComponent(workflowEngine)}${debugQuery}`, { label: "runTask", method: "POST", body: JSON.stringify(overrides ?? {}) });
  },
  dag: (taskId: string) => request<Dag>(`/api/tasks/${taskId}/dag`, { label: "dag", method: "GET" }),
  evidence: (taskId: string) => request<Evidence[]>(`/api/tasks/${taskId}/evidence`, { label: "evidence", method: "GET" }),
  qa: (taskId: string) => request<QaResult>(`/api/tasks/${taskId}/qa`, { label: "qa", method: "GET" }),
  report: (taskId: string) => request<Report>(`/api/tasks/${taskId}/report`, { label: "report", method: "GET" }),
  traces: (taskId: string) => request<TraceRecord[]>(`/api/tasks/${taskId}/traces`, { label: "traces", method: "GET" }),
  runs: (taskId: string) => request<TaskRun[]>(`/api/tasks/${taskId}/runs`, { label: "runs", method: "GET" }),
  latestRun: (taskId: string) => request<TaskRun>(`/api/tasks/${taskId}/runs/latest`, { label: "latestRun", method: "GET" }),
  runEvidence: (taskId: string, runId: string) => request<Evidence[]>(`/api/tasks/${taskId}/runs/${runId}/evidence`, { label: "runEvidence", method: "GET" }),
  runQa: (taskId: string, runId: string) => request<QaResult>(`/api/tasks/${taskId}/runs/${runId}/qa`, { label: "runQa", method: "GET" }),
  runReport: (taskId: string, runId: string) => request<Report>(`/api/tasks/${taskId}/runs/${runId}/report`, { label: "runReport", method: "GET" }),
  runTraces: (taskId: string, runId: string) => request<TraceRecord[]>(`/api/tasks/${taskId}/runs/${runId}/traces`, { label: "runTraces", method: "GET" }),
  runProgress: (taskId: string, runId: string) => request<WorkflowProgress>(`/api/tasks/${taskId}/runs/${runId}/progress`, { label: "runProgress", method: "GET" }),
  runPlannerAttempts: (taskId: string, runId: string) => request<PlannerAttempt[]>(`/api/tasks/${taskId}/runs/${runId}/planner-attempts`, { label: "runPlannerAttempts", method: "GET" }),
  cancelRun: (taskId: string, runId: string) => request<TaskRun>(`/api/tasks/${taskId}/runs/${runId}/cancel`, { label: "cancelRun", method: "POST" }),
  generateSurvey: (taskId: string, runId: string) => request<Survey>(`/api/tasks/${taskId}/runs/${runId}/survey`, { label: "generateSurvey", method: "POST" }),
  runSurveys: (taskId: string, runId: string) => request<Survey[]>(`/api/tasks/${taskId}/runs/${runId}/surveys`, { label: "runSurveys", method: "GET" }),
  survey: (surveyId: string) => request<Survey>(`/api/surveys/${surveyId}`, { label: "survey", method: "GET" }),
  surveyResponses: (surveyId: string) => request<SurveyResponse[]>(`/api/surveys/${surveyId}/responses`, { label: "surveyResponses", method: "GET" }),
  surveyInvite: (inviteCode: string) => request<Survey>(`/api/survey-invites/${encodeURIComponent(inviteCode)}`, { label: "surveyInvite", method: "GET" }),
  submitSurveyInvite: (inviteCode: string, answers: Array<{ question_id: string; answer: unknown }>, metadata?: Record<string, unknown>) =>
    request<SurveyResponse>(`/api/survey-invites/${encodeURIComponent(inviteCode)}/responses`, { label: "submitSurveyInvite", method: "POST", body: JSON.stringify({ answers, metadata }) }),
  llmStatus: () => request<LlmStatus>("/api/llm/status", { label: "llmStatus", method: "GET" }),
  testLlm: () => request<LlmStatus>("/api/llm/test", { label: "testLlm", method: "POST" }),
  collectorStatus: () => request<CollectorStatus>("/api/search/status", { label: "collectorStatus", method: "GET" }),
  testSearch: (query: string) => request<SearchTestResult>("/api/search/test", { label: "testSearch", method: "POST", body: JSON.stringify({ query }) })
};
