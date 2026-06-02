# 当前 LangGraph 主执行流程

本文基于以下文件整理：`AGENTS.md`、`docs/architecture.md`、`backend/app/agents/langgraph_runner.py`、`backend/app/schemas/workflow_state.py`、`backend/app/schemas/agent_io.py`、`backend/app/schemas/models.py`、`backend/app/agents/planner.py`、`backend/app/agents/collector.py`、`backend/app/agents/analyst.py`、`backend/app/agents/report_writer.py`、`backend/app/agents/qa.py`、`backend/app/agents/final_report.py`、`backend/app/api/routes.py`。

未读取其他文件，因此服务内部持久化细节只按这些文件中的调用点说明。

## 1. API 入口

前端通过 FastAPI REST API 进入后端，路由统一挂在 `/api` 前缀下。

主要入口：

- `POST /api/tasks`：创建任务，调用 `TaskService.create_task()`。
- `GET /api/tasks`：获取任务列表。
- `GET /api/tasks/{task_id}`：获取单个任务。
- `POST /api/tasks/{task_id}/run`：运行 workflow，是 LangGraph 主流程入口。

`POST /api/tasks/{task_id}/run` 接收 query 参数：

- `demo_mode`
- `auto_rework`
- `writer_mode`
- `collector_mode`
- `analyst_mode`
- `workflow_engine`
- `content_mode`

该接口先调用 `create_workflow_runner(db, workflow_engine)` 选择 runner。如果最终 `engine == "langgraph"`，直接调用：

```python
runner.run(
    task_id,
    demo_mode=demo_mode,
    auto_rework=auto_rework,
    writer_mode=writer_mode,
    collector_mode=collector_mode,
    analyst_mode=analyst_mode,
    workflow_engine_requested=workflow_engine or "env/default",
    content_mode=content_mode,
)
```

Custom Runner 仍存在，但它是 legacy fallback，不是当前主线。

## 2. TaskRun / run_id 创建位置

LangGraph 主线中，`TaskRun` 在 `LangGraphWorkflowRunner.run()` 一开始创建：

```python
task_run = self.task_run_service.create_run(...)
```

创建参数包括：

- `task_id`
- `workflow_engine="langgraph"`
- `collector_mode`
- `analyst_mode`
- `writer_mode`
- `content_mode`
- `demo_mode`
- `auto_rework`

创建后：

- `task_run.run_id` 写入 `WorkflowState["run_id"]`
- `TraceService.set_run_context(task_run.run_id)` 设置当前 Trace run 上下文
- `TaskService.update_status(task_id, "running", rework_count=0)` 将任务置为运行中

workflow 结束后，`TaskRunService.finish_run()` 根据 workflow summary 写入最终状态、`final_status`、耗时和错误信息。

## 3. WorkflowState 初始化字段

`LangGraphWorkflowRunner.run()` 初始化 `initial_state: WorkflowState`。核心字段分组如下。

运行与任务：

- `task_id`
- `run_id`
- `trace_id`
- `task`
- `task_run`
- `run_status`
- `workflow_engine_requested`
- `workflow_engine_used`
- `demo_mode`
- `collector_mode`
- `analyst_mode`
- `writer_mode`
- `content_mode`
- `auto_rework`
- `rework_count`
- `max_rework`

Agent 输出：

- `planner_output`
- `collector_output`
- `analyst_output`
- `report_writer_output`
- `qa_output`
- `final_report_output`

中间数据：

- `evidence_gate_output`
- `page_fetch_output`
- `evidence`
- `report`
- `qa_result`
- `route_to`
- `final_status`
- `errors`

Planner 扩展规划字段：

- `intent_summary`
- `intent_classification`
- `ambiguity_level`
- `scope_type`
- `scope_size`
- `extracted_context`
- `domain_pack`
- `selected_dimensions`
- `analysis_dimension_plan`
- `planner_core_summary`
- `downstream_guidance`
- `survey_needed`
- `survey_recommended`
- `survey_objective`
- `survey_inputs`
- `survey_extension`
- `confirmed_scope`
- `inferred_scope`
- `suggested_scope`
- `recommended_next_constraints`
- `assumptions`
- `candidate_competitors`
- `clarification_targets`
- `planning_stages`
- `planner_notes`
- `planner_confidence`

后续能力预留：

- `dimension_results`
- `capability_map`
- `survey_evidence`
- `chunks`
- `retrieval_results`
- `claim_support_results`
- `swot_analysis`
- `rework_context`

可观测与 run 隔离：

