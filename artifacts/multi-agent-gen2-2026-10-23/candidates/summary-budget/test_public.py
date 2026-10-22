"""Existing summary API compatibility checks; local scripted adapter only."""
import unittest

from minicode.agent_budget import AgentTurnBudget
from minicode.context_compactor import LLMSummaryGenerator
from minicode.types import AgentStep, ModelUsage


class SummaryCompatibilityTests(unittest.TestCase):
    def test_summary_keeps_plain_adapter_compatibility(self):
        calls = []

        class Model:
            def next(self, messages):
                calls.append(messages)
                return AgentStep(type="assistant", content="The task is to preserve the deployment decision and complete the remaining documentation.",
                                 usage=ModelUsage(input_tokens=280, output_tokens=20, source="provider"))

        budget = AgentTurnBudget(max_total_tokens=None, max_model_calls=3)
        result = LLMSummaryGenerator(Model(), agent_budget=budget).summarize([
            {"role": "user", "content": "Preserve our deployment decision."},
            {"role": "assistant", "content": "Documentation is the remaining task."},
        ])
        self.assertEqual(len(calls), 1)
        self.assertIn("documentation", result)
        self.assertEqual(budget.snapshot().used_total_tokens, 300)
        self.assertEqual(budget.snapshot().reserved_total_tokens, 0)

    def test_exhausted_balance_uses_existing_fallback(self):
        calls = []

        class Model:
            def next(self, messages):
                calls.append(messages)
                raise AssertionError("A one-token balance cannot admit the summary prompt")

        budget = AgentTurnBudget(max_total_tokens=1, max_model_calls=3)
        result = LLMSummaryGenerator(Model(), agent_budget=budget).summarize([
            {"role": "user", "content": "Keep the existing task and verified facts."},
            {"role": "assistant", "content": "The prior check passed and the documentation remains."},
        ])
        self.assertIsNone(result)
        self.assertEqual(calls, [])
        self.assertEqual(budget.snapshot().used_total_tokens, 0)


if __name__ == "__main__":
    unittest.main()
