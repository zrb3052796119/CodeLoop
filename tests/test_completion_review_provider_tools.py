"""The real provider body must expose the same tools the nested loop executes."""

import io
import json

import pytest

from minicode.openai_adapter import OpenAIModelAdapter
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from test_implementation_completion_review import PROBE, SOURCE, _fixture, _rows, _coverage_approval


@pytest.mark.parametrize("inject_parent_tools,force_parent_choice", [(False, False), (True, False), (True, True)])
def test_shared_openai_adapter_uses_nested_review_registry_with_phases_disabled(
        tmp_path, monkeypatch, inject_parent_tools, force_parent_choice):
    review, _assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    parent_tools = ToolRegistry([read_file_tool, edit_file_tool])
    runtime = {"model": "gpt-test", "openaiBaseUrl": "https://openai.invalid/v1",
               "openaiApiKey": "test", "modelMaxRetries": 0, "maxOutputTokens": 4096}
    parent_names = {"read_file", "edit_file"}
    if inject_parent_tools:
        runtime["_openrouter_params"] = {"tools": [{"type": "function", "function": {
            "name": "parent_runtime_write", "description": "Parent configured write capability",
            "parameters": {"type": "object", "properties": {}}}}]}
        parent_names = {"parent_runtime_write"}
    if force_parent_choice:
        runtime["_openrouter_params"]["tool_choice"] = {
            "type": "function", "function": {"name": "parent_runtime_write"}}
    adapter = OpenAIModelAdapter(runtime, parent_tools)
    # Reuse the existing adapter and prime its original tool cache, just as a
    # completed implementation would. The nested review must not mutate it.
    assert {tool["function"]["name"] for tool in adapter.request_tool_schemas()} == parent_names
    expected = {"read_file", "review_test", "review_probe_read", "completion_review_submit"}
    audit_expected = expected - {"completion_review_submit"} | {
        "completion_coverage_audit_submit", "review_evidence_read"}
    requests = []

    class Response(io.BytesIO):
        status = 200

    def transport(request, *, timeout):
        del timeout
        body = json.loads(request.data.decode())
        requests.append(body)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if names not in (expected, audit_expected):
            # A real provider can only select its advertised capabilities.
            # End this local attempt instead of fabricating an unadvertised
            # native call, which would conceal the production wiring defect.
            message = {"role": "assistant", "content": "Required review tools are unavailable."}
            finish = "stop"
        else:
            packet = {"requirements": list(review.requirements)}
            if names == audit_expected:
                packet = next(json.loads(m["content"]) for m in body["messages"]
                              if m.get("role") == "user" and '"evidenceIndex"' in m.get("content", ""))
                reads = [m for m in body["messages"] if m.get("role") == "tool"]
                if not reads:
                    name, args = "review_probe_read", {"probeId": packet["evidenceIndex"][0]["probeId"],
                                                       "receiptView": "current", "limit": 16000}
                else:
                    source = json.loads(json.loads(reads[-1]["content"])["text"])["source"]
                    name, args = "completion_coverage_audit_submit", _coverage_approval(packet, {source["probeId"]: source})
            elif len(requests) == 1:
                name, args = "review_test", {"purpose": "acceptance", "criterionIds": [u["id"] for u in review.requirements],
                    "scenario": "Both public quantity operations work", "code": PROBE}
            else:
                assert len(requests) == 2
                receipt = json.loads(next(message["content"] for message in reversed(body["messages"])
                                         if message.get("role") == "tool"))
                name, args = "completion_review_submit", {"status": "approved", "findings": [],
                    "requirements": _rows(packet, ids=[receipt["probeId"]])}
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"provider-call-{len(requests)}", "type": "function", "function": {
                    "name": name, "arguments": json.dumps(args)}}]}
            finish = "tool_calls"
        return Response(json.dumps({"choices": [{"index": 0, "message": message,
            "finish_reason": finish}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}}).encode())

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    context = ToolContext(cwd=str(tmp_path), _runtime=runtime)
    try:
        result = review.assess(model=adapter, tools=parent_tools, context=context,
            agent_budget=None, event_sink=None, cancellation_token=None,
            deadline_monotonic=None, checks_receipt=capability.ensure_completion_checks(context))
        assert requests
        assert all({tool["function"]["name"] for tool in body.get("tools", [])} in (expected, audit_expected)
                   for body in requests), "Provider schema must use the actual nested registry, even without phases."
        for body in requests:
            functions = {tool["function"]["name"]: tool["function"] for tool in body["tools"]}
            assert functions["read_file"]["parameters"] == read_file_tool.input_schema
            probe_schema = functions["review_test"]["parameters"]
            assert "purpose" in probe_schema["required"]
            assert set(probe_schema["properties"]["purpose"]["enum"]) == {"diagnostic", "acceptance"}
            submit_name = ("completion_review_submit" if "completion_review_submit" in functions
                           else "completion_coverage_audit_submit")
            submit_schema = functions[submit_name]["parameters"]
            if submit_name == "completion_coverage_audit_submit":
                assert {frozenset(branch["required"]) for branch in submit_schema["anyOf"]} == {
                    frozenset({"status", "requirements", "findings"}),
                    frozenset({"draftId", "updates"}),
                }
                assert submit_schema["properties"]["draftId"]["type"] == "string"
                assert submit_schema["properties"]["updates"]["type"] == "array"
            else:
                assert set(submit_schema["required"]) == {"status", "requirements", "findings"}
            assert "tool_choice" not in body
        assert result["status"] == "approved"
        assert len(requests) == 4
        assert adapter.tools is parent_tools
        assert {tool["function"]["name"] for tool in adapter.request_tool_schemas()} == parent_names
        if force_parent_choice:
            assert runtime["_openrouter_params"]["tool_choice"]["function"]["name"] == "parent_runtime_write"
        assert (tmp_path / "stock.py").read_text() == SOURCE
    finally:
        capability.close()