- `node_sequence`
- `conditional_routes_taken`
- `workflow_summary_core`
- `workflow_summary_extensions`
- `workflow_summary_diagnostics`
- `workflow_summary`
- `run_isolation_strategy="run_id"`
- `run_cleanup_summary`

## 4. LangGraph 节点顺序

`_build_graph()` 中定义 LangGraph 主线节点：

```text
planner
-> collector
-> evidence_gate
-> page_fetcher
-> analyst
-> report_writer
-> qa
-> final_report
-> END
```

其中 `evidence_gate` 和 `qa` 后面是 conditional edges。

EvidenceGate 后：

- `page_fetcher`
- `collector`
- `final_report`

QA 后：

- `collector`
- `analyst`
- `report_writer`
- `final_report`
- `end`

正常成功路径是：

```text
planner -> collector -> evidence_gate -> page_fetcher -> analyst -> report_writer -> qa -> final_report
```

## 5. 每个 Agent 输入

### PlannerAgent

节点：`planner_node`

输入 Schema：

```python
PlannerInput(task=task, run_id=state.get("run_id"), retry_count=state["rework_count"])
```

职责：

- 解析任务意图
- 选择分析维度
- 生成 `AnalysisDimensionPlan`
- 生成下游 guidance
- 判断是否需要问卷/调研侧边流程

### CollectorAgent

节点：`collector_node`

输入 Schema：

```python
CollectorInput(
    task=task,
    run_id=state.get("run_id"),
    retry_count=state["rework_count"],
    collector_mode=state["collector_mode"],
    planner_query_hints=state["analysis_dimension_plan"].query_hints,
    gate_context={
        **state.get("evidence_gate_output", {}),
        "rework_context": ...,
        "targeted_recollection": ...
    },
)
```

职责：

- `collector_mode=mock` 时生成 mock Evidence
- `collector_mode=web` 时调用 Web Search 并转换为 Evidence
- 使用 Planner 的 `query_hints`
- 使用 EvidenceGate / QA 返工上下文做定向补采

### EvidenceGate

节点：`evidence_gate_node`

不是独立 Agent 类，而是 LangGraph runner 内部节点。输入来自 `WorkflowState["evidence"]` 和当前 Task。

职责：

- 按 competitor 统计 high / medium relevance Evidence
- 识别缺少相关证据的 competitor
- 决定是否通过、打回 Collector、或进入最终状态

### PageFetcher

节点：`page_fetcher_node`

输入：

```python
self.page_fetcher.enrich(
    state.get("evidence", []),
    run_id=state.get("run_id"),
    enabled=fetch_enabled,
)
```

`fetch_enabled` 的规则：

- `content_mode == "page"` 时启用
- 或 `content_mode is None and collector_mode == "web"` 时启用

职责：

- 位于 EvidenceGate 之后、AnalystAgent 之前
- 对通过门禁的 Evidence 补充网页正文摘要
- 失败时保留 snippet fallback

### AnalystAgent

节点：`analyst_node`

输入 Schema：

```python
AnalystInput(
    task=task,
    run_id=state.get("run_id"),
    evidence=state.get("evidence", []),
    retry_count=state["rework_count"],
    force_invalid_extraction=...,
    analyst_mode=state["analyst_mode"],
    selected_dimensions=state.get("selected_dimensions", []),
    rework_context=state.get("rework_context"),
)
```

职责：

- 当前主线是 evidence-based analyst
- 从 relevant Evidence 抽取 `ProductProfile`、`FeatureTree`、`CapabilityMap`、`PricingModel`、`UserPersona`、`SwotAnalysis`
- 使用 Planner 的 `selected_dimensions`
- 使用 QA 返工上下文调整分析，尤其是 SWOT 问题

### ReportWriterAgent

节点：`report_writer_node`

输入 Schema：

```python
ReportWriterInput(
    task=task,
    run_id=state.get("run_id"),
    knowledge=state["analyst_output"],
    evidence=state.get("evidence", []),
    retry_count=state["rework_count"],
    force_bad_format=...,
    writer_mode=state["writer_mode"],
    selected_dimensions=state.get("selected_dimensions", []),
    writer_guidance=state.get("downstream_guidance").writer if ... else [],
    intent_classification=state.get("intent_classification"),
    survey_needed=state.get("survey_needed", False),
    survey_recommended=state.get("survey_recommended", False),
    survey_objective=state.get("survey_objective"),
    survey_inputs=state.get("survey_inputs"),
    questionnaire_follow_up=state.get("questionnaire_follow_up"),
)
```

职责：

