from __future__ import annotations

from dataclasses import dataclass
from email.message import Message
import html
import re
import time

from minicode.tooling import ToolDefinition, ToolResult
from minicode.tools.http_utils import execute_safe_get
from minicode.tools.network_safety import (
    NetworkSafetyError,
    NormalizedHttpRequest,
    normalize_http_request,
)

MAX_CONTENT_LENGTH = 50000
_FETCH_TIMEOUT_SECONDS = 30.0
_FETCH_HEADERS = {
    "User-Agent": "MiniCode-Python/0.5.0 (Terminal Coding Assistant)",
    "Accept": "text/html,application/json,text/plain;q=0.9",
    "Accept-Encoding": "identity",
}


@dataclass(frozen=True, slots=True)
class NormalizedWebFetchRequest:
    request: NormalizedHttpRequest
    max_chars: int


def _validate(input_data: object) -> NormalizedWebFetchRequest:
    if not isinstance(input_data, dict) or not set(input_data) <= {
        "url",
        "max_chars",
    }:
        raise NetworkSafetyError("invalid_request")
    max_chars = input_data.get("max_chars", 10_000)
    if (
        isinstance(max_chars, bool)
        or not isinstance(max_chars, int)
        or not 100 <= max_chars <= MAX_CONTENT_LENGTH
    ):
        raise NetworkSafetyError("invalid_request")
    request = normalize_http_request(
        {
            "url": input_data.get("url"),
            "method": "GET",
            "headers": _FETCH_HEADERS,
            "timeout": _FETCH_TIMEOUT_SECONDS,
        }
    )
    return NormalizedWebFetchRequest(request=request, max_chars=max_chars)


def _run(input_data: NormalizedWebFetchRequest, context) -> ToolResult:
    max_chars = input_data.max_chars
    deadline = time.monotonic() + input_data.request.timeout

    try:
        response = execute_safe_get(
            input_data.request,
            deadline=deadline,
        )
    except NetworkSafetyError as error:
        return ToolResult(ok=False, output=error.tool_output())

    try:
        if response.content_encoding not in {"", "identity"}:
            raise NetworkSafetyError("unsupported_response_type")
        content_type = response.content_type
        parsed_content_type = Message()
        parsed_content_type["Content-Type"] = content_type
        media_type = parsed_content_type.get_content_type().casefold()
        is_json = media_type == "application/json" or media_type.endswith("+json")
        if not media_type.startswith("text/") and not is_json:
            raise NetworkSafetyError("unsupported_response_type")
        charset = parsed_content_type.get_content_charset("utf-8") or "utf-8"
        try:
            text = response.payload.decode(charset, errors="replace")
        except LookupError:
            text = response.payload.decode("utf-8", errors="replace")
        if media_type == "text/html":
            text = _extract_text_from_html(text)
        rendered_chars = len(text)
        truncated = rendered_chars > max_chars
        if truncated:
            text = (
                text[:max_chars]
                + f"\n\n... [Content truncated at {max_chars} chars]"
            )
        header = "\n".join(
            [
                f"STATUS: {response.status}",
                f"CONTENT_TYPE: {media_type}",
                f"WIRE_BYTES: {len(response.payload)}",
                f"RENDERED_CHARS: {rendered_chars}",
                f"TRUNCATED: {'yes' if truncated else 'no'}",
                "",
            ]
        )
        return ToolResult(ok=True, output=header + text)
    except NetworkSafetyError as error:
        return ToolResult(ok=False, output=error.tool_output())
    except Exception:
        return ToolResult(
            ok=False,
            output=NetworkSafetyError("request_failed").tool_output(),
        )


def _extract_text_from_html(html_text: str) -> str:
    """Extract readable text from HTML content."""
    # Remove script and style elements
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        "",
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Remove all tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode HTML entities
    text = html.unescape(text)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)
    text = text.strip()

    return text


web_fetch_tool = ToolDefinition(
    name="web_fetch",
    description="Fetch content from a URL. Supports HTML (extracted to text), JSON, and plain text. Useful for reading documentation, APIs, or web content.",
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch content from"},
            "max_chars": {
                "type": "integer",
                "minimum": 100,
                "maximum": MAX_CONTENT_LENGTH,
                "description": "Maximum characters to return (default: 10000)",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    validator=_validate,
    run=_run,
)
