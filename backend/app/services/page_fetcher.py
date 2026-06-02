import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.schemas import Evidence


DEFAULT_USER_AGENT = "cis-agent-demo-page-fetcher/0.1 (+public evidence validation)"
REPLACEMENT_CHAR = "\ufffd"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class PageFetchResult:
    success: bool
    status_code: int | None = None
    page_title: str | None = None
    content_excerpt: str | None = None
    content_chars: int | None = None
    error: str | None = None
    fetched_at: datetime | None = None


class PageFetcher:
    """Lightweight, compliant page excerpt fetcher for relevant public Evidence."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        timeout: int | None = None,
        max_bytes: int | None = None,
        content_max_chars: int | None = None,
        excerpt_max_chars: int | None = None,
        max_per_competitor: int | None = None,
        max_per_run: int | None = None,
        respect_robots: bool | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.provider = provider or os.getenv("PAGE_FETCH_PROVIDER", "local")
        self.timeout = timeout or _env_int("PAGE_FETCH_TIMEOUT", 10)
        self.max_bytes = max_bytes or _env_int("PAGE_FETCH_MAX_BYTES", 500000)
        self.content_max_chars = content_max_chars or _env_int("PAGE_CONTENT_MAX_CHARS", 3000)
        self.excerpt_max_chars = excerpt_max_chars or _env_int("PAGE_EXCERPT_MAX_CHARS", 1000)
        self.max_per_competitor = max_per_competitor or _env_int("PAGE_FETCH_MAX_PER_COMPETITOR", 2)
        self.max_per_run = max_per_run or _env_int("PAGE_FETCH_MAX_PER_RUN", 10)
        self.respect_robots = _env_bool("PAGE_FETCH_RESPECT_ROBOTS", True) if respect_robots is None else respect_robots
        self.transport = transport

    def enrich(self, evidence: list[Evidence], *, run_id: str | None = None, enabled: bool = True) -> tuple[list[Evidence], dict]:
        attempted = 0
        success_count = 0
        failed_count = 0
        skipped_count = 0
        fallback_count = 0
        content_lengths: list[int] = []
        fetched_ids: list[str] = []
        skipped_ids: list[str] = []
        error_counter: Counter[str] = Counter()
        fetched_by_competitor: defaultdict[str, int] = defaultdict(int)
        enriched: list[Evidence] = []

        for item in evidence:
            updated = item.model_copy(update={"run_id": run_id or item.run_id})
            skip_reason = "skipped:content_mode_snippet" if not enabled else self._skip_reason(updated, fetched_by_competitor, attempted)
            if skip_reason:
                skipped_count += 1
                skipped_ids.append(updated.evidence_id)
                enriched.append(
                    updated.model_copy(
                        update={
                            "content_mode": updated.content_mode or "snippet",
                            "page_fetch_success": False,
                            "page_fetch_error": skip_reason,
                        }
                    )
                )
                continue

            attempted += 1
            fetched_by_competitor[updated.competitor or "__unknown__"] += 1
            result = self.fetch(updated.url or "", run_id=run_id, evidence_id=updated.evidence_id, competitor=updated.competitor)
            if result.success:
                success_count += 1
                fetched_ids.append(updated.evidence_id)
                content_lengths.append(result.content_chars or 0)
                enriched.append(
                    updated.model_copy(
                        update={
                            "content_mode": "page",
                            "page_fetch_success": True,
                            "page_title": result.page_title,
                            "content_excerpt": result.content_excerpt,
                            "content_chars": result.content_chars,
                            "fetch_status_code": result.status_code,
                            "page_fetch_error": None,
                            "fetched_at": result.fetched_at,
                        }
                    )
                )
            else:
                failed_count += 1
                fallback_count += 1
                error_counter[result.error or "unknown_error"] += 1
                enriched.append(
                    updated.model_copy(
                        update={
                            "content_mode": "snippet",
                            "page_fetch_success": False,
                            "fetch_status_code": result.status_code,
                            "page_fetch_error": result.error,
                            "fetched_at": result.fetched_at,
                        }
                    )
                )

        diagnostics = {
            "page_fetch_provider": self.provider,
            "page_fetch_attempted": attempted > 0,
            "page_fetch_attempt_count": attempted,
            "page_fetch_success_count": success_count,
            "page_fetch_failed_count": failed_count,
            "page_fetch_skipped_count": skipped_count,
            "page_fetch_fallback_count": fallback_count,
            "page_fetch_error_summary": dict(error_counter),
            "avg_content_chars": int(sum(content_lengths) / len(content_lengths)) if content_lengths else 0,
            "max_content_chars": max(content_lengths) if content_lengths else 0,
            "fetched_evidence_ids": fetched_ids,
            "skipped_evidence_ids": skipped_ids,
            "run_id": run_id,
        }
        return enriched, diagnostics

    def fetch(self, url: str, *, run_id: str | None, evidence_id: str, competitor: str | None) -> PageFetchResult:
        fetched_at = datetime.utcnow()
        if not url:
            return PageFetchResult(success=False, error="missing_url", fetched_at=fetched_at)
        if self.provider != "local":
            return PageFetchResult(success=False, error=f"unsupported_page_fetch_provider:{self.provider}", fetched_at=fetched_at)
        try:
            headers = {"User-Agent": DEFAULT_USER_AGENT}
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers=headers,
                transport=self.transport,
            ) as client:
                if self.respect_robots:
                    allowed, robots_error = self._robots_allowed(client, url)
                    if not allowed:
                        return PageFetchResult(success=False, error=robots_error or "robots_disallowed", fetched_at=fetched_at)
                with client.stream("GET", url) as response:
                    status_code = response.status_code
                    content_type = response.headers.get("content-type", "").lower()
                    if status_code != 200:
                        return PageFetchResult(success=False, status_code=status_code, error=f"http_status_{status_code}", fetched_at=fetched_at)
                    if "text/html" not in content_type:
                        return PageFetchResult(success=False, status_code=status_code, error="non_html_content", fetched_at=fetched_at)
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > self.max_bytes:
                            return PageFetchResult(success=False, status_code=status_code, error="content_too_large", fetched_at=fetched_at)
                    html = self._decode_html(bytes(chunks), response.encoding, content_type)
        except httpx.TimeoutException:
            return PageFetchResult(success=False, error="timeout", fetched_at=fetched_at)
        except httpx.HTTPError as exc:
            return PageFetchResult(success=False, error=f"http_error:{type(exc).__name__}", fetched_at=fetched_at)
        except Exception as exc:  # pragma: no cover - defensive guard for network/parser edge cases.
            return PageFetchResult(success=False, error=f"unexpected_error:{type(exc).__name__}", fetched_at=fetched_at)

        title, plain_text = self._extract_text(html)
        if not plain_text:
            return PageFetchResult(success=False, status_code=200, error="empty_extracted_text", fetched_at=fetched_at)
        trimmed = plain_text[: self.content_max_chars]
        excerpt = trimmed[: self.excerpt_max_chars]
        return PageFetchResult(
            success=True,
            status_code=200,
            page_title=title,
            content_excerpt=excerpt,
            content_chars=len(trimmed),
            fetched_at=fetched_at,
        )

    def _skip_reason(self, evidence: Evidence, fetched_by_competitor: dict[str, int], attempted_count: int) -> str | None:
        if not evidence.url:
            return "skipped:missing_url"
        if evidence.relevance_level not in {"high", "medium"}:
            return f"skipped:relevance_{evidence.relevance_level}"
        if evidence.source_quality == "low_quality":
            return "skipped:low_quality_source"
        if attempted_count >= self.max_per_run:
            return "skipped:max_per_run"
        competitor_key = evidence.competitor or "__unknown__"
        if fetched_by_competitor[competitor_key] >= self.max_per_competitor:
            return "skipped:max_per_competitor"
        return None

    def _robots_allowed(self, client: httpx.Client, url: str) -> tuple[bool, str | None]:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False, "invalid_url"
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            response = client.get(robots_url)
        except httpx.TimeoutException:
            return False, "robots_check_timeout"
        except httpx.HTTPError:
            return False, "robots_check_failed"
        if response.status_code in {401, 403}:
            return False, f"robots_status_{response.status_code}"
        if response.status_code >= 400:
            return True, None
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        return parser.can_fetch(DEFAULT_USER_AGENT, url), "robots_disallowed"

    @staticmethod
    def _decode_html(raw: bytes, response_encoding: str | None, content_type: str) -> str:
        candidates: list[str] = []
        for encoding in (
            response_encoding,
            PageFetcher._charset_from_content_type(content_type),
            PageFetcher._charset_from_meta(raw),
            "utf-8",
            "gb18030",
            "gbk",
            "big5",
        ):
            if encoding and encoding.lower() not in {item.lower() for item in candidates}:
                candidates.append(encoding)

        best_text = raw.decode("utf-8", errors="replace")
        best_score = PageFetcher._decode_quality_score(best_text)
        for encoding in candidates:
            try:
                text = raw.decode(encoding, errors="replace")
            except LookupError:
                continue
            score = PageFetcher._decode_quality_score(text)
            if score < best_score:
                best_text = text
                best_score = score
        return best_text

    @staticmethod
    def _charset_from_content_type(content_type: str) -> str | None:
        match = re.search(r"charset\s*=\s*['\"]?([^;'\"]+)", content_type, flags=re.IGNORECASE)
        return match.group(1).strip() if match else None

    @staticmethod
    def _charset_from_meta(raw: bytes) -> str | None:
        head = raw[:4096].decode("ascii", errors="ignore")
        match = re.search(r"<meta[^>]+charset\s*=\s*['\"]?\s*([a-zA-Z0-9_\-]+)", head, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match = re.search(r"content\s*=\s*['\"][^'\"]*charset\s*=\s*([a-zA-Z0-9_\-]+)", head, flags=re.IGNORECASE)
        return match.group(1).strip() if match else None

    @staticmethod
    def _decode_quality_score(text: str) -> int:
        replacement_penalty = text.count(REPLACEMENT_CHAR) * 10
        mojibake_penalty = sum(text.count(marker) * 3 for marker in ("�", "锟", "Ã", "Â"))
        return replacement_penalty + mojibake_penalty

    @staticmethod
    def _extract_text(html: str) -> tuple[str | None, str]:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else None
        parts: list[str] = []
        for tag in soup.find_all(["title", "h1", "h2", "h3", "p", "li"]):
            text = tag.get_text(" ", strip=True)
            if text:
                parts.append(text)
        text = "\n".join(parts)
        text = re.sub(r"\s+", " ", text).strip()
        return title, text
