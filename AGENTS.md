# AGENTS.md

## Project Positioning

本项目虽然仓库名仍为 `cis-agent-demo`，但后续开发目标不是一次性 Demo，而是面向真实企业竞品分析场景的多 Agent 竞品分析系统。

CIS AI 挑战赛是当前交付节点，不是系统终点。后续工程决策应优先满足：

- 可生产化：同一套流程能够稳定处理不同行业、不同竞品和多次运行。
- 可扩展：后续 RAG、Retriever、PageFetcher、HumanReview、SWOT 等能力应接入统一 workflow。
- 可审计：每条结论、每次 Agent 决策、每次返工都能追溯。
- 可信输出：系统宁可输出证据不足，也不能用无关证据生成看似正式的强结论。
- 合规采集：只处理公开、可引用、可追溯的信息摘要，不绕过站点规则。

## Scoring Rubric Alignment

项目建设优先级应对齐评分细则。

### 1. 多 Agent 协作与输出可信度，35%

- Agent 角色必须清晰，包括采集、分析、报告撰写、质检和最终报告整合。
- Agent 之间必须通过结构化 Schema 传递数据，不能依赖纯自然语言上下文。
- LangGraph / DAG 流程必须可视化、可追溯、可复现。
- QA 反馈闭环必须真实触发，能打回 CollectorAgent、AnalystAgent 或 ReportWriterAgent。
- 返工后必须能看到新的 Trace、路由历史和输出变化，不能是伪闭环。
- 输出必须符合竞品知识 Schema，包括 ProductProfile、FeatureTree、PricingModel、UserPersona。
- 每条 Claim 必须绑定可追溯 Evidence，并能在前端查看来源。

### 2. 技术深度与工程完整度，25%

- 系统必须保持端到端链路完整：采集 -> 编排 -> 结构化知识 -> 报告 -> QA -> 前端展示。
- 每个 Agent 的输入、输出、耗时、错误、fallback、token usage 如可得，都应进入 Trace。
- 外部能力调用必须有超时、异常捕获和 fallback。
- Web Collector、LLM ReportWriter、EvidenceGate、未来 RAG 节点都必须有可观测状态。
- 幻觉抑制必须通过 Schema 校验、Evidence 绑定、relevance gate、QA hard check 和 manual review 实现。
- 新增复杂能力优先接入 LangGraph 主线，而不是扩展 legacy runner。

### 3. 业务价值与产品体验，20%

- 系统应服务真实企业竞品分析流程，而不只是展示固定样例。
- 应支持跨行业、跨区域、多竞品对比。
- 前端必须能清楚展示任务、DAG、竞品知识、报告、证据、QA、Trace 和返工历史。
- 用户应能理解为什么某个结论可信，为什么某个竞品证据不足，为什么 workflow 被打回。
- 后续应逐步补充业务指标：覆盖率、相关证据率、引用覆盖率、QA 通过率、fallback 率、人工复核率。

### 4. 代码质量与文档，10%

- 代码模块边界必须清晰，Agent 业务逻辑、Schema、Service、API、前端组件分离。
- 重要阶段必须在 `docs/` 下形成文档。
- README、架构图、Agent 协议、启动说明、环境变量说明必须持续更新。
- 不要提交 `.env`、API Key 或真实密钥。
- 有意义的代码改动必须运行相关测试并报告结果。

### 5. 合规、材料与答辩，10%

- Web 采集必须遵守公开数据、robots.txt、服务条款、版权和隐私限制。
- 不采集私人数据、敏感个人信息或受保护的长篇正文。
- 只展示公开来源摘要、URL、snippet、source_domain、source_quality、confidence、relevance 等元数据。
- 答辩材料应能清楚说明 Agent 协作、Evidence 可信度、QA 反馈闭环和 Trace 可观测性。

## Core Workflow

当前 LangGraph 主线 workflow 为：

```text
PlannerAgent
-> CollectorAgent
-> EvidenceGate
-> PageFetcher
-> AnalystAgent
-> ReportWriterAgent
-> QaAgent
-> FinalReportAgent
```

Custom Runner 作为 legacy fallback，仍保持旧主链路：

```text
PlannerAgent -> CollectorAgent -> AnalystAgent -> ReportWriterAgent -> QaAgent -> FinalReportAgent
```

