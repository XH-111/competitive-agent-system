import json
from typing import Any

from app.services.survey_llm_client import SurveyLLMClient

QUESTIONNAIRE_SYSTEM_PROMPT = """你是竞品分析系统中的 QuestionnaireAgent。
你的任务不是生成通用满意度问卷，而是基于 PlannerAgent 的调研规划、竞品分析报告、关键 claims，以及 PainPointResearchAgent 提取出的产品痛点，生成一份可投放给真实用户的痛点验证型问卷。
问卷目标：
1. 验证报告中的产品痛点是否真实存在；
2. 衡量每个痛点的出现频率和严重程度；
3. 判断痛点是否影响购买、续费、推荐或转向竞品；
4. 判断用户对解决方案的优先级和付费意愿；
5. 为最终竞品分析报告提供可引用的用户侧证据。
输出必须是合法 JSON。"""

QUESTIONNAIRE_REVISION_SYSTEM_PROMPT = """你是竞品分析系统中的 QuestionnaireAgent。
你需要根据用户的修改意见，对已有痛点验证问卷进行返工。请保留 pain_points、question_pain_mapping 和仍然有价值的问题；新增问题尽量绑定到已有 pain point。输出必须是合法 JSON。"""

QUESTIONNAIRE_TOPIC_SYSTEM_PROMPT = """你是竞品分析系统中的 QuestionnaireAgent。
你的任务是根据用户输入的任意研究话题生成一份可直接投放、可 CSV 分析的问卷。问卷可以独立于竞品分析任务运行，但输出仍必须结构化、可统计、避免隐私敏感信息。输出必须是合法 JSON。"""

STRICT_JSON_OUTPUT_CONTRACT = (
    "\n输出约束：只返回合法 JSON object，不要输出 JSON 外的解释文字，不要使用 Markdown 代码块包裹 JSON。"
    "用户可见文本可以使用中文，但 JSON key、Schema 字段名、field_name 和枚举值必须保持英文。"
)

QUESTIONNAIRE_SYSTEM_PROMPT += STRICT_JSON_OUTPUT_CONTRACT
QUESTIONNAIRE_REVISION_SYSTEM_PROMPT += STRICT_JSON_OUTPUT_CONTRACT
QUESTIONNAIRE_TOPIC_SYSTEM_PROMPT += STRICT_JSON_OUTPUT_CONTRACT


