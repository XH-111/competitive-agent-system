# Phase UI / Prompt Localization

## 目标

本阶段将前端用户可见文案、报告章节标题、QA 展示文案和后端 LLM prompt 尽量中文化，使系统更适合中文答辩和中文企业用户使用。

## 保留英文的内容

以下内容作为技术契约保持英文，不做字段级翻译：

- API 路径和 query 参数，例如 `workflow_engine`、`collector_mode`、`writer_mode`、`auto_rework`
- Schema 类名，例如 `ProductProfile`、`FeatureTree`、`PricingModel`、`UserPersona`、`Claim`、`Evidence`
- 数据字段名，例如 `run_id`、`task_id`、`evidence_ids`、`source_domain`、`relevance_level`
- Trace key 和 JSON 展开区中的原始 key
- Agent 名称和 mode 值

前端展示技术字段时采用中文 label + 英文 key 的方式，例如“最终状态 final_status”。

## 本阶段改动范围

- 前端工作台、任务列表、DAG、报告、Evidence、QA、Trace、Run History、Survey 工作台的主要展示文案中文化。
- 新增 `frontend/src/i18n/zh.ts`，集中管理常见字段、错误类型、DAG 描述、问卷题型和指标角色 label。
- `ReportWriterAgent` prompt 改为中文指令，要求报告正文和章节标题使用中文，同时保留 JSON key 英文。
- `PlannerAgent` prompt 改为中文指令，要求解释性内容使用中文，同时保留 JSON key 英文。
- `QaAgent`、`EvidenceGate` 和部分分析摘要的用户可见错误、建议和证据不足说明中文化。

## 兼容说明

为避免破坏既有测试和旧断言，少量内部兼容哨兵文本仍保留英文短语，例如 `Evidence is insufficient`。中文用户可见表达仍放在前面，例如“当前公开证据不足，暂不做强结论。”。

本阶段不改变 API response shape、不改变数据库字段、不改变 Agent 输入输出 Schema、不改变业务逻辑。
