"""Crash-safe migration of legacy model settings into the private user env."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from minicode.config import (
    MINI_CODE_ENV_PATH,
    MINI_CODE_SETTINGS_PATH,
    USER_MODEL_ENV_KEYS,
)
from minicode.env_file import (
    parse_env_file,
    read_private_env_file,
    update_private_env_file,
)

_MIGRATION_VERSION = 1
_ENV_ASSIGNMENT = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

_TOP_LEVEL_FIELDS = {
    "model": "MINI_CODE_MODEL",
    "provider": "MINI_CODE_PROVIDER",
    "maxOutputTokens": "MINI_CODE_MAX_OUTPUT_TOKENS",
    "baseUrl": "ANTHROPIC_BASE_URL",
    "authToken": "ANTHROPIC_AUTH_TOKEN",
    "apiKey": "ANTHROPIC_API_KEY",
    "openaiBaseUrl": "OPENAI_BASE_URL",
    "openaiApiKey": "OPENAI_API_KEY",
    "openrouterBaseUrl": "OPENROUTER_BASE_URL",
    "openrouterApiKey": "OPENROUTER_API_KEY",
    "openrouterReferer": "OPENROUTER_REFERER",
    "openrouterTitle": "OPENROUTER_TITLE",
    "openrouterTransforms": "OPENROUTER_TRANSFORMS",
    "customBaseUrl": "CUSTOM_API_BASE_URL",
    "customApiKey": "CUSTOM_API_KEY",
    "customApiExtraHeaders": "CUSTOM_API_EXTRA_HEADERS",
    "reflectionSynthesizerMode": "MINI_CODE_REFLECTION_SYNTHESIZER_MODE",
    "reflectionModel": "MINI_CODE_REFLECTION_MODEL",
    "reflectionLLMTimeoutSeconds": "MINI_CODE_REFLECTION_LLM_TIMEOUT_SECONDS",
    "reflectionLLMMaxOutputTokens": "MINI_CODE_REFLECTION_LLM_MAX_OUTPUT_TOKENS",
    "reflectionLLMMaxInputBytes": "MINI_CODE_REFLECTION_LLM_MAX_INPUT_BYTES",
    "reflectionLLMMaxOutputBytes": "MINI_CODE_REFLECTION_LLM_MAX_OUTPUT_BYTES",
    "reflectionLLMMaxClaims": "MINI_CODE_REFLECTION_LLM_MAX_CLAIMS",
    "allowRemoteReflectionModel": "MINI_CODE_ALLOW_REMOTE_REFLECTION_MODEL",
    "reflectionShadowMetricsEnabled": "MINI_CODE_REFLECTION_SHADOW_METRICS_ENABLED",
    "reflectionShadowMetricsPath": "MINI_CODE_REFLECTION_SHADOW_METRICS_PATH",
    "reflectionShadowSampleRate": "MINI_CODE_REFLECTION_SHADOW_SAMPLE_RATE",
    "reflectionShadowMaxRecords": "MINI_CODE_REFLECTION_SHADOW_MAX_RECORDS",
    "reflectionShadowMaxFileBytes": "MINI_CODE_REFLECTION_SHADOW_MAX_FILE_BYTES",
    "reflectionPromptVersion": "MINI_CODE_REFLECTION_PROMPT_VERSION",
    "reflectionLLMSelectionStrategy": "MINI_CODE_REFLECTION_LLM_SELECTION_STRATEGY",
}

_BUDGET_FIELDS = {
    "maxTokens": "MINI_CODE_TURN_BUDGET_TOKENS",
    "maxModelCalls": "MINI_CODE_TURN_BUDGET_MODEL_CALLS",
    "maxCostUsd": "MINI_CODE_TURN_BUDGET_COST_USD",
}

_SUBAGENT_FIELDS = {
    "enabled": "MINI_CODE_SUBAGENT_ROUTING_ENABLED",
    "provider": "MINI_CODE_SUBAGENT_PROVIDER",
    "baseUrl": "MINI_CODE_SUBAGENT_BASE_URL",
    "apiKey": "MINI_CODE_SUBAGENT_API_KEY",
    "defaultModel": "MINI_CODE_SUBAGENT_MODEL",
}

_MEMORY_HYBRID_FIELDS = {
    "enabled": "MINI_CODE_MEMORY_HYBRID_ENABLED",
    "embeddingProvider": "MINI_CODE_MEMORY_HYBRID_EMBEDDING_PROVIDER",
    "allowRemoteEmbedding": "MINI_CODE_ALLOW_REMOTE_MEMORY_EMBEDDING",
    "modelPath": "MINI_CODE_MEMORY_HYBRID_MODEL_PATH",
    "evidencePath": "MINI_CODE_MEMORY_HYBRID_EVIDENCE_PATH",
    "verifierModel": "MINI_CODE_MEMORY_HYBRID_VERIFIER_MODEL",
}


@dataclass(frozen=True, slots=True)
class ModelEnvMigrationReport:
    """Secret-free migration result."""

    env_path: str
    settings_path: str
    migrated_keys: tuple[str, ...]
    removed_legacy_fields: tuple[str, ...]
    scrubbed_workspace_keys: tuple[str, ...] = ()


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value).strip()


def _copy_mapping(
    source: Mapping[str, Any],
    field_map: Mapping[str, str],
    target: dict[str, str],
) -> None:
    for field, env_name in field_map.items():
        if field not in source:
            continue
        value = _stringify(source[field])
        if value:
            target[env_name] = value


def _infer_provider(model: str, values: Mapping[str, str]) -> str:
    lowered = model.lower()
    if values.get("CUSTOM_API_BASE_URL"):
        return "custom"
    if (
        values.get("DEEPSEEK_API_KEY")
        and "deepseek" in lowered
        and "/" not in lowered
    ):
        return "custom"
    if lowered.startswith("openrouter/") or "/" in lowered:
        return "openrouter"
    if lowered.startswith(("gpt-", "o1", "o3", "chatgpt-")):
        return "openai"
    return "anthropic"


def _workspace_importable(name: str) -> bool:
    return (
        name.startswith("MINI_CODE_SUBAGENT_")
        or name.startswith("MINICODE_EMBEDDING_")
        or name.startswith("MINI_CODE_MEMORY_HYBRID_")
        or name == "MINI_CODE_ALLOW_REMOTE_MEMORY_EMBEDDING"
    )


def _extract_candidates(
    settings: Mapping[str, Any],
    workspace_env_path: Path | None,
) -> tuple[dict[str, str], set[str]]:
    candidates: dict[str, str] = {}
    _copy_mapping(settings, _TOP_LEVEL_FIELDS, candidates)

    budget = settings.get("agentTurnBudget", {})
    if isinstance(budget, Mapping):
        _copy_mapping(budget, _BUDGET_FIELDS, candidates)

    subagents = settings.get("subagentRouting", {})
    if isinstance(subagents, Mapping):
        _copy_mapping(subagents, _SUBAGENT_FIELDS, candidates)
        models = subagents.get("models", {})
        if isinstance(models, Mapping):
            for role in ("default", "explore", "plan", "general"):
                value = _stringify(models.get(role))
                if not value:
                    continue
                env_name = (
                    "MINI_CODE_SUBAGENT_MODEL"
                    if role == "default"
                    else f"MINI_CODE_SUBAGENT_{role.upper()}_MODEL"
                )
                candidates[env_name] = value

    hybrid = settings.get("memoryHybrid", {})
    if isinstance(hybrid, Mapping):
        _copy_mapping(hybrid, _MEMORY_HYBRID_FIELDS, candidates)

    # Match load_runtime_config: values in the legacy ``env`` mapping have
    # authority over their structured/top-level compatibility fields.
    raw_env = settings.get("env", {})
    if isinstance(raw_env, Mapping):
        if "ANTHROPIC_MODEL" in raw_env and "MINI_CODE_MODEL" not in raw_env:
            candidates.pop("MINI_CODE_MODEL", None)
        if "OPENAI_API_BASE" in raw_env and "OPENAI_BASE_URL" not in raw_env:
            candidates.pop("OPENAI_BASE_URL", None)
        for name, value in raw_env.items():
            if name in USER_MODEL_ENV_KEYS:
                # Presence is authoritative even when the value is empty: an
                # empty env entry is a tombstone that must not revive a lower
                # priority structured credential during migration.
                candidates[name] = _stringify(value)

    workspace_keys: set[str] = set()
    if workspace_env_path is not None and workspace_env_path.is_file():
        workspace_values = parse_env_file(workspace_env_path)
        workspace_keys = {
            name
            for name in workspace_values
            if name in USER_MODEL_ENV_KEYS and _workspace_importable(name)
        }
        for name in workspace_keys:
            value = _stringify(workspace_values[name])
            if value:
                candidates[name] = value

    model = candidates.get("MINI_CODE_MODEL") or candidates.get(
        "ANTHROPIC_MODEL",
        "",
    )
    if model and not candidates.get("MINI_CODE_PROVIDER"):
        candidates["MINI_CODE_PROVIDER"] = _infer_provider(model, candidates)
    return candidates, workspace_keys


def _scrub_settings(settings: Mapping[str, Any]) -> tuple[dict[str, Any], set[str]]:
    scrubbed = dict(settings)
    removed: set[str] = set()
    for field in _TOP_LEVEL_FIELDS:
        if field in scrubbed:
            removed.add(field)
            scrubbed.pop(field, None)

    raw_env = scrubbed.get("env")
    if isinstance(raw_env, Mapping):
        next_env = {
            str(name): value
            for name, value in raw_env.items()
            if name not in USER_MODEL_ENV_KEYS
        }
        removed.update(
            f"env.{name}" for name in raw_env if name in USER_MODEL_ENV_KEYS
        )
        if next_env:
            scrubbed["env"] = next_env
        else:
            scrubbed.pop("env", None)
    for field in ("agentTurnBudget", "subagentRouting", "memoryHybrid"):
        if field in scrubbed:
            removed.add(field)
            scrubbed.pop(field, None)
    scrubbed["modelEnvMigrationVersion"] = _MIGRATION_VERSION
    return scrubbed, removed


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path.parent, 0o700)
    if path.exists() and path.is_symlink():
        raise RuntimeError("model_env_settings_symlink")
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor = -1
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        if os.name == "posix":
            os.chmod(path, 0o600)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


@contextmanager
def _migration_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path.parent, 0o700)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        if os.name == "posix":
            import fcntl

            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _scrub_workspace_env(path: Path, keys: set[str]) -> tuple[str, ...]:
    if not keys or not path.exists():
        return ()
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("workspace_env_not_regular")
    text = path.read_text(encoding="utf-8", errors="strict")
    kept: list[str] = []
    removed: set[str] = set()
    for line in text.splitlines(keepends=True):
        match = _ENV_ASSIGNMENT.match(line)
        if match and match.group(1) in keys:
            removed.add(match.group(1))
            continue
        kept.append(line)
    if not removed:
        return ()
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            descriptor = -1
            handle.write("".join(kept))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = ""
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return tuple(sorted(removed))


def migrate_model_config_to_user_env(
    *,
    settings_path: str | Path | None = None,
    env_path: str | Path | None = None,
    workspace_env_path: str | Path | None = None,
    scrub_workspace: bool = False,
) -> ModelEnvMigrationReport:
    """Migrate model settings without ever exposing credential values."""
    settings_file = Path(settings_path or MINI_CODE_SETTINGS_PATH)
    env_file = Path(env_path or MINI_CODE_ENV_PATH)
    workspace_file = Path(workspace_env_path) if workspace_env_path else None
    lock_file = env_file.parent / "model-env-migration.lock"

    with _migration_lock(lock_file):
        if settings_file.exists():
            parsed = json.loads(settings_file.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise RuntimeError("model_env_settings_invalid")
            settings: dict[str, Any] = parsed
        else:
            settings = {}

        candidates, workspace_keys = _extract_candidates(settings, workspace_file)
        existing = read_private_env_file(
            env_file,
            allowed_keys=USER_MODEL_ENV_KEYS,
        )
        conflicts = sorted(
            name
            for name, value in candidates.items()
            if name in existing and existing[name] != value
        )
        if conflicts:
            raise RuntimeError(
                "model_env_conflict:" + ",".join(conflicts)
            )
        target = {**existing, **candidates}
        target_model = (
            target.get("MINI_CODE_MODEL")
            if "MINI_CODE_MODEL" in target
            else target.get("ANTHROPIC_MODEL")
        )
        if not target_model:
            raise RuntimeError("model_env_model_missing")

        verified = (
            update_private_env_file(
                env_file,
                candidates,
                allowed_keys=USER_MODEL_ENV_KEYS,
            )
            if candidates
            else existing
        )
        for name, value in candidates.items():
            if verified.get(name) != value:
                raise RuntimeError("model_env_verification_failed")

        scrubbed_settings, removed = _scrub_settings(settings)
        if scrubbed_settings != settings:
            _atomic_write_json(settings_file, scrubbed_settings)

        scrubbed_workspace_keys: tuple[str, ...] = ()
        if scrub_workspace and workspace_file is not None:
            scrubbed_workspace_keys = _scrub_workspace_env(
                workspace_file,
                workspace_keys,
            )

    return ModelEnvMigrationReport(
        env_path=str(env_file),
        settings_path=str(settings_file),
        migrated_keys=tuple(sorted(candidates)),
        removed_legacy_fields=tuple(sorted(removed)),
        scrubbed_workspace_keys=scrubbed_workspace_keys,
    )


__all__ = ["ModelEnvMigrationReport", "migrate_model_config_to_user_env"]
