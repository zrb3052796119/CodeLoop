"""Revocable implementation feedback over host-selected verification checks."""

from __future__ import annotations

import contextvars
import json
import os
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from minicode.capability_contracts import (
    CapabilityDomain, CapabilityEffects, CapabilityProfile, CapabilityScope,
)
from minicode.deterministic_verifier import VerificationCheck, VerificationDeadlineExceeded
from minicode.executor_result_submission import executor_submit_channel
from minicode.scheduler_artifacts import SchedulerArtifactError
from minicode.task_deadline import TaskDeadlineExceeded, raise_task_deadline
from minicode.tooling import ToolDefinition, ToolResult, ToolRegistry, ToolExecutionAbandoned
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.file_review import apply_reviewed_file_change
from minicode.workspace import resolve_tool_path


@dataclass(frozen=True, slots=True)
class CheckedCandidateSnapshot:
    """Immutable host observations, with no retained execution capability."""

    assignment_id: str
    order_id: str
    patch_digest: str
    receipts_json: tuple[str, ...]
    handoff_reason: str = "executor_budget_exceeded"


class _WriteFeedbackBatch:
    """Host-owned consecutive writes; result text never controls this scope."""

    def __init__(self, capability):
        self.capability = capability
        self.call_id = None
        self.changed = []
        self.uncertain = []
        self.context = None
        self.closed = False
        self.token = capability._write_batch.set(self)

    def observe(self, context, *, uncertain=False):
        self.context = context
        identities = self.uncertain if uncertain else self.changed
        if self.call_id not in identities:
            identities.append(self.call_id)

    def finish(self):
        if self.closed:
            return None, None
        self.closed = True
        cap = self.capability
        cap._write_batch.reset(self.token)
        if self.context is None:
            return None, None
        receipts = []
        control_error = None
        try:
            if cap._closed:
                raise RuntimeError("implementation check capability is closed")
            abandoned = getattr(self.context, "_tool_abandoned", None)
            if abandoned is not None and abandoned.is_set():
                raise ToolExecutionAbandoned("owning invocation was abandoned")
            raise_task_deadline(self.context)
            for index in range(1, len(cap._checks) + 1):
                receipts.append(json.loads(cap._run({"check": index}, self.context).output))
        except BaseException as error:
            # Keep each completed check and describe the unexecuted remainder.
            # A cancellation is handed back to the owner after evidence is saved.
            if isinstance(error, (TurnCancellationRequested, ToolExecutionAbandoned, KeyboardInterrupt, SystemExit)):
                control_error = error
            receipts.extend({"checkId": check.check_id, "status": "inconclusive",
                             "reason": "batch_check_interrupted", "errorType": type(error).__name__,
                             "verification": "implementation_feedback"}
                            for check in cap._checks[len(receipts):])
        digests = {receipt.get("patchDigest") for receipt in receipts}
        status = ("failed" if any(receipt["status"] == "failed" for receipt in receipts) else
                  "passed" if receipts and all(receipt["status"] == "passed" for receipt in receipts)
                  and len(digests) == 1 and None not in digests else "inconclusive")
        document = {"batchVersion": 1, "batchId": uuid.uuid4().hex,
                    "verification": "implementation_feedback", "status": status,
                    "changedCallIds": self.changed, "uncertainWriteCallIds": self.uncertain,
                    "patchDigest": next(iter(digests)) if len(digests) == 1 and None not in digests else None,
                    "checks": receipts}
        recorder = getattr(getattr(self.context, "_event_sink", None), "record_tool_evidence", None)
        if callable(recorder):
            try:
                recorder(tool_name="implementation_batch_check", phase="finished",
                         output=json.dumps(document, ensure_ascii=False, sort_keys=True),
                         is_error=status != "passed")
            except Exception:
                pass
        return document, control_error


