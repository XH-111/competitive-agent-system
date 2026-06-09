type RecorderRequestMeta = {
  label: string;
  url: string;
  method: string;
  query?: Record<string, string>;
  requestBody?: unknown;
};

type RecorderStatus = "success" | "error";

export type FrontendApiRecord = {
  id: string;
  started_at: string;
  completed_at: string;
  duration_ms: number;
  status: RecorderStatus;
  label: string;
  method: string;
  url: string;
  query: Record<string, string>;
  request_body?: unknown;
  response_status?: number;
  response_ok?: boolean;
  response_body?: unknown;
  error_message?: string;
  task_id?: string | null;
  run_id?: string | null;
  inferred_stage: string;
  inferred_agent_hint?: string | null;
  inference_basis: string[];
};

type RecorderSnapshot = {
  enabled: boolean;
  records: FrontendApiRecord[];
};

type CompleteMeta = {
  responseStatus?: number;
  responseOk?: boolean;
  responseBody?: unknown;
  errorMessage?: string;
};

type StageSection = {
  title: string;
  description: string;
  entries: FrontendApiRecord[];
};

const DEFAULT_SNAPSHOT: RecorderSnapshot = {
  enabled: false,
  records: [],
};

const listeners = new Set<() => void>();
let snapshot: RecorderSnapshot = DEFAULT_SNAPSHOT;

function emit() {
  listeners.forEach((listener) => listener());
}

function updateSnapshot(nextSnapshot: RecorderSnapshot) {
  snapshot = nextSnapshot;
  emit();
}

function safeClone<T>(value: T): T {
  if (value === undefined) return value;
  try {
    return JSON.parse(JSON.stringify(value)) as T;
  } catch {
    return value;
  }
}

function parseTaskId(url: string, body?: unknown, responseBody?: unknown): string | null {
  const match = url.match(/\/tasks\/([^/]+)/);
  if (match?.[1]) return decodeURIComponent(match[1]);
  if (isObject(body) && typeof body.task_id === "string") return body.task_id;
  if (isObject(responseBody) && typeof responseBody.task_id === "string") return responseBody.task_id;
  if (isObject(responseBody) && isObject(responseBody.run) && typeof responseBody.run.task_id === "string") return responseBody.run.task_id;
  return null;
}

function parseRunId(url: string, responseBody?: unknown): string | null {
  const match = url.match(/\/runs\/([^/]+)/);
  if (match?.[1]) return decodeURIComponent(match[1]);
  if (isObject(responseBody) && typeof responseBody.run_id === "string") return responseBody.run_id;
  if (isObject(responseBody) && isObject(responseBody.workflow_summary) && typeof responseBody.workflow_summary.run_id === "string") {
    return responseBody.workflow_summary.run_id;
  }
  if (isObject(responseBody) && typeof responseBody.run_id === "string") return responseBody.run_id;
  return null;
}

function isObject(value: unknown): value is Record<string, any> {
  return typeof value === "object" && value !== null;
}

function inferStage(record: Pick<FrontendApiRecord, "label" | "url" | "response_body">): {
  stage: string;
  agentHint?: string | null;
  basis: string[];
} {
  const basis = [`label=${record.label}`, `endpoint=${record.url}`];
  const responseBody = record.response_body;

  if (record.label === "createTask" || record.url === "/api/tasks") {
    return { stage: "Task creation / setup", agentHint: null, basis };
  }
  if (record.label === "runTask" || /\/api\/tasks\/[^/]+\/run/.test(record.url)) {
    if (isObject(responseBody) && isObject(responseBody.workflow_summary)) {
      basis.push("response.workflow_summary");
    }
    return { stage: "Run start / workflow kickoff", agentHint: "PlannerAgent", basis };
  }
  if (record.label === "dag" || /\/dag$/.test(record.url)) {
    return { stage: "Planner / workflow structure", agentHint: "PlannerAgent", basis };
  }
  if (record.label === "evidence" || record.label === "runEvidence" || /\/evidence$/.test(record.url)) {
    return { stage: "Collector / evidence stage", agentHint: "CollectorAgent", basis };
  }
  if (record.label === "qa" || record.label === "runQa" || /\/qa$/.test(record.url)) {
    return { stage: "QA stage", agentHint: "QaAgent", basis };
  }
  if (record.label === "report" || record.label === "runReport" || /\/report$/.test(record.url)) {
    return { stage: "ReportWriter / final report stage", agentHint: "ReportWriterAgent, FinalReportAgent", basis };
  }
  if (record.label === "traces" || record.label === "runTraces" || /\/traces$/.test(record.url)) {
    if (Array.isArray(responseBody)) {
      const agentNames = Array.from(
        new Set(
          responseBody
            .filter(isObject)
            .map((item) => (typeof item.agent_name === "string" ? item.agent_name : null))
            .filter(Boolean),
        ),
      );
      if (agentNames.length > 0) basis.push(`trace.agent_name=${agentNames.join(",")}`);
    }
    return { stage: "Trace / supporting observability fetches", agentHint: "WorkflowEngine + agent traces", basis };
  }
  if (record.label === "runs" || record.label === "latestRun" || /\/runs(\/latest)?$/.test(record.url)) {
    return { stage: "Run history / selection", agentHint: null, basis };
  }
  if (record.label === "listTasks" || /\/api\/tasks$/.test(record.url)) {
    return { stage: "Task list / selection", agentHint: null, basis };
  }
  if (record.label === "llmStatus" || record.label === "testLlm" || record.label === "collectorStatus" || record.label === "testSearch") {
    return { stage: "Environment / supporting fetches", agentHint: null, basis };
  }

  return { stage: "Other frontend follow-up fetches", agentHint: null, basis };
}