QA 可以把未通过质检的任务打回 CollectorAgent、AnalystAgent 或 ReportWriterAgent，并通过 Trace 展示完整执行过程。

EvidenceGate 位于 CollectorAgent 之后、PageFetcher 之前，用于在正文抓取和报告生成前拦截明显无关或不足的 Evidence。PageFetcher 只处理通过相关性门禁的 Evidence。

## Engineering Principles

- Agent 职责必须清晰且相互隔离。
- 每个 Agent 的输入和输出都必须使用结构化 Schema。
- 最终报告中的任何关键结论都必须包含 `evidence_ids`。
- `evidence_ids` 只代表引用绑定，不代表事实自动可信。
- 每次 Agent 执行都必须通过 Trace 记录可观测信息。
- 优先沿用项目已有模式，避免随意新增抽象。
- 改动范围要小，并且必须可测试、可回滚。
- 不要把 API Key、真实密钥或 `.env` 提交到仓库。
- 成品系统开发中，不允许为了页面效果牺牲数据可信度。

## Agent Definition

在本项目中，Agent 不只是 DAG 节点。

一个 Agent 由以下部分组成：

- 角色和职责
- 输入 Schema
- 输出 Schema
- `run()` 方法
- Trace 日志
- 重试和错误处理策略
- 可选外部能力，例如 LLM、Web Search

在 LangGraph 中，一个 Agent 可以实现为一个 node，但 node 只能包装现有 `Agent.run()`，不能复制 Agent 业务逻辑。

## Required Agents

### PlannerAgent

- 解析用户任务。
- 生成任务计划和 DAG。
- 输出结构化 `PlannerOutput`。

### CollectorAgent

- 支持 `collector_mode=mock/web`。
- Web 模式通过 Tavily Search API 搜索公开网页结果。
- 生成结构化 `Evidence`。
- 每条 Evidence 必须可追溯，包含来源、snippet、confidence、source_domain、source_quality、competitor、relevance 信息。
- 多竞品任务必须按 competitor 分组采集，避免单一竞品信息带偏。
- CollectorAgent 必须接收 EvidenceGate 的返工上下文，优先补齐缺失 competitor 的相关证据。

### EvidenceGate

- EvidenceGate 是 LangGraph 主线中的数据质量防线。
- 位于 CollectorAgent 之后、AnalystAgent 之前。
- 检查每个 competitor 是否至少有 high / medium relevance Evidence。
- 如果缺少相关证据且 `auto_rework=true`，打回 CollectorAgent。
- 如果缺少相关证据且 `auto_rework=false`，停止进入 AnalystAgent / ReportWriterAgent，并进入 `insufficient_evidence` 或 `manual_review` 状态。
- EvidenceGate 必须记录 workflow-level Trace 和 `conditional_routes_taken`。

### PageFetcher

- PageFetcher 是 LangGraph 主线中的轻量正文摘要抓取节点。
- 位于 EvidenceGate 之后、AnalystAgent 之前。
- 只处理 `relevance_level=high/medium` 且 `source_quality` 不是 `low_quality` 的公开网页 Evidence。
- 不采集登录后内容、私人数据或敏感数据，不绕过 robots.txt，不做浏览器自动化。
- 只保存截断后的 `content_excerpt`，不保存完整网页正文。
- 抓取失败、非 HTML、超时、403/404 或内容过大时必须 fallback 到原始 snippet，并写入 Trace。
- PageFetcher 是 RAG 前的内容准备层，未来 Chunker / Indexer 只能消费相关 Evidence 的受控摘要或 chunk。

### AnalystAgent

- 支持 `analyst_mode=mock/evidence/llm`。
- 当前主线是 evidence-based analyst。
- 从 Evidence 中抽取 `ProductProfile`、`FeatureTree`、`PricingModel`、`UserPersona`。
- 多竞品任务必须按 `Evidence.competitor` 分组分析。
- 不允许用一个竞品的证据支撑另一个竞品的结构化结论。
- 不允许使用 `relevance_level=unrelated` 的 Evidence 抽取结构化知识。
- Evidence 不足时必须输出保守结论，而不是编造具体功能、定价或用户画像。

### ReportWriterAgent

