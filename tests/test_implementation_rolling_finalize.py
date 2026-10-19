"""Replay a real V16 public-failure boundary without replaying model requests."""
from pathlib import Path
import hashlib
import json
import unittest

from minicode.agent_budget import AgentTurnBudget
from minicode.context_manager import estimate_message_tokens
from minicode.implementation_phases import ImplementationPhaseController
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult


FIXTURES = Path(__file__).with_name("fixtures")


def tool(name):
    return ToolDefinition(name=name, description=name, input_schema={"type": "object"},
                          validator=lambda value: value,
                          run=lambda _value, _ctx: ToolResult(ok=True, output="unused"))


def replay(native=False):
    raw = (FIXTURES / "v16-remediation-transcript.json").read_bytes()
    trace = json.loads((FIXTURES / "v16-remediation-trace.json").read_text())
    assert hashlib.sha256(raw).hexdigest() == trace["transcriptSha256"]
    messages = json.loads(raw)
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=40,
        now=0, deadline=600, final_response_calls=1, allow_test_completion=True,
        allow_checked_source_completion=True, require_structured_handoff=native)
    registry = ToolRegistry([tool("write_file"), tool("executor_test")]
                            + ([tool("executor_submit")] if native else []))
    observed_input = 0
    for entry, following in zip(trace["requests"][:15], trace["requests"][1:16]):
        estimated = max(observed_input, sum(estimate_message_tokens(m)
                        for m in messages[:entry["prefixEnd"]]))
        decision = policy.request(budget.snapshot(), estimated_input=estimated,
                                  now=entry["relativeStartSeconds"])
        policy.tool_view(registry)
        reservation = budget.reserve_model_call(estimated,
            preserve_model_calls=1 + decision.preserve_calls,
            preserve_total_tokens=decision.preserve_tokens)
        policy.admitted()
        budget.record_model_call(input_tokens=entry["inputTokens"],
                                 output_tokens=entry["outputTokens"], reservation=reservation)
        observed_input = entry["inputTokens"]
        for message in messages[entry["prefixEnd"]:following["prefixEnd"]]:
            content = message.get("content", "")
            marker = "=== IMPLEMENTATION CHECK FEEDBACK ==="
            if message.get("role") == "tool_result" and marker in content:
                checks = json.loads(content.split(marker, 1)[1])
                policy.observe_verification(failed=any(c["status"] != "passed" for c in checks))
                policy.observe_change(True)
    last = trace["requests"][15]
    estimated = max(observed_input, sum(estimate_message_tokens(m)
                    for m in messages[:last["prefixEnd"]]))
    return policy, budget, registry, last, estimated


