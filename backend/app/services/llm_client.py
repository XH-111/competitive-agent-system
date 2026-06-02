import json
import os
from time import perf_counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / "backend" / ".env")


@dataclass
class LlmResponse:
    available: bool
    content: str | None = None
    fallback_reason: str | None = None
    provider: str = "openai_compatible"
    model: str = "mock"
    attempted: bool = False
    success: bool = False
    elapsed_time_ms: int = 0
    error_type: str | None = None
    error_message: str | None = None
    response_preview: str | None = None


class LlmClient:
    def __init__(self) -> None:
        self.provider = os.getenv("LLM_PROVIDER", "openai_compatible")
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
        self.model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.timeout = self._float_env("LLM_TIMEOUT", 120.0)

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    def status(self, last_check_status: str = "not_checked", last_error: str | None = None) -> dict:
        return {
            "llm_provider": self.provider,
            "llm_model": self.model,
            "base_url_configured": bool(self.base_url),
            "api_key_configured": bool(self.api_key),
            "llm_enabled": self.is_available,
            "llm_timeout_seconds": self.timeout,
            "last_check_status": last_check_status,
            "last_error": self._sanitize_error(last_error),
            "suggested_action": self._suggested_action(last_check_status, last_error),
        }

    def test_connection(self) -> dict:
        response = self.chat_json(
            [{"role": "user", "content": 'Return JSON only: {"ok": true}'}],
            timeout=15.0,
        )
        status = "success" if response.success else "failed"
        result = self.status(status, response.error_message or response.fallback_reason)
        result["llm_elapsed_time_ms"] = response.elapsed_time_ms
        result["llm_response_preview"] = response.response_preview
        return result

    def chat_json(self, messages: list[dict[str, str]], timeout: float | None = None) -> LlmResponse:
        if not self.api_key:
            return LlmResponse(
                available=False,
                fallback_reason="LLM_API_KEY is not configured; fallback to mock ReportWriter.",
                provider=self.provider,
                model=self.model,
                error_type="missing_api_key",
                error_message="LLM_API_KEY is not configured.",
            )

        start = perf_counter()
        try:
            effective_timeout = timeout if timeout is not None else self.timeout
            request_json = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.2,
            }
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=request_json,
                timeout=effective_timeout,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            content = payload["choices"][0]["message"]["content"]
            elapsed = int((perf_counter() - start) * 1000)
            return LlmResponse(
                available=True,
                content=content,
                provider=self.provider,
                model=self.model,
                attempted=True,
                success=True,
                elapsed_time_ms=elapsed,
                response_preview=content[:300],
            )
        except Exception as exc:  # noqa: BLE001 - workflow must not crash on provider errors.
            elapsed = int((perf_counter() - start) * 1000)
            error_message = self._sanitize_error(str(exc))
            if isinstance(exc, httpx.HTTPStatusError):
                detail = self._sanitize_error(exc.response.text[:600])
                error_message = f"{error_message}; response_body={detail}"
            return LlmResponse(
                available=False,
                fallback_reason=f"LLM call failed: {exc}",
                provider=self.provider,
                model=self.model,
                attempted=True,
                success=False,
                elapsed_time_ms=elapsed,
                error_type=exc.__class__.__name__,
                error_message=error_message,
            )

    @staticmethod
    def _sanitize_error(error: str | None) -> str | None:
        if not error:
            return error
        return error.replace(os.getenv("LLM_API_KEY", ""), "***") if os.getenv("LLM_API_KEY") else error

    @staticmethod
    def _float_env(name: str, default: float) -> float:
        value = os.getenv(name)
        if not value:
            return default
        try:
            parsed = float(value)
        except ValueError:
            return default
        return parsed if parsed > 0 else default

    def _suggested_action(self, status: str, error: str | None) -> str:
        if not self.api_key:
            return "Set LLM_API_KEY in .env or use Mock ReportWriter."
        if status == "success":
            return "LLM configuration is usable."
        if error:
            return "Check LLM_BASE_URL, LLM_MODEL, API key permissions, and network connectivity."
        return "Click test button to verify the current LLM configuration."


def parse_llm_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"} and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1]).strip()
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("LLM output must be a JSON object.")
    return payload
