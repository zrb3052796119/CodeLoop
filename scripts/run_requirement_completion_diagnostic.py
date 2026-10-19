#!/usr/bin/env python3
"""One retained complex task; production completion feedback, one final oracle.

Prepare/plan/validate are local. Opaque source oracle bytes are only copied,
hashed and passed to the existing isolated finalizer, never exposed to a model.
This entrypoint checks the actual production completion capabilities before
freezing or execution. No historical runner or experiment output is modified.
"""
from __future__ import annotations

import argparse
import ast
from functools import wraps
import importlib.util
import inspect
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from scripts import run_collaboration_demand_pilot as previous  # noqa: E402

ENTRY = ROOT / "scripts/run_requirement_completion_diagnostic.py"
DEFAULT = ROOT / "artifacts/requirement-completion-2026-10-18/memory-explicit-v1"
CASE = "real-memory-corroboration"
PROTOCOL = "requirement-completion-single-diagnostic-v1"
SOURCE, ORIGINAL_SOURCE = previous.SOURCE, previous.ORIGINAL_SOURCE
shared = previous.shared
HARNESS_FILES = (*previous.HARNESS_FILES, ENTRY.name)
PRODUCTION_COMPLETION_INTEGRATED = True
COMPLETION_MECHANISM_VERSION = "direct-owner-public-coverage-v1"
INTERRUPTED_EVIDENCE_FILES = ("interrupted-transcript.json", "execution-evidence.json")
MECHANISM_FILES = (
    "minicode/agent_loop.py", "minicode/implementation_execution.py",
    "minicode/executor_verification.py", "minicode/implementation_completion_review.py",
    "minicode/run_journal.py",
)
ANALYSIS_PLAN = """# One retained task completion diagnostic

One real-memory-corroboration explicit execution from the original frozen public
baseline. This is a production-mechanism diagnostic, not a comparative study.
The previous public prompt, common requirements, allowed paths, explicit read
collaboration recipe, internal public checks and original private final oracle
remain unchanged. No task-specific hint or private result is inserted into the
running model context. Any completion rejection and repair must come from the
production completion mechanism using public requirements and public evidence.
The same implementation owner continues with its current files and context;
the harness must not create a fresh runtime or issue an operator repair prompt.

Every physical request uses GLM-5.3-Flash, high reasoning and temperature 0.
No cumulative token, call, cost or execution-wall limit is set. No total main
request deadline; max output 131072, idle 120 seconds and connect 30 seconds.
Any auxiliary caller-local deadlines remain separately visible in the ledger.
All usage, failures and unknown costs remain in the result. API-equivalent fees
are not a subscription bill; unknown usage cannot be replaced by zero.

After production execution terminates, the unchanged final oracle runs once,
alongside the separate generated regression test and the original cleanup/scope
gates. Final judgement retains the existing shared 120-second post-execution
deadline. Private results are not used to resume or tune the candidate. No
automatic rerun, replacement, best-of selection or omission of failure.

Freeze occurs only after generic local tests demonstrate completion rejection,
same-owner continuation, existing edits retained, current-candidate public
checks and accepted delivery. Frozen production/harness/source hashes cannot
change while the model runs. Only this diagnostic's new directory may be written;
all prior six-cell and task-source artifacts remain immutable.

The mechanism version is direct-owner-public-coverage-v1 and applies to this
host-bound ImplementationSpec, not all unconfigured default chats. Both ordinary
final entrypoints invoke current public checks and the public requirement review;
unavailable authorized checking ends as partial, rather than an endless retry.
The contract verifies the production APIs, both gate call sites, durable event
registration and exact source/entrypoint hashes before freezing or execution.
Each production review persistence output is copied as opaque bytes into the
private trial archive. No evidence content is used to modify the execution.
Read-only summaries select safe completion owner/attempt/status/digest metadata;
they distinguish an actual rejected→edited→accepted sequence from a first-attempt
approval. Archive failures remain visible and do not replace production results.
On an exception that unwinds the parent runtime, retain its current ContextManager
messages privately before disposal. Preserve the original exception; never turn
this evidence into a successful response or feed it to a resumed model run.
"""


def schedule() -> list[dict]:
    return [{"case": CASE, "arm": "explicit", "repeat": 1, "id": CASE + "-r1-explicit"}]


