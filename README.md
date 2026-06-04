# CIS 多 Agent 竞品分析系统 Demo

这是一个面向 CIS AI 挑战赛的“多 Agent 竞品分析系统”初步 Demo。

当前版本默认使用确定性的 Mock Agent，并提供可选的最小真实 LLM ReportWriter 与最小 Web Collector。系统不接入 RAG 或向量数据库，不执行复杂爬虫。它的目标是先把可演示、可替换、可扩展的工程骨架搭好：Schema、API、持久化、Mock 工作流、QA 打回闭环、证据绑定、Trace 可观测、前端页面和测试。

## 架构概览

```text
React + Vite 前端
       |
FastAPI REST API
       |
SQLite + SQLAlchemy
       |
RunnerFactory
       |
LangGraphWorkflowRunner:
PlannerAgent -> CollectorAgent -> EvidenceGate -> PageFetcher -> AnalystAgent -> ReportWriterAgent -> QaAgent -> FinalReportAgent

CustomWorkflowRunner:
PlannerAgent -> CollectorAgent -> AnalystAgent -> ReportWriterAgent -> QaAgent -> FinalReportAgent
```

QA 支持以下打回路径：

- 缺少证据 -> `CollectorAgent`
- 抽取错误或结论冲突 -> `AnalystAgent`
- 报告格式错误或 Claim 缺少证据 -> `ReportWriterAgent`
- 返工次数超过 3 次 -> `manual_review`

## 项目结构

```text
backend/
  app/
    api/                 FastAPI 路由
    agents/              Mock Agent 和工作流 Runner
    schemas/             Pydantic Schema 与 Agent I/O Schema
    services/            Task、Trace、Evidence、Report 服务
    database.py          SQLite 初始化
    db_models.py         SQLAlchemy 数据表模型
    main.py              FastAPI 入口
  tests/                 pytest 测试
  requirements.txt
frontend/
  src/
    api/                 API Client 与 TypeScript 类型
    App.tsx              Demo 工作台页面
  package.json
docs/
  requirements_summary.md
  demo_status_and_next_steps.md
  demo_gap_analysis.md
```

## 后端启动

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r backend\requirements.txt
.\scripts\start_backend.ps1
```

或者不激活环境，直接显式使用根目录 Python 3.12：

```bash
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

说明：

- 项目现在统一使用仓库根目录 `.venv`
- 不再推荐使用 `backend/.venv`
- `backend/.venv` 如果仍存在，可能是旧的 Python 3.8 环境

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

成功时返回：

```json
{"status":"ok"}
```

## 前端启动

```bash
cd frontend
npm install
npm run dev
```

访问：

```text
http://127.0.0.1:5173
```

## 运行测试

```bash
cd backend
pytest
```

前端构建检查：

```bash
cd frontend
npm run build
```

## REST API

- `POST /api/tasks`：创建竞品分析任务
- `GET /api/tasks`：查询任务列表
- `GET /api/tasks/{task_id}`：查询任务详情
- `GET /api/tasks/{task_id}/runs`：查询该任务的历史运行列表
- `GET /api/tasks/{task_id}/runs/latest`：查询最新一次运行
- `GET /api/tasks/{task_id}/runs/{run_id}`：查询指定运行
- `GET /api/tasks/{task_id}/runs/{run_id}/evidence`：查询指定运行的 Evidence
- `GET /api/tasks/{task_id}/runs/{run_id}/report`：查询指定运行的 Report
- `GET /api/tasks/{task_id}/runs/{run_id}/qa`：查询指定运行的 QA 结果
- `GET /api/tasks/{task_id}/runs/{run_id}/traces`：查询指定运行的 Trace
- `POST /api/tasks/{task_id}/run?demo_mode=normal`：启动正常 Mock 工作流
- `POST /api/tasks/{task_id}/run?demo_mode=qa_missing_evidence`：演示 QA 打回 CollectorAgent
- `POST /api/tasks/{task_id}/run?demo_mode=qa_invalid_extraction`：演示 QA 打回 AnalystAgent
- `POST /api/tasks/{task_id}/run?demo_mode=qa_bad_report`：演示 QA 打回 ReportWriterAgent
- `POST /api/tasks/{task_id}/run?collector_mode=mock`：使用稳定 Mock Evidence
- `POST /api/tasks/{task_id}/run?collector_mode=web`：使用最小 Web Collector，失败时 fallback 到 Mock
- `GET /api/collector/status`：查询 Web Collector 配置状态
- `GET /api/tasks/{task_id}/dag`：查询 DAG 节点和边
- `GET /api/tasks/{task_id}/traces`：查询 Trace 记录
- `GET /api/tasks/{task_id}/evidence`：查询 Evidence 列表
- `GET /api/tasks/{task_id}/qa`：查询 QA 结果
- `GET /api/tasks/{task_id}/report`：查询任务报告
- `GET /api/reports/{report_id}`：按报告 ID 查询报告

