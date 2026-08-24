from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "artifacts" / "persistent-memory-large-study-v3"
ATTRIBUTION = STUDY / "auth-policy-attribution.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_auth_policy_attribution_joins_public_frozen_evidence() -> None:
    artifact = json.loads(ATTRIBUTION.read_text(encoding="utf-8"))

    assert artifact["schemaVersion"] == 1
    assert artifact["artifactType"] == "sanitized-memory-attribution-projection"
    assert artifact["scope"] == {
        "repository": "synthetic",
        "task": "read-only path recovery",
        "providerCalls": "remote-live",
        "modelIdentityFrozen": False,
    }
    assert artifact["sourceDigests"]["publicManifestSha256"] == _sha256(
        STUDY / "manifest.json"
    )
    assert artifact["sourceDigests"]["publicResultSha256"] == _sha256(
        STUDY / "full-results-initial.json"
    )

    result = json.loads(
        (STUDY / "full-results-initial.json").read_text(encoding="utf-8")
    )
    cases = {case["id"]: case for case in result["results"]}
    warm = cases[artifact["caseId"]]
    cold = cases["pmem-b1-auth-policy-cold"]
    assert warm["runId"] == artifact["reuse"]["runId"]
    assert artifact["learning"]["runId"] in warm["relatedRunIds"]
    assert cold["runId"] == artifact["matchedCold"]["runId"]
    assert {
        "source-failure",
        "source-read",
        "lesson-written",
        "lesson-injected",
        "marker-found",
    }.issubset(warm["passedOracleIds"])
    assert {"source-read", "marker-found"}.issubset(cold["passedOracleIds"])

    with (STUDY / "analysis-output" / "turn-level.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = {row["case_id"]: row for row in csv.DictReader(handle)}

    warm_row = rows[artifact["caseId"]]
    cold_row = rows["pmem-b1-auth-policy-cold"]
    assert warm_row["run_id"] == artifact["reuse"]["runId"]
    assert int(warm_row["tool_calls"]) == artifact["reuse"]["toolCalls"]
    assert int(warm_row["task_model_calls"]) == artifact["reuse"]["modelCalls"]
    assert int(warm_row["task_input_tokens"]) == artifact["reuse"]["inputTokens"]
    assert warm_row["first_tool"] == artifact["reuse"]["firstRepositoryAction"]
    assert int(cold_row["tool_calls"]) == artifact["matchedCold"]["toolCalls"]
    assert int(cold_row["task_model_calls"]) == artifact["matchedCold"]["modelCalls"]
    assert int(cold_row["task_input_tokens"]) == artifact["matchedCold"]["inputTokens"]
    assert cold_row["first_tool"] == artifact["matchedCold"]["firstRepositoryAction"]


def test_auth_policy_attribution_is_explicit_about_verification_and_privacy() -> None:
    artifact = json.loads(ATTRIBUTION.read_text(encoding="utf-8"))

    recovery = artifact["learning"]["recoveryEvidence"]
    assert recovery["kind"] == "tool_recovery"
    assert recovery["scope"] == "targeted"
    assert recovery["runtimeResult"] == "passed"
    assert recovery["independentVerifierInLearningRun"] is False
    assert recovery["externalExperimentOracleAfterRun"] is True
    assert artifact["learning"]["lesson"]["entryId"] in artifact["reuse"][
        "renderedEntryIds"
    ]
    assert artifact["privacy"]["rawEvidenceCommitted"] is False