def _selected(document: dict) -> dict:
    cases = document.get("cases")
    if not isinstance(cases, list):
        raise ValueError("missing source case list")
    matching = [item for item in cases if item.get("id") == CASE]
    if len(matching) != 1:
        raise ValueError("selected public task must have exactly one source identity")
    return {**document, "cases": matching}


def _library():
    """Private module globals reuse the reviewed harness without patching it."""
    spec = importlib.util.spec_from_file_location(
        "_requirement_completion_demand", ROOT / "scripts/run_collaboration_demand_pilot.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load shared demand runner")
    library = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(library)
    library.__file__ = str(ENTRY)
    library.ENTRY, library.DEFAULT, library.PROTOCOL = ENTRY, DEFAULT, PROTOCOL
    library.CASES, library.ARMS = (CASE,), ("explicit",)
    library.HARNESS_FILES, library.ANALYSIS_PLAN = HARNESS_FILES, ANALYSIS_PLAN
    library.schedule, library.prepared_document = schedule, prepared_document
    base_static = library.static_contract

    def diagnostic_contract():
        return {**base_static(), "phase": "diagnostic", "seed": None,
                "comparison": "one retained task; production completion diagnostic only",
                "completionSourcePolicy": "production only; public original request and current candidate evidence",
                "continuationPolicy": "same implementation owner, files and message context; no harness repair turns",
                "privateJudgePolicy": "one terminal run; never feedback into model",
                "abnormalExecutionEvidence": {"version": 1, "source": "parent_runtime_context_manager",
                    "privateOnly": True, "preservesOriginalException": True, "overwritesExisting": False},
                "productionCompletionIntegrated": PRODUCTION_COMPLETION_INTEGRATED,
                "completionMechanism": completion_mechanism_contract()}

    library.static_contract = diagnostic_contract
    original_runner = library.runner

    def observed_runner(output):
        module = original_runner(output)
        original_execute, original_finalize = module.execute_trial, module.finalize_trial

        def execute(trial_output, trial_id):
            require_completion_integration()
            root = Path(trial_output) / "private" / trial_id
            library.assert_fresh_attempt(Path(trial_output), trial_id)
            if any(os.path.lexists(root / name) for name in (
                    "completion-review-archive.json", "completion-summary.json", *INTERRUPTED_EVIDENCE_FILES)):
                raise ValueError("prior completion evidence exists; never overwrite or rerun")
            with ParentExecutionEvidenceArchive(root), CompletionEvidenceArchive(root):
                return original_execute(trial_output, trial_id)

        def finalize(*args, **kwargs):
            row = original_finalize(*args, **kwargs)
            summary = completion_summary(Path(args[0]), row["id"])
            row["completionMechanism"] = summary
            shared.write_json(Path(args[0]) / "trials" / (row["id"] + ".json"), row)
            return row

        module.execute_trial, module.finalize_trial = execute, finalize
        return module

    library.runner = observed_runner
    return library


def prepare(output: Path, source: Path = SOURCE, original_source: Path = ORIGINAL_SOURCE) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError("prepare requires an empty new output directory")
    source, original_source = source.resolve(), original_source.resolve()
    raw = (source / "private/tasks.json").read_bytes()
    original_raw = (original_source / "private/tasks.json").read_bytes()
    document = _selected(json.loads(raw))
    original = _selected(json.loads(original_raw))
    library = _library()
    baselines = library.baseline_hashes(document)
    layers = library.layered_validation(document, original)
    hashes = {name: shared.sha((source / name).read_bytes())
              for name in ("private/tasks.json", "manifest.json", "public-cases.json")}
    public = _selected(shared.read_json(source / "public-cases.json"))
    output.mkdir(parents=True, exist_ok=True)
    shared.write_json(output / "tasks.json", document)
    (output / "tasks.json").chmod(0o600)
    shared.write_json(output / "public-cases.json", public)
    (output / "analysis-plan.md").write_text(ANALYSIS_PLAN)
    shared.write_json(output / "source-provenance.json", {
        "sourceDirectory": str(source), "sourceHashes": hashes,
        "selectedCases": [CASE], "selectionPolicy": "exact identity; all selected task fields unchanged",
        "preparedTaskSha256": shared.sha((output / "tasks.json").read_bytes()),
        "preparedPublicSha256": shared.sha((output / "public-cases.json").read_bytes()),
        "baselineFilesSha256": baselines, "originalSourceDirectory": str(original_source),
        "originalTaskSha256": shared.sha(original_raw), "validationLayers": layers})