- 生成 Markdown 报告和 JSON 报告
- 生成 `Claim`
- 每条 Claim 必须绑定 `evidence_ids`
- LLM 模式失败或 schema contract 失败时 fallback 到 mock

### QaAgent

节点：`qa_node`

输入 Schema：

```python
QaInput(
    task=task,
    run_id=state.get("run_id"),
    evidence=state.get("evidence", []),
    analysis=state.get("analyst_output"),
    report_output=state.get("report_writer_output"),
    retry_count=state["rework_count"],
    demo_mode=state["demo_mode"],
)
```

职责：

- 校验 Evidence 是否存在
- 校验 competitor coverage
- 校验 relevance coverage
- 校验 AnalystOutput
- 校验 ReportWriterOutput / Report / Markdown / Claim
- 校验 Claim 和 Evidence 的 competitor 是否匹配
- 校验是否引用 unrelated Evidence
- 校验 SWOT 质量
- 生成 `QaResult`，可能包含 `route_to`

### FinalReportAgent

节点：`final_report_node`

只有在 QA passed 且 writer 有 report 时才调用：

```python
FinalReportInput(
    task=task,
    run_id=state.get("run_id"),
    report=writer_output.report,
    qa_result=qa_result,
    evidence=state.get("evidence", []),
    retry_count=qa_result.rework_count,
)
```

职责：

- 把 QA 结果写入 report
- 生成 evidence summary
- 返回最终 `FinalReportOutput`

## 6. 每个节点写回 WorkflowState 的字段

### planner_node

写回：

- `planner_output`
- `intent_summary`
- `intent_classification`
- `ambiguity_level`
- `scope_type`
- `scope_size`
- `extracted_context`
- `selected_dimensions`
- `analysis_dimension_plan`
- `planner_core_summary`
- `domain_pack`
- `downstream_guidance`
- `survey_needed`
- `survey_recommended`
- `survey_objective`
- `survey_inputs`
- `survey_extension`
- `questionnaire_follow_up`
- `confirmed_scope`
- `inferred_scope`
- `suggested_scope`
- `recommended_next_constraints`
- `assumptions`
- `candidate_competitors`
- `clarification_targets`
- `planning_stages`
- `planner_notes`
- `planner_confidence`
- `node_sequence += ["planner"]`

### collector_node

写回：

- `task`
- `collector_output`
- `evidence`
- `node_sequence += ["collector"]`

并调用 `EvidenceService.save_many(task.task_id, output.evidence, run_id=state.get("run_id"))` 保存 Evidence。

### evidence_gate_node

写回：

- `task`
- `demo_mode`
- `evidence_gate_output`
- `qa_result`
- `route_to`
- `rework_count`
- `final_status`
- `conditional_routes_taken`
- `node_sequence += ["evidence_gate"]`

如果不通过，还会构造并保存一个 `QaResult`。

### page_fetcher_node

写回：

- `task`
- `evidence`
- `page_fetch_output`
- `node_sequence += ["page_fetcher"]`

并再次调用 `EvidenceService.save_many(...)` 保存 PageFetcher 增强后的 Evidence。

### analyst_node

写回：

- `task`
- `analyst_output`
- `capability_map`
- `swot_analysis`
- `node_sequence += ["analyst"]`

### report_writer_node

写回：

- `task`
- `report_writer_output`
- `report`
- `node_sequence += ["report_writer"]`

### qa_node

写回：

- `task`
- `qa_output`
- `qa_result`
- `rework_context`
- `route_to`
- `rework_count`
- `node_sequence += ["qa"]`

如果 `qa_result.status == "failed"` 且 `auto_rework=true` 且存在 `route_to`，还会写回：

- `conditional_routes_taken`
- `final_status`，仅 unknown route 时
- `demo_mode="normal"`，用于真实返工
- 更新 Task 状态为 `qa_failed` 或 `manual_review`

并调用 `ReportService.save_qa(qa_result, run_id=state.get("run_id"))` 保存 QA。

### final_report_node

成功条件：

- `qa_result.status == "passed"`
- `writer_output.report` 存在

成功时写回：

- `task`
- `final_report_output`
- `report`
- `final_status="completed"`
- `node_sequence += ["final_report"]`

并调用：

- `ReportService.save_report(output.report, run_id=state.get("run_id"))`
- `TaskService.update_status(task.task_id, "completed", ...)`

失败或未通过 QA 时：

- `report=None`
- `final_status` 根据 QA 或 state 得出
- `node_sequence += ["final_report"]`
- 更新 Task 状态为 `qa_failed`、`manual_review`、`failed` 等

