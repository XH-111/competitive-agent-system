import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any, Callable

import httpx
from dotenv import load_dotenv

from app.schemas import Evidence
from app.services.llm_client import ROOT_DIR
from app.services.web_search_client import WebSearchClient

load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / "backend" / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class EvidenceContentFetchResult:
    success: bool
    page_title: str | None = None
    content_excerpt: str | None = None
    content_chars: int | None = None
    error: str | None = None
    fetched_at: datetime | None = None
    elapsed_time_ms: int = 0


class EvidenceContentFetcher:
    """Fetch richer content for Collector Evidence without changing Collector semantics."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        content_max_chars: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        search_config = WebSearchClient()
        self.provider = (provider or os.getenv("EVIDENCE_CONTENT_FETCH_PROVIDER") or search_config.provider).strip().lower()
        raw_key = api_key if api_key is not None else os.getenv("TAVILY_API_KEY") or search_config.api_key
        self.api_key = WebSearchClient._normalize_api_key(raw_key or "")
        self.base_url = (base_url or os.getenv("TAVILY_BASE_URL") or search_config.base_url or "https://api.tavily.com").strip().rstrip("/")
        self.timeout = timeout if timeout is not None else float(os.getenv("EVIDENCE_CONTENT_FETCH_TIMEOUT", "20") or "20")
        self.content_max_chars = content_max_chars or _env_int("EVIDENCE_CONTENT_MAX_CHARS", 12000)
        self.transport = transport

    @property
    def is_available(self) -> bool:
        return self.provider == "tavily" and bool(self.api_key and self.base_url)

    def enrich(
        self,
        evidence: list[Evidence],
        *,
        run_id: str | None = None,
        enabled: bool = True,
        max_per_competitor_dimension: int | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[list[Evidence], dict]:
        attempted = 0
        success_count = 0
        failed_count = 0
        skipped_count = 0
        fallback_count = 0
        elapsed_time_ms = 0
        content_lengths: list[int] = []
        fetched_ids: list[str] = []
        skipped_ids: list[str] = []
        failed_ids: list[str] = []
        limit_skipped_ids: list[str] = []
        error_counter: Counter[str] = Counter()
        enriched: list[Evidence] = []
        allowed_fetch_ids = self._allowed_fetch_ids(
            evidence,
            enabled=enabled,
            max_per_competitor_dimension=max_per_competitor_dimension,
        )
        fetch_candidates = [
            item
            for item in evidence
            if not self._skip_reason(item.model_copy(update={"run_id": run_id or item.run_id}), 0, enabled)
            and (allowed_fetch_ids is None or item.evidence_id in allowed_fetch_ids)
        ]
        fetch_total = len(fetch_candidates)

        for item in evidence:
            updated = item.model_copy(update={"run_id": run_id or item.run_id})
            skip_reason = self._skip_reason(updated, attempted, enabled)
            if not skip_reason and allowed_fetch_ids is not None and updated.evidence_id not in allowed_fetch_ids:
                skip_reason = "skipped:strategy_dimension_limit"
                limit_skipped_ids.append(updated.evidence_id)
            if skip_reason:
                skipped_count += 1
                skipped_ids.append(updated.evidence_id)
                if skip_reason == "skipped:already_fetched":
                    enriched.append(
                        self._mark_fetch_metadata(
                            updated,
                            success=True,
                            error=None,
                        )
                    )
                    continue
                enriched.append(
                    self._mark_fetch_metadata(
                        updated.model_copy(
                            update={
                                "content_mode": updated.content_mode or "snippet",
                                "page_fetch_success": False,
                                "page_fetch_error": skip_reason,
                            }
                        ),
                        success=False,
                        error=skip_reason,
                    )
                )
                continue

            attempted += 1
            if progress_callback:
                progress_callback(
                    {
                        "current": attempted,
                        "total": fetch_total,
                        "unit": "evidence",
                        "detail": f"{updated.competitor or '-'} / {(updated.entity_match_signals or {}).get('collector_dimension') or '-'}",
                        "evidence_id": updated.evidence_id,
                    }
                )
            result = self.fetch(updated.url or "")
            elapsed_time_ms += result.elapsed_time_ms
            if result.success:
                success_count += 1
                fetched_ids.append(updated.evidence_id)
                content_lengths.append(result.content_chars or 0)
                enriched.append(
                    self._mark_fetch_metadata(
                        updated.model_copy(
                            update={
                                "content_mode": "page",
                                "page_fetch_success": True,
                                "page_title": result.page_title,
                                "content_excerpt": result.content_excerpt,
                                "content_chars": result.content_chars,
                                "page_fetch_error": None,
                                "fetched_at": result.fetched_at,
                            }
                        ),
                        success=True,
                        error=None,
                    )
                )
            else:
                failed_count += 1
                fallback_count += 1
                failed_ids.append(updated.evidence_id)
                error_counter[result.error or "unknown_error"] += 1
                enriched.append(
                    self._mark_fetch_metadata(
                        updated.model_copy(
                            update={
                                "content_mode": "snippet",
                                "page_fetch_success": False,
                                "page_fetch_error": result.error,
                                "fetched_at": result.fetched_at,
                            }
                        ),
                        success=False,
                        error=result.error,
                    )
                )

        diagnostics = {
            "content_fetch_provider": self.provider,
            "content_fetch_available": self.is_available,
            "content_fetch_attempted": attempted > 0,
            "content_fetch_attempt_count": attempted,
            "content_fetch_success_count": success_count,
            "content_fetch_failed_count": failed_count,
            "content_fetch_skipped_count": skipped_count,
            "content_fetch_fallback_count": fallback_count,
            "content_fetch_error_summary": dict(error_counter),
            "content_fetch_elapsed_time_ms": elapsed_time_ms,
            "avg_content_chars": int(sum(content_lengths) / len(content_lengths)) if content_lengths else 0,
            "max_content_chars": max(content_lengths) if content_lengths else 0,
            "fetched_evidence_ids": fetched_ids,
            "failed_evidence_ids": failed_ids,
            "skipped_evidence_ids": skipped_ids,
            "content_fetch_limit_skipped_ids": limit_skipped_ids,
            "content_fetch_max_per_competitor_dimension": max_per_competitor_dimension,
            "run_id": run_id,
        }
        return enriched, diagnostics

    def fetch(self, url: str) -> EvidenceContentFetchResult:
        fetched_at = datetime.utcnow()
        if not url:
            return EvidenceContentFetchResult(success=False, error="missing_url", fetched_at=fetched_at)
        if self.provider != "tavily":
            return EvidenceContentFetchResult(success=False, error=f"unsupported_content_fetch_provider:{self.provider}", fetched_at=fetched_at)
        if not self.api_key:
            return EvidenceContentFetchResult(success=False, error="missing_tavily_api_key", fetched_at=fetched_at)
        if not self.base_url:
            return EvidenceContentFetchResult(success=False, error="missing_tavily_base_url", fetched_at=fetched_at)

        start = perf_counter()
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(
                    f"{self.base_url}/extract",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "urls": [url],
                        "extract_depth": "basic",
                        "include_images": False,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001 - content fetch must fall back to snippet.
            elapsed = int((perf_counter() - start) * 1000)
            return EvidenceContentFetchResult(
                success=False,
                error=f"{type(exc).__name__}:{self._sanitize_error(str(exc))}",
                fetched_at=fetched_at,
                elapsed_time_ms=elapsed,
            )

        elapsed = int((perf_counter() - start) * 1000)
        item = self._first_result(payload)
        if not item:
            return EvidenceContentFetchResult(success=False, error="empty_extract_result", fetched_at=fetched_at, elapsed_time_ms=elapsed)
        content = self._content_from_result(item)
        if not content:
            return EvidenceContentFetchResult(success=False, error="empty_extracted_content", fetched_at=fetched_at, elapsed_time_ms=elapsed)
        trimmed = content[: self.content_max_chars]
        return EvidenceContentFetchResult(
            success=True,
            page_title=self._title_from_result(item),
            content_excerpt=trimmed,
            content_chars=len(trimmed),
            fetched_at=fetched_at,
            elapsed_time_ms=elapsed,
        )

    def _skip_reason(self, evidence: Evidence, attempted_count: int, enabled: bool) -> str | None:
        if not enabled:
            return "skipped:disabled"
        if evidence.content_excerpt and evidence.page_fetch_success:
            return "skipped:already_fetched"
        if not evidence.url:
            return "skipped:missing_url"
        if evidence.relevance_level == "unrelated":
            return "skipped:relevance_unrelated"
        if evidence.source_quality == "low_quality":
            return "skipped:low_quality_source"
        if not self.is_available:
            return "skipped:content_fetch_unavailable"
        return None

    def _allowed_fetch_ids(
        self,
        evidence: list[Evidence],
        *,
        enabled: bool,
        max_per_competitor_dimension: int | None,
    ) -> set[str] | None:
        if max_per_competitor_dimension is None or max_per_competitor_dimension <= 0:
            return None
        grouped: dict[tuple[str | None, str | None], list[Evidence]] = {}
        for item in evidence:
            if self._skip_reason(item, 0, enabled):
                continue
            signals = item.entity_match_signals or {}
            key = (item.competitor, signals.get("collector_dimension"))
            grouped.setdefault(key, []).append(item)

        allowed: set[str] = set()
        for items in grouped.values():
            required = [
                item
                for item in items
                if (item.entity_match_signals or {}).get("content_fetch_priority") == "required"
            ]
            allowed.update(item.evidence_id for item in required)
            ranked = sorted(
                [
                    item
                    for item in items
                    if item.evidence_id not in allowed
                ],
                key=self._fetch_priority,
                reverse=True,
            )
            allowed.update(item.evidence_id for item in ranked[:max_per_competitor_dimension])
        return allowed

    @staticmethod
    def _fetch_priority(evidence: Evidence) -> tuple[float, float, float, float]:
        relevance_rank = {
            "high": 3.0,
            "medium": 2.0,
            "low": 1.0,
            "unrelated": 0.0,
        }.get(evidence.relevance_level, 0.0)
        quality_rank = {
            "official": 5.0,
            "documentation": 4.0,
            "media": 3.0,
            "review": 2.0,
            "unknown": 1.0,
            "low_quality": 0.0,
        }.get(evidence.source_quality, 0.0)
        return (
            10.0 if (evidence.entity_match_signals or {}).get("content_fetch_priority") == "required" else 0.0,
            relevance_rank,
            quality_rank,
            evidence.relevance_score or 0.0,
            evidence.confidence or 0.0,
        )

    @staticmethod
    def _first_result(payload: dict[str, Any]) -> dict[str, Any] | None:
        results = payload.get("results") or payload.get("data") or []
        if isinstance(results, list) and results and isinstance(results[0], dict):
            return results[0]
        if isinstance(payload, dict) and any(key in payload for key in ("raw_content", "content", "text")):
            return payload
        return None

    @staticmethod
    def _content_from_result(item: dict[str, Any]) -> str:
        value = item.get("raw_content") or item.get("content") or item.get("text") or item.get("markdown") or ""
        return str(value).strip()

    @staticmethod
    def _title_from_result(item: dict[str, Any]) -> str | None:
        value = item.get("title") or item.get("page_title")
        return str(value).strip() if value else None

    def _sanitize_error(self, error: str | None) -> str | None:
        if not error:
            return error
        return error.replace(self.api_key, "***") if self.api_key else error

    @staticmethod
    def _mark_fetch_metadata(evidence: Evidence, *, success: bool, error: str | None) -> Evidence:
        signals = dict(evidence.entity_match_signals or {})
        signals["content_fetcher"] = "EvidenceContentFetcher"
        signals["extract_provider"] = "tavily"
        signals["content_fetch_success"] = success
        if error:
            signals["content_fetch_error"] = error
        return evidence.model_copy(update={"entity_match_signals": signals})