def prepared_document(output: Path) -> tuple[dict, dict]:
    provenance = shared.read_json(output / "source-provenance.json")
    source = Path(provenance["sourceDirectory"])
    for name, expected in provenance["sourceHashes"].items():
        if (name not in ("private/tasks.json", "manifest.json", "public-cases.json")
                or shared.sha((source / name).read_bytes()) != expected):
            raise ValueError("source artifacts changed")
    raw = (output / "tasks.json").read_bytes()
    document = json.loads(raw)
    if (shared.sha(raw) != provenance["preparedTaskSha256"]
            or provenance["selectedCases"] != [CASE]
            or document != _selected(shared.read_json(source / "private/tasks.json"))):
        raise ValueError("prepared task changed or differs from exact source selection")
    if (shared.sha((output / "public-cases.json").read_bytes()) != provenance["preparedPublicSha256"]
            or shared.read_json(output / "public-cases.json") != _selected(shared.read_json(source / "public-cases.json"))):
        raise ValueError("prepared public specification changed")
    original_raw = (Path(provenance["originalSourceDirectory"]) / "private/tasks.json").read_bytes()
    library = _library()
    if (shared.sha(original_raw) != provenance["originalTaskSha256"]
            or library.layered_validation(document, _selected(json.loads(original_raw))) != provenance["validationLayers"]
            or library.baseline_hashes(document) != provenance["baselineFilesSha256"]
            or (output / "analysis-plan.md").read_text() != ANALYSIS_PLAN):
        raise ValueError("quality gate, baseline or diagnostic plan changed")
    return document, provenance


