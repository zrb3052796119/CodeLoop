from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone, timedelta

from minicode.tooling import ToolDefinition, ToolContext, ToolResult


# ---------------------------------------------------------------------------
# Current Time
# ---------------------------------------------------------------------------

# Precompute timezone offsets for fast lookup
_TIMEZONE_OFFSETS: dict[str, int] = {
    "EST": -5, "EDT": -4, "CST": -6, "CDT": -5,
    "PST": -8, "PDT": -7, "JST": 9, "CET": 1, "CEST": 2,
    "GMT": 0, "UTC": 0,
}

# Precompute format strings for fast lookup
_TIME_FORMATS: dict[str, str] = {
    "iso": "iso",
    "unix": "unix",
    "date": "%Y-%m-%d",
    "time": "%H:%M:%S",
    "full": "%Y-%m-%d %H:%M:%S %Z",
}


def _run_current_time(input_data: dict, context: ToolContext) -> ToolResult:
    """Get current time in various formats."""
    tz_name = input_data.get("timezone", "UTC")
    format_str = input_data.get("format", "iso")

    try:
        # Get timezone
        if tz_name == "local":
            now = datetime.now()
        elif tz_name == "UTC":
            now = datetime.now(timezone.utc)
        else:
            offset_hours = _TIMEZONE_OFFSETS.get(tz_name.upper(), 0)
            now = datetime.now(timezone.utc) + timedelta(hours=offset_hours)

    except Exception:
        now = datetime.now()

    # Format output using precomputed formats
    fmt = _TIME_FORMATS.get(format_str, "iso")
    if fmt == "iso":
        output = now.isoformat()
    elif fmt == "unix":
        output = str(int(now.timestamp()))
    else:
        output = now.strftime(fmt)

    return ToolResult(ok=True, output=output)


current_time_tool = ToolDefinition(
    name="current_time",
    description="Get current time in various formats and timezones.",
    input_schema={
        "type": "object",
        "properties": {
            "timezone": {"type": "string", "description": "Timezone: local, UTC, EST, CST, PST, JST, etc."},
            "format": {"type": "string", "description": "Format: iso, unix, date, time, full"}
        }
    },
    validator=lambda x: x,
    run=_run_current_time,
)


# ---------------------------------------------------------------------------
# Timestamp Convert
# ---------------------------------------------------------------------------

def _validate_timestamp(input_data: dict) -> dict:
    value = input_data.get("value", "")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value is required")
    return {"value": value.strip(), "direction": input_data.get("direction", "to_iso")}


def _run_timestamp(input_data: dict, context: ToolContext) -> ToolResult:
    value = input_data["value"]
    direction = input_data.get("direction", "to_iso")
    
    try:
        if direction == "to_iso":
            # Unix timestamp -> ISO
            ts = int(value)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            output = dt.isoformat()
        else:
            # ISO -> Unix timestamp
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            output = str(int(dt.timestamp()))
        
        return ToolResult(ok=True, output=output)
    except Exception as e:
        return ToolResult(ok=False, output=f"Conversion error: {e}")


timestamp_tool = ToolDefinition(
    name="timestamp_convert",
    description="Convert between Unix timestamp and ISO date format.",
    input_schema={
        "type": "object",
        "properties": {
            "value": {"type": "string", "description": "Timestamp (int) or ISO date (str)"},
            "direction": {"type": "string", "description": "to_iso or to_timestamp"}
        },
        "required": ["value"]
    },
    validator=_validate_timestamp,
    run=_run_timestamp,
)


# ---------------------------------------------------------------------------
# Hash (MD5, SHA)
# ---------------------------------------------------------------------------

def _validate_hash(input_data: dict) -> dict:
    text = input_data.get("text", "")
    algorithm = input_data.get("algorithm", "sha256").lower()
    if not isinstance(text, str):
        raise ValueError("text is required and must be a string")
    if algorithm not in {"md5", "sha1", "sha256", "sha512"}:
        raise ValueError("algorithm must be: md5, sha1, sha256, or sha512")
    return {"text": text, "algorithm": algorithm, "hex": input_data.get("hex", True)}


def _run_hash(input_data: dict, context: ToolContext) -> ToolResult:
    text = input_data["text"]
    algorithm = input_data["algorithm"]
    as_hex = input_data.get("hex", True)
    
    hash_obj = hashlib.new(algorithm)
    hash_obj.update(text.encode("utf-8"))
    
    if as_hex:
        output = hash_obj.hexdigest()
    else:
        output = hash_obj.digest().hex()
    
    return ToolResult(ok=True, output=output)


hash_tool = ToolDefinition(
    name="hash",
    description="Calculate hash (MD5, SHA1, SHA256, SHA512) of text.",
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to hash"},
            "algorithm": {"type": "string", "description": "md5, sha1, sha256, sha512 (default: sha256)"},
            "hex": {"type": "boolean", "description": "Return hex string (default: true)"}
        },
        "required": ["text"]
    },
    validator=_validate_hash,
    run=_run_hash,
)


# ---------------------------------------------------------------------------
# HMAC
# ---------------------------------------------------------------------------

def _validate_hmac(input_data: dict) -> dict:
    text = input_data.get("text", "")
    key = input_data.get("key", "")
    algorithm = input_data.get("algorithm", "sha256").lower()
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is required")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("key is required")
    if algorithm not in {"md5", "sha1", "sha256", "sha512"}:
        raise ValueError("algorithm must be: md5, sha1, sha256, or sha512")
    return {"text": text, "key": key, "algorithm": algorithm}


def _run_hmac(input_data: dict, context: ToolContext) -> ToolResult:
    text = input_data["text"]
    key = input_data["key"]
    algorithm = input_data["algorithm"]
    
    h = hmac.new(key.encode("utf-8"), text.encode("utf-8"), algorithm)
    output = h.hexdigest()
    
    return ToolResult(ok=True, output=output)


hmac_tool = ToolDefinition(
    name="hmac",
    description="Calculate HMAC (keyed-hash message authentication code).",
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Message to authenticate"},
            "key": {"type": "string", "description": "Secret key"},
            "algorithm": {"type": "string", "description": "md5, sha1, sha256, sha512 (default: sha256)"}
        },
        "required": ["text", "key"]
    },
    validator=_validate_hmac,
    run=_run_hmac,
)