- 支持 `writer_mode=mock/llm`。
- LLM 模式没有 API Key 或调用失败时必须 fallback 到 mock。
- 生成 Markdown 报告和前端可渲染 JSON 报告。
- 每条 Claim 必须包含 `claim_id`、`competitor`、`category`、`text`、`evidence_ids`、`confidence`。
- 不允许用一个竞品的 Evidence 支撑另一个竞品的 Claim。
- 不允许用 `relevance_level=unrelated` 的 Evidence 支撑 Claim。
- 如果某个 competitor 证据不足，只能写“当前公开证据不足，暂不做强结论”。

### QaAgent

- 校验 Schema、证据覆盖、竞品覆盖、报告格式、Claim evidence 绑定、Evidence 质量和 Evidence relevance。
- 区分 hard error 和 soft suggestion。
- 支持以下路由：
  - 缺少 Evidence / 相关证据不足 -> CollectorAgent
  - 抽取错误 / 结构化冲突 -> AnalystAgent
  - 报告格式错误 / Claim 问题 -> ReportWriterAgent
- 最大返工次数默认 3，超过后进入 `manual_review`。
- QaAgent 不只是格式检查器，也承担业务可信度检查。

### FinalReportAgent

- 负责整合最终报告、QA 结果和 Evidence summary。
- 是真实执行节点，不只是 DAG 终态标签。
- 证据不足或人工复核状态也应生成可解释的最终状态，而不是伪造完整报告。

## Schema Rules

核心 Schema 包括：

- ProductProfile
- FeatureTree
- PricingModel
- UserPersona
- Evidence
- Claim
- AgentMessage
- QaResult
- TraceRecord
- Task
- Report
- WorkflowState

强制规则：

- `Claim.evidence_ids` 必填且不能为空。
- `Claim.competitor` 在多竞品报告中应标识对应竞品。
- Evidence 必须包含 `source_type`、`url` 或 `local_ref`、`collected_at`、`snippet`、`confidence`。
- Web Evidence 应包含 `source_domain`、`source_quality`、`competitor`。
- Web Evidence 应包含 `relevance_score`、`relevance_level`、`relevance_reason` 和 `entity_match_signals`。
- PageFetcher 增强后的 Evidence 应包含 `content_mode`、`page_fetch_success`、`page_title`、`content_excerpt`、`content_chars`、`fetch_status_code`、`page_fetch_error` 和 `fetched_at`。
- AgentMessage 必须包含 `trace_id`、`task_id`、`from_agent`、`to_agent`、`message_type` 和 `schema_name`。
- 模型输出不合法时不能静默通过。

## Product-grade Trust Policy

成品系统必须把“证据存在”和“证据可信”区分开。

- Evidence 必须通过 source quality、confidence、relevance 三层检查。
- Claim 不能引用 unrelated Evidence。
- 强结论只能由 high / medium relevance Evidence 支撑。
- low relevance Evidence 只能触发弱提示或 soft suggestion。
- 随机、不存在或无法确认的竞品名称不得生成正式强结论。
- 如果公开证据不足，系统应明确输出“未找到与该竞品明确相关的公开证据，暂不生成强结论”。
- ReportWriterAgent 不允许为了让报告完整而编造事实。
- QaAgent 必须检查 Claim 与 Evidence 的 competitor 是否匹配。
- EvidenceGate 应在 ReportWriterAgent 之前阻止明显无效的数据进入报告生成。

## Evidence Relevance Policy

Evidence 不只需要存在，还必须与对应 competitor 实体相关。

- `evidence_ids` 只表示报告结论绑定了证据，不代表事实自动可信。
- Web Evidence 必须记录 `relevance_score`、`relevance_level`、`relevance_reason` 和 `entity_match_signals`。
- `entity_match_signals` 至少应包含 competitor 是否出现在 title、snippet、url、domain、alias 中，以及 domain similarity。
- Claim 不能引用 `relevance_level=unrelated` 的 Evidence。
- 具体功能、定价、定位、用户画像等强结论只能由 `high` 或 `medium` relevance Evidence 支撑。
- `low` relevance Evidence 只能作为弱提示或 soft suggestion，不应支撑强结论。
- 随机或不存在的竞品名称不得生成看似正式的强结论。
- CollectorAgent 需要过滤或降级 unrelated Evidence，并在 Trace 中记录 missing relevant evidence 的竞品。
- AnalystAgent 在 evidence 模式下不得用 unrelated Evidence 抽取结构化知识。
- ReportWriterAgent 不得因为 Evidence 有 URL 就默认可信。
- QaAgent 必须检查 Evidence relevance，并在缺少相关证据或 Claim 引用 unrelated Evidence 时打回。

