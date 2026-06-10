import json
from collections.abc import Callable
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from app.schemas import AgentMessage, TraceRecord
from app.services.trace_service import TraceService


class AgentExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        output: dict | None = None,
        fallback_to_mock: bool = False,
    ):
        super().__init__(message)
        self.output = output
        self.fallback_to_mock = fallback_to_mock


class AgentOutputValidationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        output: dict | None = None,
        fallback_to_mock: bool = False,
    ):
        super().__init__(message)
        self.output = output
        self.fallback_to_mock = fallback_to_mock


def _diagnostic_summary(payload: dict | None, default: str) -> str:
    if not isinstance(payload, dict):
        return default
    diagnostics = payload.get("diagnostics", payload)
    if isinstance(diagnostics, dict):
        return json.dumps(diagnostics, ensure_ascii=False)
    return default


def _trace_llm_metadata(payload: dict | None) -> tuple[str, int | None]:
    if not isinstance(payload, dict):
        return "mock-runner-v0", None
    diagnostics = payload.get("diagnostics", payload)
    if not isinstance(diagnostics, dict):
        return "mock-runner-v0", None
    model = str(diagnostics.get("llm_model") or diagnostics.get("model_name") or "mock-runner-v0")
    total_tokens = diagnostics.get("llm_total_tokens")
    return model, int(total_tokens) if isinstance(total_tokens, int) else None


def run_with_trace(
    *,
    trace_service: TraceService,
    task_id: str,
    run_id: str | None = None,
    agent_name: str,
    to_agent: str,
    message_type: str,
    schema_name: str,
    input_summary: str,
    retry_count: int,
    fn: Callable[[], dict],
) -> Any:
    trace_id = f"trace_{uuid4().hex[:12]}"
    start = perf_counter()
    try:
        output = fn()
        payload = output.model_dump(mode="json") if isinstance(output, BaseModel) else output
        AgentMessage(
            trace_id=trace_id,
            task_id=task_id,
            from_agent=agent_name,
            to_agent=to_agent,
            message_type=message_type,
            schema_name=schema_name,
            payload=payload,
        )
        model_name, token_usage = _trace_llm_metadata(payload)
        trace = TraceRecord(
            trace_id=trace_id,
            task_id=task_id,
            run_id=run_id,
            agent_name=agent_name,
            input_summary=input_summary,
            output_summary=_diagnostic_summary(payload, f"已生成 {schema_name}"),
            schema_validation_result="passed",
            model_name=model_name,
            token_usage=token_usage,
            elapsed_time_ms=int((perf_counter() - start) * 1000),
            retry_count=retry_count,
        )
        trace_service.save(trace)
        return output
    except (ValidationError, ValueError, RuntimeError) as exc:
        error_output = getattr(exc, "output", None)
        model_name, token_usage = _trace_llm_metadata(error_output)
        trace = TraceRecord(
            trace_id=trace_id,
            task_id=task_id,
            run_id=run_id,
            agent_name=agent_name,
            input_summary=input_summary,
            output_summary=_diagnostic_summary(error_output, "Agent 输出未通过校验"),
            schema_validation_result="failed",
            model_name=model_name,
            token_usage=token_usage,
            elapsed_time_ms=int((perf_counter() - start) * 1000),
            retry_count=retry_count,
            error_message=str(exc),
        )
        trace_service.save(trace)
        raise AgentExecutionError(
            str(exc),
            output=error_output,
            fallback_to_mock=getattr(exc, "fallback_to_mock", False),
        ) from exc
