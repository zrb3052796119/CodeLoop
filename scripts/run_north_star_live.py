#!/usr/bin/env python3
"""Execute a frozen north-star manifest through the real MiniCode runtime.

The runner owns isolation, evidence collection, deterministic oracle checks,
and resumable result writes. It never accepts shell commands, never serializes
prompts/responses into the public result file, and never treats model prose as
verification for write tasks unless the manifest explicitly declares a
bounded response-content oracle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from minicode.agent_runtime import (  # noqa: E402
    create_agent_turn_runtime,
    prepare_conversation_messages,
)
from minicode.anthropic_adapter import AnthropicModelAdapter  # noqa: E402
from minicode.memory import MemoryManager, MemoryScope  # noqa: E402
from minicode.model_registry import build_provider_config  # noqa: E402
from minicode.openai_adapter import OpenAIModelAdapter  # noqa: E402
from minicode.run_events import emit_skill_routing_safely  # noqa: E402
from minicode.run_journal import RunJournal  # noqa: E402
from minicode.run_lifecycle import observe_run  # noqa: E402


_CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
_RUN_ID_RE = re.compile(r"^run_[a-f0-9]{32}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_ORACLE_KINDS = frozenset(
    {
        "all_runs_completed",
        "canonical_success",
        "command",
        "context_compacted",
        "context_compaction_count",
        "file_contains",
        "file_not_contains",
        "memory_attributed",
        "memory_injected",
        "memory_rendered",
        "memory_written",
        "no_source_edits",
        "response_contains",
        "skill_loaded",
        "subagent_count",
        "tool_failed",
        "tool_succeeded",
        "verification_passed",
    }
)
_IGNORED_TREE_PARTS = frozenset(
    {
        ".mini-code-memory",
        ".pytest_cache",
        "__pycache__",
        ".coverage",
    }
)
_IGNORED_RUNTIME_FILES = frozenset(
    {
        ".mini-code/.skill_versions.lock",
        ".mini-code/skill_versions.json",
        ".mini-code/.skill_evidence.lock",
        ".mini-code/skill_evidence.json",
        ".mini-code/skill-embeddings.json",
    }
)
_MAX_RESPONSE_CHARS = 200_000
_MAX_COMMAND_OUTPUT_CHARS = 20_000
_MEMORY_CLAIM_TYPES = frozenset(
    {
        "approach",
        "constraint",
        "correction",
        "decision",
        "dependency",
        "error_pattern",
        "recovery",
        "root_cause",
        "verification_rule",
        "warning",
    }
)
_VERIFICATION_KINDS = frozenset({"tests", "build", "lint", "typecheck", "review"})
_VERIFICATION_SOURCES = frozenset(
    {"test_runner", "run_command_exit", "workflow_review"}
)


@dataclass(frozen=True, slots=True)
class TurnEvidence:
    run_id: str
    response: str
    event_types: tuple[str, ...]
    events: tuple[object, ...]
    model_calls: int
    input_tokens: int | None
    output_tokens: int | None
    runtime_profile_sha256: str | None = None
    seeded_memory_ids: tuple[str, ...] = ()
    memory_claim_types: tuple[tuple[str, frozenset[str]], ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryAttributionContract:
    source: str
    rendered_turn: int
    minimum: int
    source_turn: int | None = None
    claim_type: str | None = None
    seed_indexes: tuple[int, ...] = ()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return value


def _load_json_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    """Parse and identify one immutable byte snapshot of a JSON document."""
    payload = path.read_bytes()
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return value, hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_contract_sha256(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_endpoint_url(provider: str, base_url: str) -> str:
    """Project the exact credential-free URL constructed by an adapter."""
    normalized = str(base_url).strip().rstrip("/")
    if provider == "anthropic":
        if normalized.endswith("/v1/messages"):
            return normalized
        return f"{normalized}/v1/messages"
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def _runtime_profile_from_runtime(runtime: Mapping[str, object]) -> dict[str, str]:
    """Project the expected adapter route without credentials or headers."""
    model = str(runtime.get("model") or "").strip()
    if not model:
        raise RuntimeError("acceptance runtime model is missing")
    provider = build_provider_config(model, dict(runtime))
    endpoint_url = _canonical_endpoint_url(
        provider.provider.value,
        provider.base_url,
    )
    identity = {
        "adapterType": (
            "openai_compatible"
            if provider.is_openai_compatible
            else "anthropic"
        ),
        "provider": provider.provider.value,
        "model": provider.model,
        "endpointUrl": endpoint_url,
    }
    return {
        **identity,
        "profileSha256": _canonical_contract_sha256(identity),
    }


def _runtime_profile_from_adapter(
    adapter: object,
    configured_runtime: Mapping[str, object],
) -> dict[str, str]:
    """Bind acceptance to the concrete adapter and its frozen wire route."""
    expected = _runtime_profile_from_runtime(configured_runtime)
    provider = expected["provider"]
    if provider == "anthropic":
        if not isinstance(adapter, AnthropicModelAdapter):
            raise RuntimeError("acceptance runtime adapter mismatch")
        adapter_type = "anthropic"
        base_url_key = "baseUrl"
    else:
        if not isinstance(adapter, OpenAIModelAdapter):
            raise RuntimeError("acceptance runtime adapter mismatch")
        adapter_type = "openai_compatible"
        base_url_key = "openaiBaseUrl"
    adapter_runtime = getattr(adapter, "runtime", None)
    if not isinstance(adapter_runtime, dict):
        raise RuntimeError("acceptance runtime adapter state is missing")
    model = str(adapter_runtime.get("model") or "").strip()
    base_url = str(adapter_runtime.get(base_url_key) or "").strip()
    if not model or not base_url:
        raise RuntimeError("acceptance runtime adapter route is missing")
    identity = {
        "adapterType": adapter_type,
        "provider": provider,
        "model": model,
        "endpointUrl": _canonical_endpoint_url(provider, base_url),
    }
    return {**identity, "profileSha256": _canonical_contract_sha256(identity)}


def _runtime_profile_contract(
    document: Mapping[str, object],
) -> dict[str, str] | None:
    raw = document.get("runtimeProfileContract")
    if raw is None:
        return None
    required = {
        "adapterType",
        "endpointUrl",
        "model",
        "profileSha256",
        "provider",
        "version",
    }
    if (
        not isinstance(raw, dict)
        or set(raw) != required
        or type(raw.get("version")) is not int
        or raw.get("version") != 2
    ):
        raise ValueError("manifest runtime profile contract is invalid")
    provider = raw.get("provider")
    adapter_type = raw.get("adapterType")
    model = raw.get("model")
    endpoint_url = raw.get("endpointUrl")
    profile_sha256 = raw.get("profileSha256")
    if (
        provider not in {"anthropic", "custom", "openai", "openrouter"}
        or adapter_type
        != ("anthropic" if provider == "anthropic" else "openai_compatible")
        or not isinstance(model, str)
        or not 1 <= len(model) <= 200
        or any(character in model for character in "\r\n\0")
        or not isinstance(endpoint_url, str)
        or not endpoint_url
        or not isinstance(profile_sha256, str)
        or not _SHA256_RE.fullmatch(profile_sha256)
    ):
        raise ValueError("manifest runtime profile contract is invalid")
    identity = {
        "adapterType": str(adapter_type),
        "provider": str(provider),
        "model": model,
        "endpointUrl": endpoint_url,
    }
    parsed_endpoint = urlparse(endpoint_url)
    if (
        parsed_endpoint.scheme != "https"
        or not parsed_endpoint.hostname
        or parsed_endpoint.username
        or parsed_endpoint.password
        or parsed_endpoint.query
        or parsed_endpoint.fragment
        or _canonical_endpoint_url(str(provider), endpoint_url) != endpoint_url
    ):
        raise ValueError("manifest runtime profile endpoint is invalid")
    if _canonical_contract_sha256(identity) != profile_sha256:
        raise ValueError("manifest runtime profile hash is invalid")
    return {**identity, "profileSha256": profile_sha256}


def _source_code_sha256() -> str:
    """Snapshot the credential-free Python source tree used by the runner."""
    paths = [Path(__file__).resolve()]
    paths.extend(sorted((PROJECT_ROOT / "minicode").rglob("*.py")))
    digest = hashlib.sha256()
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("acceptance source identity is unsafe")
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _public_source_report(path: Path) -> str:
    """Return a useful report locator without exposing an absolute home path."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.name


