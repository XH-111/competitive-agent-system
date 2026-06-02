export const fieldLabels: Record<string, string> = {
  run_id: "运行 ID",
  task_id: "任务 ID",
  evidence_id: "证据 ID",
  claim_id: "结论 ID",
  workflow_engine: "工作流引擎",
  workflow_engine_used: "实际工作流引擎",
  workflow_engine_requested: "请求的工作流引擎",
  collector_mode: "采集模式",
  analyst_mode: "分析模式",
  writer_mode: "报告撰写模式",
  content_mode: "内容模式",
  auto_rework: "自动返工",
  evidence_ids: "绑定证据",
  source_domain: "来源域名",
  source_quality: "来源质量",
  relevance_score: "相关性分数",
  relevance_level: "相关性等级",
  relevance_reason: "相关性说明",
  confidence: "置信度",
  final_status: "最终状态",
  elapsed_time_ms: "耗时",
  retry_count: "重试次数",
  model_name: "模型名称",
  token_usage: "Token 用量",
  error_message: "错误信息",
  input_summary: "输入摘要",
  output_summary: "输出摘要",
  node_sequence: "节点执行顺序",
  conditional_routes_taken: "条件路由记录",
  selected_dimensions: "已选分析维度",
  dimension_coverage_summary: "维度覆盖情况",
  fallback_reason: "兜底原因",
  fallback_used: "已启用兜底",
  llm_call_success: "大模型调用是否成功",
  hard_errors: "严重问题",
  soft_suggestions: "优化建议",
  rework_instructions: "返工指令",
  rework_history: "返工历史",
};

export const errorTypeLabels: Record<string, string> = {
  missing_evidence: "缺少证据",
  bad_report_format: "报告格式问题",
  invalid_extraction: "结构化抽取异常",
  missing_relevant_evidence: "缺少相关证据",
  claim_evidence_support_mismatch: "结论与证据支撑不匹配",
  swot_missing_support: "SWOT 缺少证据支撑",
  swot_competitor_mismatch: "SWOT 竞品证据不匹配",
  swot_over_inference: "SWOT 过度推断",
  swot_sparse_competitor_coverage: "SWOT 竞品覆盖不足",
  swot_dimension_gap: "SWOT 未覆盖规划维度",
};

export const dagDescriptions: Record<string, string> = {
  PlannerAgent: "规划任务范围与分析维度",
  CollectorAgent: "采集公开来源证据",
  EvidenceGate: "检查证据相关性与覆盖情况",
  PageFetcher: "抓取网页正文摘要",
  AnalystAgent: "抽取结构化竞品知识",
  ReportWriterAgent: "撰写带证据引用的分析报告",
  QaAgent: "校验输出质量并决定是否返工",
  FinalReport: "生成最终报告与状态摘要",
  FinalReportAgent: "生成最终报告与状态摘要",
};

export const questionTypeLabels: Record<string, string> = {
  single_choice: "单选 single_choice",
  multiple_choice: "多选 multiple_choice",
  rating: "评分 rating",
  text: "文本 text",
  number: "数字 number",
};

export const metricRoleLabels: Record<string, string> = {
  background: "背景信息 background",
  pain_existence: "痛点是否存在 pain_existence",
  pain_severity: "痛点严重程度 pain_severity",
  pain_frequency: "痛点频率 pain_frequency",
  pain_priority: "痛点优先级 pain_priority",
  switching_risk: "切换风险 switching_risk",
  competitor_preference: "竞品偏好 competitor_preference",
  solution_preference: "方案偏好 solution_preference",
  willingness_to_pay: "付费意愿 willingness_to_pay",
  open_feedback: "开放反馈 open_feedback",
};

export function labelFor(key: string): string {
  return fieldLabels[key] ? `${fieldLabels[key]} ${key}` : key;
}

export function errorTypeLabel(value?: string): string {
  if (!value) return "-";
  return errorTypeLabels[value] ? `${errorTypeLabels[value]} ${value}` : value;
}
