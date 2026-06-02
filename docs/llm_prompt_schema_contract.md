# LLM Prompt Schema Contract

本项目允许将用户可见内容、报告正文、章节标题、解释性说明中文化，但 LLM 结构化输出必须严格遵守后端 Schema 合约。

## 通用规则

- JSON key、Pydantic Schema 字段名、API 参数名、数据库字段名必须保持英文。
- Agent 类名、mode 参数、Trace key 必须保持英文。
- 用户可见正文可以使用中文，尤其是报告正文、QA 说明、问卷题目和分析解释。
- LLM 只能返回合法 JSON object。
- 不允许在 JSON 外输出自然语言解释。
- 不允许使用 Markdown 代码块包裹 JSON。
- Schema 校验失败必须 fallback 或进入 QA 失败路径，不能把非法 LLM 输出作为最终报告展示。

## ReportWriterOutput 合约

ReportWriterAgent 的 LLM 输出顶层必须至少包含：

```json
{
  "markdown_report": "# 竞品分析报告\n\n## 执行摘要\n...",
  "json_report": {
    "summary": "中文摘要",
    "sections": [
      {
        "title": "产品定位",
        "content": "中文内容",
        "competitors": ["竞品A"],
        "evidence_ids": ["ev_xxx"]
      }
    ]
  },
  "claims": [
    {
      "claim_id": "claim_001",
      "competitor": "竞品A",
      "category": "feature",
      "text": "根据公开来源，竞品A具备某项能力。",
      "evidence_ids": ["ev_xxx"],
      "confidence": 0.8
    }
  ]
}
```

`markdown_report` 是中文 Markdown 报告正文。

`json_report` 是结构化报告摘要对象，可以包含中文 section title 和中文 content，但 key 必须保持英文。

`claims` 必须是顶层数组，不允许放进 `json_report` 内部。

如果证据不足，允许返回 `claims: []`，但报告正文必须写明“当前公开证据不足，暂不做强结论。”。

如果 Evidence 足够，必须生成 `claims`，且每条 Claim 必须绑定非空 `evidence_ids`。

## Claim 合约

每条 Claim 必须包含：

- `claim_id`
- `competitor`
- `category`
- `text`
- `evidence_ids`
- `confidence`

`category` 使用当前项目允许的英文枚举值，例如 `positioning`、`feature`、`pricing`、`persona`、`risk`、`recommendation`。

`evidence_ids` 必须非空。它表示引用绑定，不表示事实自动可信。

## 失败处理

- 缺少顶层 `markdown_report`、`json_report` 或 `claims`：schema validation failed，并 fallback 到 mock ReportWriter。
- `claims` 被放在 `json_report` 内：schema validation failed，并 fallback 到 mock ReportWriter。
- 只返回 Markdown 或返回中文 JSON key：schema validation failed，并 fallback 到 mock ReportWriter。
- JSON 外包含自然语言解释：解析失败，并 fallback 到 mock ReportWriter。
- Claim 缺少 `evidence_ids`：作为 Claim Schema 失败进入 QA / ReportWriter 返工路径。