## Run Isolation Policy

Phase 10 起采用 `run_id` 作为产品化 run isolation 策略。

- 每次 workflow run 都必须创建 `TaskRun`。
- Evidence、Report、QA、Trace、WorkflowSummary 都必须绑定 `run_id`。
- 前端默认展示 latest run。
- 用户可以查看历史 run、对比报告版本、回放 Trace。
- 不允许不同 run 的 Evidence / Report / QA 混用。
- `/api/tasks/{task_id}/evidence`、`/api/tasks/{task_id}/report`、`/api/tasks/{task_id}/qa`、`/api/tasks/{task_id}/traces` 默认返回 latest run。
- 历史运行必须通过 `/api/tasks/{task_id}/runs/{run_id}/...` 查询。
- Phase 9.1 的 cleanup 只作为旧过渡策略背景，不再作为 LangGraph 主线隔离方式。

## EvidenceGate Policy

EvidenceGate 是 RAG 前的数据质量防线，位于 LangGraph 主线的 CollectorAgent 之后、PageFetcher 之前。

```text
PlannerAgent -> CollectorAgent -> EvidenceGate -> PageFetcher -> AnalystAgent -> ReportWriterAgent -> QaAgent -> FinalReportAgent
```

- EvidenceGate 检查每个 competitor 是否至少有 high / medium relevance Evidence。
- 如果缺少相关证据且 `auto_rework=true`，EvidenceGate 打回 CollectorAgent。
- 如果缺少相关证据且 `auto_rework=false`，EvidenceGate 停止进入 AnalystAgent / ReportWriterAgent，并进入 `insufficient_evidence` 终态。
- EvidenceGate 必须记录 workflow-level Trace。
- `conditional_routes_taken` 必须记录 EvidenceGate 的跳转。
- 未来 RAG 只能索引 relevant Evidence / chunks，不能索引 unrelated Evidence。

## PageFetcher Policy

PageFetcher 是 Production Web Collection 的最小实现，不是复杂爬虫。

- PageFetcher 只接入 `LangGraphWorkflowRunner`，不扩展 Custom Runner。
- PageFetcher 只抓取通过 EvidenceGate 的 high / medium relevance Evidence。
- 每个 competitor 和每次 run 都必须有抓取数量上限。
- 下载大小、正文长度和保存 excerpt 长度都必须受环境变量限制。
- 只处理 `text/html`，不处理 PDF、图片、视频或二进制文件。
- 不保存完整网页全文，只保存用于分析的短摘要 `content_excerpt`。
- 抓取失败不应导致 workflow 崩溃，应 fallback 到搜索 snippet。
- AnalystAgent 在 evidence 模式下应优先使用 `content_excerpt`，没有时再使用 `snippet`。

## Workflow Engine Status

当前系统有两个 workflow engine。

### 1. CustomWorkflowRunner / MockWorkflowRunner

- 用途：legacy stable fallback。
- 负责当前稳定旧主链路：

```text
PlannerAgent -> CollectorAgent -> AnalystAgent -> ReportWriterAgent -> QaAgent -> FinalReportAgent
```

- 不再作为未来功能扩展主线。
- 不要求支持未来新增的 RAG / Retriever / SWOT / Questionnaire / Interview 节点。
- 不应继续扩展新产品能力，除非明确需要 emergency fallback。

### 2. LangGraphWorkflowRunner

- 用途：main workflow engine for future development。
- 使用 `StateGraph` 显式表达 Agent DAG、`WorkflowState`、QA conditional routing、EvidenceGate 和 `auto_rework`。
- 后续新增 workflow 节点只接入 `LangGraphWorkflowRunner`。

## Workflow Engine Policy

1. Agent business logic must remain single-source.

   `PlannerAgent`、`CollectorAgent`、`AnalystAgent`、`ReportWriterAgent`、`QaAgent`、`FinalReportAgent` 的业务逻辑只能写在各自 `Agent.run()` 中。

