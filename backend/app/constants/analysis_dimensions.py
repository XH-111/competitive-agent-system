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
    "pricing": "\u4ef7\u683c",
    "feature": "\u529f\u80fd",
    "persona": "\u7528\u6237\u753b\u50cf",
    "strength": "\u4f18\u52bf",
    "weakness": "\u52a3\u52bf",
    "opportunity": "\u673a\u4f1a",
    "threat": "\u5a01\u80c1",
}

FIXED_DIMENSION_KEYWORDS = {
    "pricing": ["\u4ef7\u683c", "\u5b9a\u4ef7", "\u5957\u9910", "\u7248\u672c", "pricing", "price", "plan", "subscription"],
    "feature": ["\u529f\u80fd", "\u53c2\u6570", "\u80fd\u529b", "\u7279\u6027", "feature", "capability", "documentation"],
    "persona": ["\u7528\u6237\u753b\u50cf", "\u76ee\u6807\u7528\u6237", "\u9002\u5408\u4eba\u7fa4", "\u4f7f\u7528\u573a\u666f", "customers", "users", "use cases"],
    "strength": ["\u4f18\u52bf", "\u4f18\u70b9", "\u4eae\u70b9", "\u5dee\u5f02\u5316", "strength", "advantage", "differentiator"],
    "weakness": ["\u52a3\u52bf", "\u7f3a\u70b9", "\u95ee\u9898", "\u6295\u8bc9", "\u8d1f\u9762", "weakness", "complaint", "pain point"],
    "opportunity": ["\u673a\u4f1a", "\u589e\u957f", "\u8d8b\u52bf", "\u5e02\u573a", "opportunity", "growth", "trend", "market"],
    "threat": ["\u5a01\u80c1", "\u98ce\u9669", "\u66ff\u4ee3", "\u7ade\u4e89", "threat", "risk", "alternative", "competition"],
}


def fixed_dimension_ids() -> list[str]:
    return list(FIXED_COMPETITIVE_DIMENSIONS)


def fixed_dimension_plans() -> list[AnalysisDimension]:
    return [
        AnalysisDimension(
            dimension_id=dimension_id,
            label=FIXED_DIMENSION_LABELS[dimension_id],
            description=f"\u56f4\u7ed5{FIXED_DIMENSION_LABELS[dimension_id]}\u7ef4\u5ea6\u91c7\u96c6\u3001\u62bd\u53d6\u548c\u62a5\u544a\u3002",
            keywords=FIXED_DIMENSION_KEYWORDS[dimension_id],
            required=True,
            priority=index,
            metadata={"source": "fixed_competitive_dimension"},
        )
        for index, dimension_id in enumerate(FIXED_COMPETITIVE_DIMENSIONS, start=1)
    ]


def fixed_query_hints_for_competitor(competitor: str, industry: str) -> list[str]:
    industry_part = industry.strip() if industry and industry.strip() else "\u7ade\u54c1"
    return [
        f"{competitor} \u4ef7\u683c \u5b9a\u4ef7 \u5957\u9910 \u5b98\u65b9 {industry_part}",
        f"{competitor} \u529f\u80fd \u53c2\u6570 \u80fd\u529b \u5b98\u65b9 \u6587\u6863 {industry_part}",
        f"{competitor} \u7528\u6237\u753b\u50cf \u76ee\u6807\u7528\u6237 \u4f7f\u7528\u573a\u666f \u5ba2\u6237 {industry_part}",
        f"{competitor} \u4f18\u52bf \u4f18\u70b9 \u4eae\u70b9 \u5dee\u5f02\u5316 \u8bc4\u6d4b {industry_part}",
        f"{competitor} \u52a3\u52bf \u7f3a\u70b9 \u95ee\u9898 \u6295\u8bc9 \u8d1f\u9762\u8bc4\u4ef7 {industry_part}",
        f"{competitor} \u673a\u4f1a \u589e\u957f \u8d8b\u52bf \u5e02\u573a\u7a7a\u95f4 {industry_part}",
        f"{competitor} \u5a01\u80c1 \u98ce\u9669 \u66ff\u4ee3\u54c1 \u7ade\u4e89\u5bf9\u624b {industry_part}",
    ]


def fixed_dimension_query_hints_for_competitor(competitor: str, industry: str) -> dict[str, str]:
    queries = fixed_query_hints_for_competitor(competitor, industry)
    return dict(zip(FIXED_COMPETITIVE_DIMENSIONS, queries, strict=True))


def fixed_query_hints(task: Task) -> dict[str, list[str]]:
    return {
        competitor: fixed_query_hints_for_competitor(competitor, task.industry)
        for competitor in task.competitors
    }


def apply_fixed_dimensions_to_plan(plan: AnalysisDimensionPlan | None, task: Task) -> AnalysisDimensionPlan:
    existing = plan or AnalysisDimensionPlan()
    hints = dict(existing.query_hints or {})
    dimension_query_hints = {
        competitor: fixed_dimension_query_hints_for_competitor(competitor, task.industry)
        for competitor in task.competitors
    }
    for competitor, queries in fixed_query_hints(task).items():
        hints[competitor] = _dedupe([*queries, *hints.get(competitor, [])])
    metadata = dict(existing.metadata or {})
    metadata.update(
        {
            "dimension_policy": "fixed_competitive_dimensions",
            "dimension_query_hints": dimension_query_hints,
        }
    )
    return existing.model_copy(
        update={
            "selected_dimensions": fixed_dimension_ids(),
            "dimension_plans": fixed_dimension_plans(),
            "research_goals": [
                "\u6309\u4ef7\u683c\u3001\u529f\u80fd\u3001\u7528\u6237\u753b\u50cf\u3001\u4f18\u52bf\u3001\u52a3\u52bf\u3001\u673a\u4f1a\u3001\u5a01\u80c1\u4e03\u4e2a\u56fa\u5b9a\u7ef4\u5ea6\u91c7\u96c6\u516c\u5f00\u8bc1\u636e\u3002",
                "\u7ed3\u6784\u5316\u62bd\u53d6\u548c\u62a5\u544a\u751f\u6210\u5fc5\u987b\u6cbf\u7528\u540c\u4e00\u7ec4\u7ef4\u5ea6\u3002",
            ],
            "query_hints": hints,
            "metadata": metadata,
        }
    )


def dimension_for_query(query: str) -> str | None:
    text = query.lower()
    best_dimension: str | None = None
    best_count = 0
    for dimension_id, keywords in FIXED_DIMENSION_KEYWORDS.items():
        count = sum(1 for keyword in keywords if keyword.lower() in text)
        if count > best_count:
            best_dimension = dimension_id
            best_count = count
    return best_dimension


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
