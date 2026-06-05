import json
import os
import re
from dataclasses import dataclass
from typing import Any

from app.schemas.models import Task
from app.services.llm_client import LlmClient


MAX_ALIASES_PER_COMPETITOR = 12


BRAND_ALIASES = {
    "\u82f9\u679c": ["Apple", "iPhone"],
    "\u5c0f\u7c73": ["Xiaomi", "Mi"],
    "\u534e\u4e3a": ["Huawei"],
    "\u8363\u8000": ["Honor"],
    "\u4e09\u661f": ["Samsung", "Galaxy"],
    "\u98de\u4e66": ["\u98de\u4e66", "Feishu", "Lark"],
    "\u9489\u9489": ["\u9489\u9489", "DingTalk"],
    "\u4f01\u4e1a\u5fae\u4fe1": ["\u4f01\u4e1a\u5fae\u4fe1", "WeCom", "WeChat Work", "Weixin Work"],
}


@dataclass(frozen=True)
class EntityResolutionResult:
    competitor: str
    canonical_name: str
    aliases: list[str]
    source: str
    confidence: float
    diagnostics: dict[str, Any]

    def model_dump(self) -> dict[str, Any]:
        return {
            "competitor": self.competitor,
            "canonical_name": self.canonical_name,
            "aliases": self.aliases,
            "source": self.source,
            "confidence": self.confidence,
            "diagnostics": self.diagnostics,
        }