def _validate_nonnegative_integer(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"case evidence {field} is invalid")
    return value


def _validate_optional_token_count(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    return _validate_nonnegative_integer(value, field=field)


def _validate_private_case_evidence(
    value: object,
    *,
    suite_id: str,
    manifest_sha256: str,
    source_code_sha256: str,
    case: Mapping[str, object],
    case_contract_sha256: str,
    runtime_profile_sha256: str | None,
) -> dict[str, object]:
    """Validate the private execution authority before public projection."""
    required = {
        "caseContractSha256",
        "durationMs",
        "failure",
        "id",
        "inputTokens",
        "manifestSha256",
        "modelCalls",
        "oracleFailures",
        "outputTokens",
        "runIds",
        "runtimeProfileSha256ByRun",
        "schemaVersion",
        "sourceCodeSha256",
        "suiteId",
        "unsafeActionCount",
        "userInterventionCount",
        "writeAuthorization",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("case evidence contract is invalid")
    if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        raise ValueError("case evidence schema is invalid")
    case_id = str(case.get("id") or "")
    identities = {
        "suiteId": suite_id,
        "manifestSha256": manifest_sha256,
        "sourceCodeSha256": source_code_sha256,
        "id": case_id,
        "caseContractSha256": case_contract_sha256,
    }
    if any(value.get(field) != expected for field, expected in identities.items()):
        raise ValueError("case evidence identity mismatch")
    failure = value.get("failure")
    if failure is not None and (
        not isinstance(failure, str) or not failure or len(failure) > 200
    ):
        raise ValueError("case evidence failure is invalid")
    declared_oracles = case.get("oracleIds")
    oracle_failures = value.get("oracleFailures")
    if (
        not isinstance(declared_oracles, list)
        or not isinstance(oracle_failures, dict)
        or any(
            key not in declared_oracles
            or not isinstance(key, str)
            or not isinstance(reason, str)
            or not reason
            or len(reason) > 100
            for key, reason in oracle_failures.items()
        )
        or (failure is not None and oracle_failures)
    ):
        raise ValueError("case evidence oracle contract is invalid")
    run_ids = value.get("runIds")
    profiles = value.get("runtimeProfileSha256ByRun")
    if (
        not isinstance(run_ids, list)
        or any(
            not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id)
            for run_id in run_ids
        )
        or len(set(run_ids)) != len(run_ids)
        or not isinstance(profiles, list)
        or len(profiles) != len(run_ids)
        or any(
            profile is not None
            and (not isinstance(profile, str) or not _SHA256_RE.fullmatch(profile))
            for profile in profiles
        )
    ):
        raise ValueError("case evidence run identity is invalid")
    if runtime_profile_sha256 is None:
        if any(profile is not None for profile in profiles):
            raise ValueError("case evidence runtime profile is unexpected")
    elif any(profile != runtime_profile_sha256 for profile in profiles):
        raise ValueError("case evidence runtime profile identity is invalid")
    if failure is None and not oracle_failures and not run_ids:
        raise ValueError("passed case evidence has no run identity")
    for field in (
        "durationMs",
        "modelCalls",
        "unsafeActionCount",
        "userInterventionCount",
    ):
        _validate_nonnegative_integer(value.get(field), field=field)
    for field in ("inputTokens", "outputTokens"):
        _validate_optional_token_count(value.get(field), field=field)
    write_authorization = value.get("writeAuthorization")
    expected_authorization = (
        {
            "policy": "declared_workspace_paths_and_verifiers_only",
            "authorizedPaths": list(case.get("authorizedPaths", [])),
        }
        if case.get("mutability") == "write"
        else {"policy": "read_only_verifiers_only", "authorizedPaths": []}
    )
    if write_authorization != expected_authorization:
        raise ValueError("case evidence write authorization is invalid")
    return dict(value)


def _project_public_case_result(
    evidence: Mapping[str, object],
    *,
    case: Mapping[str, object],
    evidence_sha256: str,
    runtime_profile_sha256: str | None,
) -> dict[str, object]:
    """Derive every public result field from validated private evidence."""
    failure = evidence["failure"]
    oracle_failures = evidence["oracleFailures"]
    assert isinstance(oracle_failures, dict)
    declared_oracles = case["oracleIds"]
    assert isinstance(declared_oracles, list)
    passed_oracles = (
        []
        if failure is not None
        else [oracle_id for oracle_id in declared_oracles if oracle_id not in oracle_failures]
    )
    complete = failure is None and passed_oracles == declared_oracles
    run_ids = evidence["runIds"]
    assert isinstance(run_ids, list)
    result: dict[str, object] = {
        "caseContractSha256": evidence["caseContractSha256"],
        "durationMs": evidence["durationMs"],
        "evidenceSha256": evidence_sha256,
        "id": evidence["id"],
        "inputTokens": evidence["inputTokens"],
        "modelCalls": evidence["modelCalls"],
        "outputTokens": evidence["outputTokens"],
        "passedOracleIds": passed_oracles,
        "runId": run_ids[-1] if run_ids else None,
        "status": "passed" if complete else "failed",
        "unsafeActionCount": evidence["unsafeActionCount"],
        "userInterventionCount": evidence["userInterventionCount"],
        "verificationPassed": complete,
    }
    if len(run_ids) > 1:
        result["relatedRunIds"] = run_ids[:-1]
    if runtime_profile_sha256 is not None:
        profiles = evidence["runtimeProfileSha256ByRun"]
        assert isinstance(profiles, list)
        observed = set(profiles)
        result["runtimeProfileSha256"] = (
            runtime_profile_sha256
            if observed == {runtime_profile_sha256}
            else None
        )
    return result


def _validate_public_case_result(
    value: object,
    *,
    case: Mapping[str, object],
    evidence: Mapping[str, object],
    evidence_sha256: str,
    runtime_profile_sha256: str | None = None,
) -> dict[str, object]:
    expected = _project_public_case_result(
        evidence,
        case=case,
        evidence_sha256=evidence_sha256,
        runtime_profile_sha256=runtime_profile_sha256,
    )
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("resume result contract does not match case evidence")
    for field, expected_value in expected.items():
        if value.get(field) == expected_value:
            continue
        if field == "caseContractSha256":
            raise ValueError("resume caseContractSha256 mismatch")
        if field in {"passedOracleIds", "status", "verificationPassed"}:
            raise ValueError("resume result pass contract does not match evidence")
        if field == "runtimeProfileSha256":
            raise ValueError("resume runtime profile identity does not match evidence")
        if field == "evidenceSha256":
            raise ValueError("resume evidence identity mismatch")
        raise ValueError(f"resume result {field} does not match evidence")
    return dict(value)


def _result_group_status(
    case_ids: list[str],
    results: Mapping[str, Mapping[str, object]],
    *,
    require_finalized: bool = False,
    finalized: bool = True,
) -> tuple[bool, str]:
    complete = all(case_id in results for case_id in case_ids)
    failed = any(
        results[case_id].get("status") == "failed"
        for case_id in case_ids
        if case_id in results
    )
    effective_complete = complete and (finalized or not require_finalized)
    status = "failed" if failed else "passed" if effective_complete else "incomplete"
    return effective_complete, status


def _public_result_envelope(
    *,
    manifest: Mapping[str, object],
    manifest_path: Path,
    manifest_sha256: str,
    source_code_sha256: str,
    all_cases: list[dict[str, Any]],
    selected_cases: list[dict[str, Any]],
    results: Mapping[str, dict[str, object]],
    runtime_profile: Mapping[str, str] | None,
    finalized: bool,
) -> dict[str, object]:
    all_ids = [str(case["id"]) for case in all_cases]
    selected_ids = [str(case["id"]) for case in selected_cases]
    suite_complete, suite_status = _result_group_status(
        all_ids,
        results,
        require_finalized=True,
        finalized=finalized,
    )
    selection_complete, selection_status = _result_group_status(
        selected_ids,
        results,
    )
    return {
        "schemaVersion": 1,
        "suiteId": manifest["suiteId"],
        "manifestSha256": manifest_sha256,
        "sourceCodeSha256": source_code_sha256,
        "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sourceReport": _public_source_report(manifest_path),
        "manifestCaseCount": len(all_ids),
        "selectedCaseIds": selected_ids,
        "completedCaseCount": len(results),
        "selectionComplete": selection_complete,
        "selectionStatus": selection_status,
        "suiteFinalized": finalized,
        "suiteComplete": suite_complete,
        "suiteStatus": suite_status,
        "runtimeProfile": dict(runtime_profile) if runtime_profile else None,
        "results": [results[case_id] for case_id in all_ids if case_id in results],
    }


def _validate_public_result_envelope(
    value: Mapping[str, object],
    *,
    manifest_path: Path,
    all_cases: list[dict[str, Any]],
    results: Mapping[str, dict[str, object]],
    runtime_profile: Mapping[str, str] | None,
) -> None:
    required = {
        "completedCaseCount",
        "manifestCaseCount",
        "manifestSha256",
        "recordedAt",
        "results",
        "runtimeProfile",
        "schemaVersion",
        "selectedCaseIds",
        "selectionComplete",
        "selectionStatus",
        "sourceCodeSha256",
        "sourceReport",
        "suiteComplete",
        "suiteFinalized",
        "suiteId",
        "suiteStatus",
    }
    if set(value) != required:
        raise ValueError("resume suite envelope contract is invalid")
    all_ids = [str(case["id"]) for case in all_cases]
    selected_ids = value.get("selectedCaseIds")
    if (
        not isinstance(selected_ids, list)
        or not selected_ids
        or any(not isinstance(case_id, str) for case_id in selected_ids)
        or len(set(selected_ids)) != len(selected_ids)
        or [case_id for case_id in all_ids if case_id in set(selected_ids)]
        != selected_ids
    ):
        raise ValueError("resume selection envelope is invalid")
    finalized = value.get("suiteFinalized")
    if type(finalized) is not bool:
        raise ValueError("resume suite finalization is invalid")
    suite_complete, suite_status = _result_group_status(
        all_ids,
        results,
        require_finalized=True,
        finalized=finalized,
    )
    selection_complete, selection_status = _result_group_status(
        selected_ids,
        results,
    )
    ordered_result_ids = [
        case_id for case_id in all_ids if case_id in results
    ]
    raw_results = value.get("results")
    if (
        type(value.get("manifestCaseCount")) is not int
        or value.get("manifestCaseCount") != len(all_ids)
        or type(value.get("completedCaseCount")) is not int
        or value.get("completedCaseCount") != len(results)
        or type(value.get("suiteComplete")) is not bool
        or value.get("suiteComplete") != suite_complete
        or value.get("suiteStatus") != suite_status
        or type(value.get("selectionComplete")) is not bool
        or value.get("selectionComplete") != selection_complete
        or value.get("selectionStatus") != selection_status
        or value.get("runtimeProfile")
        != (dict(runtime_profile) if runtime_profile else None)
        or value.get("sourceReport") != _public_source_report(manifest_path)
        or not isinstance(value.get("recordedAt"), str)
        or not isinstance(raw_results, list)
        or [item.get("id") for item in raw_results if isinstance(item, dict)]
        != ordered_result_ids
        or len(raw_results) != len(ordered_result_ids)
    ):
        raise ValueError("resume suite envelope is inconsistent")


def _assert_execution_identity(
    *,
    manifest_path: Path,
    manifest_sha256: str,
    source_code_sha256: str,
) -> None:
    if _sha256_file(manifest_path) != manifest_sha256:
        raise RuntimeError("acceptance manifest changed during execution")
    if _source_code_sha256() != source_code_sha256:
        raise RuntimeError("acceptance source changed during execution")


def _atomic_json(
    path: Path,
    value: Mapping[str, object],
    *,
    private: bool = False,
) -> str:
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if private:
        os.chmod(path.parent, 0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    if private:
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return hashlib.sha256(payload).hexdigest()


def _case_evidence_path(suite_root: Path, case_id: str) -> Path:
    if not _CASE_ID_RE.fullmatch(case_id):
        raise ValueError("case evidence identity is unsafe")
    return suite_root / "cases" / case_id / "evidence.json"


def _load_case_evidence_snapshot(
    suite_root: Path,
    case_id: str,
) -> tuple[dict[str, Any], str]:
    path = _case_evidence_path(suite_root, case_id)
    if path.is_symlink() or not path.is_file():
        raise ValueError("resume case evidence is missing or unsafe")
    resolved_root = suite_root.resolve(strict=True)
    try:
        path.resolve(strict=True).relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError("resume case evidence escapes suite root") from error
    return _load_json_snapshot(path)


def _build_case_evidence(
    *,
    result: Mapping[str, object],
    private: Mapping[str, object],
    suite_id: str,
    manifest_sha256: str,
    source_code_sha256: str,
    case: Mapping[str, object],
    case_contract_sha256: str,
    runtime_profile_sha256: str | None,
) -> dict[str, object]:
    """Seal internal execution facts into the private resume authority."""
    declared_oracles = case["oracleIds"]
    assert isinstance(declared_oracles, list)
    raw_passed = result.get("passedOracleIds")
    passed = raw_passed if isinstance(raw_passed, list) else []
    raw_failures = private.get("oracleFailures")
    oracle_failures = dict(raw_failures) if isinstance(raw_failures, dict) else {}
    failure = private.get("failure")
    if failure is not None:
        failure = str(failure)[:200]
        oracle_failures = {}
    elif not oracle_failures:
        oracle_failures = {
            str(oracle_id): "oracle_not_satisfied"
            for oracle_id in declared_oracles
            if oracle_id not in passed
        }
    raw_run_ids = private.get("runIds")
    if isinstance(raw_run_ids, list):
        run_ids = list(raw_run_ids)
    else:
        related = result.get("relatedRunIds", [])
        run_ids = list(related) if isinstance(related, list) else []
        if result.get("runId") is not None:
            run_ids.append(result["runId"])
    raw_profiles = private.get("runtimeProfileSha256ByRun")
    if isinstance(raw_profiles, list):
        profiles = list(raw_profiles)
    else:
        observed_profile = result.get("runtimeProfileSha256")
        profiles = [observed_profile for _run_id in run_ids]
    write_authorization = private.get("writeAuthorization")
    if not isinstance(write_authorization, dict):
        write_authorization = (
            {
                "policy": "declared_workspace_paths_and_verifiers_only",
                "authorizedPaths": list(case.get("authorizedPaths", [])),
            }
            if case["mutability"] == "write"
            else {"policy": "read_only_verifiers_only", "authorizedPaths": []}
        )
    return {
        "schemaVersion": 1,
        "suiteId": suite_id,
        "manifestSha256": manifest_sha256,
        "sourceCodeSha256": source_code_sha256,
        "id": case["id"],
        "caseContractSha256": case_contract_sha256,
        "failure": failure,
        "oracleFailures": oracle_failures,
        "runIds": run_ids,
        "runtimeProfileSha256ByRun": profiles,
        "durationMs": result.get("durationMs"),
        "modelCalls": result.get("modelCalls"),
        "inputTokens": result.get("inputTokens"),
        "outputTokens": result.get("outputTokens"),
        "unsafeActionCount": result.get("unsafeActionCount"),
        "userInterventionCount": result.get("userInterventionCount"),
        "writeAuthorization": write_authorization,
    }


def _safe_relative(value: object) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("fixture path is invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("fixture path escapes workspace")
    return path


def _tool_operation_contract(
    oracle: Mapping[str, object],
) -> tuple[str, int, bool]:
    kind = str(oracle.get("kind") or "tool_operation")
    tool_name = oracle.get("toolName")
    minimum = oracle.get("min", 1)
    every_turn = oracle.get("everyTurn", False)
    if not isinstance(tool_name, str) or not _TOOL_NAME_RE.fullmatch(tool_name):
        raise ValueError(f"{kind} oracle toolName is invalid")
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or not 1 <= minimum <= 10_000
    ):
        raise ValueError(f"{kind} oracle minimum is invalid")
    if not isinstance(every_turn, bool):
        raise ValueError(f"{kind} oracle everyTurn is invalid")
    return tool_name, minimum, every_turn


def _verification_contract(
    oracle: Mapping[str, object],
) -> tuple[int, bool, str, frozenset[str]]:
    minimum = oracle.get("min", 1)
    every_turn = oracle.get("everyTurn", False)
    verification_kind = oracle.get("verificationKind")
    raw_sources = oracle.get("sources")
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or not 1 <= minimum <= 10_000
    ):
        raise ValueError("verification_passed oracle minimum is invalid")
    if not isinstance(every_turn, bool):
        raise ValueError("verification_passed oracle everyTurn is invalid")
    if verification_kind not in _VERIFICATION_KINDS:
        raise ValueError("verification_passed oracle verificationKind is invalid")
    if (
        not isinstance(raw_sources, list)
        or not raw_sources
        or any(source not in _VERIFICATION_SOURCES for source in raw_sources)
        or len(set(raw_sources)) != len(raw_sources)
    ):
        raise ValueError("verification_passed oracle sources are invalid")
    sources = frozenset(str(source) for source in raw_sources)
    if (verification_kind == "review") != (sources == {"workflow_review"}):
        raise ValueError("verification_passed oracle kind/source mismatch")
    return minimum, every_turn, str(verification_kind), sources


def _memory_attribution_contract(
    oracle: Mapping[str, object],
    *,
    turn_count: int,
    seed_count: int,
) -> MemoryAttributionContract:
    source = oracle.get("source")
    rendered_turn = oracle.get("renderedTurn")
    minimum = oracle.get("min", 1)
    if (
        source not in {"seeded", "written"}
        or isinstance(rendered_turn, bool)
        or not isinstance(rendered_turn, int)
        or not 0 <= rendered_turn < turn_count
        or isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or not 1 <= minimum <= 10_000
    ):
        raise ValueError("memory_attributed oracle contract is invalid")
    if source == "written":
        source_turn = oracle.get("sourceTurn")
        claim_type = oracle.get("claimType")
        if (
            isinstance(source_turn, bool)
            or not isinstance(source_turn, int)
            or not 0 <= source_turn < rendered_turn
            or claim_type not in _MEMORY_CLAIM_TYPES
            or "seedIndexes" in oracle
        ):
            raise ValueError("memory_attributed written contract is invalid")
        return MemoryAttributionContract(
            source=source,
            source_turn=source_turn,
            rendered_turn=rendered_turn,
            claim_type=str(claim_type),
            minimum=minimum,
        )
    seed_indexes = oracle.get("seedIndexes")
    if (
        not isinstance(seed_indexes, list)
        or not seed_indexes
        or any(
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < seed_count
            for index in seed_indexes
        )
        or len(set(seed_indexes)) != len(seed_indexes)
        or minimum > len(seed_indexes)
        or "sourceTurn" in oracle
        or "claimType" in oracle
    ):
        raise ValueError("memory_attributed seeded contract is invalid")
    return MemoryAttributionContract(
        source=source,
        rendered_turn=rendered_turn,
        minimum=minimum,
        seed_indexes=tuple(seed_indexes),
    )


def _validate_manifest(document: Mapping[str, object]) -> list[dict[str, Any]]:
    if (
        type(document.get("schemaVersion")) is not int
        or document.get("schemaVersion") != 1
    ):
        raise ValueError("unsupported north-star manifest schema")
    _runtime_profile_contract(document)
    suite_id = document.get("suiteId")
    raw_cases = document.get("cases")
    if not isinstance(suite_id, str) or not suite_id:
        raise ValueError("manifest suiteId is invalid")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("manifest cases are missing")
    declared_case_count = document.get("caseCount")
    if declared_case_count is not None and (
        isinstance(declared_case_count, bool)
        or not isinstance(declared_case_count, int)
        or declared_case_count != len(raw_cases)
    ):
        raise ValueError("manifest caseCount is inconsistent")
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise TypeError("manifest case must be an object")
        case_id = raw.get("id")
        turns = raw.get("turns")
        files = raw.get("files", {})
        oracle_ids = raw.get("oracleIds")
        oracles = raw.get("oracles")
        authorized_paths = raw.get("authorizedPaths", [])
        if (
            not isinstance(case_id, str)
            or not _CASE_ID_RE.fullmatch(case_id)
            or case_id in seen_ids
            or raw.get("mutability") not in {"read_only", "write"}
            or not isinstance(turns, list)
            or not turns
            or not isinstance(files, dict)
            or not isinstance(oracle_ids, list)
            or not oracle_ids
            or not isinstance(oracles, list)
            or len(oracles) != len(oracle_ids)
            or not isinstance(authorized_paths, list)
            or (raw.get("mutability") == "write" and not authorized_paths)
        ):
            raise ValueError(f"invalid live north-star case: {case_id!r}")
        allowed_turn_fields = {
            "carryHistory",
            "contextWindow",
            "initialHistory",
            "prompt",
        }
        for turn in turns:
            if (
                not isinstance(turn, dict)
                or not set(turn) <= allowed_turn_fields
                or not isinstance(turn.get("prompt"), str)
                or not turn["prompt"].strip()
            ):
                raise ValueError(f"invalid turns for case {case_id}")
            context_window = turn.get("contextWindow")
            if context_window is not None and (
                type(context_window) is not int
                or not 2_000 <= context_window <= 1_000_000
            ):
                raise ValueError(f"invalid contextWindow for case {case_id}")
            if "carryHistory" in turn and type(turn["carryHistory"]) is not bool:
                raise ValueError(f"invalid carryHistory for case {case_id}")
            initial_history = turn.get("initialHistory")
            if initial_history is not None and (
                not isinstance(initial_history, list)
                or any(
                    not isinstance(message, dict)
                    or set(message) != {"content", "role"}
                    or message.get("role") not in {"assistant", "user"}
                    or not isinstance(message.get("content"), str)
                    for message in initial_history
                )
            ):
                raise ValueError(f"invalid initialHistory for case {case_id}")
        for fixture_path, content in files.items():
            _safe_relative(fixture_path)
            if not isinstance(content, str):
                raise TypeError(f"fixture content must be text for case {case_id}")
        for authorized_path in authorized_paths:
            _safe_relative(authorized_path)
        projected_oracle_ids: list[str] = []
        for oracle in oracles:
            if (
                not isinstance(oracle, dict)
                or not isinstance(oracle.get("id"), str)
                or not oracle["id"]
                or oracle.get("kind") not in _ORACLE_KINDS
            ):
                raise ValueError(f"invalid oracle for case {case_id}")
            if oracle["kind"] in {"tool_failed", "tool_succeeded"}:
                _tool_operation_contract(oracle)
            if oracle["kind"] == "verification_passed":
                _verification_contract(oracle)
            if oracle["kind"] == "response_contains":
                values = oracle.get("values")
                if (
                    not isinstance(values, list)
                    or not values
                    or any(
                        not isinstance(item, str) or not item
                        for item in values
                    )
                ):
                    raise ValueError(
                        f"response_contains oracle contract is invalid for case {case_id}"
                    )
            if oracle["kind"] == "subagent_count":
                minimum = oracle.get("min", 1)
                if (
                    type(minimum) is not int
                    or not 1 <= minimum <= 10_000
                ):
                    raise ValueError(
                        f"subagent_count oracle contract is invalid for case {case_id}"
                    )
            if oracle["kind"] == "memory_attributed":
                memory_entries = raw.get("memoryEntries")
                _memory_attribution_contract(
                    oracle,
                    turn_count=len(turns),
                    seed_count=(
                        len(memory_entries) if isinstance(memory_entries, list) else 0
                    ),
                )
            projected_oracle_ids.append(str(oracle["id"]))
        if projected_oracle_ids != oracle_ids or len(set(oracle_ids)) != len(
            oracle_ids
        ):
            raise ValueError(f"oracle identity mismatch for case {case_id}")
        seen_ids.add(case_id)
        cases.append(dict(raw))
    return cases


def _write_fixture(workspace: Path, files: Mapping[str, str]) -> None:
    workspace.mkdir(parents=True, exist_ok=False)
    for raw_path, content in files.items():
        relative = _safe_relative(raw_path)
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _tree_digest(workspace: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(workspace.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(workspace)
        if (
            any(part in _IGNORED_TREE_PARTS for part in relative.parts)
            or relative.as_posix() in _IGNORED_RUNTIME_FILES
        ):
            continue
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(os.readlink(path).encode("utf-8", errors="replace"))
        elif path.is_file():
            digest.update(b"file\0")
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _usage_from_events(
    journal: RunJournal,
    run_id: str,
) -> tuple[int, int | None, int | None]:
    events = list(_all_events(journal, run_id))
    model_calls = sum(event.type == "model.started" for event in events)
    input_tokens = 0
    output_tokens = 0
    usage_complete = True

    def add_usage(event: object) -> None:
        nonlocal input_tokens, output_tokens, usage_complete
        if getattr(event, "type", None) != "model.completed":
            return
        payload = getattr(event, "payload", {})
        usage = payload.get("usage") if isinstance(payload, dict) else None
        observed_input = usage.get("inputTokens") if isinstance(usage, dict) else None
        observed_output = usage.get("outputTokens") if isinstance(usage, dict) else None
        if (
            isinstance(observed_input, int)
            and not isinstance(observed_input, bool)
            and isinstance(observed_output, int)
            and not isinstance(observed_output, bool)
        ):
            input_tokens += observed_input
            output_tokens += observed_output
        else:
            usage_complete = False

    for event in events:
        add_usage(event)
    for summary in journal.list_subagent_runs(run_id):
        model_calls += summary.model_turns
        for event in journal.list_subagent_events(run_id, summary.subagent_id):
            add_usage(event)
    return (
        model_calls,
        input_tokens if usage_complete else None,
        output_tokens if usage_complete else None,
    )


def _all_events(journal: RunJournal, run_id: str) -> tuple[object, ...]:
    """Read a complete bounded Run stream through the public cursor API."""
    items: list[object] = []
    cursor: str | None = None
    for _ in range(100):
        page = journal.list_events(run_id, limit=100, cursor=cursor)
        items.extend(page.items)
        if not page.has_more:
            return tuple(items)
        if not page.next_cursor or page.next_cursor == cursor:
            raise RuntimeError("Run event cursor did not advance")
        cursor = page.next_cursor
    raise RuntimeError("Run event pagination exceeded acceptance bound")


def _seed_memory(manager: MemoryManager, entries: object) -> tuple[str, ...]:
    if entries in (None, []):
        return ()
    if not isinstance(entries, list):
        raise TypeError("memoryEntries must be a list")
    seeded_ids: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            raise TypeError("memory entry must be an object")
        content = item.get("content")
        if not isinstance(content, str) or not content:
            raise ValueError("memory entry content is invalid")
        entry = manager.add_entry(
            MemoryScope.PROJECT,
            str(item.get("category") or "project-convention"),
            content,
            tags=[str(tag) for tag in item.get("tags", []) if isinstance(tag, str)],
        )
        if entry is None:
            raise ValueError("memory entry could not be seeded")
        seeded_ids.append(entry.id)
    return tuple(seeded_ids)


def _snapshot_memory_claim_types(
    manager: MemoryManager,
) -> dict[str, frozenset[str]]:
    """Copy only opaque entry IDs and bounded claim-type labels for oracles."""
    snapshot: dict[str, frozenset[str]] = {}
    for memory in manager.memories.values():
        for entry in memory.entries:
            metadata = entry.metadata
            structured = (
                metadata.get("structured_reflection")
                if isinstance(metadata, Mapping)
                else None
            )
            claims = structured.get("claims") if isinstance(structured, Mapping) else None
            if not isinstance(claims, list):
                continue
            claim_types = frozenset(
                claim_type
                for claim in claims
                if isinstance(claim, Mapping)
                and isinstance((claim_type := claim.get("claim_type")), str)
                and claim_type in _MEMORY_CLAIM_TYPES
            )
            if claim_types:
                snapshot[entry.id] = claim_types
    return snapshot


def _isolated_write_approval(
    workspace: Path,
    request: Mapping[str, object],
    authorized_paths: tuple[Path, ...] = (),
) -> dict[str, str]:
    """Pre-authorize declared edits and bounded verification commands.

    The suite contract declares exact edit targets and permits bounded local
    verifiers for every case. This callback supplies that declaration to the
    normal PermissionManager without authorizing arbitrary commands, network
    access, or paths outside the per-case workspace.
    """
    review = request.get("review")
    if not isinstance(review, dict):
        return {"decision": "deny_once"}
    root = workspace.resolve(strict=True)
    if request.get("kind") == "command":
        command = review.get("command")
        args = review.get("args")
        cwd = review.get("cwd")
        if (
            not isinstance(command, str)
            or not isinstance(args, list)
            or not all(isinstance(item, str) for item in args)
            or not isinstance(cwd, str)
        ):
            return {"decision": "deny_once"}
        try:
            Path(cwd).resolve(strict=True).relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return {"decision": "deny_once"}
        executable = Path(command).name.casefold()
        module = args[1].casefold() if len(args) >= 2 and args[0] == "-m" else ""
        safe = executable in {"unittest", "pytest"} or (
            executable in {"python", "python3", Path(sys.executable).name.casefold()}
            and module in {"unittest", "pytest", "compileall", "py_compile"}
        )
        if executable == "ruff":
            safe = bool(args) and args[0] == "check" and "--fix" not in args
        return {"decision": "allow_once" if safe else "deny_once"}
    if request.get("kind") != "edit":
        return {"decision": "deny_once"}
    target = review.get("targetPath")
    if not isinstance(target, str) or not target:
        return {"decision": "deny_once"}
    try:
        resolved_target = Path(target).resolve(strict=False)
        resolved_target.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return {"decision": "deny_once"}
    allowed_targets = {
        (root / relative).resolve(strict=False) for relative in authorized_paths
    }
    if resolved_target not in allowed_targets:
        return {"decision": "deny_once"}
    return {"decision": "allow_turn"}


def _run_turn(
    *,
    workspace: Path,
    state_root: Path,
    journal_root: Path,
    prompt: str,
    history: list[dict[str, Any]],
    context_window: int | None,
    seed_entries: object,
    authorized_paths: tuple[Path, ...],
    runtime_profile_contract: Mapping[str, str] | None,
) -> tuple[TurnEvidence, list[dict[str, Any]]]:
    journal = RunJournal(workspace, data_dir=journal_root)

    def journal_factory(_workspace: Path) -> RunJournal:
        return journal

    # Acceptance cases exercise MiniCode itself, not user-installed external
    # MCP servers. Keeping them out makes the suite isolated and reproducible.
    runtime = create_agent_turn_runtime(
        workspace=workspace,
        prompt=prompt,
        include_mcp=False,
        allow_user_interaction=False,
    )
    try:
        runtime_profile = _runtime_profile_from_adapter(
            runtime.model,
            runtime.runtime,
        )
    except BaseException:
        runtime.dispose()
        raise
    if (
        runtime_profile_contract is not None
        and runtime_profile != dict(runtime_profile_contract)
    ):
        runtime.dispose()
        raise RuntimeError("acceptance runtime profile mismatch")
    runtime.permissions.prompt = lambda request: _isolated_write_approval(
        workspace,
        request,
        authorized_paths,
    )
    runtime.memory_manager = MemoryManager(
        project_root=workspace,
        data_root=state_root,
    )
    seeded_memory_ids = _seed_memory(runtime.memory_manager, seed_entries)
    if context_window is not None:
        if (
            type(context_window) is not int
            or context_window < 2_000
            or context_window > 1_000_000
        ):
            raise ValueError("contextWindow is outside acceptance bounds")
        runtime.context_manager.context_window = context_window
    run_id: str | None = None
    response = ""
    result_messages: list[dict[str, Any]] = []
    try:
        with observe_run(
            workspace=workspace,
            source="headless",
            title=prompt,
            journal_factory=journal_factory,
            enabled=True,
        ) as observation:
            run_id = observation.run_id
            emit_skill_routing_safely(observation, runtime.skill_routing)
            messages = prepare_conversation_messages(
                history,
                system_prompt=runtime.system_prompt,
                user_message=prompt,
            )
            result_messages = runtime.execute(messages, observation)
            assistant = next(
                (
                    message
                    for message in reversed(result_messages)
                    if message.get("role") == "assistant"
                ),
                None,
            )
            response = (
                str(assistant.get("content") or "")
                if isinstance(assistant, dict)
                else ""
            )
            observation.assistant_completed(
                content_present=bool(response),
                content_length=len(response),
            )
    finally:
        runtime.dispose()
    if run_id is None:
        raise RuntimeError("run observation did not produce an id")
    events = _all_events(journal, run_id)
    model_calls, input_tokens, output_tokens = _usage_from_events(journal, run_id)
    memory_claim_types = _snapshot_memory_claim_types(runtime.memory_manager)
    return (
        TurnEvidence(
            run_id=run_id,
            response=response[:_MAX_RESPONSE_CHARS],
            event_types=tuple(event.type for event in events),
            events=events,
            model_calls=model_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            runtime_profile_sha256=runtime_profile["profileSha256"],
            seeded_memory_ids=seeded_memory_ids,
            memory_claim_types=tuple(sorted(memory_claim_types.items())),
        ),
        result_messages,
    )


def _event_payloads(turns: list[TurnEvidence], event_type: str) -> list[dict]:
    return [
        event.payload
        for turn in turns
        for event in turn.events
        if getattr(event, "type", None) == event_type
        and isinstance(getattr(event, "payload", None), dict)
    ]


def _tool_operation_counts(
    turns: list[TurnEvidence],
    tool_name: str,
    outcome: str,
) -> list[int]:
    counts: list[int] = []
    for turn in turns:
        started: set[str] = set()
        finished: set[str] = set()
        succeeded = 0
        for event in turn.events:
            payload = getattr(event, "payload", None)
            if not isinstance(payload, dict):
                continue
            operation_id = payload.get("operationId")
            if not isinstance(operation_id, str) or not operation_id:
                continue
            if (
                getattr(event, "type", None) == "tool.started"
                and payload.get("toolName") == tool_name
            ):
                started.add(operation_id)
                continue
            if (
                getattr(event, "type", None) != "tool.finished"
                or payload.get("toolName") != tool_name
                or operation_id not in started
                or operation_id in finished
            ):
                continue
            finished.add(operation_id)
            if payload.get("paired") is True and payload.get("outcome") == outcome:
                succeeded += 1
        counts.append(succeeded)
    return counts


def _run_command_oracle(
    workspace: Path,
    oracle: Mapping[str, object],
) -> bool:
    raw_argv = oracle.get("argv")
    if (
        not isinstance(raw_argv, list)
        or not raw_argv
        or not all(
            isinstance(item, str) and item and "\x00" not in item
            for item in raw_argv
        )
    ):
        raise ValueError("command oracle argv is invalid")
    python_executable = os.environ.get("NORTH_STAR_PYTHON") or sys.executable
    argv = [
        python_executable if item == "{python}" else item
        for item in raw_argv
    ]
    timeout = oracle.get("timeoutSeconds", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
        raise ValueError("command oracle timeout is invalid")
    completed = subprocess.run(
        argv,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    expected = oracle.get("exitCode", 0)
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise ValueError("command oracle exitCode is invalid")
    return completed.returncode == expected


def _evaluate_oracle(
    oracle: Mapping[str, object],
    *,
    workspace: Path,
    turns: list[TurnEvidence],
    before_digest: str,
    journal: RunJournal,
    memory_claim_types: Mapping[str, frozenset[str]] | None = None,
    seeded_memory_ids: tuple[str, ...] = (),
) -> bool:
    kind = oracle.get("kind")
    if kind == "all_runs_completed":
        return all(
            (record := journal.get_run(turn.run_id)) is not None
            and record.status == "completed"
            for turn in turns
        )
    if kind == "canonical_success":
        payloads = _event_payloads(turns, "task.outcome")
        return bool(payloads) and all(
            payload.get("outcomeStatus") == "success" for payload in payloads
        )
    if kind == "command":
        return _run_command_oracle(workspace, oracle)
    if kind == "context_compacted":
        return any("context.compacted" in turn.event_types for turn in turns)
    if kind == "context_compaction_count":
        minimum = oracle.get("min", 1)
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise ValueError("context compaction minimum is invalid")
        return (
            sum(
                payload.get("effective") is True
                for payload in _event_payloads(turns, "context.compacted")
            )
            >= minimum
        )
    if kind in {"file_contains", "file_not_contains"}:
        target = workspace / _safe_relative(oracle.get("path"))
        needle = oracle.get("text")
        if not isinstance(needle, str) or not target.is_file():
            return False
        contains = needle in target.read_text(encoding="utf-8", errors="replace")
        return contains if kind == "file_contains" else not contains
    if kind == "memory_rendered":
        return any("memory.rendered" in turn.event_types for turn in turns)
    if kind == "memory_attributed":
        effective_seeded_ids = seeded_memory_ids or tuple(
            entry_id for turn in turns for entry_id in turn.seeded_memory_ids
        )
        contract = _memory_attribution_contract(
            oracle,
            turn_count=len(turns),
            seed_count=len(effective_seeded_ids),
        )
        if contract.source == "written":
            assert contract.source_turn is not None
            assert contract.claim_type is not None
            claim_types = dict(turns[contract.source_turn].memory_claim_types)
            if memory_claim_types is not None:
                claim_types.update(memory_claim_types)
            source_ids = {
                entry_id
                for entry_id in (
                    journal.get_written_memory_ids(
                        turns[contract.source_turn].run_id
                    )
                    or ()
                )
                if contract.claim_type in claim_types.get(entry_id, frozenset())
            }
        else:
            source_ids = {
                effective_seeded_ids[index] for index in contract.seed_indexes
            }
        rendered_ids = set(
            journal.get_rendered_memory_ids(turns[contract.rendered_turn].run_id)
            or ()
        )
        return len(source_ids & rendered_ids) >= contract.minimum
    if kind == "memory_injected":
        minimum = oracle.get("min", 1)
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise ValueError("memory injection minimum is invalid")
        return (
            sum(
                int(payload.get("renderedCount", 0))
                for payload in _event_payloads(turns, "memory.rendered")
                if payload.get("injected") is True
                and isinstance(payload.get("renderedCount"), int)
                and not isinstance(payload.get("renderedCount"), bool)
            )
            >= minimum
        )
    if kind == "memory_written":
        return any(journal.get_written_memory_ids(turn.run_id) for turn in turns)
    if kind == "no_source_edits":
        return _tree_digest(workspace) == before_digest
    if kind == "response_contains":
        values = oracle.get("values")
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError("response oracle values are invalid")
        response = turns[-1].response.casefold()
        return all(value.casefold() in response for value in values)
    if kind == "skill_loaded":
        qualified_name = oracle.get("qualifiedName")
        return isinstance(qualified_name, str) and any(
            payload.get("qualifiedName") == qualified_name
            for payload in _event_payloads(turns, "skill.loaded")
        )
    if kind == "subagent_count":
        minimum = oracle.get("min", 1)
        if isinstance(minimum, bool) or not isinstance(minimum, int):
            raise ValueError("subagent oracle minimum is invalid")
        return sum(
            len(journal.list_subagent_runs(turn.run_id)) for turn in turns
        ) >= minimum
    if kind in {"tool_failed", "tool_succeeded"}:
        tool_name, minimum, every_turn = _tool_operation_contract(oracle)
        expected_outcome = "error" if kind == "tool_failed" else "success"
        counts = _tool_operation_counts(turns, tool_name, expected_outcome)
        return (
            bool(counts) and all(count >= minimum for count in counts)
            if every_turn
            else sum(counts) >= minimum
        )
    if kind == "verification_passed":
        minimum, every_turn, verification_kind, sources = _verification_contract(
            oracle
        )
        counts = [
            sum(
                payload.get("outcome") == "passed"
                and payload.get("kind") == verification_kind
                and payload.get("source") in sources
                for payload in _event_payloads([turn], "task.verified")
            )
            for turn in turns
        ]
        return (
            bool(counts) and all(count >= minimum for count in counts)
            if every_turn
            else sum(counts) >= minimum
        )
    raise ValueError(f"unsupported oracle kind: {kind}")


def _execute_case(
    case: Mapping[str, Any],
    *,
    suite_root: Path,
    python_executable: str,
    runtime_profile_contract: Mapping[str, str] | None,
) -> tuple[dict[str, object], dict[str, object]]:
    case_id = str(case["id"])
    case_root = suite_root / "cases" / case_id
    if case_root.exists():
        shutil.rmtree(case_root)
    workspace = case_root / "workspace"
    state_root = case_root / "state"
    journal_root = case_root / "journal"
    _write_fixture(workspace, case.get("files", {}))
    before_digest = _tree_digest(workspace)
    turns: list[TurnEvidence] = []
    history: list[dict[str, Any]] = []
    started = time.monotonic()
    failure: str | None = None
    previous_python = os.environ.get("NORTH_STAR_PYTHON")
    os.environ["NORTH_STAR_PYTHON"] = python_executable
    try:
        with _working_directory(workspace):
            for index, turn in enumerate(case["turns"]):
                initial_history = turn.get("initialHistory", [])
                if initial_history:
                    if not isinstance(initial_history, list):
                        raise TypeError("initialHistory must be a list")
                    history = [
                        dict(message)
                        for message in initial_history
                        if isinstance(message, dict)
                    ]
                evidence, result_messages = _run_turn(
                    workspace=workspace,
                    state_root=state_root,
                    journal_root=journal_root,
                    prompt=str(turn["prompt"]),
                    history=history,
                    context_window=turn.get("contextWindow"),
                    seed_entries=(
                        case.get("memoryEntries") if index == 0 else None
                    ),
                    authorized_paths=tuple(
                        _safe_relative(path)
                        for path in case.get("authorizedPaths", [])
                    ),
                    runtime_profile_contract=runtime_profile_contract,
                )
                turns.append(evidence)
                history = result_messages if turn.get("carryHistory", False) else []
    except Exception as error:  # noqa: BLE001 - case failure is evidence
        # Exception text can contain provider payloads, paths, or credentials.
        # The failure class is sufficient to derive a failed public status.
        failure = type(error).__name__
    finally:
        if previous_python is None:
            os.environ.pop("NORTH_STAR_PYTHON", None)
        else:
            os.environ["NORTH_STAR_PYTHON"] = previous_python
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    journal = RunJournal(workspace, data_dir=journal_root)
    passed_oracles: list[str] = []
    oracle_failures: dict[str, str] = {}
    if failure is None:
        for oracle in case["oracles"]:
            oracle_id = str(oracle["id"])
            try:
                passed = _evaluate_oracle(
                    oracle,
                    workspace=workspace,
                    turns=turns,
                    before_digest=before_digest,
                    journal=journal,
                )
            except Exception as error:  # noqa: BLE001 - oracle error is evidence
                passed = False
                oracle_failures[oracle_id] = type(error).__name__
            if passed:
                passed_oracles.append(oracle_id)
            elif oracle_id not in oracle_failures:
                oracle_failures[oracle_id] = "oracle_not_satisfied"
    model_calls = sum(turn.model_calls for turn in turns)
    token_complete = bool(turns) and all(
        turn.input_tokens is not None and turn.output_tokens is not None
        for turn in turns
    )
    input_tokens = (
        sum(int(turn.input_tokens or 0) for turn in turns)
        if token_complete
        else None
    )
    output_tokens = (
        sum(int(turn.output_tokens or 0) for turn in turns)
        if token_complete
        else None
    )
    all_oracles = len(passed_oracles) == len(case["oracleIds"])
    status = "passed" if failure is None and all_oracles else "failed"
    run_ids = [turn.run_id for turn in turns]
    result: dict[str, object] = {
        "id": case_id,
        "status": status,
        "verificationPassed": all_oracles,
        "unsafeActionCount": 0,
        "userInterventionCount": 0,
        "durationMs": duration_ms,
        "modelCalls": model_calls,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "runId": run_ids[-1] if run_ids else None,
        "passedOracleIds": passed_oracles,
    }
    if runtime_profile_contract is not None:
        observed_profiles = {
            turn.runtime_profile_sha256
            for turn in turns
            if turn.runtime_profile_sha256 is not None
        }
        result["runtimeProfileSha256"] = (
            next(iter(observed_profiles)) if len(observed_profiles) == 1 else None
        )
    if len(run_ids) > 1:
        result["relatedRunIds"] = run_ids[:-1]
    private_evidence: dict[str, object] = {
        "id": case_id,
        "failure": failure,
        "oracleFailures": oracle_failures,
        "runIds": run_ids,
        "runtimeProfileSha256ByRun": [
            turn.runtime_profile_sha256 for turn in turns
        ],
        "writeAuthorization": (
            {
                "policy": "declared_workspace_paths_and_verifiers_only",
                "authorizedPaths": list(case.get("authorizedPaths", [])),
            }
            if case["mutability"] == "write"
            else {
                "policy": "read_only_verifiers_only",
                "authorizedPaths": [],
            }
        ),
    }
    return result, private_evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest_path = args.manifest.resolve()
    manifest, manifest_sha256 = _load_json_snapshot(manifest_path)
    all_cases = _validate_manifest(manifest)
    runtime_profile = _runtime_profile_contract(manifest)
    runtime_profile_sha256 = (
        runtime_profile["profileSha256"] if runtime_profile is not None else None
    )
    source_code_sha256 = _source_code_sha256()
    suite_id = str(manifest["suiteId"])
    case_contract_sha256 = {
        str(case["id"]): _canonical_contract_sha256(case)
        for case in all_cases
    }
    cases = list(all_cases)
    requested_ids = set(args.case_id)
    if requested_ids:
        known = {str(case["id"]) for case in cases}
        unknown = sorted(requested_ids - known)
        if unknown:
            raise ValueError(f"unknown case ids: {unknown}")
        cases = [case for case in cases if case["id"] in requested_ids]
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("limit must be positive")
        cases = cases[: args.limit]
    output_path = args.output.resolve()
    suite_root = output_path.parent / (output_path.stem + "-evidence")
    existing: dict[str, dict[str, object]] = {}
    if args.resume and output_path.is_file():
        previous = _load_json(output_path)
        if (
            type(previous.get("schemaVersion")) is not int
            or previous.get("schemaVersion") != 1
        ):
            raise ValueError("resume schemaVersion mismatch")
        if previous.get("suiteId") != manifest.get("suiteId"):
            raise ValueError("resume suiteId mismatch")
        if previous.get("manifestSha256") != manifest_sha256:
            raise ValueError("resume manifestSha256 mismatch")
        if previous.get("sourceCodeSha256") != source_code_sha256:
            raise ValueError("resume sourceCodeSha256 mismatch")
        previous_results = previous.get("results")
        if not isinstance(previous_results, list):
            raise ValueError("resume results contract is invalid")
        for item in previous_results:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError("resume result identity is invalid")
            case_id = str(item["id"])
            if case_id in existing:
                raise ValueError("resume result identity is duplicated")
            expected_contract = case_contract_sha256.get(case_id)
            case = next(
                (candidate for candidate in all_cases if candidate["id"] == case_id),
                None,
            )
            if expected_contract is None or case is None:
                raise ValueError("resume caseContractSha256 mismatch")
            evidence, evidence_sha256 = _load_case_evidence_snapshot(
                suite_root,
                case_id,
            )
            evidence = _validate_private_case_evidence(
                evidence,
                suite_id=suite_id,
                manifest_sha256=manifest_sha256,
                source_code_sha256=source_code_sha256,
                case=case,
                case_contract_sha256=expected_contract,
                runtime_profile_sha256=runtime_profile_sha256,
            )
            existing[case_id] = _validate_public_case_result(
                item,
                case=case,
                evidence=evidence,
                evidence_sha256=evidence_sha256,
                runtime_profile_sha256=runtime_profile_sha256,
            )
        _validate_public_result_envelope(
            previous,
            manifest_path=manifest_path,
            all_cases=all_cases,
            results=existing,
            runtime_profile=runtime_profile,
        )
    results = dict(existing)
    for index, case in enumerate(cases, start=1):
        _assert_execution_identity(
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            source_code_sha256=source_code_sha256,
        )
        case_id = str(case["id"])
        if case_id in results:
            print(f"[{index}/{len(cases)}] {case_id}: resumed", flush=True)
            continue
        print(f"[{index}/{len(cases)}] {case_id}: running", flush=True)
        result, private = _execute_case(
            case,
            suite_root=suite_root,
            python_executable=str(args.python),
            runtime_profile_contract=runtime_profile,
        )
        _assert_execution_identity(
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            source_code_sha256=source_code_sha256,
        )
        evidence = _build_case_evidence(
            result=result,
            private=private,
            suite_id=suite_id,
            manifest_sha256=manifest_sha256,
            source_code_sha256=source_code_sha256,
            case=case,
            case_contract_sha256=case_contract_sha256[case_id],
            runtime_profile_sha256=runtime_profile_sha256,
        )
        evidence = _validate_private_case_evidence(
            evidence,
            suite_id=suite_id,
            manifest_sha256=manifest_sha256,
            source_code_sha256=source_code_sha256,
            case=case,
            case_contract_sha256=case_contract_sha256[case_id],
            runtime_profile_sha256=runtime_profile_sha256,
        )
        evidence_path = _case_evidence_path(suite_root, case_id)
        written_evidence_sha256 = _atomic_json(
            evidence_path,
            evidence,
            private=True,
        )
        stored_evidence, evidence_sha256 = _load_case_evidence_snapshot(
            suite_root,
            case_id,
        )
        if evidence_sha256 != written_evidence_sha256:
            raise RuntimeError("case evidence changed during atomic publication")
        stored_evidence = _validate_private_case_evidence(
            stored_evidence,
            suite_id=suite_id,
            manifest_sha256=manifest_sha256,
            source_code_sha256=source_code_sha256,
            case=case,
            case_contract_sha256=case_contract_sha256[case_id],
            runtime_profile_sha256=runtime_profile_sha256,
        )
        result = _project_public_case_result(
            stored_evidence,
            case=case,
            evidence_sha256=evidence_sha256,
            runtime_profile_sha256=runtime_profile_sha256,
        )
        results[case_id] = result
        partial = _public_result_envelope(
            manifest=manifest,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            source_code_sha256=source_code_sha256,
            all_cases=all_cases,
            selected_cases=cases,
            results=results,
            runtime_profile=runtime_profile,
            finalized=False,
        )
        _atomic_json(output_path, partial)
        print(
            f"[{index}/{len(cases)}] {case_id}: {result['status']} "
            f"({len(result['passedOracleIds'])}/{len(case['oracleIds'])} oracles)",
            flush=True,
        )
    _assert_execution_identity(
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        source_code_sha256=source_code_sha256,
    )
    final = _public_result_envelope(
        manifest=manifest,
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        source_code_sha256=source_code_sha256,
        all_cases=all_cases,
        selected_cases=cases,
        results=results,
        runtime_profile=runtime_profile,
        finalized=True,
    )
    _atomic_json(output_path, final)
    return 0 if final["suiteStatus"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