2. LangGraph nodes are adapters only.

   LangGraph node 只负责：

   - 从 `WorkflowState` 读取输入
   - 构造 Agent Input Schema
   - 调用 `Agent.run()`
   - 将 Agent Output 写回 `WorkflowState`

   不允许把 Agent 业务逻辑复制到 LangGraph node 里。

3. Future workflow nodes must target LangGraph only.

   后续新增节点只接入 `LangGraphWorkflowRunner`，包括但不限于：

   - PageFetcher
   - Chunker
   - Indexer
   - Retriever
   - RAG-related nodes
   - SWOTAgent
   - QuestionnaireAgent
   - InterviewAgent
   - HumanReviewAgent

   不再同步改 `CustomWorkflowRunner`。

4. Custom Runner is frozen as legacy fallback.

   Custom Runner 只保证当前稳定旧流程可运行，不再跟随新功能演进。

5. `workflow_engine` selection:

   - `workflow_engine=custom` 表示 legacy fallback
   - `workflow_engine=langgraph` 表示推荐主流程
   - 参数优先级：query 参数 > `WORKFLOW_ENGINE` 环境变量 > 默认 `custom`

6. QA routing policy:

   LangGraph 中 `QaAgent` 后必须使用 conditional edge：

   - passed -> `FinalReportAgent`
   - `route_to=CollectorAgent` -> `CollectorAgent`
   - `route_to=AnalystAgent` -> `AnalystAgent`
   - `route_to=ReportWriterAgent` -> `ReportWriterAgent`
   - max_rework reached -> `manual_review` / `FinalReportAgent`

7. EvidenceGate routing policy:

   LangGraph 中 `EvidenceGate` 后必须使用 conditional edge：

   - passed -> `AnalystAgent`
   - failed 且 `auto_rework=true` -> `CollectorAgent`
   - failed 且 `auto_rework=false` -> `FinalReportAgent` / `insufficient_evidence`
   - max_rework reached -> `manual_review` / `FinalReportAgent`

8. Rework loop policy:

   - `max_rework` 默认 3
   - 每次 QA failed 或 EvidenceGate failed 且需要返工时 `rework_count + 1`
   - 不允许死循环
   - `conditional_routes_taken` 必须记录每次跳转

## RAG Readiness Policy

后续可以接入 RAG，但不能直接把搜索结果或无关网页送入向量库。

- RAG 不直接消费原始搜索结果。
- 只有通过 EvidenceGate 的 relevant Evidence 才能进入 PageFetcher / Chunker / Indexer。
- Chunk 必须继承 `task_id`、未来 `run_id`、`competitor`、`evidence_id`、`source_url`、`source_domain`、`source_quality`、`confidence`、`relevance_score`。
- Retriever 返回内容必须保留 citation metadata。
- LLM 只能基于 retrieved chunks 和结构化 knowledge 写结论。
- Claim 必须能追溯到 chunk，再追溯到 Evidence 和原始 URL。
- RAG 节点只接入 LangGraph 主线。

## Future Development Rule

From Phase 9 onward, new capabilities must be developed on the LangGraph workflow path first. `CustomWorkflowRunner` should not be expanded unless explicitly required for emergency fallback.

## Upcoming Development Priorities

### Phase 10: Run ID / Report Versioning

- 用 `run_id` 替代 Phase 9.1 cleanup 策略。
- Evidence / Report / QA / Trace / WorkflowSummary 全部按 run 隔离。
- 支持历史 run 查询、报告版本对比和 Trace 回放。

### Phase 11: Production Web Collection

- 增加 PageFetcher。
- 检查 robots.txt 和合规限制。
- 提取正文摘要，不复制长篇原文。
- 做 URL canonicalization、来源新鲜度和内容截断。
- 继续保留搜索 API fallback。

### Phase 12: Minimal RAG

- 增加 Chunker、Indexer、Vector DB、Retriever。
- 只索引 relevant Evidence / chunks。
- 报告生成必须 citation-aware。

### Phase 13: Human Review / Correction Loop

- 支持人工标记 Evidence 是否相关。
- 支持人工修正 Claim。
- 支持人工接受 / 驳回 QA。
- 记录人工修正率和人工复核原因。

