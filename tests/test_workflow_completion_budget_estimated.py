"""Local budget accounting on the durable repair fixture, not model capability.

Reuse the proven fixture unchanged and instrument only its scripted adapters.
Every response is priced from the actual messages, exposed tool schemas, and
response payload, and explicitly labelled estimated rather than provider usage.
"""

import json
from collections import Counter

import test_workflow_completion_coverage as coverage

from minicode.agent_budget import AgentTurnBudget
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.run_journal import RunJournal
from minicode.types import ModelUsage


def test_durable_repair_accounts_actual_inputs_and_leaves_parent_budget(tmp_path, monkeypatch):
    calls, ledgers, settlements, receipts = [], [], [], []

    def create_budget(**limits):
        budget = AgentTurnBudget(**limits)
        ledgers.append(budget)
        return budget

    monkeypatch.setattr(coverage, "AgentTurnBudget", create_budget)
    real_task_run = coverage.task_module.task_tool.run

    def capture_receipt(*args, **kwargs):
        result = real_task_run(*args, **kwargs)
        receipts.append(result)
        return result

    monkeypatch.setattr(coverage.task_module.task_tool, "run", capture_receipt)

    class MeteredFixturePatch:
        """Add metering at the fixture's two existing adapter/worker seams."""

        def setattr(self, *args):
            if len(args) == 3 and args[0] is coverage.subagent_model_routing:
                target, name, factory = args

                def metered_factory(runtime, agent_type, *, tools):
                    adapter = factory(runtime, agent_type, tools=tools)
                    scripted_next = adapter.next
                    role = "executor" if agent_type == "general" else "reviewer"

                    def next_step(messages, tools_override=None, **options):
                        exposed = tools_override if tools_override is not None else tools
                        schemas = [] if options.get("disable_tools") else [{
                            "type": "function", "function": {
                                "name": tool.name, "description": tool.description,
                                "parameters": tool.input_schema,
                            },
                        } for tool in exposed.list()]
                        message_tokens = sum(estimate_message_tokens(message) for message in messages)
                        schema_tokens = estimate_tokens(json.dumps(schemas, ensure_ascii=False))
                        step = scripted_next(messages, tools_override=tools_override, **options)
                        output_tokens = estimate_tokens(json.dumps({
                            "type": step.type, "content": step.content, "calls": step.calls,
                        }, ensure_ascii=False))
                        step.usage = ModelUsage(
                            input_tokens=message_tokens + schema_tokens,
                            output_tokens=output_tokens, source="estimated",
                        )
                        calls.append({
                            "role": role, "adapter": id(adapter),
                            "input": step.usage.input_tokens, "output": output_tokens,
                            "messages": message_tokens, "schemas": schema_tokens,
                            "toolCount": len(schemas), "source": step.usage.source,
                        })
                        return step

                    adapter.next = next_step
                    return adapter

                return monkeypatch.setattr(target, name, metered_factory)
            if len(args) == 3 and args[0] is coverage.task_module and args[1] == "_run":
                target, name, worker = args

                def metered_worker(input_data, context):
                    ledger = context._agent_budget
                    while hasattr(ledger, "_budget"):
                        ledger = ledger._budget
                    assert ledger is ledgers[0]
                    before, first = ledger.snapshot(), len(calls)
                    result = worker(input_data, context)
                    after = ledger.snapshot()
                    observed = calls[first:]
                    assert after.used_model_calls - before.used_model_calls == len(observed)
                    assert after.used_input_tokens - before.used_input_tokens == sum(c["input"] for c in observed)
                    assert after.used_output_tokens - before.used_output_tokens == sum(c["output"] for c in observed)
                    settlements.append({
                        "role": context._logical_agent_role, "calls": len(observed),
                        "parentTokenFloor": context._logical_agent_preserve_total_tokens,
                    })
                    return result

                return monkeypatch.setattr(target, name, metered_worker)
            return monkeypatch.setattr(*args)

    # Retains every original assertion: failed missing test, same Executor and
    # history, actual pytest, fresh review/verification, commit-ready and reload.
    coverage.test_green_partial_candidate_is_reviewed_before_commit_and_resumes_same_executor(
        tmp_path, MeteredFixturePatch(), repair=True, durable=True,
    )
    assert len(ledgers) == 1 and len(receipts) == 1 and receipts[0].ok
    snapshot = ledgers[0].snapshot()
    assert snapshot.limit_model_calls == 40 and snapshot.limit_total_tokens == 500000
    assert snapshot.used_model_calls == len(calls) == 11
    assert Counter(c["role"] for c in calls) == {"executor": 5, "reviewer": 6}
    assert len({c["adapter"] for c in calls if c["role"] == "executor"}) == 1
    assert [s["role"] for s in settlements] == ["executor", "reviewer", "executor", "reviewer"]
    assert all(c["input"] == c["messages"] + c["schemas"] and c["schemas"] > 0 for c in calls)
    assert all(c["source"] == "estimated" for c in calls)
    assert snapshot.used_input_tokens == sum(c["input"] for c in calls)
    assert snapshot.used_output_tokens == sum(c["output"] for c in calls)
    assert snapshot.used_total_tokens == snapshot.used_input_tokens + snapshot.used_output_tokens
    assert snapshot.reserved_total_tokens == 0

    journal = RunJournal(tmp_path, data_dir=tmp_path.parent / f"{tmp_path.name}-journal")
    run = journal.list_runs().items[0]
    usage_events = [event.payload["usage"]
                    for child in journal.list_subagent_runs(run.id)
                    for event in journal.list_subagent_events(run.id, child.subagent_id)
                    if event.type == "model.completed"]
    assert len(usage_events) == len(calls)
    assert all(usage["source"] == "estimated" for usage in usage_events)
    assert sum(usage["inputTokens"] for usage in usage_events) == snapshot.used_input_tokens
    assert sum(usage["outputTokens"] for usage in usage_events) == snapshot.used_output_tokens

    parent_messages = [
        {"role": "user", "content": coverage.TASK},
        {"role": "tool_result", "content": receipts[0].output},
        {"role": "user", "content": "Summarize the delivered changes and actual test results."},
    ]
    parent_required = sum(estimate_message_tokens(message) for message in parent_messages) + 2048
    remaining = snapshot.limit_total_tokens - snapshot.used_total_tokens
    assert snapshot.limit_model_calls - snapshot.used_model_calls >= 1
    assert remaining >= max(parent_required, max(s["parentTokenFloor"] for s in settlements))
    print(json.dumps({
        "kind": "local-estimated-budget-regression", "source": "estimated",
        "modelCalls": snapshot.used_model_calls, "inputTokens": snapshot.used_input_tokens,
        "outputTokens": snapshot.used_output_tokens, "remainingTokens": remaining,
        "parentClosingInputAndOutputEstimate": parent_required,
        "roleCalls": dict(Counter(c["role"] for c in calls)),
        "durableSameExecutorRepairCommitted": True,
    }, sort_keys=True))
