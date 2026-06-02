import json
from typing import Any

from app.services.survey_llm_client import SurveyLLMClient

SURVEY_ANALYSIS_SYSTEM_PROMPT = """你是竞品分析系统中的 SurveyAnalysisAgent。
你的任务不是只做问卷统计摘要，而是根据问卷结构、pain_points、question-to-pain mapping、用户上传反馈统计结果、PlannerAgent 上下文和原竞品分析报告，判断报告中的产品痛点是否被用户侧反馈验证。
问卷数据不是绝对事实，只能代表当前样本。你必须说明样本量、样本偏差、置信度和不可过度推断的地方。
输出必须是合法 JSON。"""

SURVEY_ANALYSIS_SYSTEM_PROMPT += (
    "\n输出约束：只返回合法 JSON object，不要输出 JSON 外的解释文字，不要使用 Markdown 代码块包裹 JSON。"
    "用户可见文本可以使用中文，但 JSON key、Schema 字段名和枚举值必须保持英文。"
)


class SurveyAnalysisAgent:
    def __init__(self, llm_client: SurveyLLMClient | None = None):
        self.llm_client = llm_client or SurveyLLMClient()

    def analyze(
        self,
        survey_json: dict[str, Any],
        survey_stats_json: dict[str, Any],
        report_context: dict[str, Any],
    ) -> dict[str, Any]:
        return self.llm_client.generate_json(
            SURVEY_ANALYSIS_SYSTEM_PROMPT,
            build_survey_analysis_prompt(survey_json, survey_stats_json, report_context),
        ).data


def build_survey_analysis_prompt(
    survey_json: dict[str, Any],
    survey_stats_json: dict[str, Any],
    report_context: dict[str, Any],
) -> str:
    return f"""【原始问卷结构】
{json.dumps(survey_json, ensure_ascii=False)}
【pain_points】
{json.dumps(survey_json.get("pain_points", []), ensure_ascii=False)}
【question_pain_mapping】
{json.dumps(survey_json.get("question_pain_mapping", {}), ensure_ascii=False)}
【问卷反馈统计结果】
{json.dumps(survey_stats_json, ensure_ascii=False)}
【样本量】
{survey_stats_json.get("sample_size", 0)}
【PlannerAgent 快照】
{json.dumps(survey_json.get("planner_snapshot", {}), ensure_ascii=False)}
【原竞品分析报告】
{report_context.get("report_markdown", "")}
【原报告关键 Claims】
{json.dumps(report_context.get("claims_json", []), ensure_ascii=False)}

分析要求：
1. 判断每个 pain point 是否被当前样本支持、削弱、反驳或仍不确定。
2. 对每道关键问题给出统计解释。
3. 输出 pain_point_validation、pain_point_ranking、claim_validation_matrix。
4. 判断哪些原报告结论需要修正或谨慎表达。
5. 输出用户痛点、付费意愿、替代意愿、满意度等维度洞察。
6. 明确指出样本局限，不要过度推断。
7. 给出可以进入最终报告的 SurveyEvidence 摘要。
8. 输出必须是合法 JSON，不要输出 Markdown。

输出格式必须严格为：
{{
  "executive_summary": "string",
  "sample_summary": {{"sample_size": 0, "valid_count": 0, "limitations": ["string"]}},
  "key_findings": [
    {{"finding": "string", "supporting_questions": ["Q1"], "confidence": 0.0, "explanation": "string"}}
  ],
  "question_level_analysis": [
    {{"question_id": "Q1", "field_name": "string", "summary": "string", "notable_stats": ["string"]}}
  ],
  "claim_updates": [
    {{"claim_id": "string", "original_claim": "string", "survey_result": "string", "impact": "support | weaken | refine | no_clear_signal", "recommended_revision": "string"}}
  ],
  "user_pain_points": ["string"],
  "willingness_to_pay": "string",
  "switching_risk": "string",
  "survey_evidence": {{
    "snippet": "string",
    "confidence": 0.0,
    "metadata": {{"sample_size": 0, "source_type": "survey", "analysis_mode": "pain_point_validation"}}
  }},
  "pain_point_validation": [
    {{"pain_id": "P1", "pain_point": "string", "validation_result": "strongly_supported | partially_supported | not_supported | contradicted | inconclusive", "evidence_summary": "string", "frequency_score": 0.0, "severity_score": 0.0, "switching_risk_score": 0.0, "willingness_to_pay_score": 0.0, "priority_score": 0.0, "affected_segments": ["string"], "supporting_questions": ["Q1"], "recommended_report_update": "string", "confidence": 0.0}}
  ],
  "pain_point_ranking": [],
  "claim_validation_matrix": [],
  "segment_insights": [],
  "competitor_switching_analysis": {{}},
  "pricing_and_wtp_analysis": {{}},
  "recommended_report_revisions": [],
  "limitations": ["string"],
  "next_research_questions": ["string"],
  "dashboard_summary": "string"
}}"""