function inferAgentSummary(entries: FrontendApiRecord[]): string {
  const hints = Array.from(new Set(entries.map((entry) => entry.inferred_agent_hint).filter(Boolean)));
  return hints.length > 0 ? hints.join(", ") : "No direct agent mapping";
}

function summarizeResponse(entry: FrontendApiRecord): string {
  const body = entry.response_body;
  if (Array.isArray(body)) return `array(${body.length})`;
  if (!isObject(body)) return typeof body === "undefined" ? "no body" : String(body);
  if (typeof body.task_id === "string" && typeof body.run_id === "string") return `task=${body.task_id}, run=${body.run_id}`;
  if (isObject(body.workflow_summary)) {
    return `workflow_summary final_status=${String(body.workflow_summary.final_status ?? "-")}`;
  }
  if (Array.isArray(body.claims)) return `report claims=${body.claims.length}`;
  if (Array.isArray(body.hard_errors)) return `qa status=${String(body.status ?? "-")}`;
  if (typeof body.trace_id === "string") return `trace ${body.trace_id}`;
  return `keys=${Object.keys(body).slice(0, 6).join(", ")}`;
}

function groupRecords(records: FrontendApiRecord[]): StageSection[] {
  const sectionDefinitions = [
    {
      title: "Task creation / run start",
      description: "Frontend calls that create tasks, start workflow runs, or fetch run metadata immediately after kickoff.",
      match: (entry: FrontendApiRecord) => ["Task creation / setup", "Run start / workflow kickoff", "Run history / selection", "Task list / selection"].includes(entry.inferred_stage),
    },
    {
      title: "Planner-related workflow stage",
      description: "Calls inferred to expose planner scope, workflow structure, or workflow summary fields. Mapping is inferred from endpoint purpose and response shape.",
      match: (entry: FrontendApiRecord) => entry.inferred_stage === "Planner / workflow structure" || (entry.label === "runTask" && !!entry.response_body && isObject(entry.response_body) && isObject(entry.response_body.workflow_summary)),
    },
    {
      title: "Collector / evidence stage",
      description: "Evidence-oriented fetches inferred to reflect CollectorAgent outputs or follow-up evidence retrieval for a selected run.",
      match: (entry: FrontendApiRecord) => entry.inferred_stage === "Collector / evidence stage",
    },
    {
      title: "Analyst / knowledge stage",
      description: "The frontend does not call AnalystAgent directly. This section is inferred indirectly from report and trace responses that carry structured knowledge or SWOT.",
      match: (entry: FrontendApiRecord) => {
        const body = entry.response_body;
        return (
          (entry.label === "report" || entry.label === "runReport") &&
          isObject(body) &&
          isObject(body.json_report) &&
          isObject(body.json_report.knowledge)
        );
      },
    },
    {
      title: "ReportWriter / report stage",
      description: "Report fetches likely reflecting ReportWriterAgent and FinalReportAgent outputs. Mapping is inferred from report endpoints and response structure.",
      match: (entry: FrontendApiRecord) => entry.inferred_stage === "ReportWriter / final report stage",
    },
    {
      title: "QA stage",
      description: "QA result fetches inferred to correspond to QaAgent outputs or run-scoped QA retrieval.",
      match: (entry: FrontendApiRecord) => entry.inferred_stage === "QA stage",
    },
    {
      title: "Final report / workflow summary",
      description: "Calls whose response body contains workflow_summary or final_status fields used by the frontend to show run outcomes.",
      match: (entry: FrontendApiRecord) => {
        const body = entry.response_body;
        return isObject(body) && (isObject(body.workflow_summary) || typeof body.final_status === "string");
      },
    },
    {
      title: "Trace / run history / supporting fetches",
      description: "Trace, run-history, and environment support calls. Agent mapping is inferred, not guaranteed, and often represents frontend follow-up fetches rather than direct agent execution.",
      match: (entry: FrontendApiRecord) => entry.inferred_stage === "Trace / supporting observability fetches" || entry.inferred_stage === "Environment / supporting fetches",
    },
  ];

  const sections = sectionDefinitions.map((section) => ({
    title: section.title,
    description: section.description,
    entries: records.filter(section.match),
  }));

  const assignedIds = new Set(sections.flatMap((section) => section.entries.map((entry) => entry.id)));
  const unassigned = records.filter((entry) => !assignedIds.has(entry.id));
  if (unassigned.length > 0) {
    sections.push({
      title: "Unclassified / misc follow-up fetches",
      description: "Calls that were recorded but did not match a more specific inferred workflow grouping.",
      entries: unassigned,
    });
  }
  return sections;
}