## 前端功能

- 创建分析任务，并提供示例任务
- 选择正常流程或三类 QA 失败 Demo
- 展示当前任务、任务状态、DAG、竞品知识、报告、证据、QA 和 Trace
- 展示 Run History，支持切换历史运行并回放对应 Evidence、Report、QA 和 Trace
- 点击报告 Claim 后查看对应 Evidence
- Trace Viewer 支持按 Agent 过滤

## Phase 10: TaskRun 与 run_id 隔离

`Task` 表示一个竞品分析任务，例如“企业协作工具竞品分析”。`TaskRun` 表示该任务的一次具体 workflow 执行。

每次调用：

```text
POST /api/tasks/{task_id}/run
```

都会创建一个新的 `run_id`。新生成的 Evidence、Report、QA Result、TraceRecord 和 WorkflowEngine summary 都会绑定当前 `run_id`。

旧接口仍然保留：

```text
GET /api/tasks/{task_id}/evidence
GET /api/tasks/{task_id}/report
GET /api/tasks/{task_id}/qa
GET /api/tasks/{task_id}/traces
```

它们默认返回 latest run 的结果。需要回放历史版本时，请使用 `/runs/{run_id}/...` 系列接口。

Phase 9.1 的 cleanup 是过渡策略。Phase 10 之后，产品化主线采用 `run_id` 隔离，不删除旧运行数据，支持历史报告版本查看和 Trace 回放。

## Phase 11: Production Web Collection / PageFetcher

LangGraph 主线在 `EvidenceGate` 之后增加 `PageFetcher`：

```text
PlannerAgent -> CollectorAgent -> EvidenceGate -> PageFetcher -> AnalystAgent -> ReportWriterAgent -> QaAgent -> FinalReportAgent
```

PageFetcher 只处理 `high / medium` relevance 且非低质量来源的 Evidence。它会轻量获取公开 HTML 页面，抽取 `title/h1/h2/h3/p/li` 文本，保存截断后的 `content_excerpt`，不保存完整网页正文。

可配置环境变量：

```bash
PAGE_FETCH_PROVIDER=local
PAGE_FETCH_TIMEOUT=10
PAGE_FETCH_MAX_BYTES=500000
PAGE_CONTENT_MAX_CHARS=3000
PAGE_EXCERPT_MAX_CHARS=1000
PAGE_FETCH_MAX_PER_COMPETITOR=2
PAGE_FETCH_MAX_PER_RUN=10
PAGE_FETCH_RESPECT_ROBOTS=true
```

抓取失败、超时、非 HTML、403/404 或内容过大时，workflow 不会崩溃；Evidence 会保持 `content_mode=snippet`，继续使用 Tavily snippet，并在 Trace / Evidence Panel 中记录 `page_fetch_error`。

## ReportWriter 模式

系统默认使用 Mock ReportWriter，不需要任何 API Key：

```text
POST /api/tasks/{task_id}/run?writer_mode=mock
```

也可以选择最小真实 LLM ReportWriter：

```text
POST /api/tasks/{task_id}/run?writer_mode=llm
```

前端“运行 Demo 工作流”旁边提供：

- `Mock ReportWriter`
- `LLM ReportWriter`

### 配置 LLM

复制 `.env.example` 并按本地环境设置：

```bash
LLM_PROVIDER=openai_compatible
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
```

当前实现使用 OpenAI-compatible Chat Completions 协议，后续可以接 OpenAI、DeepSeek、豆包或其他兼容 API。

不要把真实 API Key 提交到 GitHub。

### Fallback 行为

- 没有 `LLM_API_KEY` 时，即使选择 `writer_mode=llm`，系统也会自动 fallback 到 Mock ReportWriter。
- LLM 调用异常时，workflow 不会崩溃，会 fallback 到 Mock ReportWriter。
- LLM 返回非法 JSON 时，会记录失败 Trace，然后 fallback 到 Mock ReportWriter。
- LLM 返回的 Claim 缺少 `evidence_ids` 时，不会静默修复，会进入 QA failed，并路由回 `ReportWriterAgent`。

## Collector 模式

系统默认使用 Mock Collector，适合现场稳定演示：

```text
POST /api/tasks/{task_id}/run?collector_mode=mock
```

也可以选择最小 Web Collector：

```text
POST /api/tasks/{task_id}/run?collector_mode=web
```

前端“运行 Demo 工作流”旁边提供：

- `Mock Collector`
- `Web Collector`

Web Collector 当前只调用配置好的公开搜索 API，把搜索结果转成结构化 `Evidence`，不做复杂爬虫、不绕过 robots.txt、不采集隐私数据、不复制长篇网页内容。

### 配置 Web Collector

复制 `.env.example` 并按本地环境设置：

```bash
SEARCH_PROVIDER=tavily
SEARCH_API_KEY=
SEARCH_BASE_URL=https://api.tavily.com
SEARCH_TIMEOUT=15
SEARCH_MAX_RESULTS=8
```