## 7. EvidenceGate / PageFetcher 位置

主线位置：

```text
CollectorAgent -> EvidenceGate -> PageFetcher -> AnalystAgent
```

EvidenceGate 是 PageFetcher 和 AnalystAgent 的前置质量门禁。它先检查每个 competitor 是否至少有一条 high / medium relevance Evidence。

如果 EvidenceGate 不通过：

- `auto_rework=false`：进入 `final_report`，`final_status="insufficient_evidence"`
- `auto_rework=true` 且未到上限：打回 `collector`
- 达到返工上限：进入 `manual_review`

PageFetcher 只在 EvidenceGate 通过后执行。它对 Evidence 做轻量正文摘要增强，然后把增强后的 Evidence 写回 state 和存储层。

## 8. PlannerAgent 如何把 selected_dimensions / analysis_dimension_plan 传给下游

Planner 输出：

- `selected_dimensions`
- `analysis_dimension_plan`
- `downstream_guidance`

传递路径：

1. `planner_node` 把 `output.selected_dimensions` 写入 `WorkflowState["selected_dimensions"]`。
2. `planner_node` 把 `output.analysis_dimension_plan` 写入 `WorkflowState["analysis_dimension_plan"]`。
3. `collector_node` 从 `state["analysis_dimension_plan"].query_hints` 取出 `planner_query_hints`，传给 `CollectorInput`。
4. `analyst_node` 把 `state["selected_dimensions"]` 传给 `AnalystInput.selected_dimensions`。
5. `report_writer_node` 把 `state["selected_dimensions"]` 传给 `ReportWriterInput.selected_dimensions`。
6. `report_writer_node` 把 `state["downstream_guidance"].writer` 传给 `ReportWriterInput.writer_guidance`。
7. `_workflow_summary()` 把 `selected_dimensions` 和 `downstream_guidance` 写入最终 workflow summary。

因此 Planner 不是只给前端看的节点，它实际影响：

- Collector 搜索 query
- Analyst 抽取重点
- ReportWriter 报告章节和 Claim 重点
- QA 对 SWOT 是否覆盖 Planner 维度的检查
- Workflow summary 展示

## 9. QaAgent conditional routing

`QaAgent.evaluate()` 根据错误类型设置 `QaResult.route_to`。

典型路由：

- 缺少 Evidence：`route_to="CollectorAgent"`
- 缺少相关 Evidence：`route_to="CollectorAgent"`
- Analyst 输出缺失或 ProductProfile 不完整：`route_to="AnalystAgent"`
- Report 输出为空、Markdown 格式错误、Claim 问题：`route_to="ReportWriterAgent"`
- SWOT 缺证据、过度推断、竞品错配、维度缺口：根据问题打回 `CollectorAgent` 或 `AnalystAgent`

`qa_node` 保存 QA 后，如果：

```text
qa_result.status == "failed"
auto_rework == true
qa_result.route_to exists
```

会写入 `conditional_routes_taken`，并把 route 转成 LangGraph node：

- `CollectorAgent -> collector`
- `AnalystAgent -> analyst`
- `ReportWriterAgent -> report_writer`
- unknown route -> `final_report` + `manual_review`

真正的条件跳转由 `route_after_qa()` 决定：

- passed -> `final_report`
- manual_review 或达到 max_rework -> `final_report`
- `auto_rework=false` -> `final_report`
- route_to CollectorAgent -> `collector`
- route_to AnalystAgent -> `analyst`
- route_to ReportWriterAgent -> `report_writer`
- 其他 -> `final_report`

## 10. FinalReportAgent 如何生成最终结果

`final_report_node` 先判断 QA 是否通过。

如果 QA passed 且 ReportWriter 有 report：

1. 调用 `FinalReportAgent.run(FinalReportInput(...))`
2. `FinalReportAgent` 生成 `evidence_summary`
3. `FinalReportAgent` 复制并校验 Report
4. 把 `qa_result` 写入 `report.qa_result`
5. 把 `qa_result` 和 `evidence_summary` 写入 `report.json_report`
6. 返回 `FinalReportOutput`
7. `final_report_node` 保存 Report
8. Task 状态更新为 `completed`
9. `final_status="completed"`

如果 QA 未通过或没有报告：

- 不调用 `FinalReportAgent.run()`
- `report=None`
- 根据 `qa_result` 或 state 生成 `final_status`
- 更新 Task 为 `qa_failed` / `manual_review` / `failed`

## 11. TraceRecord 生成位置

基于当前读取文件，Trace 主要由两类位置生成。