class RollingFinalizeTests(unittest.TestCase):
    def _real_trace(self, native):
        policy, budget, registry, last, estimated = replay(native)
        self.assertEqual(budget.snapshot().used_total_tokens, 378151)
        self.assertEqual(budget.snapshot().used_model_calls, 15)
        self.assertEqual(estimated, 46370)
        self.assertFalse(policy.exhausted)
        decision = policy.request(budget.snapshot(), estimated_input=estimated,
                                  now=last["relativeStartSeconds"])
        self.assertFalse(decision.handoff_only,
                         "V16 has 121849 tokens and >200s: allow a funded repair plus real handoff")
        self.assertIn("write_file", {t.name for t in policy.tool_view(registry).list()})
        self.assertEqual(decision.preserve_calls, 1)
        # The optional request uses the production atomic reservation, not an
        # extra budget. Charge this bounded simulated attempt; no claim is made
        # that a real model would produce a correct repair.
        reservation = budget.reserve_model_call(estimated,
            preserve_model_calls=1 + decision.preserve_calls,
            preserve_total_tokens=decision.preserve_tokens)
        policy.admitted()
        budget.record_model_call(input_tokens=estimated, output_tokens=4096,
                                 reservation=reservation)
        self.assertFalse(policy.exhausted, "the funded handoff must still reach the next request")
        final = policy.request(budget.snapshot(), estimated_input=estimated + 6000,
                               now=last["relativeStartSeconds"] + 40)
        self.assertTrue(final.handoff_only)
        self.assertEqual({t.name for t in policy.tool_view(registry).list()},
                         {"executor_submit"} if native else set())
        reservation = budget.reserve_model_call(estimated + 6000,
            preserve_model_calls=1, preserve_total_tokens=final.preserve_tokens)
        policy.admitted()
        budget.record_model_call(input_tokens=estimated + 6000, output_tokens=2048,
                                 reservation=reservation)
        self.assertTrue(policy.exhausted)
        self.assertLessEqual(budget.snapshot().used_total_tokens, 500000)

    def test_real_v16_single_failure_keeps_one_affordable_repair_and_final(self):
        self._real_trace(False)

    def test_real_v16_native_failure_keeps_submit_after_repair(self):
        self._real_trace(True)


    def test_actual_output_growth_can_retire_the_optional_repair(self):
        policy, budget, registry, last, _estimated = replay()
        decision = policy.request(budget.snapshot(), estimated_input=90000,
                                  now=last["relativeStartSeconds"])
        self.assertTrue(decision.handoff_only)
        self.assertNotIn("write_file", {t.name for t in policy.tool_view(registry).list()})

    def test_committed_handoff_floor_cannot_be_renewed_by_more_repair_requests(self):
        policy, budget, registry, last, _estimated = replay()
        self.assertIsNotNone(policy.finalization_started_at)
        self.assertEqual(policy.handoff_start, 555)
        optional = policy.request(budget.snapshot(), estimated_input=1000, now=554)
        self.assertFalse(optional.handoff_only)
        self.assertEqual(optional.deadline, 555)
        self.assertLess(optional.deadline, policy.deadline)
        policy.admitted()
        final = policy.request(budget.snapshot(), estimated_input=1000, now=555)
        self.assertTrue(final.handoff_only)
        self.assertEqual(final.deadline, 600)
        self.assertEqual(policy.tool_view(registry).list(), [])
        self.assertEqual(policy.deadline, 600)

    def test_outer_final_call_is_not_spent_by_a_repair(self):
        budget = AgentTurnBudget(max_model_calls=20, max_total_tokens=500000)
        policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=20,
            now=0, deadline=600, final_response_calls=1, allow_test_completion=True)
        for _ in range(18):
            reservation = budget.reserve_model_call(1)
            budget.record_model_call(input_tokens=1, reservation=reservation)
        policy.observe_verification(failed=True)
        final = policy.request(budget.snapshot(), estimated_input=1000, now=480)
        self.assertTrue(final.handoff_only)
        reservation = budget.reserve_model_call(1000, preserve_model_calls=1,
                                                preserve_total_tokens=final.preserve_tokens)
        policy.admitted()
        budget.record_model_call(input_tokens=1000, output_tokens=100, reservation=reservation)
        self.assertEqual(budget.snapshot().used_model_calls, 19)
        self.assertTrue(policy.exhausted)

    def test_consumed_handoff_never_reopens_even_if_a_snapshot_has_more_space(self):
        policy, budget, registry, last, _estimated = replay(True)
        policy.request(budget.snapshot(), estimated_input=90000,
                       now=last["relativeStartSeconds"])
        policy.tool_view(registry)
        policy.admitted()
        self.assertTrue(policy.exhausted)
        larger = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
        decision = policy.request(larger.snapshot(), estimated_input=1, now=500)
        self.assertTrue(decision.handoff_only)
        self.assertEqual({t.name for t in policy.tool_view(registry).list()}, {"executor_submit"})

    def test_passing_feedback_retires_the_extra_repair(self):
        policy, budget, registry, last, estimated = replay()
        policy.observe_verification(failed=False)
        final = policy.request(budget.snapshot(), estimated_input=estimated,
                               now=last["relativeStartSeconds"])
        self.assertTrue(final.handoff_only)
        self.assertEqual(policy.tool_view(registry).list(), [])


if __name__ == "__main__":
    unittest.main()
