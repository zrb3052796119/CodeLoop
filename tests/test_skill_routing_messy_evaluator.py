"""Quality contract for irregular, independently authored routing prompts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.skill_routing_messy_evaluator import evaluate_messy_skill_routing

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "agent_quality"
    / "skill-routing-messy.json"
)
_CRITICAL_CASES = frozenset(
    {
        "zh-typo-explicit-skill",
        "zh-negation-two-denials-security",
        "zh-quote-command-source",
        "en-neg-no-code-audit",
        "en-quote-log-deadlock",
        "en-code-fence-explain-no-execute",
        "en-context-unsafe-delete",
        "en-explicit-unknown-typo",
        "en-backtick-skill-sentence",
    }
)


@pytest.fixture(scope="module")
def report() -> dict[str, object]:
    return evaluate_messy_skill_routing(_FIXTURE)


def test_messy_routing_report_is_deterministic_offline_and_real_catalog(
    report: dict[str, object],
) -> None:
    repeated = evaluate_messy_skill_routing(_FIXTURE)

    assert repeated == report
    assert report["evaluator"] == "skill-routing-messy"
    assert report["mode"] == "offline-real-catalog"
    assert report["remoteCallCount"] == 0
    assert report["caseCount"] >= 74
    assert len(report["fixtureSha256"]) == 64
    assert len(report["catalogSha256"]) == 64


def test_messy_routing_quality_floor_and_safety_cases(
    report: dict[str, object],
) -> None:
    """Freeze promotion gates before repairing against this challenge set."""

    assert report["operationExactRate"] >= 0.85
    assert report["casePassRate"] >= 0.70
    assert report["outcomeExactRate"] >= 0.90
    assert report["forbiddenFinalRate"] == 1.0
    assert report["pairExactRate"] >= 0.70

    results = {
        row["id"]: row
        for row in report["caseResults"]
        if row["id"] in _CRITICAL_CASES
    }
    assert set(results) == _CRITICAL_CASES
    assert not {
        case_id
        for case_id, row in results.items()
        if not row["passed"]
    }


def test_messy_fixture_coverage_cannot_be_silently_shrunk(tmp_path: Path) -> None:
    document = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    document["cases"] = document["cases"][:-1]
    path = tmp_path / "skill-routing-messy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    catalog = _FIXTURE.parent / document["catalogFixture"]
    (tmp_path / catalog.name).write_bytes(catalog.read_bytes())

    document["coverageMinimums"]["caseCount"] = len(document["cases"]) + 1
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="caseCount"):
        evaluate_messy_skill_routing(path)


def test_messy_fixture_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    document = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    document["cases"][1]["id"] = document["cases"][0]["id"]
    path = tmp_path / "skill-routing-messy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    catalog = _FIXTURE.parent / document["catalogFixture"]
    (tmp_path / catalog.name).write_bytes(catalog.read_bytes())

    with pytest.raises(ValueError, match="case identity"):
        evaluate_messy_skill_routing(path)