### Phase 14: Business Metrics

- 竞品覆盖率。
- 相关证据率。
- Claim citation coverage。
- QA pass rate。
- fallback rate。
- manual review rate。

## Current Completed Phases

- Phase 1: FastAPI + React + Schema + Mock Agents + Trace + QA
- Phase 2: Frontend demo experience
- Phase 3: auto_rework
- Phase 4: LLM ReportWriter
- Phase 5: Web Collector with Tavily
- Phase 6: Evidence quality scoring
- Phase 7: Evidence-based AnalystAgent
- Phase 7.1: Competitor Coverage Fix
- Phase 8: LangGraph Workflow Engine
- Phase 9: Evidence Relevance Gate
- Phase 9.1: Run Isolation + EvidenceGate
- Phase 10: Run ID / Report Versioning
- Phase 11: Production Web Collection / PageFetcher

## Product Requirements

前端应包含：

- 任务创建页
- 任务列表和任务切换
- DAG 执行页
- 竞品知识页
- 报告页
- Evidence / Source Panel
- QA Result Panel
- Trace Viewer
- Workflow Engine 选择和运行摘要
- EvidenceGate / QA 返工历史展示
- 竞品覆盖和相关证据覆盖展示

报告页必须支持点击 Claim 查看对应 Evidence。

## Compliance Requirements

- 遵守 robots.txt 和网站服务条款。
- 不采集私人或敏感个人数据。
- 不复制长篇受版权保护文本。
- 只展示公开来源摘要、URL、snippet 和可追溯元数据。
- 不把真实 API Key 提交到仓库。
- RAG 阶段也必须保留来源引用和版权安全截断。

## Testing Requirements

常规改动至少覆盖：

- Schema 校验
- Claim 必须包含 evidence_ids
- Evidence 必须可追溯
- Evidence relevance 检查
- Agent Input / Output Schema
- QA 路由逻辑
- EvidenceGate 路由逻辑
- max_rework 限制
- Trace 记录创建
- LangGraph workflow 正常通过
- Custom Runner 旧主链路不被破坏

任何后续新增节点时，必须至少测试：

1. LangGraph workflow 正常通过。
2. QA conditional routing 不死循环。
3. EvidenceGate / QA 打回路径不会跳过必要节点。
4. 每个新节点产生 Trace 或可观测记录。
5. Custom Runner 旧主链路仍然不被破坏。
6. `workflow_engine=langgraph` 不破坏已有 mode 参数：
   - `collector_mode`
   - `analyst_mode`
   - `writer_mode`
   - `content_mode`
   - `demo_mode`
   - `auto_rework`
7. 随机或不存在的竞品不能生成强结论。
8. 外部工具调用失败必须 fallback 或进入 manual review。
9. 未来 RAG 节点只能索引 relevant Evidence / chunks。

在完成有意义的代码改动后，应运行相关测试并报告结果。

## Structured Fact QA Contract Override

以下规则覆盖本文档中“ReportWriterAgent 必须生成 Claim”和“QA 必须检查 Claim”的旧要求，适用于新的 LangGraph 主流程：

- `DimensionResult` 是结构化事实与证据溯源的主数据单元。
- 每条 `DimensionResult` 使用 `dimension_result_id` 唯一标识，并通过 `evidence_ids` 绑定 Evidence。
- `ReportWriterAgent` 只基于 AnalystAgent 已校验的 `dimension_results` 撰写报告，不再生成新的 Claim。
- 新生成的 `Report.claims` 保持为空；该字段仅用于读取历史 run 和兼容旧数据。
- QaAgent 按 Planner、Evidence、DimensionResult、Report 四层执行检查。
- Planner 合约错误打回 `PlannerAgent`。
- 竞品缺少有效 Evidence 时打回 `CollectorAgent`。
- DimensionResult 缺失、引用不存在、竞品错配或维度错配时打回 `AnalystAgent`。
- Report 遗漏竞品、修改结构化事实或缺少事实/证据引用时打回 `ReportWriterAgent`。
- 证据不足是合法状态：必须设置 `insufficient_evidence=true`、使用低置信度、清空 `evidence_ids` 并输出保守说明。
- QA 不再依赖 ReportWriterAgent 临时生成的 Claim 判断事实可信度。