class ExecutorCheckCapability:
    """The model chooses a check number, never argv, paths or approval evidence."""

    def __init__(self, checks: Sequence[VerificationCheck],
                 run_check: Callable[[VerificationCheck, object], dict],
                 test_paths: Sequence[str] = (), *, spec=None,
                 current_patch_digest: Callable[[object], str] | None = None,
                 output_reader=None) -> None:
        if output_reader is not None:
            from minicode.verification_output_tool import VerificationOutputAccess
            if not isinstance(output_reader, VerificationOutputAccess):
                raise TypeError("output reader must be a host verification output capability")
        self._checks = tuple(checks)
        self._run_check = run_check
        self._closed = False
        self._output_reader = output_reader
        self.spec = spec
        self._feedback_deadline = None
        self._observation_lock = threading.RLock()
        self._observation_sequence = 0
        self._mutation_epoch = 0
        self._active_writes = 0
        self._unresolved_write_errors = set()
        self._latest_receipts = {}
        self._latest_full_receipts = {}
        self._current_patch_digest = current_patch_digest
        self._submission_channel = None
        self._accepted_completion_digest = None
        self._submission_context = contextvars.ContextVar("implementation_submission_context", default=None)
        self._write_batch = contextvars.ContextVar("implementation_write_batch", default=None)
        self.test_tool = self._create_test_tool(tuple(test_paths)) if test_paths else None
        self.tool = ToolDefinition(
            name="executor_check",
            description=(
                "Run one approved verification check against an isolated copy of "
                "your current implementation. Inspect the actual stdout/stderr, "
                "repair failures, and rerun before executor_submit. This does not "
                "submit your result or authorize writeback. Checks: "
                + json.dumps([{"check": i, "argv": list(check.argv)}
                              for i, check in enumerate(self._checks, 1)])
            ),
            input_schema={
                "type": "object", "additionalProperties": False,
                "properties": {"check": {"type": "integer", "minimum": 1,
                                            "maximum": len(self._checks)}},
                "required": ["check"],
            },
            validator=self._validate,
            run=self._run,
            capability_profile=CapabilityProfile(
                domain=CapabilityDomain.EXECUTION, scope=CapabilityScope.WRITE,
                effects=CapabilityEffects(read_only=False, destructive=False,
                                          external=False, executes_code=True, delegates=False),
                purposes=("Observe an approved check on isolated candidate bytes",),
            ),
        )

    def bind_registry(self, registry: ToolRegistry) -> ToolRegistry:
        submissions = []
        if callable(self._current_patch_digest) and self._checks and not self._closed:
            for tool in registry.list():
                channel = executor_submit_channel(tool)
                if channel is None:
                    continue
                if self._submission_channel is not None and self._submission_channel is not channel:
                    raise ValueError("implementation checks belong to another submission channel")
                channel.bind_completion_guard(self._guard_completion)
                self._submission_channel = channel
                submissions.append(self._with_submission_context(tool))
        bound = registry.overlay([
            *(self.with_write_feedback(tool) if tool.name in {"edit_file", "write_file"}
              else tool for tool in registry.list() if tool.name in {"edit_file", "write_file"}),
            *([self.tool] if self._checks else []), *([self.test_tool] if self.test_tool is not None else []),
            *([self._output_reader.tool] if self._output_reader is not None else []),
            *submissions,
        ], remove_names=frozenset({"executor_check", "executor_test", "verification_output_read"}))
        bound._implementation_feedback = self
        return bound

    @property
    def supports_conditional_submit(self) -> bool:
        """Only a live host-bound current-candidate gate permits a combined handoff."""
        return (self.has_automatic_write_checks and self._submission_channel is not None
                and callable(self._current_patch_digest))

    @property
    def completion_pending(self) -> bool:
        """Read host receipt state without probing bytes or granting permission."""
        with self._observation_lock:
            if not self.supports_conditional_submit:
                return False
            if self._unresolved_write_errors or self._active_writes:
                return True
            receipts = [self._latest_receipts.get(check.check_id) for check in self._checks]
            if any(receipt is None or receipt.get("status") != "passed"
                   or not isinstance(receipt.get("patchDigest"), str) or not receipt["patchDigest"]
                   for receipt in receipts):
                return True
            return len({receipt["patchDigest"] for receipt in receipts}) != 1

    def _with_submission_context(self, tool):
        def submit(value, context):
            # Original native run checks owner identity before channel.write.
            token = self._submission_context.set(context)
            try:
                result = tool.run(value, context)
                if (result.ok and result._implementation_submitted
                        and isinstance(value, dict) and value.get("status") == "completed"):
                    with self._observation_lock:
                        receipts = [self._latest_receipts.get(check.check_id) for check in self._checks]
                        digests = {item.get("patchDigest") for item in receipts if item is not None}
                        if (not self._closed and not self._active_writes and not self._unresolved_write_errors
                                and receipts and all(item is not None and item.get("status") == "passed" for item in receipts)
                                and len(digests) == 1 and all(isinstance(item, str) and item for item in digests)):
                            self._accepted_completion_digest = next(iter(digests))
                return result
            finally:
                self._submission_context.reset(token)
        return replace(tool, run=submit)

    @property
    def accepted_completion_digest(self) -> str | None:
        """A real bound native acceptance, never a declaration inferred from text."""
        with self._observation_lock:
            return self._accepted_completion_digest if not self._closed else None

    def _guard_completion(self, declaration):
        with self._observation_lock:
            if self._closed:
                raise SchedulerArtifactError("implementation completion gate is closed")
            if declaration.get("status") != "completed":
                return
            context = self._submission_context.get()
            if (context is None or getattr(context, "_subagent_mailbox", None)
                    is not self._submission_channel):
                raise SchedulerArtifactError("completed requires the owner-bound executor_submit tool")
            if self._active_writes:
                raise SchedulerArtifactError("completed requires all candidate writes to finish")
            if self._unresolved_write_errors:
                raise SchedulerArtifactError("completed requires observing failed candidate writes in a new response; passing checks alone cannot clear a write error")
            receipts = [self._latest_receipts.get(check.check_id) for check in self._checks]
            if (not receipts or any(receipt is None or receipt.get("status") != "passed"
                                    for receipt in receipts)):
                raise SchedulerArtifactError("completed requires every approved check to pass on the current candidate")
            digests = {receipt.get("patchDigest") for receipt in receipts}
            if len(digests) != 1 or not all(isinstance(item, str) and item for item in digests):
                raise SchedulerArtifactError("completed requires one nonempty patch digest for all approved checks")
            try:
                current = self._current_patch_digest(context)
            except (TaskDeadlineExceeded, ToolExecutionAbandoned, TurnCancellationRequested):
                raise
            except Exception as error:
                raise SchedulerArtifactError("current candidate digest is unavailable") from error
            if current not in digests:
                raise SchedulerArtifactError("completed check receipts are stale for the current candidate")

    def _invalidate_receipts(self):
        self._mutation_epoch += 1
        self._accepted_completion_digest = None
        self._latest_receipts.clear()
        self._latest_full_receipts.clear()

    def capture_checked_candidate(self, *, context):
        """Seal complete current feedback before the owning worker is revoked.

        Completed checks may pass or fail: independent verification decides
        whether this candidate needs repair. An unobserved write error makes
        the snapshot repair-only: its owner must receive that feedback before
        it can be delivered, even if all checks passed. This is never a
        declaration that the model completed a step or satisfied a criterion.
        """
        with self._observation_lock:
            channel = self._submission_channel
            if (not self.supports_conditional_submit
                    or self._active_writes
                    or getattr(context, "_subagent_mailbox", None) is not channel):
                return None
            receipts = [self._latest_receipts.get(check.check_id) for check in self._checks]
            if any(receipt is None or receipt.get("status") not in {"passed", "failed"}
                   for receipt in receipts):
                return None
            abandoned = getattr(context, "_tool_abandoned", None)
            if abandoned is not None and abandoned.is_set():
                return None
            raise_task_deadline(context)
            digest = self._current_patch_digest(context)
            if (not isinstance(digest, str) or not digest
                    or any(receipt.get("patchDigest") != digest for receipt in receipts)
                    or any(check.check_id not in self._latest_full_receipts for check in self._checks)):
                return None
            return CheckedCandidateSnapshot(
                assignment_id=channel._assignment.assignment_id,
                order_id=channel._order.order_id,
                patch_digest=digest,
                receipts_json=tuple(self._latest_full_receipts[check.check_id] for check in self._checks),
                handoff_reason=("executor_write_feedback_pending" if self._unresolved_write_errors
                                else "executor_submission_feedback_pending" if channel.validation_errors
                                else "executor_budget_exceeded"),
            )

    @staticmethod
    def _write_key(value, context):
        # Correlation only, never path authorization or filesystem resolution.
        path = value.get("path") if isinstance(value, dict) else None
        return (os.path.normcase(os.path.abspath(os.path.join(context.cwd, path)))
                if isinstance(path, str) else None)

    def observe_tool_result(self, tool_name, tool_input, result, *, context):
        """Observe a host pipeline rejection that may precede the tool's run.

        The loop calls this once at each actual result, not by replaying model
        messages. Re-observing a failure already seen by a wrapper is harmless.
        """
        if (not isinstance(result, ToolResult) or result.ok
                or (self._submission_channel is not None
                    and getattr(context, "_subagent_mailbox", None) is not self._submission_channel)):
            return
        with self._observation_lock:
            if tool_name in {"edit_file", "write_file", "executor_test"}:
                key = self._write_key(tool_input, context)
                if key not in self._unresolved_write_errors:
                    self._invalidate_receipts()
                    self._unresolved_write_errors.add(key)
            elif tool_name == "executor_check":
                number = tool_input.get("check") if isinstance(tool_input, dict) else None
                if type(number) is int and 1 <= number <= len(self._checks):
                    check_id = self._checks[number - 1].check_id
                    receipt = self._latest_receipts.get(check_id)
                    if receipt is not None and receipt.get("status") == "passed":
                        self._latest_receipts.pop(check_id)
                        self._latest_full_receipts.pop(check_id, None)
                else:
                    self._latest_receipts.clear()
                    self._latest_full_receipts.clear()

    def begin_model_response(self, *, context):
        """A newly admitted request delivers previous failures to their owner.

        Clear only the unobserved-write-error barrier. Invalidated receipts
        remain invalid; previewing or denying a request must not call this.
        """
        with self._observation_lock:
            if (self._closed or (self._submission_channel is not None
                    and getattr(context, "_subagent_mailbox", None) is not self._submission_channel)):
                return
            self._unresolved_write_errors.clear()

    @property
    def has_automatic_write_checks(self) -> bool:
        """Whether this live capability can check actual wrapped file changes."""
        return bool(self._checks) and not self._closed and callable(self._run_check)

    def ensure_completion_checks(self, context):
        """Admit a direct final using current host receipts, never model prose.

        A direct implementation has no executor_submit channel. It still needs
        the identical candidate/check invariant before semantic review. Missing
        and stale checks run here; failures return to the owning loop for repair.
        No write, session reset or permission expansion is performed.
        """
        raise_task_deadline(context)
        with self._observation_lock:
            if self._closed or not callable(self._current_patch_digest) or not self._checks:
                return {"status": "inconclusive", "reason": "completion_checks_unavailable", "checks": []}
            if self._active_writes or self._unresolved_write_errors:
                return {"status": "inconclusive", "reason": "candidate_write_feedback_pending", "checks": []}
            current = self._current_patch_digest(context)
            missing = [index for index, check in enumerate(self._checks, 1)
                       if self._latest_receipts.get(check.check_id, {}).get("patchDigest") != current]
        for index in missing:
            self._run({"check": index}, context)
        with self._observation_lock:
            receipts = [json.loads(self._latest_full_receipts[check.check_id])
                        for check in self._checks if check.check_id in self._latest_full_receipts]
            current_after = self._current_patch_digest(context)
            valid = (not self._closed and not self._active_writes and not self._unresolved_write_errors
                     and current_after == current and len(receipts) == len(self._checks)
                     and all(receipt.get("patchDigest") == current for receipt in receipts))
            status = ("failed" if valid and any(r.get("status") == "failed" for r in receipts)
                      else "passed" if valid and all(r.get("status") == "passed" for r in receipts)
                      else "inconclusive")
            return {"status": status, "patchDigest": current_after, "checks": receipts,
                    **({"reason": "candidate_or_checks_not_current"} if not valid else {})}

    def reserve_handoff_time(self, deadline: float | None) -> None:
        self._feedback_deadline = deadline

    def begin_write_batch(self):
        """Defer only this owner's automatic checks, never explicit checks."""
        if self._closed or self._write_batch.get() is not None:
            raise RuntimeError("implementation feedback batch is unavailable")
        return _WriteFeedbackBatch(self)

    def _create_test_tool(self, paths: tuple[str, ...]) -> ToolDefinition:
        def validate(value):
            if (not isinstance(value, dict) or set(value) != {"path", "code"}
                    or value.get("path") not in paths or not isinstance(value.get("code"), str)):
                raise ValueError("write code only to an assigned regression-test path")
            return value

        def write(value, context):
            target = resolve_tool_path(context, value["path"], "write")
            return apply_reviewed_file_change(context, value["path"], target, value["code"])

        return self.with_write_feedback(ToolDefinition(
            name="executor_test",
            description=("Create or repair the requested regression test, then run the approved "
                         "checks on an isolated copy. Test failures retain the file for repair. "
                         "Use this to finish the test deliverable before executor_submit, including "
                         "during finalization; it cannot edit source files. Assigned test paths: "
                         + json.dumps(paths)),
            input_schema={"type": "object", "additionalProperties": False,
                          "properties": {"path": {"type": "string", "enum": list(paths)},
                                         "code": {"type": "string"}},
                          "required": ["path", "code"]},
            validator=validate, run=write,
            capability_profile=CapabilityProfile(
                domain=CapabilityDomain.EXECUTION, scope=CapabilityScope.WRITE,
                effects=CapabilityEffects(read_only=False, destructive=False, external=False,
                                          executes_code=True, delegates=False),
                purposes=("Finish an assigned test and obtain real feedback",),
            ),
        ))

    def _validate(self, value: dict) -> dict:
        if (not isinstance(value, dict) or set(value) != {"check"}
                or type(value["check"]) is not int
                or not 1 <= value["check"] <= len(self._checks)):
            raise ValueError("choose an approved check number; no command overrides are allowed")
        return value

    def _run(self, value: dict, _context) -> ToolResult:
        if self._closed:
            return ToolResult(ok=False, output="error[executor_check_closed]: worker has ended")
        import time
        check = self._checks[value["check"] - 1]
        with self._observation_lock:
            epoch = self._mutation_epoch
            # A retry must not leave an older pass usable if it never completes.
            self._latest_receipts.pop(check.check_id, None)
            self._latest_full_receipts.pop(check.check_id, None)
        if self._feedback_deadline is not None:
            if time.monotonic() >= self._feedback_deadline:
                receipt = {"checkId": check.check_id, "status": "inconclusive",
                           "reason": "handoff_time_reserved", "verification": "implementation_feedback"}
                return ToolResult(ok=False, output=json.dumps(receipt))
            current = getattr(_context, "_task_deadline_monotonic", None)
            _context = replace(_context, _task_deadline_monotonic=min(current, self._feedback_deadline)
                               if current is not None else self._feedback_deadline)
        abandoned = getattr(_context, "_tool_abandoned", None)
        if abandoned is not None and abandoned.is_set():
            raise ToolExecutionAbandoned("owning invocation was abandoned")
        try:
            receipt = self._run_check(check, _context)
        except (VerificationDeadlineExceeded, TaskDeadlineExceeded) as error:
            # The edit has already happened. A feedback deadline must not turn
            # that successful mutation into a failed/no-change tool result.
            # Cancellation and abandoned execution are distinct control flow
            # and deliberately remain uncaught.
            receipt = {"checkId": check.check_id, "argv": list(check.argv),
                       "status": "inconclusive", "reason": "verification_deadline",
                       "errorType": type(error).__name__,
                       "verification": "implementation_feedback"}
        # Tool results are displayed in call order, which can differ from
        # completion order for concurrent writes/checks. This local sequence
        # identifies the latest observed feedback for context projection only;
        # patch-bound verification and final promotion retain their own gates.
        # Diagnostics references do not change the acceptance receipt schema.
        # Retain them only in the model/journal projection after capturing the
        # original gate evidence below.
        output_manifests = receipt.pop("verificationOutputs", None)
        with self._observation_lock:
            self._observation_sequence += 1
            receipt = {**receipt, "observationSequence": self._observation_sequence}
            if (not self._closed and not self._active_writes and epoch == self._mutation_epoch
                    and receipt.get("checkId") == check.check_id):
                # Capture before model-display projection removes duplicate
                # stream encodings. Strings survive close without callbacks or
                # mutable references to the provider/tool result.
                self._latest_full_receipts[check.check_id] = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
                self._latest_receipts[check.check_id] = {
                    "status": receipt.get("status"), "patchDigest": receipt.get("patchDigest"),
                }
        if output_manifests:
            receipt = {**receipt, "verificationOutputs": output_manifests}
        recorder = getattr(getattr(_context, "_event_sink", None), "record_tool_evidence", None)
        if callable(recorder):
            try:
                recorder(tool_name="implementation_check", phase="finished",
                         output=json.dumps(receipt, ensure_ascii=False),
                         is_error=receipt["status"] != "passed")
            except Exception:
                pass  # Evidence persistence never changes check or promotion semantics.
        # Binary and duplicate stdout encodings add no information to a text
        # model. Keep the real bounded textual stream and its digest.
        evidence = receipt.get("evidence", {})
        for stream in ("stdout", "stderr"):
            if isinstance(evidence.get(stream), dict):
                evidence[stream].pop("previewBase64", None)
        return ToolResult(ok=receipt["status"] == "passed",
                          output=json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                          _implementation_checks_failed=(True if receipt["status"] == "failed" else
                              False if receipt["status"] == "passed" and len(self._checks) == 1 else None))

    def with_write_feedback(self, tool: ToolDefinition) -> ToolDefinition:
        """Observe actual mutations, even when the model never requests a test."""
        def write_and_check(value, context):
            if self._closed:
                return ToolResult(ok=False, output="error[executor_check_closed]: worker has ended")
            import time
            if self._feedback_deadline is not None and time.monotonic() >= self._feedback_deadline:
                result = ToolResult(ok=False, output="error[implementation_handoff_reserved]: no time remains for candidate writes")
                self.observe_tool_result(tool.name, value, result, context=context)
                return result
            if self._feedback_deadline is not None:
                current = getattr(context, "_task_deadline_monotonic", None)
                context = replace(context, _task_deadline_monotonic=min(current, self._feedback_deadline)
                                  if current is not None else self._feedback_deadline)
            batch = self._write_batch.get()
            # This key only correlates actual writer invocations; it grants no
            # path access. Unknown inputs fail closed until capability revocation.
            write_key = self._write_key(value, context)
            with self._observation_lock:
                self._active_writes += 1
            try:
                result = tool.run(value, context)
            except BaseException:
                with self._observation_lock:
                    self._active_writes -= 1
                    self._invalidate_receipts()
                    self._unresolved_write_errors.add(write_key)
                if batch is not None:
                    # The underlying writer may have mutated before failing.
                    # Check the current candidate, never infer rollback/success.
                    batch.observe(context, uncertain=True)
                raise
            with self._observation_lock:
                self._active_writes -= 1
                if result._file_changed or not result.ok:
                    self._invalidate_receipts()
                if not result.ok:
                    self._unresolved_write_errors.add(write_key)
            if batch is not None:
                if result._file_changed:
                    batch.observe(context)
                elif not result.ok:
                    batch.observe(context, uncertain=True)
                return result
            # A rejected write invalidates previous receipts just like a batch's
            # uncertain write. Recheck current bytes without acknowledging the
            # original error or clearing its completion barrier.
            if (result.ok and not result._file_changed) or not self._checks:
                return result
            receipts = [json.loads(self._run({"check": index}, context).output)
                        for index in range(1, len(self._checks) + 1)]
            failed = (True if any(r["status"] == "failed" for r in receipts) else
                      False if all(r["status"] == "passed" for r in receipts) else None)
            return replace(result, output=result.output +
                           "\n\n=== IMPLEMENTATION CHECK FEEDBACK ===\n" +
                           json.dumps(receipts, ensure_ascii=False, sort_keys=True),
                           _implementation_checks_failed=failed)

        return replace(tool, run=write_and_check,
                       description=tool.description +
                       " Actual changes automatically return approved test results from an isolated copy.")

    def close(self) -> None:
        with self._observation_lock:
            self._closed = True
            if self._output_reader is not None:
                self._output_reader.close()
            # Drop execution authority as well as disabling its tool entry point.
            self._run_check = None
            self._current_patch_digest = None
            self._invalidate_receipts()
            self._unresolved_write_errors.clear()
