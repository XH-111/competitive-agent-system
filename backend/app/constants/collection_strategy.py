from typing import Literal

from app.schemas import CollectorConfig


CollectionStrategyMode = Literal["simple", "balanced", "expert"]


STRATEGY_DEFAULTS: dict[CollectionStrategyMode, dict[str, int]] = {
    "simple": {
        "max_results_per_query": 3,
        "max_evidence_per_dimension": 5,
        "min_valid_evidence_required": 1,
        "content_fetch_max_per_dimension": 1,
    },
    "balanced": {
        "max_results_per_query": 5,
        "max_evidence_per_dimension": 10,
        "min_valid_evidence_required": 2,
        "content_fetch_max_per_dimension": 0,
    },
    "expert": {
        "max_results_per_query": 8,
        "max_evidence_per_dimension": 20,
        "min_valid_evidence_required": 3,
        "content_fetch_max_per_dimension": 0,
    },
}


def normalize_collection_strategy_mode(value: str | None) -> CollectionStrategyMode:
    if value in STRATEGY_DEFAULTS:
        return value  # type: ignore[return-value]
    return "balanced"


def collector_config_for_strategy(mode: str | None) -> CollectorConfig:
    defaults = STRATEGY_DEFAULTS[normalize_collection_strategy_mode(mode)]
    return CollectorConfig(
        default={
            "max_results_per_query": defaults["max_results_per_query"],
            "max_evidence_per_dimension": defaults["max_evidence_per_dimension"],
            "min_valid_evidence_required": defaults["min_valid_evidence_required"],
            "include_domains": [],
            "exclude_domains": [],
        },
        overrides=[],
    )


def content_fetch_max_per_dimension_for_strategy(mode: str | None) -> int | None:
    value = STRATEGY_DEFAULTS[normalize_collection_strategy_mode(mode)]["content_fetch_max_per_dimension"]
    return value or None
