#!/usr/bin/env python3
"""Execute the frozen multi-agent-cost manifest through the real MiniCode runtime.

The runner reuses the sealed north-star live harness (isolation, deterministic
oracles, bounded approval) and adds what the cost domain needs: per-sub-agent
telemetry rows (type, typed outcome, duration, model turns, tool calls, and
attributed input/output tokens) plus a parent/sub-agent coordination token
split, all read back from the production Run/SubagentRun journals. A
``delegation_rejected`` oracle pins the sealed direct-dispatch refusal: the
harness captures the bounded ``error[<code>]`` token from failed task-tool
results (never the prose) and requires that no sub-agent run was journaled.
Because the
unattended harness has no UI to hold the production human-approval authority,
it injects a bounded workflow approval provider that approves only when the
delegation's declared write paths stay inside the case manifest's
``authorizedPaths``; every decision is recorded in the case's private evidence.

Execution is serial and resumable. Every case runs in a child process with a
hard wall-clock cap so a stuck provider call can never deadlock the suite;
the per-turn model-call budget caps runaway loops. Prompts and responses are
never serialized into the public result file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from minicode.run_journal import RunJournal  # noqa: E402

import run_north_star_live as north_star  # noqa: E402


_MAC_CASE_ID_RE = north_star._CASE_ID_RE
_RUN_ID_RE = north_star._RUN_ID_RE
_SUBAGENT_TYPES = frozenset({"explore", "plan", "general", "workflow"})
_EXTRA_ORACLE_KINDS = frozenset({"delegation_rejected", "subagent_type_count"})
_ORACLE_KINDS = north_star._ORACLE_KINDS | _EXTRA_ORACLE_KINDS
# ToolResult error envelopes are production-emitted, not model prose: a failed
# task-tool result opens with ``error[<code>]``. The capture hook keeps only
# the bounded code token so rejection oracles stay deterministic without
# serializing tool output.
_TASK_ERROR_CODE_RE = re.compile(r"^error\[([a-z0-9_]{1,64})\]")
_MAX_CAPTURED_TASK_ERROR_CODES = 64

_CASE_WALL_TIMEOUT_SECONDS = 20 * 60
_MODEL_CALL_CAP = 100
_CHILD_CRASH_RETRIES = 2
_CHILD_CRASH_BACKOFF_SECONDS = (30, 90)
_INTER_CASE_DELAY_SECONDS = 10.0
_CHILD_RESULT_PREFIX = "CHILD_RESULT "
_TRANSIENT_FAILURE_CLASSES = frozenset(
    {
        "Timeout",
        "TimeoutError",
        "ConnectionError",
        "ConnectTimeout",
        "ReadTimeout",
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
    }
)

_APPROVAL_APPROVED_BY = "acceptance_bounded_approval"
# The approval clock is rechecked at the commit boundary, so the TTL must
# cover the remaining chain (executor -> verifier -> reviewer -> commit)
# under acceptance-provider latency; the 20-minute case wall cap still bounds
# the whole run.
_APPROVAL_TTL_SECONDS = 1800


class _BoundedWorkflowApprovalProvider:
    """Harness-owned human-approval authority bounded by the case manifest.

    Production holds workflow human approval in the UI; the unattended
    acceptance harness injects this provider through the embedder channel
    (``_workflowHumanApprovalProvider``) instead. It approves only when the
    workflow delegation captured at the task-tool boundary declares write
    paths within the case manifest's ``authorizedPaths``. Anything else — no
    captured delegation, a task-id mismatch, or an out-of-scope path — is
    denied, and denial keeps the production fail-closed ``blocked`` outcome.
    Every decision is recorded into the case's private evidence.
    """

    def __init__(self, authorized_paths: tuple[str, ...]) -> None:
        self._authorized_paths = authorized_paths
        self._delegations: list[dict[str, object]] = []
        self.records: list[dict[str, object]] = []
        self.task_error_codes: list[str] = []

    def capture_task_result(self, result: object) -> None:
        """Record the bounded error code of one failed task-tool result."""
        if len(self.task_error_codes) >= _MAX_CAPTURED_TASK_ERROR_CODES:
            return
        if getattr(result, "ok", None) is not False:
            return
        output = getattr(result, "output", None)
        if not isinstance(output, str):
            return
        match = _TASK_ERROR_CODE_RE.match(output)
        if match is not None:
            self.task_error_codes.append(match.group(1))

    def capture_task_call(self, call_input: object) -> None:
        """Observe one validated task-tool call before it runs."""
        if not isinstance(call_input, dict):
            return
        if call_input.get("agent_type") != "workflow":
            return
        request = call_input.get("workflow_request")
        scope = request.get("scope") if isinstance(request, dict) else None
        profile = request.get("profile") if isinstance(request, dict) else None
        write_paths = scope.get("writePaths") if isinstance(scope, dict) else None
        task_id = profile.get("taskId") if isinstance(profile, dict) else None
        self._delegations.append(
            {
                "taskId": task_id if isinstance(task_id, str) else None,
                "writePaths": (
                    [path for path in write_paths if isinstance(path, str)]
                    if isinstance(write_paths, list)
                    else None
                ),
            }
        )

    def _path_within_authorized(self, path: str) -> bool:
        try:
            relative = north_star._safe_relative(path).as_posix()
        except (TypeError, ValueError):
            return False
        return any(
            root == "." or relative == root or relative.startswith(root.rstrip("/") + "/")
            for root in self._authorized_paths
        )

    def consume_approval(
        self,
        *,
        approval_request_id: str,
        task_id: str,
        request_digest: str,
        base_digest: str,
        analyst_artifact_id: str,
        analyst_digest: str,
        deadline_monotonic: object = None,
    ) -> dict[str, object]:
        delegation = self._delegations[-1] if self._delegations else None
        declared = delegation["writePaths"] if delegation is not None else None
        reason: str | None = None
        if delegation is None or not isinstance(declared, list):
            reason = "no captured workflow delegation"
        elif delegation["taskId"] != task_id:
            reason = "approval task does not match the captured delegation"
        elif not all(self._path_within_authorized(path) for path in declared):
            reason = "declared write paths exceed the case authorizedPaths"
        approved = reason is None
        record: dict[str, object] = {
            "approvalRequestId": approval_request_id,
            "taskId": task_id,
            "declaredWritePaths": declared,
            "authorizedPaths": list(self._authorized_paths),
            "decision": "approved" if approved else "denied",
        }
        if reason is not None:
            record["reason"] = reason
        self.records.append(record)
        decision = "approved" if approved else "denied"
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=_APPROVAL_TTL_SECONDS)
        ).isoformat().replace("+00:00", "Z")
        # A denied decision deliberately fails the production response
        # validation, which maps it to the fail-closed blocked outcome.
        return {
            "approvalVersion": 1,
            "approvalRequestId": approval_request_id,
            "taskId": task_id,
            "requestDigest": request_digest,
            "baseDigest": base_digest,
            "analystDigest": analyst_digest,
            "decision": decision,
            "approvedBy": _APPROVAL_APPROVED_BY,
            "expiresAt": expires_at,
        }


def _load_json(path: Path) -> dict[str, Any]:
    return north_star._load_json(path)


def _case_contract_sha256(case: Mapping[str, object]) -> str:
    """Identity of the executable definition actually measured."""
    projection = {
        "id": case.get("id"),
        "mutability": case.get("mutability"),
        "turns": case.get("turns"),
        "files": case.get("files", {}),
        "oracles": case.get("oracles"),
        "authorizedPaths": case.get("authorizedPaths", []),
    }
    return north_star._canonical_contract_sha256(projection)


def _validate_executable_case(raw: object, *, suite: str) -> dict[str, Any]:
    """Mirror the north-star per-case contract, extended by cost oracles."""
    if not isinstance(raw, dict):
        raise TypeError(f"{suite} case must be an object")
    case_id = raw.get("id")
    turns = raw.get("turns")
    files = raw.get("files", {})
    oracle_ids = raw.get("oracleIds")
    oracles = raw.get("oracles")
    authorized_paths = raw.get("authorizedPaths", [])
    if (
        not isinstance(case_id, str)
        or not _MAC_CASE_ID_RE.fullmatch(case_id)
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
        raise ValueError(f"invalid multi-agent cost executable case: {case_id!r}")
    for turn in turns:
        if (
            not isinstance(turn, dict)
            or not isinstance(turn.get("prompt"), str)
            or not turn["prompt"].strip()
            or set(turn) - {"carryHistory", "contextWindow", "initialHistory", "prompt"}
        ):
            raise ValueError(f"invalid turns for case {case_id}")
    for fixture_path, content in files.items():
        north_star._safe_relative(fixture_path)
        if not isinstance(content, str):
            raise TypeError(f"fixture content must be text for case {case_id}")
    for authorized_path in authorized_paths:
        north_star._safe_relative(authorized_path)
    projected_ids: list[str] = []
    for oracle in oracles:
        if (
            not isinstance(oracle, dict)
            or not isinstance(oracle.get("id"), str)
            or not oracle["id"]
            or oracle.get("kind") not in _ORACLE_KINDS
        ):
            raise ValueError(f"invalid oracle for case {case_id}")
        kind = oracle["kind"]
        if kind in {"tool_failed", "tool_succeeded"}:
            north_star._tool_operation_contract(oracle)
        if kind == "verification_passed":
            north_star._verification_contract(oracle)
        if kind == "response_contains":
            values = oracle.get("values")
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(item, str) or not item for item in values)
            ):
                raise ValueError(f"response_contains oracle invalid for case {case_id}")
        if kind == "subagent_count":
            minimum = oracle.get("min", 1)
            if type(minimum) is not int or not 1 <= minimum <= 10_000:
                raise ValueError(f"subagent_count oracle invalid for case {case_id}")
        if kind == "delegation_rejected":
            error_code = oracle.get("errorCode")
            if (
                not isinstance(error_code, str)
                or re.fullmatch(r"[a-z0-9_]{1,64}", error_code) is None
            ):
                raise ValueError(
                    f"delegation_rejected oracle invalid for case {case_id}"
                )
        if kind == "subagent_type_count":
            minimum = oracle.get("min", 1)
            if (
                oracle.get("agentType") not in _SUBAGENT_TYPES
                or type(minimum) is not int
                or not 1 <= minimum <= 10_000
            ):
                raise ValueError(
                    f"subagent_type_count oracle invalid for case {case_id}"
                )
        if kind == "command":
            argv = oracle.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(item, str) and item for item in argv)
            ):
                raise ValueError(f"command oracle invalid for case {case_id}")
        projected_ids.append(str(oracle["id"]))
    if projected_ids != oracle_ids or len(set(oracle_ids)) != len(oracle_ids):
        raise ValueError(f"oracle identity mismatch for case {case_id}")
    return dict(raw)


def _resolve_cases(
    manifest: Mapping[str, object],
    north_star_manifest: Mapping[str, object],
) -> list[dict[str, Any]]:
    """Join every frozen case to its executable definition."""
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("multi-agent cost manifest requires non-empty cases")
    north_star_cases = {
        str(case["id"]): case
        for case in north_star_manifest.get("cases", [])
        if isinstance(case, dict)
    }
    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise TypeError("multi-agent cost case must be an object")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or case_id in seen:
            raise ValueError("multi-agent cost case identity is invalid")
        seen.add(case_id)
        north_star_case_id = raw.get("northStarCaseId")
        if north_star_case_id is not None:
            source = north_star_cases.get(str(north_star_case_id))
            if source is None:
                raise ValueError(f"north-star join target missing for {case_id}")
            if list(source.get("oracleIds", [])) != list(raw.get("oracleIds", [])):
                raise ValueError(f"north-star oracle join mismatch for {case_id}")
            executable = dict(source)
            executable["id"] = case_id
        else:
            executable = dict(raw)
        resolved.append(_validate_executable_case(executable, suite="cost"))
    return resolved


def _evaluate_cost_oracle(
    oracle: Mapping[str, object],
    *,
    workspace: Path,
    turns: list[north_star.TurnEvidence],
    before_digest: str,
    journal: RunJournal,
    observed_task_error_codes: tuple[str, ...] = (),
) -> bool:
    if oracle.get("kind") == "delegation_rejected":
        error_code = oracle.get("errorCode")
        if not isinstance(error_code, str) or not error_code:
            raise ValueError("delegation_rejected oracle error code is invalid")
        no_subagent_started = all(
            not journal.list_subagent_runs(turn.run_id) for turn in turns
        )
        return error_code in observed_task_error_codes and no_subagent_started
    if oracle.get("kind") == "subagent_type_count":
        minimum = oracle.get("min", 1)
        if type(minimum) is not int or minimum < 1:
            raise ValueError("subagent_type_count oracle minimum is invalid")
        agent_type = oracle.get("agentType")
        return (
            sum(
                1
                for turn in turns
                for summary in journal.list_subagent_runs(turn.run_id)
                if summary.agent_type == agent_type
            )
            >= minimum
        )
    return north_star._evaluate_oracle(
        oracle,
        workspace=workspace,
        turns=turns,
        before_digest=before_digest,
        journal=journal,
    )


def _parent_usage(turns: list[north_star.TurnEvidence]) -> tuple[int, int] | None:
    """Sum parent-only model usage; sub-agents write to the sidecar journal."""
    input_tokens = 0
    output_tokens = 0
    for turn in turns:
        for event in turn.events:
            if getattr(event, "type", None) != "model.completed":
                continue
            payload = getattr(event, "payload", {})
            usage = payload.get("usage") if isinstance(payload, dict) else None
            observed_input = (
                usage.get("inputTokens") if isinstance(usage, dict) else None
            )
            observed_output = (
                usage.get("outputTokens") if isinstance(usage, dict) else None
            )
            if (
                type(observed_input) is not int
                or type(observed_output) is not int
            ):
                return None
            input_tokens += observed_input
            output_tokens += observed_output
    return input_tokens, output_tokens


def _subagent_rows(
    journal: RunJournal,
    run_ids: list[str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for run_id in run_ids:
        for summary in journal.list_subagent_runs(run_id):
            row: dict[str, object] = {
                "subagentId": summary.subagent_id,
                "agentType": summary.agent_type,
                "outcome": summary.outcome,
                "durationMs": summary.duration_ms,
                "modelTurns": summary.model_turns,
                "toolCalls": summary.tool_calls,
            }
            if summary.input_tokens is not None and summary.output_tokens is not None:
                row["inputTokens"] = summary.input_tokens
                row["outputTokens"] = summary.output_tokens
            rows.append(row)
    return rows


def _case_token_split(
    turns: list[north_star.TurnEvidence],
    subagents: list[dict[str, object]],
) -> tuple[int | None, int | None, dict[str, int] | None]:
    """Derive case totals and the parent/sub-agent coordination split.

    Totals exist only when every participant's usage is recorded; partial
    telemetry degrades to absent fields instead of an understated total.
    """
    parent = _parent_usage(turns)
    if parent is None or not turns:
        return None, None, None
    if not subagents:
        return parent[0], parent[1], {
            "parentInputTokens": parent[0],
            "parentOutputTokens": parent[1],
            "subagentInputTokens": 0,
            "subagentOutputTokens": 0,
        }
    sub_input = 0
    sub_output = 0
    for row in subagents:
        row_input = row.get("inputTokens")
        row_output = row.get("outputTokens")
        if type(row_input) is not int or type(row_output) is not int:
            return None, None, None
        sub_input += row_input
        sub_output += row_output
    return (
        parent[0] + sub_input,
        parent[1] + sub_output,
        {
            "parentInputTokens": parent[0],
            "parentOutputTokens": parent[1],
            "subagentInputTokens": sub_input,
            "subagentOutputTokens": sub_output,
        },
    )


def _execute_case(case: Mapping[str, Any], *, suite_root: Path) -> dict[str, object]:
    """Run one case end to end inside the child process."""
    case_id = str(case["id"])
    case_root = suite_root / "cases" / case_id
    if case_root.exists():
        shutil.rmtree(case_root)
    workspace = north_star._case_workspace(suite_root, case_id)
    if workspace.parent.exists():
        shutil.rmtree(workspace.parent)
    state_root = case_root / "state"
    journal_root = case_root / "journal"
    north_star._write_fixture(workspace, case.get("files", {}))
    before_digest = north_star._tree_digest(workspace)
    turns: list[north_star.TurnEvidence] = []
    history: list[dict[str, Any]] = []
    started = time.monotonic()
    failure: str | None = None
    python_executable = sys.executable
    approval_provider = _BoundedWorkflowApprovalProvider(
        tuple(
            north_star._safe_relative(path).as_posix()
            for path in case.get("authorizedPaths", [])
        )
    )
    previous_python = os.environ.get("NORTH_STAR_PYTHON")
    os.environ["NORTH_STAR_PYTHON"] = python_executable
    try:
        with north_star._working_directory(workspace):
            for index, turn in enumerate(case["turns"]):
                evidence, result_messages = north_star._run_turn(
                    workspace=workspace,
                    state_root=state_root,
                    journal_root=journal_root,
                    prompt=str(turn["prompt"]),
                    history=history,
                    context_window=turn.get("contextWindow"),
                    seed_entries=None,
                    authorized_paths=tuple(
                        north_star._safe_relative(path)
                        for path in case.get("authorizedPaths", [])
                    ),
                    runtime_profile_contract=None,
                    workflow_human_approval=approval_provider,
                )
                turns.append(evidence)
                history = result_messages if turn.get("carryHistory", False) else []
    except Exception as error:  # noqa: BLE001 - case failure is evidence
        # Only the failure class is recorded; exception text can carry
        # provider payloads or credentials.
        failure = type(error).__name__
    finally:
        if previous_python is None:
            os.environ.pop("NORTH_STAR_PYTHON", None)
        else:
            os.environ["NORTH_STAR_PYTHON"] = previous_python
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    run_ids = [turn.run_id for turn in turns]
    journal = RunJournal(workspace, data_dir=journal_root)
    passed_oracles: list[str] = []
    oracle_failures: dict[str, str] = {}
    if failure is None:
        for oracle in case["oracles"]:
            oracle_id = str(oracle["id"])
            try:
                passed = _evaluate_cost_oracle(
                    oracle,
                    workspace=workspace,
                    turns=turns,
                    before_digest=before_digest,
                    journal=journal,
                    observed_task_error_codes=tuple(
                        approval_provider.task_error_codes
                    ),
                )
            except Exception as error:  # noqa: BLE001 - oracle error is evidence
                passed = False
                oracle_failures[oracle_id] = type(error).__name__
            if passed:
                passed_oracles.append(oracle_id)
            elif oracle_id not in oracle_failures:
                oracle_failures[oracle_id] = "oracle_not_satisfied"
    subagents = _subagent_rows(journal, run_ids) if run_ids else []
    input_tokens, output_tokens, coordination = _case_token_split(turns, subagents)
    parent_calls = sum(
        1
        for turn in turns
        for event in turn.events
        if getattr(event, "type", None) == "model.started"
    )
    model_calls = parent_calls + sum(
        int(row.get("modelTurns") or 0) for row in subagents
    )
    all_oracles = len(passed_oracles) == len(case["oracleIds"])
    result: dict[str, object] = {
        "id": case_id,
        "status": "passed" if failure is None and all_oracles else "failed",
        "verificationPassed": all_oracles,
        "unsafeActionCount": 0,
        "userInterventionCount": 0,
        "durationMs": duration_ms,
        "modelCalls": model_calls,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "runId": run_ids[-1] if run_ids else None,
        "passedOracleIds": passed_oracles,
        "subagents": subagents,
    }
    if coordination is not None:
        result["coordination"] = coordination
    private: dict[str, object] = {
        "id": case_id,
        "caseContractSha256": _case_contract_sha256(case),
        "failure": failure,
        "oracleFailures": oracle_failures,
        "transient": failure in _TRANSIENT_FAILURE_CLASSES,
        "workflowApprovals": approval_provider.records,
        "observedTaskErrorCodes": sorted(set(approval_provider.task_error_codes)),
    }
    shutil.rmtree(workspace.parent, ignore_errors=True)
    return {"result": result, "private": private}


def _private_evidence_path(suite_root: Path, case_id: str) -> Path:
    return suite_root / "cases" / case_id / "cost-evidence.json"


def _case_result_path(suite_root: Path, case_id: str) -> Path:
    return suite_root / "case-results" / f"{case_id}.json"


def _run_child(args: argparse.Namespace) -> int:
    """Child mode: execute exactly one case and persist its evidence."""
    os.environ.setdefault(
        "MINI_CODE_TURN_BUDGET_MODEL_CALLS", str(_MODEL_CALL_CAP)
    )
    # The 120s interactive sub-agent deadline cannot fit a multi-stage
    # workflow chain under the acceptance provider's latency; the per-case
    # 20-minute wall cap remains the bounding control.
    os.environ.setdefault("MINICODE_TASK_TIMEOUT_SECONDS", "900")
    manifest = _load_json(args.manifest.resolve())
    north_star_manifest = _load_json(args.north_star_manifest.resolve())
    cases = _resolve_cases(manifest, north_star_manifest)
    case = next(
        (candidate for candidate in cases if candidate["id"] == args.child_case_id),
        None,
    )
    if case is None:
        raise ValueError(f"unknown case id: {args.child_case_id}")
    suite_root = _suite_root(args.output)
    outcome = _execute_case(case, suite_root=suite_root)
    north_star._atomic_json(
        _private_evidence_path(suite_root, case["id"]),
        outcome["private"],
        private=True,
    )
    north_star._atomic_json(
        _case_result_path(suite_root, case["id"]),
        outcome["result"],
    )
    print(_CHILD_RESULT_PREFIX + json.dumps(outcome["private"], sort_keys=True))
    return 0


def _suite_root(output: Path) -> Path:
    return output.resolve().parent / (output.resolve().stem + "-evidence")


def _load_resume_state(
    output: Path,
    contracts: Mapping[str, str],
) -> dict[str, dict[str, object]]:
    """Read previously completed results whose case contracts still match."""
    suite_root = _suite_root(output)
    results: dict[str, dict[str, object]] = {}
    if not output.is_file():
        return results
    document = _load_json(output)
    raw_results = document.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("resume results contract is invalid")
    for item in raw_results:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("resume result identity is invalid")
        case_id = str(item["id"])
        evidence_path = _private_evidence_path(suite_root, case_id)
        if not evidence_path.is_file():
            continue
        evidence = _load_json(evidence_path)
        if evidence.get("caseContractSha256") != contracts.get(case_id):
            continue
        results[case_id] = item
    return results


def _recover_timeout_result(
    case: Mapping[str, Any],
    *,
    suite_root: Path,
    duration_ms: int,
) -> dict[str, object] | None:
    """Project whatever the killed child recorded into a failed result."""
    case_id = str(case["id"])
    case_root = suite_root / "cases" / case_id
    workspace = north_star._case_workspace(suite_root, case_id)
    journal_root = case_root / "journal"
    if not workspace.is_dir() or not journal_root.is_dir():
        return None
    try:
        journal = RunJournal(workspace, data_dir=journal_root)
        runs_dir = journal_root / "dashboard" / "workspaces"
        discovered: list[str] = []
        for workspace_dir in runs_dir.glob("*/runs/*"):
            if _RUN_ID_RE.fullmatch(workspace_dir.name):
                discovered.append(workspace_dir.name)
        if len(set(discovered)) != 1:
            return None
        run_id = discovered[0]
        record = journal.get_run(run_id)
        if record is None:
            return None
        events = north_star._all_events(journal, run_id)
        turn_stub = north_star.TurnEvidence(
            run_id=run_id,
            response="",
            event_types=tuple(event.type for event in events),
            events=events,
            model_calls=0,
            input_tokens=None,
            output_tokens=None,
        )
        subagents = _subagent_rows(journal, [run_id])
        input_tokens, output_tokens, coordination = _case_token_split(
            [turn_stub], subagents
        )
        parent_calls = sum(
            1 for event in events if getattr(event, "type", None) == "model.started"
        )
        model_calls = parent_calls + sum(
            int(row.get("modelTurns") or 0) for row in subagents
        )
        result: dict[str, object] = {
            "id": case_id,
            "status": "failed",
            "verificationPassed": False,
            "unsafeActionCount": 0,
            "userInterventionCount": 0,
            "durationMs": duration_ms,
            "modelCalls": model_calls,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "runId": run_id,
            "passedOracleIds": [],
            "subagents": subagents,
        }
        if coordination is not None:
            result["coordination"] = coordination
        return result
    except Exception:  # noqa: BLE001 - partial evidence may be unreadable
        return None


def _public_envelope(
    *,
    manifest: Mapping[str, object],
    manifest_path: Path,
    ordered_ids: list[str],
    results: Mapping[str, dict[str, object]],
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "suiteId": manifest["suiteId"],
        "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "description": (
            "Live multi-agent cost evidence collected by "
            "scripts/run_multi_agent_cost_live.py against the real runtime."
        ),
        "sourceReport": north_star._public_source_report(manifest_path),
        "results": [
            results[case_id] for case_id in ordered_ids if case_id in results
        ],
    }


def _run_orchestrator(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    manifest = _load_json(manifest_path)
    north_star_manifest = _load_json(args.north_star_manifest.resolve())
    all_cases = _resolve_cases(manifest, north_star_manifest)
    requested = set(args.case_id)
    cases = all_cases
    if requested:
        known = {str(case["id"]) for case in all_cases}
        unknown = sorted(requested - known)
        if unknown:
            raise ValueError(f"unknown case ids: {unknown}")
        cases = [case for case in all_cases if case["id"] in requested]
    # Contracts and resume span the full manifest: the output file is the
    # whole-suite artifact, so a subset rerun must not drop the other cases.
    contracts = {
        str(case["id"]): _case_contract_sha256(case) for case in all_cases
    }
    output = args.output.resolve()
    suite_root = _suite_root(output)
    results = _load_resume_state(output, contracts)
    if args.retry_failed:
        results = {
            case_id: result
            for case_id, result in results.items()
            if result.get("status") == "passed"
            or (requested and case_id not in requested)
        }
    ordered_ids = [str(case["id"]) for case in all_cases]
    for index, case in enumerate(cases, start=1):
        case_id = str(case["id"])
        if case_id in results:
            print(f"[{index}/{len(cases)}] {case_id}: resumed", flush=True)
            continue
        attempts = 0
        while True:
            print(f"[{index}/{len(cases)}] {case_id}: running", flush=True)
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--manifest",
                str(manifest_path),
                "--north-star-manifest",
                str(args.north_star_manifest.resolve()),
                "--output",
                str(output),
                "--child",
                case_id,
            ]
            started = time.monotonic()
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            try:
                child_output, _ = process.communicate(
                    timeout=_CASE_WALL_TIMEOUT_SECONDS
                )
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                child_output = ""
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            duration_ms = max(0, int((time.monotonic() - started) * 1000))
            result_path = _case_result_path(suite_root, case_id)
            evidence_path = _private_evidence_path(suite_root, case_id)
            if not timed_out and result_path.is_file() and evidence_path.is_file():
                result = _load_json(result_path)
                evidence = _load_json(evidence_path)
                results[case_id] = result
                print(
                    f"[{index}/{len(cases)}] {case_id}: {result['status']} "
                    f"({len(result['passedOracleIds'])}/{len(case['oracleIds'])} "
                    f"oracles, failure={evidence.get('failure')})",
                    flush=True,
                )
                break
            attempts += 1
            if not timed_out:
                tail = (child_output or "").strip().splitlines()
                detail = tail[-1][:200] if tail else "no output"
                print(
                    f"[{index}/{len(cases)}] {case_id}: child exited "
                    f"{process.returncode} without evidence ({detail})",
                    flush=True,
                )
                if attempts <= _CHILD_CRASH_RETRIES:
                    backoff = _CHILD_CRASH_BACKOFF_SECONDS[
                        min(attempts - 1, len(_CHILD_CRASH_BACKOFF_SECONDS) - 1)
                    ]
                    print(
                        f"[{index}/{len(cases)}] {case_id}: retrying in "
                        f"{backoff}s (attempt {attempts + 1})",
                        flush=True,
                    )
                    time.sleep(backoff)
                    continue
                raise RuntimeError(
                    f"case {case_id} child crashed without evidence after "
                    f"{attempts} attempts"
                )
            print(
                f"[{index}/{len(cases)}] {case_id}: wall timeout after "
                f"{duration_ms}ms",
                flush=True,
            )
            recovered = _recover_timeout_result(
                case, suite_root=suite_root, duration_ms=duration_ms
            )
            if recovered is None:
                print(
                    f"[{index}/{len(cases)}] {case_id}: no recoverable run "
                    "evidence; case left unreported",
                    flush=True,
                )
                break
            results[case_id] = recovered
            private = {
                "id": case_id,
                "caseContractSha256": contracts[case_id],
                "failure": "CaseWallTimeout",
                "oracleFailures": {},
                "transient": False,
            }
            north_star._atomic_json(evidence_path, private, private=True)
            north_star._atomic_json(result_path, recovered)
            break
        _atomic_envelope = _public_envelope(
            manifest=manifest,
            manifest_path=manifest_path,
            ordered_ids=ordered_ids,
            results=results,
        )
        north_star._atomic_json(output, _atomic_envelope)
        if index < len(cases):
            time.sleep(_INTER_CASE_DELAY_SECONDS)
    final = _public_envelope(
        manifest=manifest,
        manifest_path=manifest_path,
        ordered_ids=ordered_ids,
        results=results,
    )
    north_star._atomic_json(output, final)
    failed = [
        case_id
        for case_id in ordered_ids
        if case_id in results and results[case_id].get("status") != "passed"
    ]
    missing = [case_id for case_id in ordered_ids if case_id not in results]
    print(
        json.dumps(
            {
                "completed": len(results),
                "failed": failed,
                "missing": missing,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if not failed and not missing else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--north-star-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="re-run cases whose resumed result is not passed",
    )
    parser.add_argument("--child", metavar="CASE_ID", dest="child_case_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.child_case_id is not None:
        return _run_child(args)
    return _run_orchestrator(args)


if __name__ == "__main__":
    raise SystemExit(main())