class EntityResolverService:
    def __init__(self, llm_client: LlmClient | None = None) -> None:
        self.llm_client = llm_client or LlmClient()
        self.llm_enabled = os.getenv("ENTITY_RESOLVER_USE_LLM", "false").strip().lower() in {"1", "true", "yes", "on"}

    def resolve_for_task(self, task: Task) -> dict[str, dict[str, Any]]:
        rule_results = {
            competitor: self._rule_result(competitor).model_dump()
            for competitor in task.competitors
        }
        llm_results = self._llm_results(task) if self.llm_enabled and self.llm_client.is_available else {}
        output: dict[str, dict[str, Any]] = {}
        for competitor, rule_result in rule_results.items():
            llm_result = llm_results.get(competitor, {})
            merged_aliases = self._dedupe_aliases(
                [
                    competitor,
                    *rule_result.get("aliases", []),
                    *self._valid_aliases(llm_result.get("aliases", [])),
                ]
            )
            output[competitor] = {
                "competitor": competitor,
                "canonical_name": llm_result.get("canonical_name") or rule_result.get("canonical_name") or competitor,
                "aliases": merged_aliases,
                "source": "rule+llm" if llm_result else "rule",
                "confidence": max(float(rule_result.get("confidence", 0.75)), float(llm_result.get("confidence", 0.0) or 0.0)),
                "diagnostics": {
                    "rule_alias_count": len(rule_result.get("aliases", [])),
                    "llm_alias_count": len(self._valid_aliases(llm_result.get("aliases", []))) if llm_result else 0,
                    "llm_attempted": self.llm_enabled and self.llm_client.is_available,
                    "llm_used": bool(llm_result),
                },
            }
        return output

    def aliases_for_task(self, task: Task) -> dict[str, list[str]]:
        resolved = self.resolve_for_task(task)
        return {competitor: item["aliases"] for competitor, item in resolved.items()}

    def _rule_result(self, competitor: str) -> EntityResolutionResult:
        aliases = [competitor]
        compact = _compact(competitor)
        if compact != competitor.lower():
            aliases.append(compact)

        product_tail = self._product_tail(competitor)
        for brand, brand_aliases in BRAND_ALIASES.items():
            if brand in competitor:
                aliases.extend(brand_aliases)
                if product_tail:
                    aliases.extend(self._product_aliases(brand, brand_aliases, product_tail))

        return EntityResolutionResult(
            competitor=competitor,
            canonical_name=aliases[0],
            aliases=self._dedupe_aliases(aliases),
            source="rule",
            confidence=0.75,
            diagnostics={"strategy": "rule_based_brand_and_product_aliases"},
        )

    def _llm_results(self, task: Task) -> dict[str, dict[str, Any]]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You generate entity aliases for competitive analysis. "
                    "Return valid JSON only. Do not judge evidence relevance."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instruction": (
                            "Generate short aliases, English names, brand names, and common product names. "
                            "Keep JSON keys in English. Return only this schema: "
                            '{"competitors":[{"competitor":"...","canonical_name":"...","aliases":["..."],"confidence":0.8}]}'
                        ),
                        "industry": task.industry,
                        "region": task.region,
                        "competitors": task.competitors,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = self.llm_client.chat_json(messages, timeout=20.0)
        if not response.success or not response.content:
            return {}
        try:
            payload = _loads_json_object(response.content)
        except ValueError:
            return {}
        competitors = payload.get("competitors", [])
        if not isinstance(competitors, list):
            return {}
        allowed = set(task.competitors)
        output: dict[str, dict[str, Any]] = {}
        for item in competitors:
            if not isinstance(item, dict):
                continue
            competitor = item.get("competitor")
            if competitor not in allowed:
                continue
            aliases = self._valid_aliases(item.get("aliases", []))
            if not aliases:
                continue
            output[competitor] = {
                "canonical_name": _short_text(item.get("canonical_name")) or competitor,
                "aliases": aliases,
                "confidence": _bounded_float(item.get("confidence"), 0.75),
            }
        return output

    @staticmethod
    def _product_tail(competitor: str) -> str:
        tail = competitor
        for brand in BRAND_ALIASES:
            tail = tail.replace(brand, "")
        tail = tail.strip()
        return tail

    @staticmethod
    def _product_aliases(brand: str, brand_aliases: list[str], product_tail: str) -> list[str]:
        tail_spaced = _space_product_tail(product_tail)
        aliases: list[str] = []
        if brand == "\u82f9\u679c" and tail_spaced:
            aliases.extend(
                [
                    f"iPhone {tail_spaced}",
                    f"Apple iPhone {tail_spaced}",
                    f"iPhone{_compact(product_tail)}",
                    f"Apple iPhone{_compact(product_tail)}",
                    f"\u82f9\u679c {tail_spaced}",
                ]
            )
        elif brand == "\u5c0f\u7c73" and tail_spaced:
            aliases.extend(
                [
                    f"Xiaomi {tail_spaced}",
                    f"Mi {tail_spaced}",
                    f"Xiaomi{_compact(product_tail)}",
                    f"\u5c0f\u7c73 {tail_spaced}",
                ]
            )
        elif tail_spaced:
            aliases.extend([f"{alias} {tail_spaced}" for alias in brand_aliases])
        return aliases

    @classmethod
    def _dedupe_aliases(cls, aliases: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            clean = _short_text(alias)
            if not clean:
                continue
            key = _compact(clean)
            if key in seen:
                continue
            seen.add(key)
            output.append(clean)
            if len(output) >= MAX_ALIASES_PER_COMPETITOR:
                break
        return output

    @classmethod
    def _valid_aliases(cls, aliases: Any) -> list[str]:
        if not isinstance(aliases, list):
            return []
        return cls._dedupe_aliases([alias for alias in aliases if isinstance(alias, str)])


def _loads_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM alias response is not a JSON object.")
    return data


def _short_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.strip().split())
    if not text or len(text) > 48:
        return ""
    if any(token in text for token in ("\n", "\r", "{", "}", "[", "]")):
        return ""
    return text


def _compact(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).lower()


def _space_product_tail(tail: str) -> str:
    compact = _compact(tail)
    match = re.fullmatch(r"([a-z]*)(\d+)([a-z]*)", compact)
    if not match:
        return tail.strip()
    prefix, digits, suffix = match.groups()
    parts = [part for part in [prefix.upper() if len(prefix) <= 3 else prefix, digits, suffix.capitalize()] if part]
    return " ".join(parts)


def _bounded_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return round(max(0.0, min(1.0, parsed)), 2)
