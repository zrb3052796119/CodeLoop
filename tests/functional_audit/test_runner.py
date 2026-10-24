from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_functional_audit.py"
REQUIRED_CAPABILITY_FIELDS = {
    "id",
    "category",
    "name",
    "declared",
    "registered",
    "reachableFrom",
    "deterministicTest",
    "installedWheelTest",
    "liveTest",
    "safety",
    "truthfulness",
    "status",
    "evidence",
    "issues",
}


# Freeze identities, so additions/removals produce an actionable set diff.
EXPECTED_REGISTERED_TOOLS = {
    "ask_user",
    "base64_decode",
    "base64_encode",
    "batch_copy",
    "batch_delete",
    "batch_move",
    "browse_skill_directory",
    "code_review",
    "csv_create",
    "csv_parse",
    "current_time",
    "diff_viewer",
    "edit_file",
    "file_line_count",
    "file_tree",
    "find_references",
    "find_symbols",
    "get_ast_info",
    "git",
    "grep_files",
    "gzip_compress",
    "gzip_decompress",
    "hash",
    "hmac",
    "http_request",
    "inspect_image",
    "json_format",
    "json_parse",
    "line_count",
    "list_files",
    "load_skill",
    "patch_file",
    "propose_skill",
    "random_string",
    "read_file",
    "regex_replace",
    "regex_test",
    "run_command",
    "search_skills",
    "subagent_note_list",
    "subagent_note_read",
    "subagent_note_write",
    "tar_create",
    "tar_extract",
    "task",
    "test_runner",
    "text_dedupe",
    "text_join",
    "text_sort",
    "timestamp_convert",
    "todo_write",
    "url_decode",
    "url_encode",
    "uuid_generate",
    "web_fetch",
    "web_search",
    "write_file",
    "zip_create",
    "zip_extract",
}