class QuestionnaireAgent:
    def __init__(self, llm_client: SurveyLLMClient | None = None):
        self.llm_client = llm_client or SurveyLLMClient()

    def generate_survey(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.llm_client.generate_json(
            QUESTIONNAIRE_SYSTEM_PROMPT,
            build_questionnaire_prompt(context),
        ).data

    def generate_from_topic(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.llm_client.generate_json(
            QUESTIONNAIRE_TOPIC_SYSTEM_PROMPT,
            build_topic_questionnaire_prompt(context),
        ).data

    def revise_survey(
        self,
        survey_json: dict[str, Any],
        revision_request: str,
        report_context: dict[str, Any],
    ) -> dict[str, Any]:
        return self.llm_client.generate_json(
            QUESTIONNAIRE_REVISION_SYSTEM_PROMPT,
            build_revision_prompt(survey_json, revision_request, report_context),
        ).data


def build_questionnaire_prompt(context: dict[str, Any]) -> str:
    return f"""请严格基于以下输入生成问卷：
【产品名称】
{context.get("product_name", "")}
【竞品列表】
{json.dumps(context.get("competitors", []), ensure_ascii=False)}
【行业】
{context.get("industry", "")}
【地区】
{context.get("region", "")}
【竞品分析报告】
{context.get("report_markdown", "")}
【关键结论 Claims】
{json.dumps(context.get("claims_json", []), ensure_ascii=False)}
【PlannerAgent 上下文】
{json.dumps(context.get("planner_context", {}), ensure_ascii=False)}
【产品痛点 pain_points】
{json.dumps(context.get("pain_points", []), ensure_ascii=False)}
【低置信度或需要用户验证的问题】
{json.dumps(context.get("uncertain_findings", []), ensure_ascii=False)}
【用户额外要求】
{context.get("user_requirements", "")}

生成要求：
1. 问卷必须优先围绕 pain_points 生成，不要泛泛覆盖所有竞品分析维度。
2. 每个 pain point 至少被 1-3 道题覆盖。
3. 每道非背景题必须包含 maps_to_pain_id。
4. 每道题必须包含 research_purpose、analysis_method、metric_role。
5. 问卷题目数量控制在 {context.get("question_count", 10)} 题以内。
6. 优先使用单选、多选、评分题，减少开放题。
7. 题目语言要适合普通用户理解，避免行业黑话。
8. 每道题都要给出 CSV 字段名，field_name 只能使用英文字母、数字和下划线，且以字母开头。
9. 不要询问身份证号、手机号、精确地址等隐私敏感信息。
10. 输出必须是合法 JSON，不要输出 Markdown。

输出格式必须严格为：
{{
  "survey_title": "string",
  "survey_description": "string",
  "target_respondents": "string",
	  "research_goal": "string",
	  "pain_points": [],
	  "questions": [
    {{
      "question_id": "Q1",
      "field_name": "string",
      "question_text": "string",
      "question_type": "single_choice | multiple_choice | rating | text | number",
      "options": ["string"],
      "required": true,
	      "analysis_goal": "string",
	      "related_claim_id": "string or null",
	      "maps_to_pain_id": "P1 or null",
	      "research_purpose": "string",
	      "analysis_method": "string",
	      "metric_role": "background | pain_existence | pain_frequency | pain_severity | pain_priority | switching_risk | competitor_preference | solution_preference | willingness_to_pay | open_feedback",
	      "theme": "string",
	      "hypothesis": "string or null",
	      "reason": "为什么这道题对竞品分析有价值"
	    }}
	  ],
	  "expected_analysis_dimensions": ["string"],
	  "csv_columns": ["field_name"],
	  "question_pain_mapping": {{"Q1": "P1"}},
	  "metadata": {{}}
	}}"""


def build_topic_questionnaire_prompt(context: dict[str, Any]) -> str:
    return f"""请根据以下研究话题生成问卷：
【研究话题】
{context.get("topic", "")}
【目标受访者】
{context.get("target_respondents", "")}
	【研究目标】
	{context.get("research_goal", "")}
【用户额外要求】
{context.get("requirements", "")}

生成要求：
1. 问卷必须围绕研究话题，不要套用固定行业或手机样例。
2. 每道题都必须可统计，并说明 analysis_goal。
3. 问卷题目数量控制在 {context.get("question_count", 10)} 题以内。
4. 优先使用单选、多选、评分题，保留少量开放题。
5. 题目语言要适合普通用户理解。
6. 选项必须互斥、完整、可统计。
7. 每道题都要给出 CSV 字段名，field_name 只能使用英文字母、数字和下划线，且以字母开头。
8. 不要询问身份证号、手机号、精确地址等个人敏感信息。
9. 输出必须是合法 JSON，不要输出 Markdown。

输出格式必须严格为：
{{
  "survey_title": "string",
  "survey_description": "string",
  "target_respondents": "string",
	  "research_goal": "string",
	  "pain_points": [
	    {{"pain_id": "P1", "pain_point": "string", "confidence": 0.5, "metadata": {{"source": "topic_generation"}}}}
	  ],
  "questions": [
    {{
      "question_id": "Q1",
      "field_name": "string",
      "question_text": "string",
      "question_type": "single_choice | multiple_choice | rating | text | number",
      "options": ["string"],
      "required": true,
	      "analysis_goal": "string",
	      "related_claim_id": null,
	      "maps_to_pain_id": "P1 or null",
	      "research_purpose": "string",
	      "analysis_method": "string",
	      "metric_role": "background | pain_existence | pain_frequency | pain_severity | pain_priority | switching_risk | competitor_preference | solution_preference | willingness_to_pay | open_feedback",
	      "reason": "为什么这道题对研究话题有价值",
      "theme": "string",
      "hypothesis": "string or null"
    }}
  ],
	  "expected_analysis_dimensions": ["string"],
	  "csv_columns": ["field_name"],
	  "question_pain_mapping": {{"Q1": "P1"}},
	  "metadata": {{"source": "topic_generation"}}
	}}"""


def build_revision_prompt(
    survey_json: dict[str, Any],
    revision_request: str,
    report_context: dict[str, Any],
) -> str:
    return f"""【原始问卷 JSON】
{json.dumps(survey_json, ensure_ascii=False)}
【用户修改要求】
{revision_request}
【竞品分析上下文】
{json.dumps(report_context, ensure_ascii=False)}

	修改要求：
1. 不要完全推翻原问卷，除非用户明确要求重做。
2. 保留与竞品分析强相关的问题。
3. 删除重复、模糊、无法统计的问题。
4. 如果用户要求减少题量，请优先保留最能验证关键结论的问题。
5. 如果用户要求增加某个方向，请新增题目并说明分析目的。
6. 每道题必须仍然包含 field_name、question_type、options、analysis_goal。
7. 保留原问卷 pain_points；已有题目的 maps_to_pain_id 默认保留，新增问题尽量绑定已有 pain point。
8. 输出必须是合法 JSON，不要输出 Markdown。

输出格式必须严格为：
{{
  "revision_summary": "本次修改做了什么",
  "survey_title": "string",
  "survey_description": "string",
  "target_respondents": "string",
	  "research_goal": "string",
	  "pain_points": [],
	  "questions": [
    {{
      "question_id": "Q1",
      "field_name": "string",
      "question_text": "string",
      "question_type": "single_choice | multiple_choice | rating | text | number",
      "options": ["string"],
      "required": true,
	      "analysis_goal": "string",
	      "related_claim_id": "string or null",
	      "maps_to_pain_id": "P1 or null",
	      "research_purpose": "string",
	      "analysis_method": "string",
	      "metric_role": "background | pain_existence | pain_frequency | pain_severity | pain_priority | switching_risk | competitor_preference | solution_preference | willingness_to_pay | open_feedback",
	      "reason": "string"
    }}
  ],
  "removed_questions": [
    {{"question_id": "string", "reason": "为什么删除"}}
  ],
	  "added_questions": [
	    {{"question_id": "string", "reason": "为什么新增"}}
	  ],
	  "question_pain_mapping": {{"Q1": "P1"}}
	}}"""
