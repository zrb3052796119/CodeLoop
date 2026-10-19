"""inspect_image — a read-only vision tool for local images.

The visual-judge building block: one local image plus one question, one
call to a configured OpenAI-compatible vision model, and a bounded text
answer back into the conversation. Everything fails closed: the image must
live inside the workspace (network URLs are rejected outright — no SSRF or
exfiltration channel), the bytes must match a whitelisted image format's
magic numbers (a renamed text file is not an image), and the vision model
must be EXPLICITLY configured — there is deliberately no fallback to the
default text model, which would silently send images to a model that
cannot see them.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from urllib.parse import urlparse

from minicode.agent_budget import (
    AgentBudgetExceeded,
    AgentBudgetReservation,
    record_budgeted_model_call,
)
from minicode.context_manager import estimate_tokens
from minicode.model_call_control import (
    ModelCallDeadlineExceeded,
    bounded_request_timeout,
)
from minicode.pricing import pricing_failure_event_payload, project_model_cost_event
from minicode.run_events import (
    emit_event_safely,
    new_model_operation_id,
    project_model_duration_ms,
)
from minicode.task_deadline import TaskDeadlineExceeded, raise_task_deadline
from minicode.tooling import (
    ToolContext,
    ToolDefinition,
    ToolExecutionAbandoned,
    ToolResult,
)
from minicode.workspace import resolve_tool_path

_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_ANSWER_CHARS = 4000
_MAX_QUESTION_CHARS = 1000
_MAX_OUTPUT_TOKENS = 1024
_PURPOSE = "inspect_image"

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_MAGIC_CHECKS = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def _sniff_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _resolve_vision_config(runtime: dict) -> tuple[str, str, str] | None:
    """Explicit vision model + the sub-agent route's endpoint/key."""
    models = runtime.get("subagentModels")
    model = str(
        runtime.get("visionModel")
        or (models.get("visual") if isinstance(models, dict) else "")
        or ""
    ).strip()
    if not model:
        return None
    base_url = str(runtime.get("subagentBaseUrl") or "").strip().rstrip("/")
    api_key = str(runtime.get("subagentApiKey") or "").strip()
    if not base_url or not api_key:
        return None
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return model, base_url, api_key


def _validate(input_data: dict) -> dict:
    path = input_data.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path is required")
    if path.strip().lower().startswith(("http://", "https://", "ftp://")):
        raise ValueError(
            "inspect_image accepts local workspace images only; network "
            "URLs are rejected"
        )
    question = input_data.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question is required")
    if len(question) > _MAX_QUESTION_CHARS:
        raise ValueError(f"question must be at most {_MAX_QUESTION_CHARS} characters")
    return {"path": path.strip(), "question": question.strip()}


def _extract_error_message(data: bytes, status: int) -> str:
    try:
        document = json.loads(data.decode("utf-8", errors="replace"))
        message = document.get("error", {}).get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    except (ValueError, AttributeError):
        pass
    return f"vision endpoint returned HTTP {status}"


def _checkpoint(context: ToolContext) -> None:
    abandoned = getattr(context, "_tool_abandoned", None)
    if abandoned is not None and abandoned.is_set():
        raise ToolExecutionAbandoned("vision tool execution was abandoned")
    raise_task_deadline(context)


