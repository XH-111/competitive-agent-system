import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from app.config.survey_llm_config import get_survey_llm_config
from app.services.llm_client import parse_llm_json


class SurveyLLMConfigurationError(RuntimeError):
    pass


@dataclass
class SurveyLLMResult:
    data: dict[str, Any]
    elapsed_time_ms: int
    model: str


class SurveyLLMClient:
    def __init__(self) -> None:
        self.config = get_survey_llm_config()

    @property
    def is_available(self) -> bool:
        return bool(self.config.api_key)

    def status(self) -> dict[str, Any]:
        return {
            "survey_llm_provider": self.config.provider,
            "survey_llm_model": self.config.model,
            "base_url_configured": bool(self.config.base_url),
            "api_key_configured": bool(self.config.api_key),
            "survey_llm_enabled": self.is_available,
            "timeout_seconds": self.config.timeout_seconds,
        }

    def generate_json(self, system_prompt: str, user_prompt: str) -> SurveyLLMResult:
        if not self.config.api_key:
            raise SurveyLLMConfigurationError("SURVEY_LLM_API_KEY is not configured")

        start = perf_counter()
        try:
            response = httpx.post(
                f"{self.config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.model,
                    "temperature": self.config.temperature,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return SurveyLLMResult(
                data=parse_llm_json(content),
                elapsed_time_ms=int((perf_counter() - start) * 1000),
                model=self.config.model,
            )
        except json.JSONDecodeError as exc:
            raise ValueError("Survey LLM returned invalid JSON") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Survey LLM call failed: {_sanitize_error(str(exc), self.config.api_key)}") from exc


def _sanitize_error(message: str, api_key: str) -> str:
    return message.replace(api_key, "***") if api_key else message
