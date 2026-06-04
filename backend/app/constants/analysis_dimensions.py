from app.schemas.models import AnalysisDimension, AnalysisDimensionPlan, Task


FIXED_COMPETITIVE_DIMENSIONS = [
    "pricing",
    "feature",
    "persona",
    "strength",
    "weakness",
    "opportunity",
    "threat",
]

FIXED_DIMENSION_LABELS = {
    "pricing": "价格",
    "feature": "功能",
    "persona": "用户画像",
    "strength": "优势",
    "weakness": "劣势",
    "opportunity": "机会",
    "threat": "威胁",
}

FIXED_DIMENSION_KEYWORDS = {
    "pricing": ["价格", "定价", "套餐", "版本", "pricing", "price", "plan", "subscription"],
    "feature": ["功能", "参数", "能力", "特性", "feature", "capability", "documentation"],
    "persona": ["用户画像", "目标用户", "适合人群", "使用场景", "customers", "users", "use cases"],
    "strength": ["优势", "优点", "亮点", "差异化", "strength", "advantage", "differentiator"],
    "weakness": ["劣势", "缺点", "问题", "投诉", "负面", "weakness", "complaint", "pain point"],
    "opportunity": ["机会", "增长", "趋势", "市场", "opportunity", "growth", "trend", "market"],
    "threat": ["威胁", "风险", "替代", "竞争", "threat", "risk", "alternative", "competition"],
}


def fixed_dimension_ids() -> list[str]:
    return list(FIXED_COMPETITIVE_DIMENSIONS)


def fixed_dimension_plans() -> list[AnalysisDimension]:
    return [
        AnalysisDimension(
            dimension_id=dimension_id,
            label=FIXED_DIMENSION_LABELS[dimension_id],
            description=f"围绕{FIXED_DIMENSION_LABELS[dimension_id]}维度采集、抽取和报告。",
            keywords=FIXED_DIMENSION_KEYWORDS[dimension_id],
            required=True,
            priority=index,
            metadata={"source": "fixed_competitive_dimension"},
        )
        for index, dimension_id in enumerate(FIXED_COMPETITIVE_DIMENSIONS, start=1)
    ]


def fixed_query_hints_for_competitor(competitor: str, industry: str) -> list[str]:
    industry_part = industry.strip() if industry and industry.strip() else "竞品"
    return [
        f"{competitor} 价格 定价 套餐 官方 {industry_part}",
        f"{competitor} 功能 参数 能力 官方 文档 {industry_part}",
        f"{competitor} 用户画像 目标用户 使用场景 客户 {industry_part}",
        f"{competitor} 优势 优点 亮点 差异化 评测 {industry_part}",
        f"{competitor} 劣势 缺点 问题 投诉 负面评价 {industry_part}",
        f"{competitor} 机会 增长 趋势 市场空间 {industry_part}",
        f"{competitor} 威胁 风险 替代品 竞争对手 {industry_part}",
    ]


def fixed_query_hints(task: Task) -> dict[str, list[str]]:
    return {
        competitor: fixed_query_hints_for_competitor(competitor, task.industry)
        for competitor in task.competitors
    }


def apply_fixed_dimensions_to_plan(plan: AnalysisDimensionPlan | None, task: Task) -> AnalysisDimensionPlan:
    existing = plan or AnalysisDimensionPlan()
    hints = dict(existing.query_hints or {})
    for competitor, queries in fixed_query_hints(task).items():
        hints[competitor] = _dedupe([*queries, *hints.get(competitor, [])])
    metadata = dict(existing.metadata or {})
    metadata.update({"dimension_policy": "fixed_competitive_dimensions"})
    return existing.model_copy(
        update={
            "selected_dimensions": fixed_dimension_ids(),
            "dimension_plans": fixed_dimension_plans(),
            "research_goals": [
                "按价格、功能、用户画像、优势、劣势、机会、威胁七个固定维度采集公开证据。",
                "结构化抽取和报告生成必须沿用同一组维度。",
            ],
            "query_hints": hints,
            "metadata": metadata,
        }
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        normalized = " ".join(str(item).split()).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return output