def completion_mechanism_contract() -> dict:
    """Require real production capabilities and freeze their exact source bytes."""
    if not PRODUCTION_COMPLETION_INTEGRATED:
        raise RuntimeError("production completion integration pending; paid execution is blocked")
    from minicode import agent_loop, implementation_execution, executor_verification
    from minicode.implementation_completion_review import ImplementationCompletionReview
    from minicode.run_journal import EVENT_TYPES

    capabilities = {
        "candidateDigest": implementation_execution.implementation_candidate_digest,
        "directVerificationFactory": implementation_execution.direct_verification_capability,
        "currentCompletionChecks": getattr(executor_verification.ExecutorCheckCapability, "ensure_completion_checks", None),
        "publicCoverageAssessment": getattr(ImplementationCompletionReview, "assess", None),
        "pendingFailureAdjudication": getattr(ImplementationCompletionReview, "needs_failure_adjudication", None),
        "durableReviewEvidence": getattr(ImplementationCompletionReview, "_persist", None),
    }
    if not all(callable(value) for value in capabilities.values()):
        raise RuntimeError("production completion capability is missing; paid execution is blocked")
    expected_assess = {"self", "model", "tools", "context", "agent_budget", "event_sink",
                       "cancellation_token", "deadline_monotonic", "checks_receipt"}
    if set(inspect.signature(capabilities["publicCoverageAssessment"]).parameters) != expected_assess:
        raise RuntimeError("production review capability signature changed")
    loop_tree = ast.parse(inspect.getsource(agent_loop.run_agent_turn))
    gates = [node for node in ast.walk(loop_tree) if isinstance(node, ast.FunctionDef)
             and node.name == "enforce_implementation_completion_before_final"]
    calls = [node for node in ast.walk(loop_tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name)
                   and node.func.id == "enforce_implementation_completion_before_final"]
    resume_calls = [node for node in calls if any(keyword.arg == "resuming_review"
                    and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                    for keyword in node.keywords)]
    final_calls = [node for node in calls if node not in resume_calls]
    if len(gates) != 1 or len(final_calls) != 2 or len(resume_calls) != 1:
        raise RuntimeError("production final entrypoints lack their common completion gate")
    gate_calls = {node.func.attr for node in ast.walk(gates[0]) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
    if not {"ensure_completion_checks", "assess"} <= gate_calls:
        raise RuntimeError("production completion gate does not invoke checks and public review")
    if not {"implementation.completion_review_started", "implementation.completion_decided",
            "implementation.completion_review_activity"} <= EVENT_TYPES:
        raise RuntimeError("production completion events are not durable")
    return {"version": COMPLETION_MECHANISM_VERSION,
        "scope": "host-bound ImplementationSpec direct owner only; not all default chats",
        "finalEntrypoints": 2, "resumeAdjudicationEntrypoints": 1,
        "publicReviewEvidence": "production generated probes; no private oracle feedback",
        "unavailablePublicCheck": "partial stop; no infinite retry",
        "sourceSha256": {name: shared.sha((ROOT / name).read_bytes()) for name in MECHANISM_FILES},
        "entrypointSha256": shared.sha(ENTRY.read_bytes())}


def require_completion_integration() -> None:
    completion_mechanism_contract()


class ParentExecutionEvidenceArchive:
    """Retain the first parent runtime's current context on exception only.

    The outer runner's input list is stale when execute raises. The real loop
    preserves its working list on its own ContextManager during unwinding.
    Child/reviewer runtimes never replace the captured parent identity.
    """
    def __init__(self, trial_root):
        self.root = Path(trial_root)
        self.receipt = None
        self.errors = []
        self._owner = None
        self._patch = None
        self._lock = threading.Lock()
        self._directory_fd = None

    def _write_private(self, name, value):
        # O_EXCL also rejects an existing symlink; no old evidence is replaced.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(name, flags, 0o600, dir_fd=self._directory_fd)
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(value)

    def _failure(self, stage, error):
        item = {"stage": stage, "class": type(error).__name__}
        self.errors.append(item)
        # The private process log remains a fallback when the filesystem cannot
        # retain even the metadata receipt. Never log exception text or context.
        try:
            logging.getLogger(__name__).warning("Parent execution evidence unavailable (%s: %s)",
                                               stage, item["class"])
        except BaseException:
            pass

    def _record(self, runtime, error):
        self.receipt = {"schemaVersion": 1, "source": "parent_runtime_context_manager",
            "executionErrorClass": type(error).__name__, "available": False,
            "errors": self.errors}
        try:
            messages = getattr(getattr(runtime, "context_manager", None), "messages", None)
            if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
                raise ValueError("parent current context is unavailable")
            content = (json.dumps(messages, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
            self._write_private(INTERRUPTED_EVIDENCE_FILES[0], content)
            roles = {}
            for message in messages:
                role = message.get("role")
                key = role if isinstance(role, str) and role in {"system", "user", "assistant", "assistant_tool_call",
                                      "tool_result", "assistant_progress"} else "other"
                roles[key] = roles.get(key, 0) + 1
            self.receipt.update(available=True, file=INTERRUPTED_EVIDENCE_FILES[0],
                messageCount=len(messages), roles=roles, bytes=len(content), sha256=shared.sha(content))
        except BaseException as retention_error:
            self._failure("transcript", retention_error)
        try:
            self._write_private(INTERRUPTED_EVIDENCE_FILES[1],
                (json.dumps(self.receipt, ensure_ascii=False, indent=2) + "\n").encode())
        except BaseException as retention_error:
            self._failure("receipt", retention_error)

    def __enter__(self):
        from minicode.agent_runtime import AgentTurnRuntime
        if self.root.is_symlink():
            raise ValueError("private evidence directory cannot be a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fchmod(self._directory_fd, 0o700)
            for name in INTERRUPTED_EVIDENCE_FILES:
                try:
                    os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ValueError("prior execution evidence exists; never overwrite")
        except BaseException:
            os.close(self._directory_fd)
            self._directory_fd = None
            raise
        original = AgentTurnRuntime.execute

        @wraps(original)
        def execute(runtime, *args, **kwargs):
            with self._lock:
                parent = self._owner is None
                if parent:
                    self._owner = runtime
            if not parent:
                return original(runtime, *args, **kwargs)
            try:
                return original(runtime, *args, **kwargs)
            except BaseException as execution_error:
                try:
                    self._record(runtime, execution_error)
                except BaseException as retention_error:
                    self._failure("archive", retention_error)
                raise

        self._patch = patch.object(AgentTurnRuntime, "execute", execute)
        try:
            self._patch.__enter__()
        except BaseException:
            os.close(self._directory_fd)
            self._directory_fd = None
            raise
        return self

    def __exit__(self, *args):
        try:
            return self._patch.__exit__(*args)
        finally:
            try:
                os.close(self._directory_fd)
            except BaseException as retention_error:
                self._failure("close", retention_error)
            self._directory_fd = None


class CompletionEvidenceArchive:
    """Copy production-created review artifacts as opaque bytes at creation.

    This observation hook never changes a verdict, prompt, permission or model
    call. Failures are retained as measurement failures without replacing the
    production outcome. No temporary directory is deleted or source interpreted.
    """
    def __init__(self, trial_root):
        self.root = Path(trial_root)
        self.records = {}
        self.errors = []
        self.lock = threading.Lock()
        self._patch = None

    def _record(self, owner, path):
        try:
            source = Path(path)
            directory = getattr(owner, "_directory", None)
            if (not isinstance(directory, Path) or source.is_symlink() or not source.is_file()
                    or source.parent.resolve() != directory.resolve()
                    or not re.fullmatch(r"\d{5,}-[a-z_-]+\.json", source.name)):
                raise ValueError("unexpected production review artifact")
            identity = "review_" + shared.sha(str(directory.resolve()).encode())[:24]
            destination = self.root / "completion-review" / identity / source.name
            digest = shared.sha(source.read_bytes())
            with self.lock:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.parent.chmod(0o700)
                if destination.exists():
                    if shared.sha(destination.read_bytes()) != digest:
                        raise ValueError("production review evidence changed after persistence")
                else:
                    shutil.copyfile(source, destination)
                    destination.chmod(0o600)
                self.records[str(destination.relative_to(self.root))] = {
                    "reviewId": identity, "file": str(destination.relative_to(self.root)),
                    "sha256": digest, "bytes": destination.stat().st_size,
                }
                self._save()
        except Exception as error:
            with self.lock:
                self.errors.append(type(error).__name__)
                self._save()

    def _save(self):
        try:
            shared.write_json(self.root / "completion-review-archive.json", {
                "archiveVersion": 1, "archiveComplete": not self.errors,
                "recordingMethod": "opaque copy of each successful production review _persist output",
                "reviewDirectories": len({row["reviewId"] for row in self.records.values()}),
                "files": list(self.records.values()), "errors": list(self.errors),
            })
        except Exception as error:
            self.errors.append("ArchiveManifest" + type(error).__name__)

    def __enter__(self):
        from minicode.implementation_completion_review import ImplementationCompletionReview
        original = ImplementationCompletionReview._persist

        @wraps(original)
        def persisted(owner, *args, **kwargs):
            path = original(owner, *args, **kwargs)
            self._record(owner, path)
            return path

        self._patch = patch.object(ImplementationCompletionReview, "_persist", persisted)
        self._patch.__enter__()
        return self

    def __exit__(self, *args):
        self._patch.__exit__(*args)
        with self.lock:
            self._save()


def completion_summary(output: Path, trial_id: str) -> dict:
    """Read whitelisted durable event metadata, never transcripts or judge logs."""
    if trial_id != schedule()[0]["id"]:
        raise ValueError("unknown diagnostic trial")
    root = output / "private" / trial_id
    decisions, activities, writes, errors = [], [], [], []
    for path in sorted((root / "journal").glob("dashboard/workspaces/*/runs/run_*/events.ndjson")):
        try:
            for line in path.read_text().splitlines():
                event = json.loads(line)
                payload = event.get("payload", {})
                event_type = event.get("type")
                if not isinstance(payload, dict):
                    continue
                base = {"runId": path.parent.name, "sequence": event.get("sequence"),
                        "step": event.get("step") if type(event.get("step")) is int else None}
                if event_type == "implementation.completion_decided":
                    owner, attempt, status = (payload.get(key) for key in ("executorId", "attempt", "status"))
                    digest = payload.get("patchDigest")
                    if (not isinstance(owner, str) or not re.fullmatch(r"implementation_[0-9a-f]{32}", owner)
                            or type(attempt) is not int or attempt < 1
                            or status not in {"approved", "changes_required", "inconclusive"}
                            or (digest is not None and (not isinstance(digest, str)
                                or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)))
                            or type(base["sequence"]) is not int):
                        errors.append("invalid_completion_metadata")
                        continue
                    row = {**base, "executorId": owner, "attempt": attempt, "status": status, "patchDigest": digest}
                    for key in ("continuesSameExecutor", "canContinue"):
                        row[key] = payload.get(key) if type(payload.get(key)) is bool else None
                    for key in ("requirementCount", "requirementsOmitted", "findingCount"):
                        row[key] = payload.get(key) if type(payload.get(key)) is int and payload[key] >= 0 else None
                    row["publicChecksStatus"] = (payload.get("publicChecksStatus")
                        if payload.get("publicChecksStatus") in ("passed", "failed", "inconclusive") else None)
                    decisions.append(row)
                elif event_type == "implementation.completion_review_activity":
                    if isinstance(payload.get("reviewId"), str):
                        activities.append({**base, **{key: payload.get(key) for key in ("reviewId", "eventType", "toolName")}})
                elif event_type == "tool.finished" and payload.get("toolName") in {"edit_file", "write_file", "executor_test"}:
                    if type(base["sequence"]) is int:
                        writes.append({**base, "toolName": payload["toolName"], "outcome": payload.get("outcome")})
        except (OSError, ValueError, TypeError) as error:
            errors.append(type(error).__name__)
    owners = {row["executorId"] for row in decisions}
    repair_pairs = []
    for index, earlier in enumerate(decisions):
        if not earlier.get("continuesSameExecutor") or earlier["status"] == "approved":
            continue
        for later in decisions[index + 1:]:
            if (later["status"] == "approved" and later["executorId"] == earlier["executorId"]
                    and later["runId"] == earlier["runId"]):
                between = [w for w in writes if w["runId"] == earlier["runId"]
                           and earlier["sequence"] < w["sequence"] < later["sequence"] and w["outcome"] == "success"]
                repair_pairs.append({"executorId": earlier["executorId"], "rejectedAttempt": earlier["attempt"],
                    "acceptedAttempt": later["attempt"], "successfulWriteToolEvents": len(between),
                    "candidateDigestChanged": earlier["patchDigest"] != later["patchDigest"]})
                break
    archive_path = root / "completion-review-archive.json"
    archive = shared.read_json(archive_path) if archive_path.exists() else None
    result = {"schemaVersion": 1, "mechanismVersion": COMPLETION_MECHANISM_VERSION,
        "decisionCount": len(decisions), "executorCount": len(owners), "decisions": decisions,
        "sameOwnerRepairPairs": repair_pairs, "reviewActivityCount": len(activities),
        "reviewActorCount": len({row["reviewId"] for row in activities}),
        "approvedFinalObserved": bool(decisions and decisions[-1]["status"] == "approved"),
        "evidenceArchive": None if archive is None else {key: archive.get(key) for key in (
            "archiveComplete", "reviewDirectories", "errors")},
        "evidenceFileCount": len(archive.get("files", [])) if archive else 0,
        "errors": errors,
        "limits": "Successful write tool events plus changed digest support repair; tool success alone does not prove byte mutation."}
    shared.write_json(root / "completion-summary.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "prepare", "freeze", "validate", "run", "child", "summary"))
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--original-source", type=Path, default=ORIGINAL_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--trial")
    args = parser.parse_args()
    output = args.output.resolve()
    library = _library()
    if args.mode == "plan":
        print(json.dumps(library.static_contract(), ensure_ascii=False, indent=2))
    elif args.mode == "prepare":
        prepare(output, args.source, args.original_source)
    elif args.mode == "summary":
        print(json.dumps(completion_summary(output, args.trial or schedule()[0]["id"]), ensure_ascii=False, indent=2))
    else:
        require_completion_integration()
        if args.mode == "freeze":
            library.freeze(output)
        elif args.mode == "validate":
            library.validate(output)
            print(json.dumps({"valid": True, "protocol": PROTOCOL, "trials": 1}))
        else:
            library.validate(output)
            module = library.runner(output)
            if args.mode == "child":
                module.execute_trial(output, args.trial)
            else:
                module.run_suite(output)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--worker-bootstrap":
        require_completion_integration()
        raise SystemExit(_library().worker_bootstrap(sys.argv[2:]))
    main()
