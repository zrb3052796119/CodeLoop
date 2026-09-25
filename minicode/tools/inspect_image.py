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
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from minicode.tooling import ToolDefinition, ToolResult
from minicode.workspace import resolve_tool_path

_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_ANSWER_CHARS = 4000
_MAX_QUESTION_CHARS = 1000

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
        raise ValueError(
            f"question must be at most {_MAX_QUESTION_CHARS} characters"
        )
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


def _run(input_data: dict, context) -> ToolResult:
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
    data_uri = (
        f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    )
    request_body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {
                        "type": "text",
                        "text": (
                            input_data["question"]
                            + "\nAnswer concisely in plain text."
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
    timeout = float(os.environ.get("MINICODE_MODEL_TIMEOUT", "60"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read() if hasattr(error, "read") else b""
        return ToolResult(
            ok=False,
            output=(
                f"error[vision_http_{error.code}]: "
                f"{_extract_error_message(body, error.code)}"
            ),
        )
    except (urllib.error.URLError, OSError, ValueError) as error:
        return ToolResult(ok=False, output=f"error[vision_unreachable]: {error}")

    choices = payload.get("choices") if isinstance(payload, dict) else None
    message = (
        choices[0].get("message", {}) if isinstance(choices, list) and choices else {}
    )
    answer = str(message.get("content") or "").strip()
    if not answer:
        return ToolResult(
            ok=False,
            output="error[vision_empty_answer]: the vision model returned no text",
        )
    bounded = answer[:_MAX_ANSWER_CHARS]
    if len(answer) > _MAX_ANSWER_CHARS:
        bounded += f"\n…(truncated, {len(answer)} chars total)"
    usage = payload.get("usage") if isinstance(payload, dict) else None
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
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
