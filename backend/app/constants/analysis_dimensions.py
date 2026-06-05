import re
from typing import Any

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

BASE_COMPETITIVE_DIMENSIONS = FIXED_COMPETITIVE_DIMENSIONS

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

DYNAMIC_DIMENSION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "integration": {
        "label": "\u96c6\u6210\u4e0e\u5f00\u653e\u5e73\u53f0",
        "keywords": ["integration", "api", "\u5f00\u653e\u5e73\u53f0", "\u63a5\u53e3", "\u96c6\u6210"],
        "preferred_sources": ["official", "documentation"],
    },
    "security_compliance": {
        "label": "\u5b89\u5168\u5408\u89c4",
        "keywords": ["security", "compliance", "\u5b89\u5168", "\u5408\u89c4", "\u6743\u9650"],
        "preferred_sources": ["official", "documentation"],
    },
    "ecosystem": {
        "label": "\u751f\u6001\u4e0e\u5e94\u7528\u5e02\u573a",
        "keywords": ["ecosystem", "\u751f\u6001", "\u5e94\u7528\u5e02\u573a", "\u63d2\u4ef6"],
        "preferred_sources": ["official", "documentation", "media"],
    },
    "enterprise_management": {
        "label": "\u4f01\u4e1a\u7ba1\u7406\u4e0e\u7ec4\u7ec7\u6743\u9650",
        "keywords": ["enterprise management", "\u7ec4\u7ec7\u67b6\u6784", "\u7ba1\u7406\u5458", "\u6743\u9650"],
        "preferred_sources": ["official", "documentation"],
    },
    "ai_capability": {
        "label": "AI \u80fd\u529b",
        "keywords": ["ai", "\u4eba\u5de5\u667a\u80fd", "\u5927\u6a21\u578b", "\u667a\u80fd"],
        "preferred_sources": ["official", "documentation", "media"],
    },
    "battery_range": {
        "label": "\u7eed\u822a\u4e0e\u7535\u6c60",
        "keywords": ["range", "battery", "\u7eed\u822a", "\u7535\u6c60"],
        "preferred_sources": ["official", "media"],
    },
    "autonomous_driving": {
        "label": "\u667a\u9a7e\u80fd\u529b",
        "keywords": ["autonomous driving", "\u667a\u9a7e", "\u81ea\u52a8\u9a7e\u9a76", "\u8f85\u52a9\u9a7e\u9a76"],
        "preferred_sources": ["official", "media"],
    },
    "charging_network": {
        "label": "\u8865\u80fd\u4e0e\u5145\u7535\u7f51\u7edc",
        "keywords": ["charging", "\u5145\u7535", "\u8865\u80fd", "\u8d85\u5145"],
        "preferred_sources": ["official", "media"],
    },
    "vehicle_performance": {
        "label": "\u52a8\u529b\u4e0e\u64cd\u63a7",
        "keywords": ["performance", "\u52a8\u529b", "\u64cd\u63a7", "\u96f6\u767e"],
        "preferred_sources": ["official", "media", "review"],
    },
    "after_sales_service": {
        "label": "\u552e\u540e\u670d\u52a1",
        "keywords": ["after sales", "\u552e\u540e", "\u7ef4\u4fee", "\u670d\u52a1\u7f51\u70b9"],
        "preferred_sources": ["official", "review"],
    },
    "delivery_capacity": {
        "label": "\u4ea7\u80fd\u4e0e\u4ea4\u4ed8",
        "keywords": ["delivery", "\u4ea4\u4ed8", "\u4ea7\u80fd", "\u4ea7\u91cf"],
        "preferred_sources": ["official", "media"],
    },
    "hardware_specs": {
        "label": "\u786c\u4ef6\u53c2\u6570",
        "keywords": ["hardware", "specs", "\u786c\u4ef6", "\u53c2\u6570", "\u5c4f\u5e55"],
        "preferred_sources": ["official", "documentation"],
    },
    "camera_capability": {
        "label": "\u5f71\u50cf\u80fd\u529b",
        "keywords": ["camera", "\u5f71\u50cf", "\u6444\u50cf", "\u62cd\u7167"],
        "preferred_sources": ["official", "review", "media"],
    },
    "chip_performance": {
        "label": "\u82af\u7247\u6027\u80fd",
        "keywords": ["chip", "processor", "\u82af\u7247", "\u5904\u7406\u5668", "\u6027\u80fd"],
        "preferred_sources": ["official", "media", "review"],
    },
    "os_ecosystem": {
        "label": "\u7cfb\u7edf\u4e0e\u751f\u6001",
        "keywords": ["os", "ecosystem", "\u7cfb\u7edf", "\u751f\u6001"],
        "preferred_sources": ["official", "documentation", "media"],
    },
    "channel_strategy": {
        "label": "\u6e20\u9053\u7b56\u7565",
        "keywords": ["channel", "\u6e20\u9053", "\u9500\u552e", "\u95e8\u5e97"],
        "preferred_sources": ["official", "media"],
    },
    "performance_benchmark": {
        "label": "\u6027\u80fd\u8dd1\u5206",
        "keywords": ["benchmark", "\u8dd1\u5206", "\u6027\u80fd\u6d4b\u8bd5"],
        "preferred_sources": ["media", "review", "documentation"],
    },
    "power_efficiency": {
        "label": "\u529f\u8017\u80fd\u6548",
        "keywords": ["power efficiency", "\u529f\u8017", "\u80fd\u6548"],
        "preferred_sources": ["documentation", "media", "review"],
    },
    "ai_npu_capability": {
        "label": "AI / NPU \u7b97\u529b",
        "keywords": ["npu", "ai", "\u7b97\u529b", "\u795e\u7ecf\u7f51\u7edc"],
        "preferred_sources": ["official", "documentation", "media"],
    },
    "compatibility": {
        "label": "\u5e73\u53f0\u517c\u5bb9\u6027",
        "keywords": ["compatibility", "\u517c\u5bb9", "\u5e73\u53f0"],
        "preferred_sources": ["documentation", "review"],
    },
    "thermal_design": {
        "label": "\u6563\u70ed\u8bbe\u8ba1",
        "keywords": ["thermal", "\u6563\u70ed", "\u6e29\u63a7"],
        "preferred_sources": ["documentation", "review", "media"],
    },
    "market_availability": {
        "label": "\u4e0a\u5e02\u4e0e\u4f9b\u8d27",
        "keywords": ["availability", "\u4e0a\u5e02", "\u4f9b\u8d27", "\u73b0\u8d27"],
        "preferred_sources": ["official", "media"],
    },
    "gameplay_core_loop": {
        "label": "\u6838\u5fc3\u73a9\u6cd5\u5faa\u73af",
        "keywords": ["gameplay", "\u73a9\u6cd5", "\u6838\u5fc3\u5faa\u73af"],
        "preferred_sources": ["official", "media", "review"],
    },
    "monetization": {
        "label": "\u5546\u4e1a\u5316",
        "keywords": ["monetization", "\u5546\u4e1a\u5316", "\u4ed8\u8d39", "\u5185\u8d2d"],
        "preferred_sources": ["official", "media"],
    },
    "user_retention": {
        "label": "\u7528\u6237\u7559\u5b58",
        "keywords": ["retention", "\u7559\u5b58", "\u6d3b\u8dc3"],
        "preferred_sources": ["media", "review"],
    },
    "community_operation": {
        "label": "\u793e\u533a\u8fd0\u8425",
        "keywords": ["community", "\u793e\u533a", "\u8fd0\u8425"],
        "preferred_sources": ["official", "media"],
    },
    "esports_ecosystem": {
        "label": "\u8d5b\u4e8b\u751f\u6001",
        "keywords": ["esports", "\u7535\u7ade", "\u8d5b\u4e8b"],
        "preferred_sources": ["official", "media"],
    },
    "content_update": {
        "label": "\u5185\u5bb9\u66f4\u65b0\u8282\u594f",
        "keywords": ["content update", "\u7248\u672c\u66f4\u65b0", "\u5185\u5bb9\u66f4\u65b0"],
        "preferred_sources": ["official", "media"],
    },
    "traffic_source": {
        "label": "\u6d41\u91cf\u6765\u6e90",
        "keywords": ["traffic", "\u6d41\u91cf", "\u5165\u53e3"],
        "preferred_sources": ["media", "official"],
    },
    "supply_chain": {
        "label": "\u4f9b\u5e94\u94fe",
        "keywords": ["supply chain", "\u4f9b\u5e94\u94fe", "\u4f9b\u7ed9"],
        "preferred_sources": ["media", "official"],
    },
    "logistics": {
        "label": "\u5c65\u7ea6\u7269\u6d41",
        "keywords": ["logistics", "\u7269\u6d41", "\u5c65\u7ea6", "\u914d\u9001"],
        "preferred_sources": ["official", "media"],
    },
    "merchant_ecosystem": {
        "label": "\u5546\u5bb6\u751f\u6001",
        "keywords": ["merchant", "\u5546\u5bb6", "\u5e97\u94fa", "\u5165\u9a7b"],
        "preferred_sources": ["official", "media"],
    },
    "conversion_path": {
        "label": "\u8f6c\u5316\u8def\u5f84",
        "keywords": ["conversion", "\u8f6c\u5316", "\u4e0b\u5355"],
        "preferred_sources": ["official", "media"],
    },
    "subsidy_strategy": {
        "label": "\u8865\u8d34\u7b56\u7565",
        "keywords": ["subsidy", "\u8865\u8d34", "\u4f18\u60e0"],
        "preferred_sources": ["official", "media"],
    },
    "content_commerce": {
        "label": "\u5185\u5bb9\u7535\u5546",
        "keywords": ["content commerce", "\u5185\u5bb9\u7535\u5546", "\u76f4\u64ad", "\u77ed\u89c6\u9891"],
        "preferred_sources": ["official", "media"],
    },
    "brand_portfolio": {
        "label": "\u54c1\u724c\u7ec4\u5408",
        "keywords": ["brand portfolio", "\u54c1\u724c\u7ec4\u5408", "\u54c1\u724c"],
        "preferred_sources": ["official", "media"],
    },
    "membership_loyalty": {
        "label": "\u4f1a\u5458\u4e0e\u5fe0\u8bda\u5ea6",
        "keywords": ["membership", "loyalty", "\u4f1a\u5458", "\u79ef\u5206"],
        "preferred_sources": ["official", "media"],
    },
    "offline_store_network": {
        "label": "\u7ebf\u4e0b\u95e8\u5e97\u7f51\u7edc",
        "keywords": ["offline store", "\u95e8\u5e97", "\u7ebf\u4e0b"],
        "preferred_sources": ["official", "media"],
    },
    "beauty_service": {
        "label": "\u7f8e\u5986\u670d\u52a1",
        "keywords": ["beauty service", "\u7f8e\u5986\u670d\u52a1", "\u8bd5\u5986"],
        "preferred_sources": ["official", "review"],
    },
    "private_label": {
        "label": "\u81ea\u6709\u54c1\u724c",
        "keywords": ["private label", "\u81ea\u6709\u54c1\u724c"],
        "preferred_sources": ["official", "media"],
    },
    "omnichannel": {
        "label": "\u5168\u6e20\u9053",
        "keywords": ["omnichannel", "\u5168\u6e20\u9053", "o2o"],
        "preferred_sources": ["official", "media"],
    },
    "ai_beauty_tech": {
        "label": "AI \u7f8e\u5986\u79d1\u6280",
        "keywords": ["ai beauty", "\u667a\u80fd\u8bd5\u5986", "\u7f8e\u5986\u79d1\u6280"],
        "preferred_sources": ["official", "media"],
    },
}

