"""inspect_image: read-only vision tool with fail-closed routing."""

from __future__ import annotations

import base64
import io
import json
import urllib.error

import pytest

from minicode.tools.inspect_image import inspect_image_tool
from minicode.tooling import ToolContext

# Minimal valid 1x1 white PNG (67 bytes).
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4"
    "/z8AAwMBAAJDAXQAAAAASUVORK5CYII="
)

_RUNTIME = {
    "model": "fake-parent",
    "visionModel": "qwen-vl-max",
    "subagentBaseUrl": "https://dashscope.example.com/compatible-mode/v1",
    "subagentApiKey": "sk-test",
}


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _ok_payload(text: str = "A white 1x1 pixel.") -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"total_tokens": 42},
    }


def _capture(captured, payload=None, error=None):
    def fake(request, timeout=60):
        captured.append(
            {
                "url": request.full_url,
                "headers": dict(request.headers),
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        if error is not None:
            raise error
        return _Response(payload or _ok_payload())

    return fake


def _context(tmp_path, runtime=None) -> ToolContext:
    return ToolContext(cwd=str(tmp_path), _runtime=runtime or _RUNTIME)


def _write_png(tmp_path, name="pixel.png", data=_PNG_BYTES):
    target = tmp_path / name
    target.write_bytes(data)
    return target


def test_rejects_non_image_extension(tmp_path, monkeypatch):
    target = tmp_path / "notes.txt"
    target.write_bytes(_PNG_BYTES)
    result = inspect_image_tool.run(
        {"path": "notes.txt", "question": "what is this?"},
        _context(tmp_path),
    )
    assert not result.ok
    assert "extension" in result.output


def test_rejects_disguised_image(tmp_path):
    target = _write_png(tmp_path, "fake.png", b"this is plain text, not an image")
    result = inspect_image_tool.run(
        {"path": "fake.png", "question": "what?"},
        _context(tmp_path),
    )
    assert not result.ok
    assert "magic" in result.output or "image" in result.output


def test_rejects_network_url(tmp_path):
    result = inspect_image_tool.run(
        {"path": "https://evil.example.com/x.png", "question": "what?"},
        _context(tmp_path),
    )
    assert not result.ok
    assert "network_url_rejected" in result.output or "local" in result.output


def test_rejects_oversized_file(tmp_path, monkeypatch):
    import minicode.tools.inspect_image as module

    target = _write_png(tmp_path, "big.png")
    monkeypatch.setattr(module, "_MAX_IMAGE_BYTES", 10)
    result = inspect_image_tool.run(
        {"path": "big.png", "question": "what?"},
        _context(tmp_path),
    )
    assert not result.ok
    assert "too_large" in result.output


def test_missing_vision_model_fails_closed(tmp_path):
    runtime = dict(_RUNTIME)
    runtime.pop("visionModel")
    _write_png(tmp_path)
    result = inspect_image_tool.run(
        {"path": "pixel.png", "question": "what?"},
        _context(tmp_path, runtime),
    )
    assert not result.ok
    assert "vision_model_unconfigured" in result.output


def test_visual_key_in_subagent_models_suffices(tmp_path, monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(
        "urllib.request.urlopen", _capture(captured, payload=_ok_payload("ok"))
    )
    runtime = {
        "subagentModels": {"visual": "qwen-vl-plus"},
        "subagentBaseUrl": "https://dashscope.example.com/v1",
        "subagentApiKey": "sk-test",
    }
    _write_png(tmp_path)
    result = inspect_image_tool.run(
        {"path": "pixel.png", "question": "describe"},
        _context(tmp_path, runtime),
    )
    assert result.ok, result.output
    assert captured[0]["body"]["model"] == "qwen-vl-plus"


def test_missing_credentials_fail_closed(tmp_path):
    _write_png(tmp_path)
    for runtime in (
        {"visionModel": "m"},
        {"visionModel": "m", "subagentBaseUrl": "https://x.example.com"},
        {
            "visionModel": "m",
            "subagentBaseUrl": "http://insecure.example.com",
            "subagentApiKey": "k",
        },
    ):
        result = inspect_image_tool.run(
            {"path": "pixel.png", "question": "what?"},
            _context(tmp_path, runtime),
        )
        assert not result.ok, runtime


def test_request_shape_and_bounded_answer(tmp_path, monkeypatch):
    captured: list[dict] = []
    long_text = "x" * 9000
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _capture(captured, payload=_ok_payload(long_text)),
    )
    _write_png(tmp_path)
    result = inspect_image_tool.run(
        {"path": "pixel.png", "question": "describe precisely"},
        _context(tmp_path),
    )
    assert result.ok, result.output
    body = captured[0]
    assert body["url"].endswith("/chat/completions")
    headers = {key.lower(): value for key, value in captured[0]["headers"].items()}
    assert headers.get("authorization") == "Bearer sk-test"
    content = body["body"]["messages"][0]["content"]
    image_block = next(b for b in content if b["type"] == "image_url")
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(
        image_block["image_url"]["url"].split(",", 1)[1]
    ) == _PNG_BYTES
    text_block = next(b for b in content if b["type"] == "text")
    assert "describe precisely" in text_block["text"]
    assert body["body"]["temperature"] == 0
    assert len(result.output) < 5000
    assert "tokens" in result.output


def test_http_error_surfaces_cleanly(tmp_path, monkeypatch):
    error = urllib.error.HTTPError(
        url="https://dashscope.example.com/v1/chat/completions",
        code=500,
        msg="Server Error",
        hdrs=None,
        fp=io.BytesIO(b'{"error": {"message": "boom"}}'),
    )
    monkeypatch.setattr(
        "urllib.request.urlopen", _capture([], error=error)
    )
    _write_png(tmp_path)
    result = inspect_image_tool.run(
        {"path": "pixel.png", "question": "what?"},
        _context(tmp_path),
    )
    assert not result.ok
    assert "boom" in result.output or "500" in result.output


# ---------------------------------------------------------------------------
# Task 2: whitelist, registration, and the visual-judge profile
# ---------------------------------------------------------------------------


def test_read_only_whitelist_includes_inspect_image():
    from minicode.subagent_types import READ_ONLY_TOOL_WHITELIST

    assert "inspect_image" in READ_ONLY_TOOL_WHITELIST


def test_core_registry_exposes_inspect_image(tmp_path):
    from minicode.tools import create_default_tool_registry

    registry = create_default_tool_registry(str(tmp_path), include_mcp=False)
    assert registry.find("inspect_image") is not None


def test_visual_judge_profile_loads_from_this_repo():
    from pathlib import Path

    from minicode.subagent_profiles import load_agent_profiles, resolve_agent_types

    repo_root = Path(__file__).resolve().parents[1]
    profiles, errors = load_agent_profiles(repo_root)
    assert errors == []
    assert "visual-judge" in profiles
    assert "inspect_image" in profiles["visual-judge"]["allowed_tools"]
    merged = resolve_agent_types(repo_root)
    assert merged["visual-judge"]["max_turns"] == 8


def test_visual_judge_child_registry_has_vision_tool(tmp_path):
    from pathlib import Path

    from minicode.subagent_profiles import resolve_agent_types
    from minicode.tools import create_default_tool_registry

    repo_root = Path(__file__).resolve().parents[1]
    # A child registry built from THIS repo's definitions exposes the tool
    # to the judge type exactly as dispatch would.
    merged = resolve_agent_types(repo_root)
    allowed = merged["visual-judge"]["allowed_tools"]
    registry = create_default_tool_registry(str(tmp_path), include_mcp=False)
    child = registry.filtered(lambda tool: tool.name in allowed)
    assert child.find("inspect_image") is not None
    assert child.find("write_file") is None


def test_vision_model_from_env_config(monkeypatch, tmp_path):
    """MINI_CODE_VISION_MODEL flows into the runtime the tool reads."""
    monkeypatch.setenv("MINI_CODE_VISION_MODEL", "qwen3.8-flash")
    from minicode.config import load_runtime_config

    runtime = load_runtime_config(str(tmp_path))
    assert runtime.get("visionModel") == "qwen3.8-flash"


def test_vision_model_defaults_to_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("MINI_CODE_VISION_MODEL", raising=False)
    from minicode.config import load_runtime_config

    runtime = load_runtime_config(str(tmp_path))
    assert not runtime.get("visionModel")
