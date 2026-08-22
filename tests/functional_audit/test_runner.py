from __future__ import annotations

import json
import os
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
    assert payload["summary"]["registeredToolCount"] == 56

    capabilities = payload["capabilities"]
    capability_ids = [capability["id"] for capability in capabilities]
    assert len(capability_ids) == len(set(capability_ids))
    tool_names = {
        capability["name"]
        for capability in capabilities
        if capability["category"] == "tool" and capability["registered"]
    }
    assert {
        "web_search",
        "web_fetch",
        "http_request",
        "read_file",
        "run_command",
        "gzip_compress",
        "zip_extract",
    } <= tool_names
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


def test_audit_closes_web_findings_but_retains_archive_remainder(
    tmp_path: Path,
) -> None:
    _, payload = _run_audit(tmp_path)

    issues = {issue["id"]: issue for issue in payload["issues"]}
    assert payload["issues"][0]["id"] == "SEC-002"
    assert "WEB-001" not in issues
    assert "WEB-002" not in issues
    assert "SEC-001" not in issues
    assert issues["SEC-002"]["severity"] == "P0"
    assert "SEC-003" not in issues
    assert issues["SEC-004"]["capabilityId"] == "tool.gzip_decompress"
    assert issues["SEC-004"]["recommendedBatch"].startswith("Reliability 1B-2")
    assert issues["SEC-004"]["evidence"] == [
        "minicode/tools/archive_utils.py",
    ]
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
    assert payload["summary"]["capabilityCount"] == 189
    # 7 -> 5: TOOL-001 and SEC-005 are fixed and probe-verified.
    assert payload["summary"]["issueCount"] == 4
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
