#!/usr/bin/env python3
"""Private deterministic acceptance; inspect actual adapter bytes, no HTTP."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest.mock import patch


def evaluate(workspace: Path) -> dict:
    sys.path.insert(0, str(workspace.resolve()))
    from minicode.agent_budget import AgentTurnBudget
    from minicode.context_compactor import LLMSummaryGenerator
    from minicode.context_manager import estimate_messages_tokens, estimate_tokens
    from minicode.openai_adapter import OpenAIModelAdapter
    from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult

    registry = ToolRegistry([ToolDefinition(
        name="lookup_entry", description="Read one authorized knowledge entry. " * 1000,
        input_schema={"type": "object", "properties": {"key": {"type": "string"}},
                      "required": ["key"]},
        validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output="entry"),
    )])
    transcript = [
        {"role": "user", "content": "Keep the audit identifiers and the remaining task."},
        {"role": "assistant", "content": "Audit identifier A19 is verified; the remaining task is the release note."},
    ]
    summary_text = "Audit identifier A19 remains verified. The remaining authorized task is to finish the release note."

    class Response:
        status = 200

        def __init__(self, body):
            self.body = json.dumps(body).encode()
            self.offset = 0

        def read1(self, amount=-1):
            end = len(self.body) if amount < 0 else self.offset + amount
            chunk = self.body[self.offset:end]
            self.offset += len(chunk)
            return chunk

        def close(self):
            pass

    outcomes = []
    errors = []

    def scenario(label, selected, limit, expected_calls):
        budget = AgentTurnBudget(max_total_tokens=limit, max_model_calls=3)
        model = OpenAIModelAdapter({
            "model": "local-oracle", "openaiBaseUrl": "https://provider.invalid/v1",
            "openaiApiKey": "synthetic", "modelMaxRetries": 0,
            "maxOutputTokens": 128,
        }, selected)
        sent = []

        def forbidden(*_args, **_kwargs):
            raise AssertionError("oracle attempted external transport")

        def capture(request, *, timeout):
            assert timeout > 0
            body = json.loads(request.data.decode())
            tools = body.get("tools", [])
            schema_tokens = estimate_tokens(json.dumps(tools)) if tools else 0
            request_tokens = estimate_messages_tokens(body["messages"]) + schema_tokens
            sent.append({"input": request_tokens, "schemas": schema_tokens,
                         "reserved": budget.snapshot().reserved_total_tokens,
                         "toolNames": [item["function"]["name"] for item in tools]})
            return Response({"choices": [{"message": {"content": summary_text}, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": request_tokens, "completion_tokens": 23}})

        with patch("urllib.request.urlopen", forbidden), patch("socket.create_connection", forbidden), \
                patch("minicode.openai_adapter.open_verified_url", capture):
            summary = LLMSummaryGenerator(model, agent_budget=budget).summarize(transcript)
        snapshot = budget.snapshot()
        outcome = {"label": label, "limit": limit, "requests": sent,
                   "usedTokens": snapshot.used_total_tokens,
                   "usedCalls": snapshot.used_model_calls,
                   "reservedTokens": snapshot.reserved_total_tokens,
                   "summaryAvailable": summary is not None}
        outcomes.append(outcome)
        if len(sent) != expected_calls:
            errors.append(f"{label}: expected {expected_calls} requests, got {len(sent)}")
        if snapshot.reserved_total_tokens != 0:
            errors.append(f"{label}: reservation leaked")
        if expected_calls == 0:
            if summary is not None or snapshot.used_total_tokens != 0:
                errors.append(f"{label}: refused request returned summary or charged tokens")
            return
        if len(sent) != 1:
            return
        if summary is None or "A19" not in summary:
            errors.append(f"{label}: ordinary summary lost")
        expected_names = [tool.name for tool in selected.list()] if selected is not None else []
        if sent[0]["toolNames"] != expected_names:
            errors.append(f"{label}: model request catalog silently changed")
        if limit is not None and sent[0]["reserved"] < sent[0]["input"]:
            errors.append(f"{label}: admitted actual request exceeds reserved input")
        if snapshot.used_total_tokens != sent[0]["input"] + 23 or snapshot.used_model_calls != 1:
            errors.append(f"{label}: actual usage settlement changed")

    pricing_model = OpenAIModelAdapter({"model": "local-oracle"}, registry)
    schema_cost = estimate_tokens(json.dumps(pricing_model._get_serialized_tools()))
    assert schema_cost > 4000, "oracle catalog must dominate the short summary prompt"
    scenario("schema_does_not_fit", registry, schema_cost - 1, 0)
    scenario("schema_fits", registry, schema_cost + 4096, 1)
    scenario("unlimited_catalog", registry, None, 1)
    scenario("no_catalog_fits", None, 4096, 1)
    scenario("messages_do_not_fit", None, 1, 0)
    return {"ok": not errors, "schemaTokens": schema_cost, "checks": outcomes, "errors": errors}


if __name__ == "__main__":
    result = evaluate(Path(sys.argv[1]))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 1)
