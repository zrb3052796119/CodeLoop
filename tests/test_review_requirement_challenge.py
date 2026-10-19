"""Public micro-requirement: a green cache-hit test misses invalidation.

Model decisions are scripted, never claimed as model review quality. Candidate
edits, pytest, native submission, verification, audit and promotion are real.
No cumulative execution quota or hidden task/answer is used by this fixture.
"""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage

INITIAL = '''class Cache:
    def __init__(self, loader):
        self.loader = loader
        self.values = {}

    def get(self, key):
        return self.loader(key)

    def invalidate(self, key):
        pass
'''
PARTIAL = INITIAL.replace(
    "        return self.loader(key)",
    "        if key not in self.values:\n"
    "            self.values[key] = self.loader(key)\n"
    "        return self.values[key]",
)
COMPLETE = PARTIAL.replace("        pass", "        self.values.pop(key, None)")
SELF_TEST = '''from cache import Cache

def test_hit_calls_loader_once():
    calls = []
    cache = Cache(lambda key: calls.append(key) or 7)
    assert cache.get("a") == cache.get("a") == 7
    assert calls == ["a"]
'''
REPAIRED_TEST = SELF_TEST + '''
def test_invalidate_refreshes_only_target_and_missing_key_is_harmless():
    backing = {"a": 1, "b": 2}
    cache = Cache(backing.__getitem__)
    assert cache.get("a") == 1
    assert cache.get("b") == 2
    backing.update(a=3, b=4)
    cache.invalidate("a")
    cache.invalidate("missing")
    cache.invalidate("missing")
    assert cache.get("a") == 3
    assert cache.get("b") == 2
'''
CRITERIA = (
    ("hit", "Repeated get(key) calls reuse the cached value without calling the loader again."),
    ("invalidate", "invalidate(key) evicts only that key, so its next get reloads the new value."),
    ("missing", "Invalidating a missing key is harmless and idempotent."),
)


def _public_invalidation_observation(source):
    """Execute only this test's tiny local class, without imports or I/O."""
    namespace = {}
    exec(compile(source, "public-cache-probe", "exec"), namespace)  # noqa: S102 - local synthetic fixture only
    backing = {"a": 1, "b": 2}
    cache = namespace["Cache"](backing.__getitem__)
    before = cache.get("a")
    other = cache.get("b")
    backing.update(a=3, b=4)
    cache.invalidate("a")
    cache.invalidate("missing")
    return {"before": before, "afterInvalidation": cache.get("a"),
            "untouchedCachedValue": cache.get("b"), "otherBefore": other}


