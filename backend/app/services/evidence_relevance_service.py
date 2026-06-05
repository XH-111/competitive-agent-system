import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import urlparse

from app.schemas import Evidence


KNOWN_ALIASES = {
    "\u98de\u4e66": ["\u98de\u4e66", "feishu", "lark"],
    "\u9489\u9489": ["\u9489\u9489", "dingtalk"],
    "\u4f01\u4e1a\u5fae\u4fe1": ["\u4f01\u4e1a\u5fae\u4fe1", "wecom", "wechat work", "weixin work"],
}


@dataclass(frozen=True)
class EvidenceRelevanceResult:
    relevance_score: float
    relevance_level: str
    relevance_reason: str
    entity_match_signals: dict


def normalize_competitor_name(name: str) -> str:
    text = name.strip().lower()
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return re.sub(r"\s+", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def generate_competitor_aliases(name: str) -> list[str]:
    aliases = [name.strip()]
    aliases.extend(KNOWN_ALIASES.get(name.strip(), []))
    normalized = normalize_competitor_name(name)
    if normalized:
        aliases.append(normalized)
    return _merge_aliases(aliases, [])


def score_evidence_relevance(
    evidence: Evidence,
    competitor: str,
    aliases: list[str] | None = None,
    title: str = "",
) -> EvidenceRelevanceResult:
    aliases = _merge_aliases(generate_competitor_aliases(competitor), aliases or [])
    url = evidence.url or ""
    domain = evidence.source_domain or _domain(url)
    snippet = evidence.snippet or ""

    title_text = title.lower()
    snippet_text = snippet.lower()
    url_text = url.lower()
    domain_text = domain.lower()
    normalized_domain = normalize_competitor_name(domain_text)
    normalized_competitor = normalize_competitor_name(competitor)

    competitor_in_title = _contains_alias(title_text, aliases)
    competitor_in_snippet = _contains_alias(snippet_text, aliases)
    competitor_in_url = _contains_alias(url_text, aliases)
    competitor_in_domain = _contains_alias(domain_text, aliases) or _contains_alias(normalized_domain, aliases)
    strong_entity_match = any([competitor_in_title, competitor_in_url, competitor_in_domain])
    competitor_alias_matched = any([strong_entity_match, competitor_in_snippet])
    domain_similarity_score = _similarity(normalized_competitor, normalized_domain)
    strong_domain_similarity = domain_similarity_score >= 0.75

    score = 0.0
    if competitor_in_title:
        score += 0.35
    if competitor_in_snippet:
        score += 0.35
    if competitor_in_url:
        score += 0.20
    if competitor_in_domain:
        score += 0.20
    if strong_domain_similarity:
        score += 0.10

    if not competitor_alias_matched:
        score = min(score, 0.35)
    if competitor_in_snippet and not strong_entity_match and not strong_domain_similarity:
        score = min(score, 0.35)
    score = round(max(0.0, min(1.0, score)), 2)

    if not strong_entity_match and not strong_domain_similarity:
        level = "low" if competitor_in_snippet else "unrelated"
    elif score >= 0.75:
        level = "high"
    elif score >= 0.45:
        level = "medium"
    elif score >= 0.25:
        level = "low"
    else:
        level = "unrelated"

    signals = {
        "competitor_in_title": competitor_in_title,
        "competitor_in_snippet": competitor_in_snippet,
        "competitor_in_url": competitor_in_url,
        "competitor_in_domain": competitor_in_domain,
        "competitor_alias_matched": competitor_alias_matched,
        "domain_similarity_score": round(domain_similarity_score, 2),
        "strong_entity_match": strong_entity_match,
        "aliases_used": aliases,
        "alias_count": len(aliases),
    }
    reason = _reason(competitor, aliases, signals, level)
    return EvidenceRelevanceResult(
        relevance_score=score,
        relevance_level=level,
        relevance_reason=reason,
        entity_match_signals=signals,
    )


def apply_relevance(evidence: Evidence, competitor: str, title: str = "", aliases: list[str] | None = None) -> Evidence:
    result = score_evidence_relevance(evidence, competitor, aliases=aliases, title=title)
    data = evidence.model_dump()
    data.update(
        {
            "relevance_score": result.relevance_score,
            "relevance_level": result.relevance_level,
            "relevance_reason": result.relevance_reason,
            "entity_match_signals": result.entity_match_signals,
        }
    )
    return Evidence(**data)


def is_relevant_evidence(evidence: Evidence) -> bool:
    return evidence.relevance_level in {"high", "medium"}


def _contains_alias(text: str, aliases: list[str]) -> bool:
    normalized_text = normalize_competitor_name(text)
    for alias in aliases:
        clean = alias.lower().strip()
        normalized_alias = normalize_competitor_name(clean)
        if clean and clean in text:
            return True
        if normalized_alias and normalized_alias in normalized_text:
            return True
    return False


def _merge_aliases(primary: list[str], secondary: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for alias in [*primary, *secondary]:
        clean = alias.strip().lower() if isinstance(alias, str) else ""
        normalized = normalize_competitor_name(clean)
        key = normalized or clean
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(clean)
    return output


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _reason(competitor: str, aliases: list[str], signals: dict, level: str) -> str:
    matched = [key for key, value in signals.items() if key.startswith("competitor_in_") and value]
    if matched:
        return f"{competitor} matched by {', '.join(matched)}; aliases={aliases}; relevance={level}."
    return f"No competitor or alias match for {competitor}; evidence is {level}."