DYNAMIC_DIMENSION_FEW_SHOTS = [
    {
        "match_keywords": ["b2b", "saas", "\u4f01\u4e1a\u534f\u4f5c", "\u534f\u4f5c\u5de5\u5177", "\u529e\u516c"],
        "dimension_ids": ["integration", "security_compliance", "ecosystem", "enterprise_management", "ai_capability"],
        "reason": "\u4f01\u4e1a\u534f\u4f5c\u5de5\u5177\u9700\u8981\u5173\u6ce8 API\u3001\u5f00\u653e\u5e73\u53f0\u3001\u6743\u9650\u7ba1\u7406\u3001\u5b89\u5168\u5408\u89c4\u3001AI \u529e\u516c\u80fd\u529b\u548c\u751f\u6001\u5e94\u7528\u3002",
    },
    {
        "match_keywords": ["\u65b0\u80fd\u6e90", "\u7535\u52a8\u8f66", "\u6c7d\u8f66", "model 3", "su7", "\u6bd4\u4e9a\u8fea"],
        "dimension_ids": ["battery_range", "autonomous_driving", "charging_network", "vehicle_performance", "after_sales_service", "delivery_capacity"],
        "reason": "\u6c7d\u8f66\u7ade\u54c1\u9700\u8981\u6bd4\u8f83\u7eed\u822a\u3001\u667a\u9a7e\u3001\u8865\u80fd\u3001\u52a8\u529b\u64cd\u63a7\u3001\u552e\u540e\u7f51\u70b9\u548c\u4ea4\u4ed8\u80fd\u529b\u3002",
    },
    {
        "match_keywords": ["\u667a\u80fd\u624b\u673a", "\u624b\u673a", "iphone", "mate", "\u65d7\u8230\u673a"],
        "dimension_ids": ["hardware_specs", "camera_capability", "chip_performance", "os_ecosystem", "channel_strategy", "ai_capability"],
        "reason": "\u624b\u673a\u7ade\u54c1\u9700\u8981\u6bd4\u8f83\u786c\u4ef6\u53c2\u6570\u3001\u5f71\u50cf\u80fd\u529b\u3001\u82af\u7247\u6027\u80fd\u3001\u7cfb\u7edf\u751f\u6001\u3001\u6e20\u9053\u7b56\u7565\u548c\u7aef\u4fa7 AI \u80fd\u529b\u3002",
    },
    {
        "match_keywords": ["\u82af\u7247", "\u786c\u4ef6", "cpu", "gpu", "npu", "intel", "amd"],
        "dimension_ids": ["performance_benchmark", "power_efficiency", "ai_npu_capability", "compatibility", "thermal_design", "market_availability"],
        "reason": "\u82af\u7247\u5206\u6790\u9700\u8981\u6bd4\u8f83\u6027\u80fd\u8dd1\u5206\u3001\u529f\u8017\u80fd\u6548\u3001AI/NPU \u7b97\u529b\u3001\u517c\u5bb9\u6027\u3001\u6563\u70ed\u548c\u4f9b\u8d27\u3002",
    },
    {
        "match_keywords": ["\u6e38\u620f", "\u624b\u6e38", "\u7535\u7ade", "game"],
        "dimension_ids": ["gameplay_core_loop", "monetization", "user_retention", "community_operation", "esports_ecosystem", "content_update"],
        "reason": "\u6e38\u620f\u7ade\u54c1\u9700\u8981\u5173\u6ce8\u73a9\u6cd5\u5faa\u73af\u3001\u5546\u4e1a\u5316\u3001\u7559\u5b58\u3001\u793e\u533a\u8fd0\u8425\u3001\u8d5b\u4e8b\u751f\u6001\u548c\u7248\u672c\u66f4\u65b0\u3002",
    },
    {
        "match_keywords": ["\u7535\u5546", "\u7535\u5546\u5e73\u53f0", "\u96f6\u552e", "\u6dd8\u5b9d", "\u4eac\u4e1c", "\u62fc\u591a\u591a"],
        "dimension_ids": ["traffic_source", "supply_chain", "logistics", "merchant_ecosystem", "conversion_path", "subsidy_strategy", "content_commerce"],
        "reason": "\u7535\u5546\u5e73\u53f0\u9700\u8981\u5173\u6ce8\u6d41\u91cf\u3001\u4f9b\u5e94\u94fe\u3001\u7269\u6d41\u3001\u5546\u5bb6\u751f\u6001\u3001\u8f6c\u5316\u8def\u5f84\u3001\u8865\u8d34\u548c\u5185\u5bb9\u7535\u5546\u3002",
    },
    {
        "match_keywords": ["\u7f8e\u5986", "\u7f8e\u5986\u96f6\u552e", "beauty", "sephora", "ulta", "watsons"],
        "dimension_ids": ["brand_portfolio", "membership_loyalty", "offline_store_network", "beauty_service", "private_label", "omnichannel", "ai_beauty_tech"],
        "reason": "\u7f8e\u5986\u96f6\u552e\u9700\u8981\u5173\u6ce8\u54c1\u724c\u7ec4\u5408\u3001\u4f1a\u5458\u3001\u95e8\u5e97\u3001\u7f8e\u5986\u670d\u52a1\u3001\u81ea\u6709\u54c1\u724c\u3001\u5168\u6e20\u9053\u548c AI \u7f8e\u5986\u79d1\u6280\u3002",
    },
]