def _exercise_blind_approval(tmp_path, monkeypatch, *, independent_challenge=False, blind_before_probe=False,
                             format_rejections=0, harness_correction=False,
                             harness_failure_kind="exception", pending_before_probe=None, pending_rounds=1):
    target = tmp_path / "cache.py"
    target.write_text(INITIAL)
    (tmp_path / "test_compat.py").write_text(
        'from cache import Cache\ndef test_load():\n    assert Cache(lambda _: 7).get("a") == 7\n'
    )
    budget = AgentTurnBudget()
    calls, billed, workers, models, checks, worker_results, probe_receipts = [], [], [], [], [], [], []
    recovery_tool_views, correction_output_limits = [], []
    harness_receipts = []
    invalid_harness = '''from cache import Cache

def test_loader_contract():
    cache = Cache(None)
    assert cache.get("a") == 7
'''
    corrected_harness = invalid_harness.replace("Cache(None)", "Cache(lambda _: 7)")
    if harness_failure_kind == "assertion":
        invalid_harness = invalid_harness.replace("Cache(None)", "Cache(lambda _: 0)")
    original_worker = task_module._run

    def worker(data, context):
        ledger = context._agent_budget
        while hasattr(ledger, "_budget"):
            ledger = ledger._budget
        assert ledger is budget
        assert context._task_deadline_monotonic is None
        assert context._logical_agent_max_model_calls is None
        session = data.get("_executor_session")
        entry = {"role": context._logical_agent_role, "session": session, "cwd": context.cwd,
                 "beforeContext": session.context_manager if session else None,
                 "channel": context._subagent_mailbox}
        if session and any(item["role"] == "executor" for item in workers):
            assert (Path(context.cwd) / "cache.py").read_text() == PARTIAL
            assert (Path(context.cwd) / "test_regression.py").read_text() == SELF_TEST
        workers.append(entry)
        worker_result = original_worker(data, context)
        if session:
            entry.update(afterContext=session.context_manager, model=session.model)
        worker_results.append(worker_result.output)
        return worker_result

    def action(name, values, identity):
        return {"id": identity, "toolName": name, "input": values}

    def metered(role, messages, tools, actions):
        calls.append((role, deepcopy(messages)))
        schemas = [{"type": "function", "function": {"name": tool.name,
                    "description": tool.description, "parameters": tool.input_schema}}
                   for tool in tools.list()]
        inputs = sum(estimate_message_tokens(message) for message in messages) + estimate_tokens(json.dumps(schemas))
        outputs = estimate_tokens(json.dumps(actions))
        billed.append(inputs + outputs)
        return AgentStep(type="tool_calls", calls=actions, usage=ModelUsage(
            input_tokens=inputs, output_tokens=outputs, source="estimated"))

    class Executor:
        tools = None
        count = 0

        def next(self, messages, tools_override=None, **_kwargs):
            self.count += 1
            tools = tools_override or self.tools
            if self.count == 1:
                actions = [action("edit_file", {"path": "cache.py", "old": INITIAL, "new": PARTIAL}, "cache-edit"),
                           action("executor_test", {"path": "test_regression.py", "code": SELF_TEST}, "cache-self-test")]
            elif independent_challenge and self.count == 3:
                assert "invalidate" in str(messages) and "review_probe_" in str(messages)
                assert "test_invalidate_refreshes_only_target" in str(messages)
                assert any(message.get("toolUseId") == "cache-edit" for message in messages)
                if harness_correction:
                    assert harness_receipts[1]["correctionRef"] in str(messages)
                actions = [action("executor_test", {"path": "test_regression.py", "code": REPAIRED_TEST},
                                  "add-missing-regression")]
            elif independent_challenge and self.count == 4:
                assert any(item["exit"] == 1 and not item["formal"] for item in checks)
                assert "test_invalidate_refreshes_only_target" in str(messages)
                actions = [action("edit_file", {"path": "cache.py", "old": PARTIAL, "new": COMPLETE},
                                  "repair-cache-invalidation")]
            else:
                assert self.count in ({2, 5} if independent_challenge else {2}), [
                    (m.get("toolName"), m.get("content")) for m in messages if m.get("role") == "tool_result"]
                submit = next(tool for tool in tools.list() if tool.name == "executor_submit")
                count = submit.input_schema["properties"]["steps"]["items"]["properties"]["step"]["maximum"]
                actions = [action("executor_submit", {"status": "completed",
                    "steps": [{"step": step, "status": "completed"} for step in range(1, count + 1)],
                    "deviations": [], "blockers": []}, f"cache-submit-{self.count}")]
            return metered("executor", messages, tools, actions)

    class Reviewer:
        tools = None
        count = 0

        def __init__(self, revision):
            self.revision = revision

        def next(self, messages, tools_override=None, **_kwargs):
            self.count += 1
            tools = tools_override or self.tools
            assert target.read_text() == INITIAL, "Parent must remain untouched while reviewing."
            if (format_rejections and self.revision > 1
                    and len([entry for entry in workers if entry["role"] == "executor"]) == 1):
                # The buggy loop may replace the first Reviewer before its
                # feedback reaches the Executor. Return a real inconclusive
                # result so the observable same-session assertion owns failure.
                return metered("reviewer", messages, tools, [action("review_submit", {
                    "verdict": "inconclusive", "criteria": [], "blockingFindings": [],
                    "warnings": [], "requestedChecks": [], "confidence": "low",
                }, "replacement-reviewer-no-repair")])
            if independent_challenge:
                if self.revision == 1 and pending_before_probe and self.count <= pending_rounds:
                    values = {"verdict": "inconclusive", "criteria": [
                        {"criterionId": identity, "status": "unknown"} for identity, _ in CRITERIA],
                        "blockingFindings": [], "warnings": [], "requestedChecks": [], "confidence": "low"}
                    name = "review_submit"
                    if pending_before_probe == "legacy":
                        channel = workers[-1]["channel"]
                        documents = [json.loads(channel.read(ref).content) for ref in channel.readable_keys]
                        contract = next(doc["assignment"] for doc in documents
                                        if doc.get("resultType") == "AssignmentArtifact.v1"
                                        and doc["assignment"]["role"] == "reviewer")
                        matrix = next(doc for doc in documents if doc.get("resultType") == "AcceptanceEvidenceMatrix.v1")
                        values.update(resultVersion=1, assignmentId=contract["assignmentId"], patchDigest=matrix["patchDigest"])
                        for row in values["criteria"]:
                            row["evidenceRefs"] = []
                        values = {"key": channel.write_key, "content": json.dumps(values)}
                        name = "subagent_note_write"
                    return metered("reviewer", messages, tools, [action(name, values, f"pending-review-{self.count}")])
                if self.revision == 1 and harness_correction and self.count <= 2:
                    values = {"criterionIds": ["hit"], "scenario": "Calling a valid loader returns its cached value.",
                              "code": invalid_harness if self.count == 1 else corrected_harness}
                    if self.count == 2:
                        feedback = next(message for message in messages
                                        if message.get("toolUseId") == "invalid-review-harness"
                                        and message.get("role") == "tool_result")
                        receipt, _ = json.JSONDecoder().raw_decode(feedback["content"].lstrip())
                        assert receipt["status"] == "failed" and receipt["evidence"]["exitCode"] == 1
                        harness_receipts.append(receipt)
                        values.update(correctsProbeId=receipt["probeId"],
                            correctionReason=("Cache returns the loader's value. This scenario expects 7, but the fixture returned 0; "
                                "supply a loader for the intended value without changing the assertion."
                                if harness_failure_kind == "assertion" else
                                "The public Cache constructor requires a callable loader; None was a probe setup mistake. The test and assertion remain unchanged."),
                            sourceEvidence=[{"path": "cache.py", "startLine": 1, "endLine": 11}])
                    return metered("reviewer", messages, tools, [action("review_test", values,
                        "invalid-review-harness" if self.count == 1 else "correct-review-harness")])
                if self.revision == 1 and harness_correction and self.count == 3:
                    feedback = next(message for message in messages
                                    if message.get("toolUseId") == "correct-review-harness"
                                    and message.get("role") == "tool_result")
                    receipt, _ = json.JSONDecoder().raw_decode(feedback["content"].lstrip())
                    assert receipt["status"] == "passed", feedback
                    assert receipt["correctedProbeId"] == harness_receipts[0]["probeId"]
                    assert receipt["correctionRef"]
                    harness_receipts.append(receipt)
                if self.revision == 1 and blind_before_probe and self.count == 1:
                    return metered("reviewer", messages, tools, [action("review_submit", {
                        "verdict": "approved",
                        "criteria": [{"criterionId": identity, "status": "satisfied"} for identity, _ in CRITERIA],
                        "blockingFindings": [], "warnings": [], "requestedChecks": [], "confidence": "high",
                    }, "blind-before-probe")])
                if self.revision == 1 and 2 <= self.count <= format_rejections + 1:
                    if self.count > 2:
                        assert any(message.get("toolUseId") == f"format-review-{self.count - 1}"
                                   and message.get("isError") for message in messages)
                        correction_output_limits.append(_kwargs.get("max_output_tokens_override", 8192))
                    values = {"verdict": "changes_required", "criteria": [
                        {"criterionId": identity, "status": "failed" if identity == "invalidate" else "satisfied"}
                        for identity, _ in CRITERIA], "blockingFindings": [{"criterionIds": ["invalidate"],
                        "statement": "The real independent probe shows stale data after invalidation."}],
                        "warnings": [], "requestedChecks": [], "confidence": "high"}
                    values["criteria"][self.count - 2].pop("status")
                    return metered("reviewer", messages, tools, [
                        action("review_submit", values, f"format-review-{self.count}")])
                review_count = (self.count - int(blind_before_probe and self.revision == 1)
                                - (pending_rounds if pending_before_probe and self.revision == 1 else 0)
                                - (format_rejections if self.revision == 1 and self.count > format_rejections + 1 else 0)
                                - (2 if harness_correction and self.revision == 1 else 0))
                if self.revision == 1 and review_count == 1:
                    assert [check["exit"] for check in checks if check["formal"]] == [0, 0]
                    if pending_before_probe:
                        pending = next(message for message in messages if message.get("role") == "tool_result"
                                       and message.get("toolUseId") == f"pending-review-{pending_rounds}")
                        assert not pending["isError"]
                        feedback = json.loads(pending["content"])
                        assert feedback["reviewState"] == "pending_evidence"
                        assert {item["criterionId"] for item in feedback["remainingCriteria"]} == {cid for cid, _ in CRITERIA}
                        assert "review_test" in {tool.name for tool in tools.list()}
                        channel = workers[-1]["channel"]
                        assert channel._store.read(channel.write_key) is None
                    if blind_before_probe:
                        rejection = next(message for message in messages
                                         if message.get("role") == "tool_result"
                                         and message.get("toolUseId") == "blind-before-probe")
                        assert rejection["isError"]
                        assert "approval requires at least one real independent probe" in rejection["content"]
                        recovery_tool_views.append({tool.name for tool in tools.list()})
                        if "review_test" not in recovery_tool_views[-1]:
                            return metered("reviewer", messages, tools, [action("review_submit", {
                                "verdict": "inconclusive",
                                "criteria": [{"criterionId": identity, "status": "unknown"} for identity, _ in CRITERIA],
                                "blockingFindings": [], "warnings": [], "requestedChecks": [], "confidence": "high",
                            }, "missing-evidence-cannot-check")])
                    return metered("reviewer", messages, tools, [action("review_test", {
                        "criterionIds": [identity for identity, _ in CRITERIA],
                        "scenario": "A loaded key changes in the backing store; invalidate it and reload only that key.",
                        "code": REPAIRED_TEST,
                    }, "independent-invalidation-probe")])
                if self.revision == 1:
                    assert review_count == 2
                    feedback = next(message for message in messages
                                    if message.get("toolUseId") == "independent-invalidation-probe"
                                    and message.get("role") == "tool_result")
                    # Failed ToolResults retain their JSON receipt followed by
                    # the normal tool-pipeline error guidance for the model.
                    receipt, _end = json.JSONDecoder().raw_decode(feedback["content"].lstrip())
                    assert receipt["status"] == "failed" and receipt["evidence"]["exitCode"] == 1
                    assert (Path(workers[-1]["cwd"]) / "test_regression.py").read_text() == SELF_TEST
                    assert (Path(workers[-1]["cwd"]) / "cache.py").read_text() == PARTIAL
                    probe_receipts.append(receipt)
                    values = {"verdict": "changes_required",
                        "criteria": [{"criterionId": identity, "status": "failed" if identity == "invalidate" else "satisfied"}
                                     for identity, _ in CRITERIA],
                        "blockingFindings": [{"criterionIds": ["invalidate"],
                            "statement": "The independent invalidation probe keeps the stale value after invalidate; fix that behavior and add its regression."}],
                        "warnings": [], "requestedChecks": [], "confidence": "high"}
                else:
                    assert self.revision == 2 and self.count == 1
                    assert [check["exit"] for check in checks if check["formal"]] == [0, 0, 0, 0]
                    assert probe_receipts[0]["probeId"] in str(messages)
                    values = {"verdict": "approved",
                        "criteria": [{"criterionId": identity, "status": "satisfied",
                                      "rationale": "The original independent cache probe now passes on this candidate.",
                                      "probeIds": [probe_receipts[0]["probeId"]], "sourceEvidence": []}
                                     for identity, _ in CRITERIA],
                        "blockingFindings": [], "warnings": [], "requestedChecks": [], "confidence": "high"}
                return metered("reviewer", messages, tools,
                               [action("review_submit", values, f"independent-review-{self.revision}")])
            if self.count == 1:
                assert [check["exit"] for check in checks if check["formal"]] == [0, 0]
                verdict, status = "approved", "satisfied"
            else:
                # A future host evidence gate must return its actual error;
                # a blind reviewer cannot invent probe evidence or loop forever.
                assert self.count == 2
                rejection = next(message for message in messages
                                 if message.get("toolUseId") == "blind-approval"
                                 and message.get("role") == "tool_result")
                assert rejection["isError"]
                assert "approval requires at least one real independent probe" in rejection["content"]
                verdict, status = "inconclusive", "unknown"
            return metered("reviewer", messages, tools, [action("review_submit", {
                "verdict": verdict,
                "criteria": [{"criterionId": identity, "status": status} for identity, _ in CRITERIA],
                "blockingFindings": [], "warnings": [],
                # A request for additional verification authority remains a
                # terminal inconclusive result, never approval. Unknown-only
                # work with the existing tools is covered by the pending tests.
                "requestedChecks": ([] if self.count == 1 else [
                    "An additional verification command requires owner reassessment."]),
                "confidence": "high",
            }, "blind-approval" if self.count == 1 else "honest-inconclusive")])

    executor = Executor()

    def factory(_runtime, agent_type, **kwargs):
        model = executor if agent_type == "general" else Reviewer(
            1 + sum(kind != "general" for kind, _ in models))
        model.tools = kwargs["tools"]
        models.append((agent_type, model))
        return model

    class TestSandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    class Verifier(DeterministicVerifier):
        def run(self, **kwargs):
            assert target.read_text() == INITIAL
            result = super().run(**kwargs)
            formal = getattr(kwargs["evidence_sink"], "__name__", "") == "per_check_sink"
            checks.extend({"formal": formal, "exit": item["exitCode"], "cwd": kwargs["cwd"],
                           "argv": item.get("argv"), "source": (Path(kwargs["cwd"]) / "test_regression.py").read_text()
                           if (Path(kwargs["cwd"]) / "test_regression.py").exists() else None}
                          for item in result["checks"])
            return result

    verifier = Verifier(sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    from minicode.tools.subagent_notes import subagent_note_write_tool

    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([edit_file_tool, read_file_tool]
                            + ([subagent_note_write_tool] if pending_before_probe == "legacy" else [])))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    monkeypatch.setattr("minicode.subagent_types._read_max_turns_override", lambda _name: None)
    monkeypatch.delenv("MINICODE_TASK_TIMEOUT_SECONDS", raising=False)
    request = {"requestVersion": 1,
        "profile": {"taskId": "task_cache_requirement_challenge", "kind": "change", "risk": "low",
                    "complex": False, "parentHasSufficientContext": True},
        "scope": {"readRoots": ["."], "writePaths": ["cache.py", "test_regression.py"],
                  "forbiddenPaths": [".git", ".env"]},
        "criteria": [{"id": identity, "kind": "behavior", "text": text} for identity, text in CRITERIA],
        "verificationChecks": [{"checkId": name, "kind": "tests", "argv": ["python", "-m", "pytest", "-q", path],
                                "criterionIds": [identity for identity, _ in CRITERIA]}
                               for name, path in (("compat", "test_compat.py"), ("regression", "test_regression.py"))]}
    data_dir = tmp_path.parent / (tmp_path.name + "-journal")
    with observe_run(workspace=tmp_path, source="headless", title="public cache requirement challenge",
                     journal_factory=lambda workspace: RunJournal(workspace, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run({"description": "cache reuse and targeted invalidation", "agent_type": "workflow",
            "prompt": "Implement the stated cache-hit, targeted invalidation and missing-key behaviors; add regression tests.",
            "workflow_request": request}, ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture"},
                _agent_budget=budget, _event_sink=observation, _central_workflow_v2_enforced=True))
        run_id = observation.run_id
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    if format_rejections:
        assert len(correction_output_limits) == format_rejections - 1, (
            "The same Reviewer stopped before delivering all native correction feedback.", worker_results)
        assert correction_output_limits == [8192] * (format_rejections - 1)
    if blind_before_probe:
        assert recovery_tool_views and "review_test" in recovery_tool_views[0], (
            "Missing independent evidence must let this same Reviewer perform the real check; "
            f"actual tools were {recovery_tool_views}."
        )
    snapshot = budget.snapshot()
    assert (snapshot.limit_total_tokens, snapshot.limit_model_calls, snapshot.limit_cost_usd) == (None, None, None)
    assert snapshot.used_model_calls == len(calls) and snapshot.used_total_tokens == sum(billed)
    assert snapshot.usage_uncertain and snapshot.reserved_total_tokens == 0
    assert [check["exit"] for check in checks if check["formal"]] == ([0] * 4 if independent_challenge else [0, 0]), (worker_results, result.output)
    journal = RunJournal(tmp_path, data_dir=data_dir)
    audit = journal.get_scheduler_audit(run_id, trace["workflowId"])
    assert audit is not None
    return result, trace, audit, journal, run_id, calls, checks, workers, models


def test_green_self_tests_and_blind_approval_cannot_promote_unchecked_requirement(tmp_path, monkeypatch):
    result, trace, _audit, journal, run_id, calls, _checks, _workers, _models = _exercise_blind_approval(tmp_path, monkeypatch)
    observation = _public_invalidation_observation((tmp_path / "cache.py").read_text())
    assert not trace["committed"], (
        "Host committed the cache after only cache-hit self-tests and a scripted blind approval; "
        f"the independently stated public invalidation sequence returned {observation}."
    )
    assert not result.ok
    reviews = [messages for role, messages in calls if role == "reviewer"]
    assert len(reviews) == 2
    assert any(message.get("toolUseId") == "blind-approval" and message.get("isError")
               and "approval requires at least one real independent probe" in message.get("content", "")
               for message in reviews[-1])
    assert (tmp_path / "cache.py").read_text() == INITIAL
    assert journal.get_scheduler_commit_ready(run_id, trace["workflowId"]) is None


@pytest.mark.parametrize("blind_before_probe", [False, True], ids=["direct-probe", "recover-missing-evidence"])
def test_independent_requirement_probe_returns_to_same_executor_and_is_replayed(tmp_path, monkeypatch, blind_before_probe):
    result, trace, audit, journal, run_id, calls, checks, workers, models = _exercise_blind_approval(
        tmp_path, monkeypatch, independent_challenge=True, blind_before_probe=blind_before_probe)
    assert result.ok and trace["committed"], result.output
    assert (tmp_path / "cache.py").read_text() == COMPLETE
    assert (tmp_path / "test_regression.py").read_text() == REPAIRED_TEST
    executors = [entry for entry in workers if entry["role"] == "executor"]
    assert len(executors) == 2
    assert executors[0]["session"] is executors[1]["session"]
    assert executors[0]["model"] is executors[1]["model"]
    assert executors[0]["afterContext"] is executors[1]["beforeContext"]
    assert sum(kind == "general" for kind, _ in models) == 1
    assert [role for role, _ in calls] == (["executor", "executor"] + ["reviewer"] * (3 if blind_before_probe else 2)
                                         + ["executor", "executor", "executor", "reviewer"])
    assert all(not Path(item["cwd"]).exists() for item in checks)
    assert sum(item["outcome"] == "repairing" for item in audit.trace["transitions"]) == 1
    assert journal.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None
    documents = [json.loads(artifact.content) for artifact in audit.artifacts]
    sources = [doc for doc in documents if doc.get("resultType") == "ReviewProbeSource.v1"]
    probes = sorted((doc for doc in documents if doc.get("resultType") == "ReviewProbeExecution.v1"),
                    key=lambda doc: doc["sequence"])
    assert len(sources) == 1 and sources[0]["code"] == REPAIRED_TEST
    # The original probe is also part of real implementation feedback: adding
    # the missing self-test still fails it; the source edit passes it; fresh
    # Reviewer admission independently replays that exact source once more.
    assert [doc["status"] for doc in probes] == ["failed", "failed", "passed", "passed"]
    assert [doc["evidence"]["exitCode"] for doc in probes] == [1, 1, 0, 0]
    assert len({doc["probeId"] for doc in probes}) == 1
    assert len({doc["sourceRef"] for doc in probes}) == 1
    assert len({doc["patchDigest"] for doc in probes}) == 3
    assert probes[-1]["patchDigest"] == probes[-2]["patchDigest"]
    assert all(doc["evidence"]["argv"] == ["python", "-m", "pytest", "-q", "__minicode_review_probe__.py"]
               for doc in probes)
    assert all(doc["executionBinding"]["baseCheck"]["argv"]
               == ["python", "-m", "pytest", "-q", "test_regression.py"] for doc in probes)
    events = [event.payload for child in journal.list_subagent_runs(run_id)
              for event in journal.list_subagent_events(run_id, child.subagent_id)
              if event.type == "execution.executor_session"]
    assert len(events) == 2 and events[0]["sessionId"] == events[1]["sessionId"]
    resumed = [event for event in events if event["resumed"]]
    assert len(resumed) == 1 and resumed[0]["modelReused"] and resumed[0]["priorMessageCount"] > 0