当前优先支持 Tavily Search API。请求方式为 `POST {SEARCH_BASE_URL}/search`，只请求搜索结果的 `title`、`url`、`content`、`score` 等摘要字段，不请求 `raw_content`。

### Fallback 行为

- 没有 `SEARCH_API_KEY` 或 `SEARCH_BASE_URL` 时，即使选择 `collector_mode=web`，系统也会自动 fallback 到 Mock Collector。
- Web search 超时或返回异常时，workflow 不会崩溃，会 fallback 到 Mock Evidence。
- Trace Viewer 展开 `CollectorAgent` 后可以查看 `collector_mode_requested`、`collector_mode_used`、`web_search_attempted`、`web_search_success`、`query_count`、`evidence_count`、`fallback_used` 和 `fallback_reason`。

### Search API

- `GET /api/search/status`：查看搜索配置状态，不返回 API Key。
- `POST /api/search/test`：测试搜索连接。

测试请求示例：

```bash
curl -X POST http://127.0.0.1:8000/api/search/test ^
  -H "Content-Type: application/json" ^
  -d "{\"query\":\"飞书 B2B SaaS 功能 定价 官网\"}"
```

## 后续接入真实 Agent 的位置

推荐从 `backend/app/agents/runner.py` 开始接入 LangGraph。

迁移建议：

1. 保留 `backend/app/schemas/models.py` 和 `backend/app/schemas/agent_io.py` 作为 Agent 间通信契约。
2. 将每个 Mock Agent 替换成 LangGraph 节点或子图。
3. 保持 Agent `run()` 的输入输出 Schema 不变，减少 API 和前端改动。
4. 替换 `CollectorAgent` 为合规 Web Collector，仍然输出结构化 `Evidence`。
5. 在 `EvidenceService` 后面接入向量数据库或检索服务。
6. 保留 `QaAgent` 的确定性硬校验：Schema 校验、证据覆盖率、引用完整性和人工复核升级不应完全依赖自由文本 LLM 判断。

## Windows PowerShell 中文乱码怎么办

如果在 Windows PowerShell 中执行 `Get-Content README.md` 看到中文乱码，可以使用以下方式之一：

- 直接在 VS Code、GitHub 页面或支持 UTF-8 的编辑器中查看 Markdown。
- 在 PowerShell 中先执行：

```powershell
chcp 65001
```

然后重新打开或读取文件。

本仓库已增加 `.gitattributes`，将 Markdown、Python、TypeScript、JSON 文件按文本文件和 LF 换行管理，避免跨平台换行和编码显示风险。

## 合规说明

当前 Demo 默认使用 `example.com` 和 `mock://` 的模拟公开来源。启用 Web Collector 时，只应采集合规公开搜索结果，不采集个人隐私数据，不复制长篇受版权保护内容。

生产环境接入真实采集时，需要遵守：

- 目标网站 robots.txt 和服务条款
- 数据来源授权或公开声明
- 隐私和敏感信息保护要求
- 访谈、问卷等内容的脱敏要求
- 最终报告中避免复制长篇受版权保护文本

## Phase 8: LangGraph Workflow Engine

当前系统支持两种 workflow engine：

```text
POST /api/tasks/{task_id}/run?workflow_engine=custom
POST /api/tasks/{task_id}/run?workflow_engine=langgraph
```

也可以通过环境变量设置默认值：

```bash
WORKFLOW_ENGINE=custom
WORKFLOW_ENGINE=langgraph
```

优先级为：

```text
API query 参数 > WORKFLOW_ENGINE 环境变量 > 默认 custom
```

### 维护边界

- `Custom Runner` 是 legacy stable fallback，只维护当前稳定主链路。
- `LangGraph Runner` 是后续扩展主线，用 `StateGraph` 显式表达 DAG、QA conditional routing 和 auto_rework 循环。
- 后续新增节点，例如 PageFetcher、Chunker、Indexer、Retriever、SWOTAgent、QuestionnaireAgent，只接入 LangGraph Runner。
- Agent 业务逻辑只有一份，仍然在各 Agent 的 `run()` 方法中。
- LangGraph node 只负责从 `WorkflowState` 取数据、构造现有 Input Schema、调用 Agent.run()、把 Output 写回 State，不复制 Agent 业务逻辑。

### LangGraph 路由

当前 LangGraph 主链路：

```text
planner -> collector -> analyst -> report_writer -> qa -> final_report
```

QA 后使用 conditional routing：

```text
qa passed -> final_report
route_to = CollectorAgent -> collector
route_to = AnalystAgent -> analyst
route_to = ReportWriterAgent -> report_writer
manual_review / unknown route -> final_report
```

每次运行会额外生成 `WorkflowEngine` Trace，记录 workflow engine、node_sequence、conditional_routes_taken、rework_count、final_status 和 elapsed_time_ms。
