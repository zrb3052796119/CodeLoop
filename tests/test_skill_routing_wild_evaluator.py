"""Reproducibility checks for the frozen wild-language routing holdout."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from scripts.skill_routing_wild_evaluator import evaluate_wild_skill_routing

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "agent_quality"
    / "skill-routing-wild-holdout.json"
)
_CATALOG = _FIXTURE.parent / "intent-skill-production-regressions.json"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_EVALUATOR = _REPOSITORY_ROOT / "scripts" / "skill_routing_wild_evaluator.py"
_BASELINE = (
    _REPOSITORY_ROOT
    / "artifacts"
    / "skill-routing-wild-holdout-baseline-v1.json"
)
_CASE_KEYS = {
    "id",
    "category",
    "prompt",
    "operations",
    "top",
    "allowed",
    "forbidden",
    "outcome",
    "context",
    "context_used",
    "oracle",
}


@pytest.fixture(scope="module")
def reports() -> tuple[dict[str, object], dict[str, object]]:
    observed = []
    for hash_seed in ("7", "41"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        completed = subprocess.run(
            [
                sys.executable,
                str(_EVALUATOR),
                str(_FIXTURE),
                "--catalog",
                str(_CATALOG),
            ],
            cwd=_REPOSITORY_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        observed.append(json.loads(completed.stdout))
    return observed[0], observed[1]


def test_wild_holdout_fixture_has_the_frozen_schema() -> None:
    document = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    assert set(document) == {"schemaVersion", "suiteId", "cases"}
    assert document["schemaVersion"] == 1
    assert document["suiteId"] == "wild-routing-110-v1"
    assert len(document["cases"]) == 110
    assert all(set(case) == _CASE_KEYS for case in document["cases"])
    assert len({case["id"] for case in document["cases"]}) == 110
    assert Counter(case["oracle"] for case in document["cases"]) == {
        "solid": 85,
        "ambiguous": 25,
    }
    assert Counter(case["outcome"] for case in document["cases"]) == {
        "route": 91,
        "no_skill": 15,
        "clarify": 4,
    }
    assert sum(case["context"] is not None for case in document["cases"]) == 12
    assert sum(
        any(operation.startswith("negated:") for operation in case["operations"])
        for case in document["cases"]
    ) == 46
    assert sum(bool(case["forbidden"]) for case in document["cases"]) == 24
    assert {
        case["id"]
        for case in document["cases"]
        if len(case["operations"]) != len(set(case["operations"]))
    } == {"multi-06", "multi-07", "multi-11", "multi-12"}
    assert all(
        not (set(case["allowed"]) & set(case["forbidden"]))
        for case in document["cases"]
    )


def test_wild_holdout_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    document = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    document["cases"][1]["id"] = document["cases"][0]["id"]
    fixture = tmp_path / "duplicate-wild-holdout.json"
    fixture.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="case identity"):
        evaluate_wild_skill_routing(fixture, catalog_path=_CATALOG)


def test_wild_holdout_rejects_boolean_schema_version(tmp_path: Path) -> None:
    document = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    document["schemaVersion"] = True
    fixture = tmp_path / "boolean-schema-wild-holdout.json"
    fixture.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="document schema"):
        evaluate_wild_skill_routing(fixture, catalog_path=_CATALOG)


def test_wild_holdout_makes_no_network_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_connection(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("wild evaluator attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", reject_connection)

    report = evaluate_wild_skill_routing(_FIXTURE, catalog_path=_CATALOG)

    assert report["remoteCallCount"] == 0


def test_wild_holdout_is_deterministic_and_offline(
    reports: tuple[dict[str, object], dict[str, object]],
) -> None:
    first, second = reports

    assert second == first
    assert first["evaluator"] == "skill-routing-wild-holdout"
    assert first["mode"] == "offline-real-catalog-no-model"
    assert first["remoteCallCount"] == 0
    assert first["sourceStableDuringRun"] is True
    assert "not a promotion threshold" in first["measurementDefinitions"][
        "baselinePolicy"
    ]
    assert first["caseCount"] == 110
    assert first["solidCaseCount"] == 85
    assert first["ambiguousCaseCount"] == 25
    assert first["fixtureSha256"] == (
        "1022624a46db7abfacb933010bd36c4f11500bdf3969df74cf1e0c17944f8997"
    )
    assert first["caseSha256"] == (
        "dd5387bebd0c89089dc05665bde82d9b8123a530612b5eff0c7b6d2c617be5f3"
    )


def test_wild_holdout_stage_totals_are_derived_from_case_results(
    reports: tuple[dict[str, object], dict[str, object]],
) -> None:
    report = reports[0]
    rows = report["caseResults"]
    assert isinstance(rows, list)
    assert len(rows) == report["caseCount"]

    strict_stats = report["strictStageStats"]
    assert isinstance(strict_stats, dict)
    for stage, stats in strict_stats.items():
        passed = sum(bool(row["strictStagePass"][stage]) for row in rows)
        assert stats == {
            "passed": passed,
            "total": len(rows),
            "rate": round(passed / len(rows), 6),
        }

    lenient_stats = report["lenientStageStats"]
    assert isinstance(lenient_stats, dict)
    for stage in ("keyIntent", "outcome", "forbidden"):
        passed = sum(bool(row["lenientStagePass"][stage]) for row in rows)
        assert lenient_stats[stage] == {
            "passed": passed,
            "total": len(rows),
            "rate": round(passed / len(rows), 6),
        }

    strict_passes = sum(bool(row["strictPassed"]) for row in rows)
    lenient_passes = sum(bool(row["lenientPassed"]) for row in rows)
    assert report["strictPassCount"] == strict_passes
    assert report["strictPassRate"] == round(strict_passes / len(rows), 6)
    assert report["lenientPassCount"] == lenient_passes
    assert report["lenientPassRate"] == round(lenient_passes / len(rows), 6)
    assert report["failedCaseIds"] == [
        row["id"] for row in rows if not row["strictPassed"]
    ]

    negative_rows = [
        row
        for row in rows
        if any(
            operation.startswith("negated:")
            for operation in row["expectedOperations"]
        )
    ]
    recalled = sum(
        bool(row["lenientStagePass"]["negatedConstraints"])
        for row in negative_rows
    )
    exact = sum(bool(row["negatedConstraintsExact"]) for row in negative_rows)
    expected_operations = sum(
        sum(
            operation.startswith("negated:")
            for operation in row["expectedOperations"]
        )
        for row in negative_rows
    )
    recalled_operations = sum(
        sum(
            (Counter(row["expectedOperations"]) & Counter(row["actualOperations"]))[
                operation
            ]
            for operation in set(row["expectedOperations"])
            if operation.startswith("negated:")
        )
        for row in negative_rows
    )
    assert report["negativeConstraintStats"] == {
        "caseCount": len(negative_rows),
        "caseRecallCount": recalled,
        "caseRecallRate": round(recalled / len(negative_rows), 6),
        "caseExactCount": exact,
        "caseExactRate": round(exact / len(negative_rows), 6),
        "operationCount": expected_operations,
        "operationRecallCount": recalled_operations,
        "operationRecallRate": round(
            recalled_operations / expected_operations,
            6,
        ),
    }


def test_historical_wild_holdout_baseline_is_immutable_and_self_consistent() -> None:
    """Pin historical evidence without freezing today's production output."""

    baseline_bytes = _BASELINE.read_bytes()
    assert hashlib.sha256(baseline_bytes).hexdigest() == (
        "0b647d4294df24ffc6c297dda486fa67c1683b0f2869545b9177fa2fb755935e"
    )
    baseline = json.loads(baseline_bytes)
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    cases = fixture["cases"]
    failures = baseline["failures"]
    failures_by_id = {failure["id"]: failure for failure in failures}

    assert baseline["mode"] == "offline-real-catalog-no-model"
    assert baseline["remoteCallCount"] == 0
    assert baseline["sourceStableDuringRun"] is True
    assert baseline["productionSourceSha256"] == (
        "389fff9e7338bde309d61549f69b66a5bd2e6ac38b5beed31fe6bc6e60687d9d"
    )
    assert baseline["caseSha256"] == (
        "dd5387bebd0c89089dc05665bde82d9b8123a530612b5eff0c7b6d2c617be5f3"
    )
    assert baseline["catalogSha256"] == (
        "b661567b58ec7184e2a455e8f57bc0d5e5e418aa5bb9f9429761f9726b30750a"
    )
    assert baseline["caseCount"] == len(cases) == 110
    assert len(failures) == len(failures_by_id) == 98
    assert baseline["fullPassCount"] == len(cases) - len(failures) == 12
    assert baseline["fullPassRate"] == round(12 / 110, 6)

    for stage, rate in baseline["stageRates"].items():
        failed = sum(stage in failure["failedStages"] for failure in failures)
        assert rate == round((len(cases) - failed) / len(cases), 6)
    for stage in ("keyIntent", "outcome", "forbidden"):
        failed = sum(
            stage in failure["lenientFailedStages"] for failure in failures
        )
        assert baseline["lenientStageRates"][stage] == round(
            (len(cases) - failed) / len(cases), 6
        )
    lenient_failures = sum(bool(row["lenientFailedStages"]) for row in failures)
    assert baseline["lenientPassCount"] == len(cases) - lenient_failures == 24
    assert baseline["lenientPassRate"] == round(24 / 110, 6)

    for group_name, grouped_cases in (
        ("categoryStats", lambda case: case["category"]),
        ("oracleStats", lambda case: case["oracle"]),
    ):
        expected: dict[str, dict[str, int | float]] = {}
        for name in sorted({grouped_cases(case) for case in cases}):
            group = [case for case in cases if grouped_cases(case) == name]
            strict_passes = sum(case["id"] not in failures_by_id for case in group)
            lenient_passes = sum(
                case["id"] not in failures_by_id
                or not failures_by_id[case["id"]]["lenientFailedStages"]
                for case in group
            )
            expected[name] = {
                "count": len(group),
                "strictPass": strict_passes,
                "strictRate": round(strict_passes / len(group), 6),
                "lenientPass": lenient_passes,
                "lenientRate": round(lenient_passes / len(group), 6),
            }
        assert baseline[group_name] == expected

    assert Counter(failure["oracle"] for failure in failures) == {
        "solid": 74,
        "ambiguous": 24,
    }