def fixed_dimension_ids() -> list[str]:
    return list(FIXED_COMPETITIVE_DIMENSIONS)


def planned_dimension_ids(task: Task) -> list[str]:
    return _dedupe([*BASE_COMPETITIVE_DIMENSIONS, *dynamic_dimension_ids_for_task(task)])


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


def dynamic_dimension_ids_for_task(task: Task) -> list[str]:
    text = _task_text(task)
    selected: list[str] = []
    for example in DYNAMIC_DIMENSION_FEW_SHOTS:
        if any(keyword.lower() in text for keyword in example["match_keywords"]):
            selected.extend(example["dimension_ids"])
    return _dedupe(selected)


def dynamic_dimension_reasons_for_task(task: Task) -> list[str]:
    text = _task_text(task)
    return [
        example["reason"]
        for example in DYNAMIC_DIMENSION_FEW_SHOTS
        if any(keyword.lower() in text for keyword in example["match_keywords"])
    ]


def dimension_plans_for_task(task: Task) -> list[AnalysisDimension]:
    plans = fixed_dimension_plans()
    priority = len(plans) + 1
    for dimension_id in dynamic_dimension_ids_for_task(task):
        definition = DYNAMIC_DIMENSION_DEFINITIONS.get(dimension_id)
        if not definition:
            continue
        plans.append(
            AnalysisDimension(
                dimension_id=dimension_id,
                label=definition["label"],
                description=f"\u56f4\u7ed5{definition['label']}\u7ef4\u5ea6\u91c7\u96c6\u3001\u62bd\u53d6\u548c\u62a5\u544a\u3002",
                keywords=definition["keywords"],
                required=False,
                priority=priority,
                metadata={
                    "source": "planner_dynamic_dimension",
                    "preferred_sources": definition.get("preferred_sources", []),
                },
            )
        )
        priority += 1
    return plans