def _run_audit(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    home = tmp_path / "launcher-home"
    home.mkdir(parents=True)
    output = tmp_path / "audit.json"
    env = {
        **os.environ,
        "HOME": str(home),
        "MINI_CODE_DIR": str(home / ".mini-code"),
        "PYTHONPATH": str(ROOT),
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--output",
            str(output),
            "--timeout",
            "8",
            *args,
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert output.exists(), completed.stderr
    return completed, json.loads(output.read_text(encoding="utf-8"))


def test_default_audit_is_offline_isolated_and_exhaustive(tmp_path: Path) -> None:
    completed, payload = _run_audit(tmp_path)

    assert completed.returncode == 1
    assert payload["schemaVersion"] == 1
    assert payload["audit"]["liveNetwork"] is False
    assert payload["audit"]["isolation"]["home"] == "<isolated-home>"
    assert payload["audit"]["isolation"]["workspace"] == "<isolated-workspace>"

    capabilities = payload["capabilities"]
    capability_ids = [capability["id"] for capability in capabilities]
    assert len(capability_ids) == len(set(capability_ids))
    tool_names = {
        capability["name"]
        for capability in capabilities
        if capability["category"] == "tool" and capability["registered"]
    }
    assert tool_names == EXPECTED_REGISTERED_TOOLS
    assert payload["summary"]["registeredToolCount"] == len(tool_names)
    assert all(REQUIRED_CAPABILITY_FIELDS <= capability.keys() for capability in capabilities)
    assert all(
        capability["liveTest"] != "pass"
        for capability in capabilities
        if capability["name"] in {"web_search", "web_fetch", "http_request"}
    )

    encoded = json.dumps(payload, ensure_ascii=False)
    assert str(home := tmp_path / "launcher-home") not in encoded
    assert str(tmp_path) not in encoded
    assert "ANTHROPIC_API_KEY" not in encoded
    assert not (home / ".mini-code").exists()


def test_audit_closes_web_and_archive_findings(
    tmp_path: Path,
) -> None:
    _, payload = _run_audit(tmp_path)

    issues = {issue["id"]: issue for issue in payload["issues"]}
    assert "SEC-002" not in issues
    assert "WEB-001" not in issues
    assert "WEB-002" not in issues
    assert "SEC-001" not in issues
    assert "SEC-003" not in issues
    assert "SEC-004" not in issues
    # TOOL-001 (missing file reported as an empty file) and SEC-005 (tool
    # crashes leaking absolute paths/traceback) are fixed; both verdicts are
    # now driven by live probes rather than static issue entries, so they must
    # no longer appear as findings.
    assert "TOOL-001" not in issues
    assert "SEC-005" not in issues
    assert all(issue["recommendedBatch"].startswith("Reliability 1B-") for issue in issues.values())
    http_request = next(
        item
        for item in payload["capabilities"]
        if item["id"] == "tool.http_request"
    )
    assert http_request["status"] == "pass"
    assert http_request["safety"] == "pass"
    assert http_request["issues"] == []
    assert "tests/test_bounded_resolver.py" in http_request["evidence"]
    web_fetch = next(
        item
        for item in payload["capabilities"]
        if item["id"] == "tool.web_fetch"
    )
    assert web_fetch["deterministicTest"] == "pass"
    assert web_fetch["safety"] == "pass"
    assert web_fetch["truthfulness"] == "pass"
    assert web_fetch["status"] == "pass"
    assert web_fetch["issues"] == []
    assert "tests/test_web_fetch_safety.py" in web_fetch["evidence"]
    web_search = next(
        item
        for item in payload["capabilities"]
        if item["id"] == "tool.web_search"
    )
    assert web_search["deterministicTest"] == "pass"
    assert web_search["safety"] == "pass"
    assert web_search["truthfulness"] == "pass"
    assert web_search["status"] == "pass"
    assert web_search["issues"] == []
    assert "tests/test_web_search.py" in web_search["evidence"]
    assert "tests/test_search_providers.py" in web_search["evidence"]
    # Keep an independent inventory check for non-tool surfaces as well as
    # validating that the reported total agrees with the emitted entries.
    assert Counter(item["category"] for item in payload["capabilities"]) == {
        "agent": 23,
        "dashboard": 23,
        "entrypoint": 65,
        "mcp": 3,
        "persistence": 13,
        "security": 2,
        "tool": 78,
    }
    assert payload["summary"]["capabilityCount"] == len(payload["capabilities"])
    # SEC-002 and SEC-004 are closed by containment and bounded extraction
    # probes/tests; the remaining findings cover unrelated tool metadata/schema.
    assert payload["summary"]["issueCount"] == 2
    assert payload["deterministicProbes"]["memory"] == {
        "ordinaryFactPersisted": True,
        "singleErrorPatternSuppressed": True,
        # A network failure retried inside one trace clears the recurrence
        # escape hatch, so only the environment-scope rule stops it.
        "environmentErrorSuppressed": True,
        # The inverse: fail -> change a file -> the same command passes must
        # reach `verified_solution`. No runtime event reports this loop, so it
        # is derived from the trace; without that, the signal is unreachable.
        "verifiedRecoveryPersisted": True,
    }
    assert payload["deterministicProbes"]["web"] == {
        "normalParser": True,
        "explicitEmptyRecognized": True,
        "changedMarkupRecognized": True,
        "challengeRecognized": True,
        "statusTaxonomy": {
            "403": "forbidden",
            "404": "forbidden",
            "429": "rate_limited",
            "503": "server_error",
        },
        "fallbackOnce": True,
        "privateDestinationBlocked": True,
        "mixedDnsBlocked": True,
        "unsafeTargetTransportCalls": 0,
        "pinnedTransport": True,
        "oversizedResponseBlocked": True,
        "maxReadBytes": 65_536,
        "contentFreeErrors": True,
    }
    security = payload["deterministicProbes"]["security"]
    assert security["archiveWorkspaceEscape"] == {
        "gzip_compress": True,
        "tar_create": True,
        "zip_create": True,
    }
    assert security["httpMutationWithoutPermission"] is False
    assert security["httpMutationFixtureStatus"].startswith(
        "blocked:"
    )
    assert security["boundedDnsResolver"] == {
        "workerLimit": 4,
        "queueLimit": 8,
        "outstandingLimit": 12,
        "activeCount": 0,
        "queuedCount": 0,
        "accepting": True,
        "closed": False,
        "saturationError": "resolver_busy",
        "workersBlockProcessExit": False,
    }


def test_category_filter_keeps_discovery_and_requested_category(tmp_path: Path) -> None:
    completed, payload = _run_audit(tmp_path, "--category", "web")

    # The web category's only remaining finding was SEC-005 (tool crashes
    # leaking absolute paths), now fixed — so this slice exits clean.
    assert completed.returncode == 0
    assert payload["audit"]["categories"] == ["web"]
    assert {capability["category"] for capability in payload["capabilities"]} <= {
        "tool",
        "mcp",
        "security",
    }
    assert {"tool.web_search", "tool.web_fetch", "tool.http_request"} <= {
        capability["id"] for capability in payload["capabilities"]
    }


def test_output_is_reproducible_except_for_explicit_run_metadata(tmp_path: Path) -> None:
    _, first = _run_audit(tmp_path / "first")
    _, second = _run_audit(tmp_path / "second")

    for payload in (first, second):
        payload["audit"].pop("generatedAt", None)
        payload["audit"].pop("durationMs", None)
    assert first == second