function buildGroupedMarkdown(records: FrontendApiRecord[]): string {
  const sections = groupRecords(records);
  const lines: string[] = [
    "# Frontend API Trace Grouped Report",
    "",
    "This report groups frontend API calls by inferred workflow purpose.",
    "Agent/stage mapping is inferred from endpoint meaning, response shape, workflow summary fields, and trace payloads. It is not always a strict one-to-one backend agent mapping.",
    "",
    `Generated at: ${new Date().toISOString()}`,
    `Recorded call count: ${records.length}`,
    "",
  ];

  for (const section of sections) {
    if (section.entries.length === 0) continue;
    lines.push(`## ${section.title}`);
    lines.push("");
    lines.push(section.description);
    lines.push("");
    lines.push(`Inferred agent hints in this section: ${inferAgentSummary(section.entries)}.`);
    lines.push("");
    for (const entry of section.entries) {
      lines.push(`### ${entry.label}`);
      lines.push("");
      lines.push(`- Timestamp: ${entry.started_at}`);
      lines.push(`- Method: ${entry.method}`);
      lines.push(`- URL: ${entry.url}`);
      lines.push(`- Status: ${entry.response_status ?? "-"} (${entry.status})`);
      lines.push(`- task_id: ${entry.task_id ?? "-"}`);
      lines.push(`- run_id: ${entry.run_id ?? "-"}`);
      lines.push(`- Inferred stage: ${entry.inferred_stage}`);
      lines.push(`- Inferred agent hint: ${entry.inferred_agent_hint ?? "-"}`);
      lines.push(`- Response summary: ${summarizeResponse(entry)}`);
      lines.push(`- Inference basis: ${entry.inference_basis.join("; ")}`);
      if (Object.keys(entry.query).length > 0) {
        lines.push(`- Query params: \`${JSON.stringify(entry.query)}\``);
      }
      if (typeof entry.request_body !== "undefined") {
        lines.push("- Request body:");
        lines.push("```json");
        lines.push(JSON.stringify(entry.request_body, null, 2));
        lines.push("```");
      }
      if (typeof entry.response_body !== "undefined") {
        lines.push("- Response body:");
        lines.push("```json");
        lines.push(JSON.stringify(entry.response_body, null, 2));
        lines.push("```");
      }
      if (entry.error_message) {
        lines.push(`- Error message: ${entry.error_message}`);
      }
      lines.push("");
    }
  }
  return lines.join("\n");
}

function downloadFile(filename: string, content: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(objectUrl);
}

export const apiRecorder = {
  start(meta: RecorderRequestMeta) {
    const startedAt = new Date();
    return (completeMeta: CompleteMeta) => {
      if (!snapshot.enabled) return;
      const responseBody = safeClone(completeMeta.responseBody);
      const taskId = parseTaskId(meta.url, meta.requestBody, responseBody);
      const runId = parseRunId(meta.url, responseBody);
      const inferred = inferStage({
        label: meta.label,
        url: meta.url,
        response_body: responseBody,
      });
      const completedAt = new Date();
      const record: FrontendApiRecord = {
        id: `${startedAt.getTime()}_${Math.random().toString(36).slice(2, 8)}`,
        started_at: startedAt.toISOString(),
        completed_at: completedAt.toISOString(),
        duration_ms: completedAt.getTime() - startedAt.getTime(),
        status: completeMeta.errorMessage ? "error" : "success",
        label: meta.label,
        method: meta.method,
        url: meta.url,
        query: safeClone(meta.query ?? {}),
        request_body: safeClone(meta.requestBody),
        response_status: completeMeta.responseStatus,
        response_ok: completeMeta.responseOk,
        response_body: responseBody,
        error_message: completeMeta.errorMessage,
        task_id: taskId,
        run_id: runId,
        inferred_stage: inferred.stage,
        inferred_agent_hint: inferred.agentHint ?? null,
        inference_basis: inferred.basis,
      };
      updateSnapshot({
        ...snapshot,
        records: [...snapshot.records, record],
      });
    };
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  getSnapshot() {
    return snapshot;
  },
  setEnabled(enabled: boolean) {
    updateSnapshot({
      ...snapshot,
      enabled,
    });
  },
  clear() {
    updateSnapshot({
      ...snapshot,
      records: [],
    });
  },
  exportRaw() {
    downloadFile(
      "frontend_api_trace.json",
      JSON.stringify(
        {
          exported_at: new Date().toISOString(),
          recording_enabled: snapshot.enabled,
          record_count: snapshot.records.length,
          records: snapshot.records,
        },
        null,
        2,
      ),
      "application/json",
    );
  },
  exportGroupedMarkdown() {
    downloadFile("frontend_api_trace_grouped.md", buildGroupedMarkdown(snapshot.records), "text/markdown");
  },
};

export function getApiRecorderSnapshot() {
  return snapshot;
}