def _usage(payload: object, estimated_input: int, answer: str) -> dict[str, object]:
    raw = payload.get("usage") if isinstance(payload, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    def token_count(value: object) -> int | None:
        return (
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= 1_000_000_000
            else None
        )

    input_tokens = token_count(raw.get("prompt_tokens"))
    output_tokens = token_count(raw.get("completion_tokens"))
    details = raw.get("prompt_tokens_details")
    cached_tokens = (
        token_count(details.get("cached_tokens", 0)) if isinstance(details, dict) else 0
    )
    provider_receipt = (
        input_tokens is not None
        and output_tokens is not None
        and cached_tokens is not None
        and cached_tokens <= input_tokens
    )
    return {
        "source": "provider" if provider_receipt else "estimated",
        "inputTokens": input_tokens
        if provider_receipt
        else max(estimated_input, input_tokens or 0),
        "outputTokens": output_tokens
        if provider_receipt
        else max(_MAX_OUTPUT_TOKENS, output_tokens or 0, estimate_tokens(answer)),
        "cacheReadTokens": cached_tokens if provider_receipt else 0,
        "cacheCreationTokens": 0,
    }


def _account_usage(
    context: ToolContext,
    model: str,
    usage: Mapping[str, object],
    reservation: AgentBudgetReservation | None,
    operation_id: str,
) -> dict[str, object]:
    try:
        cost = project_model_cost_event(
            model=model, usage=usage, operation_id=operation_id
        )
    except BaseException:  # noqa: BLE001 - pricing observation cannot replace usage
        cost = pricing_failure_event_payload(operation_id)
    budget = getattr(context, "_agent_budget", None)
    try:
        record_budgeted_model_call(
            budget, model=model, usage=usage, reservation=reservation, cost_payload=cost
        )
    except BaseException:
        if budget is not None and reservation is not None:
            budget.fail_model_call(reservation, charge_estimate=True)
        raise
    cost["purpose"] = _PURPOSE
    return cost


def _emit(
    context: ToolContext,
    kind: str,
    operation_id: str,
    started: float,
    **payload: object,
) -> None:
    duration = project_model_duration_ms(started, time.monotonic())
    if kind != "model.started" and duration is not None:
        payload["durationMs"] = duration
    emit_event_safely(
        getattr(context, "_event_sink", None),
        kind,
        step=getattr(context, "_step", None),
        payload={"operationId": operation_id, "purpose": _PURPOSE, **payload},
    )


def _account_failure(
    context: ToolContext,
    model: str,
    reservation: AgentBudgetReservation | None,
    operation_id: str,
    started: float,
    estimated_input: int,
    sent: bool,
    failure_kind: str,
) -> None:
    cost = None
    budget = getattr(context, "_agent_budget", None)
    if sent:
        # A transmitted request without a usable receipt may still be billed.
        # Charge the bounded request/output estimate through the same attributed
        # budget path as successful calls and mark the settlement uncertain.
        cost = _account_usage(
            context, model, _usage(None, estimated_input, ""), reservation, operation_id
        )
    elif budget is not None and reservation is not None:
        budget.fail_model_call(reservation)
    _emit(context, "model.failed", operation_id, started, failureKind=failure_kind)
    if cost is not None:
        emit_event_safely(
            getattr(context, "_event_sink", None),
            "model.costed",
            step=getattr(context, "_step", None),
            payload=cost,
        )


def _run(input_data: dict, context) -> ToolResult:
    _checkpoint(context)
    raw_path = str(input_data.get("path", ""))
    # Defense in depth: direct .run() callers bypass the validator, so the
    # scheme gate is enforced here too.
    if raw_path.strip().lower().startswith(("http://", "https://", "ftp://")):
        return ToolResult(
            ok=False,
            output=(
                "error[network_url_rejected]: inspect_image accepts local "
                "workspace images only; network URLs are rejected"
            ),
        )
    runtime = getattr(context, "_runtime", None) or {}
    config = _resolve_vision_config(runtime)
    if config is None:
        models = runtime.get("subagentModels")
        has_visual = isinstance(models, dict) and bool(models.get("visual"))
        return ToolResult(
            ok=False,
            output=(
                "error[vision_model_unconfigured]: inspect_image requires an "
                "explicitly configured vision model (runtime 'visionModel' or "
                "subagentModels.visual) plus HTTPS subagentBaseUrl and "
                "subagentApiKey; there is deliberately no fallback to a "
                f"text model. (visionModel={'set' if runtime.get('visionModel') else 'unset'}, "
                f"subagentModels.visual={'set' if has_visual else 'unset'})"
            ),
        )

    try:
        target = resolve_tool_path(context, input_data["path"], "read")
    except PermissionError as error:
        return ToolResult(ok=False, output=f"error[path_denied]: {error}")
    if target.suffix.lower() not in _ALLOWED_EXTENSIONS:
        return ToolResult(
            ok=False,
            output=(
                "error[unsupported_image_extension]: allowed extensions are "
                + ", ".join(sorted(_ALLOWED_EXTENSIONS))
            ),
        )
    try:
        data = target.read_bytes()
    except OSError as error:
        return ToolResult(ok=False, output=f"error[read_failed]: {error}")
    if len(data) > _MAX_IMAGE_BYTES:
        return ToolResult(
            ok=False,
            output=(
                f"error[image_too_large]: {len(data)} bytes exceeds the "
                f"{_MAX_IMAGE_BYTES}-byte limit"
            ),
        )
    mime = _sniff_mime(data)
    if mime is None:
        return ToolResult(
            ok=False,
            output=(
                "error[not_an_image]: file content does not match any "
                "supported image magic numbers (PNG/JPEG/WebP)"
            ),
        )

    model, base_url, api_key = config
    data_uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    request_body = {
        "model": model,
        "temperature": 0,
        "max_tokens": _MAX_OUTPUT_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {
                        "type": "text",
                        "text": (
                            input_data["question"] + "\nAnswer concisely in plain text."
                        ),
                    },
                ],
            }
        ],
    }
    request = urllib.request.Request(
        url=f"{base_url}/chat/completions",
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    # The text estimator alone ignores image blocks. Reserve the serialized
    # multimodal payload, with a minimum image allowance; provider usage later
    # replaces this conservative estimate rather than adding to it.
    estimated_input = max(1024, estimate_tokens(request.data.decode("utf-8")))
    _checkpoint(context)
    budget = getattr(context, "_agent_budget", None)
    reservation = None
    try:
        configured_timeout = float(os.environ.get("MINICODE_MODEL_TIMEOUT", "60"))
        bounded_request_timeout(
            configured_timeout,
            cancellation_token=getattr(context, "_cancellation_token", None),
            deadline_monotonic=getattr(context, "_task_deadline_monotonic", None),
        )
    except ValueError:
        return ToolResult(
            ok=False,
            output="error[vision_timeout_invalid]: model timeout must be positive and finite",
        )
    try:
        if budget is not None:
            reservation = budget.reserve_model_call(estimated_input)
    except AgentBudgetExceeded as error:
        return ToolResult(
            ok=False, output=f"error[turn_budget_exceeded]: {error.reason}"
        )
    operation_id = new_model_operation_id()
    started = time.monotonic()
    sent = False
    try:
        _emit(context, "model.started", operation_id, started)
        _checkpoint(context)
        timeout = bounded_request_timeout(
            configured_timeout,
            cancellation_token=getattr(context, "_cancellation_token", None),
            deadline_monotonic=getattr(context, "_task_deadline_monotonic", None),
        )
        sent = True
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        _account_failure(
            context,
            model,
            reservation,
            operation_id,
            started,
            estimated_input,
            sent,
            "provider_error",
        )
        _checkpoint(context)
        body = error.read() if hasattr(error, "read") else b""
        return ToolResult(
            ok=False,
            output=(
                f"error[vision_http_{error.code}]: "
                f"{_extract_error_message(body, error.code)}"
            ),
        )
    except (urllib.error.URLError, OSError, ValueError) as error:
        timed_out = isinstance(error, TimeoutError) or (
            isinstance(error, urllib.error.URLError)
            and isinstance(error.reason, TimeoutError)
        )
        failure_kind = (
            "timeout"
            if timed_out
            else "network"
            if isinstance(error, (urllib.error.URLError, OSError))
            else "provider_error"
        )
        _account_failure(
            context,
            model,
            reservation,
            operation_id,
            started,
            estimated_input,
            sent,
            failure_kind,
        )
        _checkpoint(context)
        return ToolResult(ok=False, output=f"error[vision_unreachable]: {error}")
    except BaseException as error:
        _account_failure(
            context,
            model,
            reservation,
            operation_id,
            started,
            estimated_input,
            sent,
            "timeout"
            if isinstance(
                error, (TaskDeadlineExceeded, ModelCallDeadlineExceeded, TimeoutError)
            )
            else "interrupted",
        )
        raise

    choices = payload.get("choices") if isinstance(payload, dict) else None
    message = (
        choices[0].get("message", {})
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else {}
    )
    content = message.get("content") if isinstance(message, dict) else None
    answer = content.strip() if isinstance(content, str) else ""
    usage = _usage(payload, estimated_input, answer)
    cost = _account_usage(context, model, usage, reservation, operation_id)
    _emit(
        context,
        "model.completed",
        operation_id,
        started,
        resultType="assistant",
        contentPresent=bool(answer),
        toolCallCount=0,
        usage=usage,
    )
    emit_event_safely(
        getattr(context, "_event_sink", None),
        "model.costed",
        step=getattr(context, "_step", None),
        payload=cost,
    )
    # Account the already transmitted request even if its owner stopped while
    # HTTP was in flight. The late answer must not continue the abandoned tool.
    _checkpoint(context)
    if not answer:
        return ToolResult(
            ok=False,
            output="error[vision_empty_answer]: the vision model returned no text",
        )
    bounded = answer[:_MAX_ANSWER_CHARS]
    if len(answer) > _MAX_ANSWER_CHARS:
        bounded += f"\n…(truncated, {len(answer)} chars total)"
    raw_usage = payload.get("usage") if isinstance(payload, dict) else None
    tokens = raw_usage.get("total_tokens") if isinstance(raw_usage, dict) else None
    token_line = f"\n\n(vision tokens: {tokens})" if tokens is not None else ""
    return ToolResult(ok=True, output=bounded + token_line)


inspect_image_tool = ToolDefinition(
    name="inspect_image",
    description=(
        "Inspect one local image (PNG/JPEG/WebP) with the configured vision "
        "model and return a concise text answer to a question about it. "
        "Local workspace images only; network URLs are rejected. Use for "
        "visual acceptance of rendered artifacts (charts, dashboard pages, "
        "exported documents)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Workspace-relative (or absolute) path to a local image.",
            },
            "question": {
                "type": "string",
                "maxLength": _MAX_QUESTION_CHARS,
                "description": "One focused question about the image.",
            },
        },
        "required": ["path", "question"],
    },
    validator=_validate,
    run=_run,
)
