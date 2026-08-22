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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from minicode.agent_runtime import (  # noqa: E402
    create_agent_turn_runtime,
    prepare_conversation_messages,
)
from minicode.memory import MemoryManager, MemoryScope  # noqa: E402
from minicode.run_events import emit_skill_routing_safely  # noqa: E402
from minicode.run_journal import RunJournal  # noqa: E402
from minicode.run_lifecycle import observe_run  # noqa: E402


_CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
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
        "memory_injected",
        "memory_rendered",
        "memory_written",
        "no_source_edits",
        "response_contains",
        "skill_loaded",
        "subagent_count",
        "tool_failed",
        "tool_succeeded",
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
    }
)
_MAX_RESPONSE_CHARS = 200_000
_MAX_COMMAND_OUTPUT_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class TurnEvidence:
    run_id: str
    response: str
    event_types: tuple[str, ...]
    events: tuple[object, ...]
    model_calls: int
    input_tokens: int | None
    output_tokens: int | None


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return value


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


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


def _validate_manifest(document: Mapping[str, object]) -> list[dict[str, Any]]:
    if document.get("schemaVersion") != 1:
        raise ValueError("unsupported north-star manifest schema")
    suite_id = document.get("suiteId")
    raw_cases = document.get("cases")
    if not isinstance(suite_id, str) or not suite_id:
        raise ValueError("manifest suiteId is invalid")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("manifest cases are missing")
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
        if any(
            not isinstance(turn, dict)
            or not isinstance(turn.get("prompt"), str)
            or not turn["prompt"].strip()
            for turn in turns
        ):
            raise ValueError(f"invalid turns for case {case_id}")
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


def _seed_memory(manager: MemoryManager, entries: object) -> None:
    if entries in (None, []):
        return
    if not isinstance(entries, list):
        raise TypeError("memoryEntries must be a list")
    for item in entries:
        if not isinstance(item, dict):
            raise TypeError("memory entry must be an object")
        content = item.get("content")
        if not isinstance(content, str) or not content:
            raise ValueError("memory entry content is invalid")
        manager.add_entry(
            MemoryScope.PROJECT,
            str(item.get("category") or "project-convention"),
            content,
            tags=[str(tag) for tag in item.get("tags", []) if isinstance(tag, str)],
        )


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
    runtime.permissions.prompt = lambda request: _isolated_write_approval(
        workspace,
        request,
        authorized_paths,
    )
    runtime.memory_manager = MemoryManager(
        project_root=workspace,
        data_root=state_root,
    )
    _seed_memory(runtime.memory_manager, seed_entries)
    if context_window is not None:
        if context_window < 2_000 or context_window > 1_000_000:
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
    return (
        TurnEvidence(
            run_id=run_id,
            response=response[:_MAX_RESPONSE_CHARS],
            event_types=tuple(event.type for event in events),
            events=events,
            model_calls=model_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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
    raise ValueError(f"unsupported oracle kind: {kind}")


def _execute_case(
    case: Mapping[str, Any],
    *,
    suite_root: Path,
    python_executable: str,
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
                )
                turns.append(evidence)
                history = result_messages if turn.get("carryHistory", False) else []
    except Exception as error:  # noqa: BLE001 - case failure is evidence
        failure = f"{type(error).__name__}: {error}"[:500]
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
                oracle_failures[oracle_id] = f"{type(error).__name__}: {error}"[:300]
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
    if len(run_ids) > 1:
        result["relatedRunIds"] = run_ids[:-1]
    private_evidence: dict[str, object] = {
        "id": case_id,
        "failure": failure,
        "oracleFailures": oracle_failures,
        "runIds": run_ids,
        "responses": [turn.response for turn in turns],
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
    _atomic_json(case_root / "evidence.json", private_evidence)
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
    manifest = _load_json(args.manifest)
    cases = _validate_manifest(manifest)
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
        if previous.get("suiteId") != manifest.get("suiteId"):
            raise ValueError("resume suiteId mismatch")
        existing = {
            str(item["id"]): dict(item)
            for item in previous.get("results", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
    results = dict(existing)
    for index, case in enumerate(cases, start=1):
        case_id = str(case["id"])
        if case_id in results:
            print(f"[{index}/{len(cases)}] {case_id}: resumed", flush=True)
            continue
        print(f"[{index}/{len(cases)}] {case_id}: running", flush=True)
        result, _private = _execute_case(
            case,
            suite_root=suite_root,
            python_executable=str(args.python),
        )
        results[case_id] = result
        partial = {
            "schemaVersion": 1,
            "suiteId": manifest["suiteId"],
            "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "sourceReport": str(args.manifest),
            "results": [
                results[str(item["id"])]
                for item in _validate_manifest(manifest)
                if str(item["id"]) in results
            ],
        }
        _atomic_json(output_path, partial)
        print(
            f"[{index}/{len(cases)}] {case_id}: {result['status']} "
            f"({len(result['passedOracleIds'])}/{len(case['oracleIds'])} oracles)",
            flush=True,
        )
    return 0 if all(result.get("status") == "passed" for result in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
