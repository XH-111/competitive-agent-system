# Structured Fact QA Contract

## 1. 目标

LangGraph 主流程使用 `DimensionResult` 作为结构化事实和证据溯源的唯一生产单元。报告层不再生成第二套 Claim，避免“先写报告、再由同一个模型生成 Claim、最后检查 Claim”的循环自证问题。

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

## 2. 结构化事实

每条 `DimensionResult` 必须包含：

- `dimension_result_id`
- `dimension_id`
- `competitor`
- `summary`
- `findings`
- `evidence_ids`
- `confidence`
- `insufficient_evidence`
- `metadata`

有证据的事实必须绑定当前 run 中存在的 `evidence_ids`。证据必须与事实属于同一竞品，且优先属于同一分析维度。

证据不足时仍需输出对应竞品和维度的 `DimensionResult`，但必须：

- `insufficient_evidence=true`
- `evidence_ids=[]`
- `confidence <= 0.4`
- 使用保守说明，不生成具体强结论

## 3. ReportWriter 合约

ReportWriterAgent 输入为 AnalystAgent 已校验的 `dimension_results`，输出：

- `markdown_report`
- `json_report`

ReportWriterAgent 不生成 Claim，不修改 `dimension_results`，也不重新决定证据绑定。生成的 `Report.dimension_results` 必须与 AnalystAgent 输出完全一致。

`Report.claims` 暂时保留为空字段，仅用于历史数据兼容，不参与新 QA。

## 4. QA 分层检查

### Planner 层

检查固定基础维度是否完整，以及每个竞品、每个维度是否有可执行搜索计划。失败时打回 `PlannerAgent`。

### Evidence 层

检查每个竞品是否至少有 high/medium relevance 且非 low_quality 的 Evidence。失败时打回 `CollectorAgent`。

单个维度暂时没有 Evidence 不直接形成死循环；AnalystAgent 可以输出合法的证据不足事实，QA 将其记录为优化建议。

### Structured Fact 层

检查：

- 每个竞品和 selected dimension 是否都有 `DimensionResult`
- 支持性事实是否绑定 Evidence
- Evidence 是否存在于当前 run
- Evidence 与事实竞品是否一致
- Evidence 采集维度与事实维度是否一致
- 证据不足状态是否满足保守约束

失败时打回 `AnalystAgent`。

### Report 层

检查：

- Markdown 格式是否完整
- 是否覆盖全部竞品
- `Report.dimension_results` 是否原样保留
- 支持性事实是否在报告中保留 `dimension_result_id` 或 `evidence_ids`

失败时打回 `ReportWriterAgent`。

## 5. 路由与终止

QA 最大返工次数为 3。超过限制后进入 `manual_review`。LangGraph 另设递归上限，防止异常状态形成无限循环。

前端展示中文标签，但以下字段保持英文：

- `dimension_result_id`
- `dimension_id`
- `evidence_ids`
- `confidence`
- `insufficient_evidence`
- `run_id`
- `task_id`