def fixed_query_hints_for_competitor(competitor: str, industry: str) -> list[str]:
    return [
        f"{competitor} \u4ef7\u683c",
        f"{competitor} \u529f\u80fd",
        f"{competitor} \u7528\u6237\u753b\u50cf",
        f"{competitor} \u4f18\u52bf",
        f"{competitor} \u52a3\u52bf",
        f"{competitor} \u673a\u4f1a",
        f"{competitor} \u5a01\u80c1",
    ]


def fixed_dimension_query_hints_for_competitor(competitor: str, industry: str) -> dict[str, str]:
    queries = fixed_query_hints_for_competitor(competitor, industry)
    return dict(zip(FIXED_COMPETITIVE_DIMENSIONS, queries, strict=True))


def fixed_query_hints(task: Task) -> dict[str, list[str]]:
    return {
        competitor: fixed_query_hints_for_competitor(competitor, task.industry)
        for competitor in task.competitors
    }


def collector_search_plan_for_task(task: Task, selected_dimensions: list[str] | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    dimensions = selected_dimensions or planned_dimension_ids(task)
    labels = dimension_label_map(task)
    plans_by_id = {plan.dimension_id: plan for plan in dimension_plans_for_task(task)}
    output: dict[str, dict[str, dict[str, Any]]] = {}
    for competitor in task.competitors:
        output[competitor] = {}
        for dimension_id in dimensions:
            label = labels.get(dimension_id, dimension_id)
            plan = plans_by_id.get(dimension_id)
            preferred_sources = plan.metadata.get("preferred_sources", []) if plan else []
            output[competitor][dimension_id] = {
                "dimension_id": dimension_id,
                "dimension_label": label,
                "queries": [f"{competitor} {label}"],
                "intent": f"\u91c7\u96c6{competitor}\u5728{label}\u7ef4\u5ea6\u7684\u516c\u5f00\u8bc1\u636e\u3002",
                "preferred_sources": preferred_sources or ["official", "documentation", "media", "review"],
                "query_strategy": "competitor_plus_dimension_label",
            }
    return output


def dimension_query_hints_from_search_plan(search_plan: dict[str, dict[str, dict[str, Any]]]) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    for competitor, by_dimension in search_plan.items():
        output[competitor] = {}
        for dimension_id, item in by_dimension.items():
            queries = item.get("queries", []) if isinstance(item, dict) else []
            output[competitor][dimension_id] = str(queries[0]) if queries else ""
    return output


def query_hints_from_search_plan(search_plan: dict[str, dict[str, dict[str, Any]]]) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for competitor, by_dimension in search_plan.items():
        queries: list[str] = []
        for item in by_dimension.values():
            if isinstance(item, dict):
                queries.extend(str(query) for query in item.get("queries", []) if str(query).strip())
        output[competitor] = _dedupe(queries)
    return output


def apply_fixed_dimensions_to_plan(plan: AnalysisDimensionPlan | None, task: Task) -> AnalysisDimensionPlan:
    existing = plan or AnalysisDimensionPlan()
    llm_dynamic_dimensions = _normalize_llm_dynamic_dimensions((existing.metadata or {}).get("llm_dynamic_dimensions"))
    llm_dynamic_dimension_ids = [item["dimension_id"] for item in llm_dynamic_dimensions]
    selected_dimensions = _dedupe([*planned_dimension_ids(task), *llm_dynamic_dimension_ids])
    collector_search_plan = collector_search_plan_for_task(task, selected_dimensions)
    llm_search_plan = _normalize_llm_collector_search_plan(
        (existing.metadata or {}).get("llm_collector_search_plan"),
        task=task,
        selected_dimensions=selected_dimensions,
    )
    if llm_search_plan:
        collector_search_plan = _merge_collector_search_plan(collector_search_plan, llm_search_plan)
    hints = dict(existing.query_hints or {})
    dimension_query_hints = dimension_query_hints_from_search_plan(collector_search_plan)
    for competitor, queries in query_hints_from_search_plan(collector_search_plan).items():
        hints[competitor] = _dedupe([*queries, *hints.get(competitor, [])])
    metadata = dict(existing.metadata or {})
    metadata.update(
        {
            "dimension_policy": "base_plus_dynamic_planner_search_plan",
            "base_dimensions": fixed_dimension_ids(),
            "dynamic_dimensions": dynamic_dimension_ids_for_task(task),
            "llm_dynamic_dimensions": llm_dynamic_dimensions,
            "llm_dynamic_dimension_ids": llm_dynamic_dimension_ids,
            "dynamic_dimension_reasons": dynamic_dimension_reasons_for_task(task),
            "dimension_query_hints": dimension_query_hints,
            "collector_search_plan": collector_search_plan,
            "llm_collector_search_plan_used": bool(llm_search_plan),
            "llm_planner_contract": "dynamic_dimensions_and_collector_search_plan",
        }
    )
    dimension_plans = _merge_dimension_plans(dimension_plans_for_task(task), llm_dynamic_dimensions)
    return existing.model_copy(
        update={
            "selected_dimensions": selected_dimensions,
            "dimension_plans": dimension_plans,
            "research_goals": [
                "\u59cb\u7ec8\u8986\u76d6\u4ef7\u683c\u3001\u529f\u80fd\u3001\u7528\u6237\u753b\u50cf\u3001\u4f18\u52bf\u3001\u52a3\u52bf\u3001\u673a\u4f1a\u3001\u5a01\u80c1\u4e03\u4e2a\u57fa\u7840\u7ef4\u5ea6\u3002",
                "\u6839\u636e\u884c\u4e1a\u548c\u7ade\u54c1\u7c7b\u578b\u8865\u5145\u52a8\u6001\u7ef4\u5ea6\uff0cCollector \u5fc5\u987b\u6309 Planner search plan \u6267\u884c\u641c\u7d22\u3002",
                "\u7ed3\u6784\u5316\u62bd\u53d6\u548c\u62a5\u544a\u751f\u6210\u5fc5\u987b\u6cbf\u7528 Planner \u9009\u5b9a\u7684\u540c\u4e00\u7ec4\u7ef4\u5ea6\u3002",
            ],
            "query_hints": hints,
            "metadata": metadata,
        }
    )


def dimension_for_query(query: str) -> str | None:
    text = query.lower()
    best_dimension: str | None = None
    best_count = 0
    for dimension_id, keywords in dimension_keyword_map().items():
        count = sum(1 for keyword in keywords if keyword.lower() in text)
        if count > best_count:
            best_dimension = dimension_id
            best_count = count
    return best_dimension


def dimension_label_map(task: Task | None = None) -> dict[str, str]:
    labels = dict(FIXED_DIMENSION_LABELS)
    for dimension_id, definition in DYNAMIC_DIMENSION_DEFINITIONS.items():
        labels[dimension_id] = definition["label"]
    return labels


def dimension_keyword_map() -> dict[str, list[str]]:
    keywords = {dimension_id: list(items) for dimension_id, items in FIXED_DIMENSION_KEYWORDS.items()}
    for dimension_id, definition in DYNAMIC_DIMENSION_DEFINITIONS.items():
        keywords[dimension_id] = list(definition.get("keywords", []))
    return keywords


def _task_text(task: Task) -> str:
    return " ".join(
        [
            task.product_name or "",
            task.industry or "",
            task.region or "",
            *[competitor or "" for competitor in task.competitors],
        ]
    ).lower()


def _normalize_llm_dynamic_dimensions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        dimension_id = _safe_dimension_id(item.get("dimension_id"))
        if not dimension_id:
            continue
        label = str(item.get("label") or dimension_id.replace("_", " ")).strip()
        keywords = _string_list(item.get("keywords")) or [dimension_id]
        preferred_sources = [
            source
            for source in _string_list(item.get("preferred_sources"))
            if source in {"official", "documentation", "media", "review", "unknown"}
        ]
        output.append(
            {
                "dimension_id": dimension_id,
                "label": label,
                "description": str(item.get("description") or "").strip(),
                "keywords": keywords,
                "reason": str(item.get("reason") or "").strip(),
                "preferred_sources": preferred_sources or ["official", "documentation", "media", "review"],
            }
        )
    return output


def _normalize_llm_collector_search_plan(value: Any, *, task: Task, selected_dimensions: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    if not isinstance(value, dict):
        return {}
    selected = set(selected_dimensions)
    competitors = set(task.competitors)
    output: dict[str, dict[str, dict[str, Any]]] = {}
    for competitor, by_dimension in value.items():
        if competitor not in competitors or not isinstance(by_dimension, dict):
            continue
        output[competitor] = {}
        for raw_dimension_id, item in by_dimension.items():
            dimension_id = _safe_dimension_id(raw_dimension_id)
            if not dimension_id or dimension_id not in selected or not isinstance(item, dict):
                continue
            queries = _string_list(item.get("queries"))[:3]
            queries = [query for query in queries if competitor.lower() in query.lower() or competitor in query]
            if not queries:
                continue
            preferred_sources = [
                source
                for source in _string_list(item.get("preferred_sources"))
                if source in {"official", "documentation", "media", "review", "unknown"}
            ]
            output[competitor][dimension_id] = {
                "dimension_id": dimension_id,
                "queries": queries,
                "intent": str(item.get("intent") or "").strip(),
                "preferred_sources": preferred_sources or ["official", "documentation", "media", "review"],
                "query_strategy": "llm_planner_search_plan",
            }
    return {competitor: dimensions for competitor, dimensions in output.items() if dimensions}


def _merge_collector_search_plan(
    base: dict[str, dict[str, dict[str, Any]]],
    llm_plan: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    merged = {competitor: {dimension: dict(item) for dimension, item in by_dimension.items()} for competitor, by_dimension in base.items()}
    for competitor, by_dimension in llm_plan.items():
        merged.setdefault(competitor, {})
        for dimension_id, item in by_dimension.items():
            existing = dict(merged[competitor].get(dimension_id, {}))
            existing_queries = existing.get("queries", [])
            existing.update(item)
            existing["queries"] = _dedupe([*item.get("queries", []), *existing_queries])[:3]
            existing["query_strategy"] = item.get("query_strategy", "llm_planner_search_plan")
            merged[competitor][dimension_id] = existing
    return merged


def _merge_dimension_plans(plans: list[AnalysisDimension], llm_dynamic_dimensions: list[dict[str, Any]]) -> list[AnalysisDimension]:
    seen = {plan.dimension_id for plan in plans}
    priority = len(plans) + 1
    output = list(plans)
    for item in llm_dynamic_dimensions:
        dimension_id = item["dimension_id"]
        if dimension_id in seen:
            continue
        output.append(
            AnalysisDimension(
                dimension_id=dimension_id,
                label=item["label"],
                description=item["description"],
                keywords=item["keywords"],
                required=False,
                priority=priority,
                metadata={
                    "source": "llm_planner_dynamic_dimension",
                    "reason": item.get("reason", ""),
                    "preferred_sources": item.get("preferred_sources", []),
                },
            )
        )
        seen.add(dimension_id)
        priority += 1
    return output


def _safe_dimension_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    dimension_id = value.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,48}", dimension_id):
        return None
    return dimension_id


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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