### Agent.run() 通过 run_with_trace 生成

以下 Agent 的 `run()` 都通过 `run_with_trace(...)` 包装，因此会生成 TraceRecord：

- `PlannerAgent`
- `CollectorAgent`
- `AnalystAgent`
- `ReportWriterAgent`
- `QaAgent`
- `FinalReportAgent`

Trace 中包含：

- `task_id`
- `run_id`
- `agent_name`
- `input_summary`
- `output_summary`
- `schema_validation_result`
- `elapsed_time_ms`
- `retry_count`
- 错误时包含 `error_message`

### LangGraph runner 手动保存 Trace

`LangGraphWorkflowRunner` 还手动保存三个 workflow-level Trace：

- `_save_evidence_gate_trace(...)`
  - `agent_name="EvidenceGate"`
  - 记录 EvidenceGate 是否通过、缺失相关 Evidence 的 competitor 等
- `_save_page_fetcher_trace(...)`
  - `agent_name="PageFetcher"`
  - 记录正文抓取启用状态、成功/失败/跳过数量等
- `_save_workflow_trace(...)`
  - `agent_name="WorkflowEngine"`
  - 记录最终 workflow summary，包括 `node_sequence`、`conditional_routes_taken`、`final_status`、耗时等

## 12. 前端最终读取结果的接口

从 `backend/app/api/routes.py` 可见，前端可读接口包括 latest run 默认接口和指定 run 历史接口。

### 默认 latest run 接口

- `GET /api/tasks/{task_id}/traces`
  - 默认取 latest run_id 后返回 Trace
- `GET /api/tasks/{task_id}/evidence`
  - 默认取 latest run_id 后返回 Evidence
- `GET /api/tasks/{task_id}/qa`
  - 默认取 latest run_id 后返回 QA
- `GET /api/tasks/{task_id}/report`
  - 默认取 latest run_id 后返回 Report

### Run History 接口

- `GET /api/tasks/{task_id}/runs`
  - 返回该 Task 的所有 TaskRun
- `GET /api/tasks/{task_id}/runs/latest`
  - 返回 latest TaskRun
- `GET /api/tasks/{task_id}/runs/{run_id}`
  - 返回指定 TaskRun
- `GET /api/tasks/{task_id}/runs/{run_id}/evidence`
  - 返回指定 run 的 Evidence
- `GET /api/tasks/{task_id}/runs/{run_id}/report`
  - 返回指定 run 的 Report
- `GET /api/tasks/{task_id}/runs/{run_id}/qa`
  - 返回指定 run 的 QA
- `GET /api/tasks/{task_id}/runs/{run_id}/traces`
  - 返回指定 run 的 Trace

### 其他接口

- `GET /api/tasks/{task_id}/dag`
  - 返回 legacy `default_dag(task.status)`，不是 LangGraph runtime state 的完整 DAG 回放
- `GET /api/reports/{report_id}`
  - 按 report_id 读取 Report
- `GET /api/llm/status`、`POST /api/llm/test`
  - LLM 配置检查
- `GET /api/collector/status`、`GET /api/search/status`、`POST /api/search/test`
  - Web search 配置检查

## 13. 当前主流程摘要

端到端主线可以概括为：

```text
POST /api/tasks/{task_id}/run
-> create_workflow_runner(...)
-> LangGraphWorkflowRunner.run(...)
-> TaskRunService.create_run(...)
-> initial WorkflowState
-> planner_node
-> collector_node
-> evidence_gate_node
-> page_fetcher_node
-> analyst_node
-> report_writer_node
-> qa_node
-> route_after_qa(...)
-> final_report_node
-> _workflow_summary(...)
-> _save_workflow_trace(...)
-> TaskRunService.finish_run(...)
-> API response: run, run_id, plan, qa_result, report, workflow_summary
```

核心可信度控制点：

- Planner 只生成规划和下游约束，不直接生成结论。
- Collector 按 competitor 分组采集 Evidence。
- EvidenceGate 在 Analyst / ReportWriter 之前拦截无关或不足证据。
- PageFetcher 只在 EvidenceGate 通过后增强正文摘要。
- Analyst 只从 high / medium relevance Evidence 抽取结构化知识。
- ReportWriter 的 Claim 必须绑定 `evidence_ids`。
- QaAgent 检查证据、结构化抽取、报告格式、Claim/Evidence 匹配和 SWOT 质量。
- FinalReportAgent 只在 QA passed 时生成最终报告。
- 所有关键节点都有 TraceRecord，最终还有 WorkflowEngine summary Trace。
