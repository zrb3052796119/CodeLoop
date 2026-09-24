#!/usr/bin/env python3
"""Build the preregistered paired context-compaction live manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "context-compaction-paired-v2" / "manifest.json"
)
SUITE_ID = "minicode-context-compaction-paired-2026-08-25-v2"
PILOT_SUITE_ID = "minicode-context-compaction-anchor-repair-pilot-2026-08-25-v2"
CANONICAL_WINDOW = 14_000
CONTROL_WINDOW = 128_000
REPLICATES = 3
RUNTIME_PROFILE_IDENTITY = {
    "adapterType": "openai_compatible",
    "endpointUrl": "https://api.deepseek.com/v1/chat/completions",
    "model": "deepseek-v4-pro",
    "provider": "custom",
}


def _contract_sha256(value: dict[str, str]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _oracle(oracle_id: str, kind: str, **values: object) -> dict[str, object]:
    return {"id": oracle_id, "kind": kind, **values}


def _initial_history(
    *,
    goal_marker: str,
    constraint_marker: str,
    rejected_marker: str,
    edge_requirement: str,
) -> list[dict[str, str]]:
    """Generate a long, synthetic discussion with separated protected state."""
    statements = {
        0: (
            f"The accepted delivery goal is {goal_marker}. The implementation "
            "must remain small and independently testable."
        ),
        7: f"A hidden edge requirement was confirmed: {edge_requirement}",
        11: (
            f"The non-negotiable compatibility constraint is {constraint_marker}. "
            "Existing public function names and positional arguments stay stable."
        ),
        17: (
            f"The team explicitly rejected an alternative under marker "
            f"{rejected_marker}. Do not revive that approach."
        ),
    }
    filler = (
        " This is generated planning context used only to create controlled "
        "history pressure; it records no user data and authorizes no extra work."
        * 11
    )
    history: list[dict[str, str]] = []
    for index in range(18):
        statement = statements.get(
            index,
            f"Generated design note {index} is background and not authoritative.",
        )
        history.append(
            {
                "role": "user",
                "content": f"Earlier project discussion: {statement}.{filler}",
            }
        )
        history.append(
            {
                "role": "assistant",
                "content": (
                    f"Recorded generated discussion item {index}; no repository "
                    f"changes were made.{filler}"
                ),
            }
        )
    return history


def _family_specs() -> list[dict[str, object]]:
    return [
        {
            "id": "port-normalization",
            "sourcePath": "src/ports.py",
            "edgeRequirement": "boolean values are invalid even though bool subclasses int",
            "files": {
                "src/__init__.py": "",
                "src/ports.py": (
                    "def normalize_port(value):\n"
                    "    \"\"\"Return a valid TCP port as int, otherwise None.\"\"\"\n"
                    "    return int(value)\n"
                ),
                "docs/contract.md": (
                    "# Port contract\n\n"
                    "`normalize_port(value)` keeps its public name and one positional "
                    "argument. Return an integer only for inclusive range 1..65535. "
                    "Return `None` for booleans, non-numeric values, zero, negatives, "
                    "and values above the range. Do not clamp invalid values.\n"
                ),
                "tests/test_contract.py": (
                    "import unittest\n\n"
                    "from src.ports import normalize_port\n\n\n"
                    "class PortTests(unittest.TestCase):\n"
                    "    def test_valid_boundaries_and_string(self):\n"
                    "        self.assertEqual(normalize_port(1), 1)\n"
                    "        self.assertEqual(normalize_port(65535), 65535)\n"
                    "        self.assertEqual(normalize_port(\"443\"), 443)\n\n"
                    "    def test_invalid_inputs_are_none(self):\n"
                    "        for value in (True, False, 0, -1, 65536, \"bad\", None):\n"
                    "            with self.subTest(value=value):\n"
                    "                self.assertIsNone(normalize_port(value))\n\n\n"
                    "if __name__ == \"__main__\":\n"
                    "    unittest.main()\n"
                ),
            },
        },
        {
            "id": "retry-delay",
            "sourcePath": "src/retry.py",
            "edgeRequirement": "attempt numbering is one-based, so attempt one equals base",
            "files": {
                "src/__init__.py": "",
                "src/retry.py": (
                    "def retry_delay(attempt, base=0.25, cap=4.0):\n"
                    "    \"\"\"Return a bounded one-based exponential retry delay.\"\"\"\n"
                    "    return base * (2 ** attempt)\n"
                ),
                "docs/contract.md": (
                    "# Retry contract\n\n"
                    "Keep `retry_delay(attempt, base=0.25, cap=4.0)` compatible. "
                    "Attempts are positive, one-based integers. Base and cap must be "
                    "positive real numbers, but booleans are invalid. Invalid input "
                    "returns `None`. Delay is `base * 2 ** (attempt - 1)`, capped at "
                    "`cap`. Do not add jitter.\n"
                ),
                "tests/test_contract.py": (
                    "import unittest\n\n"
                    "from src.retry import retry_delay\n\n\n"
                    "class RetryTests(unittest.TestCase):\n"
                    "    def test_one_based_growth_and_cap(self):\n"
                    "        self.assertEqual(retry_delay(1), 0.25)\n"
                    "        self.assertEqual(retry_delay(3), 1.0)\n"
                    "        self.assertEqual(retry_delay(9), 4.0)\n\n"
                    "    def test_validation(self):\n"
                    "        for args in ((0,), (-1,), (True,), (1, 0), (1, True), (1, 1, 0)):\n"
                    "            with self.subTest(args=args):\n"
                    "                self.assertIsNone(retry_delay(*args))\n\n\n"
                    "if __name__ == \"__main__\":\n"
                    "    unittest.main()\n"
                ),
            },
        },
        {
            "id": "record-parser",
            "sourcePath": "src/records.py",
            "edgeRequirement": "split each record only on its first equals sign",
            "files": {
                "src/__init__.py": "",
                "src/records.py": (
                    "def parse_records(text):\n"
                    "    \"\"\"Parse newline-delimited key=value records.\"\"\"\n"
                    "    return dict(line.split(\"=\") for line in text.splitlines())\n"
                ),
                "docs/contract.md": (
                    "# Record parser contract\n\n"
                    "Keep `parse_records(text)`. Ignore blank lines, comment lines "
                    "whose trimmed form starts with `#`, lines without `=`, and "
                    "records with an empty trimmed key. Trim keys and values, split "
                    "only on the first `=`, and let the final duplicate key win. "
                    "Do not raise for malformed lines.\n"
                ),
                "tests/test_contract.py": (
                    "import unittest\n\n"
                    "from src.records import parse_records\n\n\n"
                    "class RecordTests(unittest.TestCase):\n"
                    "    def test_mixed_records(self):\n"
                    "        text = \" # note\\n a = 1 \\nurl=https://x?a=b\\nbad\\n =skip\\na=2\\n\"\n"
                    "        self.assertEqual(parse_records(text), {\"a\": \"2\", \"url\": \"https://x?a=b\"})\n\n"
                    "    def test_empty_input(self):\n"
                    "        self.assertEqual(parse_records(\"\\n # only\\n\"), {})\n\n\n"
                    "if __name__ == \"__main__\":\n"
                    "    unittest.main()\n"
                ),
            },
        },
        {
            "id": "cache-key",
            "sourcePath": "src/cache_key.py",
            "edgeRequirement": "query components are sorted lexically without URL decoding",
            "files": {
                "src/__init__.py": "",
                "src/cache_key.py": (
                    "def canonical_key(method, path, query=\"\"):\n"
                    "    \"\"\"Build a stable request cache key.\"\"\"\n"
                    "    return f\"{method} {path}?{query}\"\n"
                ),
                "docs/contract.md": (
                    "# Cache-key contract\n\n"
                    "Keep `canonical_key(method, path, query=\"\")`. Uppercase and "
                    "trim the method. Ensure a leading slash and remove trailing "
                    "slashes except for root. Split query on `&`, discard empty "
                    "components, sort components lexically, and omit `?` when empty. "
                    "Do not URL-decode or otherwise rewrite query components.\n"
                ),
                "tests/test_contract.py": (
                    "import unittest\n\n"
                    "from src.cache_key import canonical_key\n\n\n"
                    "class CacheKeyTests(unittest.TestCase):\n"
                    "    def test_normalization(self):\n"
                    "        self.assertEqual(canonical_key(\" get \", \"api/items/\", \"b=2&&a=1\"), \"GET /api/items?a=1&b=2\")\n"
                    "        self.assertEqual(canonical_key(\"post\", \"/\"), \"POST /\")\n\n"
                    "    def test_query_is_not_decoded(self):\n"
                    "        self.assertEqual(canonical_key(\"get\", \"/x/\", \"q=a%20b&x=1\"), \"GET /x?q=a%20b&x=1\")\n\n\n"
                    "if __name__ == \"__main__\":\n"
                    "    unittest.main()\n"
                ),
            },
        },
    ]


def _turns(
    *,
    source_path: str,
    context_window: int,
    history: list[dict[str, str]],
) -> list[dict[str, object]]:
    return [
        {
            "prompt": (
                "Review docs/contract.md and the current implementation. Repair the "
                f"contract bug by editing only {source_path}, then run the complete "
                "unittest suite. Do not edit tests or documentation."
            ),
            "initialHistory": history,
            "contextWindow": context_window,
            "carryHistory": True,
        },
        {
            "prompt": (
                "Perform an adversarial review of the implementation against the "
                "original contract. If you find a mismatch, fix only the authorized "
                "source file. Run the complete unittest suite before finishing."
            ),
            "contextWindow": context_window,
            "carryHistory": True,
        },
        {
            "prompt": (
                "Check the subtle edge requirement established in the earliest "
                "discussion. Preserve the public API and rejected-approach decision, "
                "fix the authorized source if needed, and rerun all unittests."
            ),
            "contextWindow": context_window,
            "carryHistory": True,
        },
        {
            "prompt": (
                "Inspect the final implementation for accidental contract drift and "
                "run the complete unittest suite one final time. Make only a necessary "
                "repair to the authorized source file."
            ),
            "contextWindow": context_window,
            "carryHistory": True,
        },
        {
            "prompt": (
                "Do not call tools. In one short line, return the exact accepted-goal, "
                "non-negotiable-constraint, and rejected-approach marker values from "
                "the earliest discussion, then state whether final verification passed."
            ),
            "contextWindow": context_window,
        },
    ]


def _case(
    spec: dict[str, object],
    *,
    replicate: int,
    condition: str,
) -> dict[str, object]:
    family = str(spec["id"])
    source_path = str(spec["sourcePath"])
    marker_prefix = family.replace("-", "_").upper()
    goal = f"CCX_{marker_prefix}_R{replicate}_GOAL"
    constraint = f"CCX_{marker_prefix}_R{replicate}_CONSTRAINT"
    rejected = f"CCX_{marker_prefix}_R{replicate}_REJECTED"
    window = CANONICAL_WINDOW if condition == "canonical" else CONTROL_WINDOW
    history = _initial_history(
        goal_marker=goal,
        constraint_marker=constraint,
        rejected_marker=rejected,
        edge_requirement=str(spec["edgeRequirement"]),
    )
    oracles = [
        _oracle("all-runs-completed", "all_runs_completed"),
        _oracle("canonical-success", "canonical_success"),
        _oracle(
            "tests-pass",
            "command",
            argv=[
                "{python}",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-q",
            ],
            exitCode=0,
            timeoutSeconds=30,
        ),
        _oracle(
            "goal-recalled",
            "response_contains",
            values=[goal],
        ),
        _oracle(
            "constraint-recalled",
            "response_contains",
            values=[constraint],
        ),
        _oracle(
            "rejected-approach-recalled",
            "response_contains",
            values=[rejected],
        ),
    ]
    if condition == "canonical":
        oracles.append(
            _oracle("compaction-observed", "context_compaction_count", min=1)
        )
    files = {"README.md": "# Synthetic context-compaction task\n"}
    files.update(dict(spec["files"]))
    case_id = f"{family}-r{replicate}-{condition}"
    return {
        "id": case_id,
        "category": "context-compaction-paired-coding",
        "promptClass": family,
        "executionMode": "headless-live",
        "fixtureId": f"context-compaction-{family}-v1",
        "mutability": "write",
        "authorizedPaths": [source_path],
        "files": files,
        "turns": _turns(
            source_path=source_path,
            context_window=window,
            history=history,
        ),
        "oracles": oracles,
        "oracleIds": [str(oracle["id"]) for oracle in oracles],
        "experiment": {
            "pairId": f"{family}-r{replicate}",
            "family": family,
            "replicate": replicate,
            "condition": condition,
            "contextWindow": window,
        },
    }


def build_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for replicate in range(1, REPLICATES + 1):
        order = (
            ("canonical", "high-window-control")
            if replicate % 2 == 1
            else ("high-window-control", "canonical")
        )
        for spec in _family_specs():
            for condition in order:
                cases.append(
                    _case(spec, replicate=replicate, condition=condition)
                )
    return cases


def build_manifest() -> dict[str, Any]:
    cases = build_cases()
    if len(cases) != 24 or sum(len(case["turns"]) for case in cases) != 120:
        raise AssertionError("expected 24 cases / 120 turns")
    return {
        "schemaVersion": 1,
        "suiteId": SUITE_ID,
        "description": (
            "Preregistered paired live study of canonical context compaction "
            "versus a matched high-window no-compaction control on generated "
            "multi-turn coding repositories."
        ),
        "caseCount": len(cases),
        "taskCount": sum(len(case["turns"]) for case in cases),
        "pairCount": len(cases) // 2,
        "protocolVersion": 2,
        "conditions": {
            "canonical": {"contextWindow": CANONICAL_WINDOW},
            "high-window-control": {"contextWindow": CONTROL_WINDOW},
        },
        "runtimeProfileContract": {
            **RUNTIME_PROFILE_IDENTITY,
            "profileSha256": _contract_sha256(RUNTIME_PROFILE_IDENTITY),
            "version": 2,
        },
        "supersedesPilot": {
            "suiteId": "minicode-context-compaction-paired-2026-08-25-v1",
            "manifest": "artifacts/context-compaction-paired-v1/manifest.json",
            "manifestSha256": "91396ecb376fe88778fd1fdde8eb6f953c15ae40008cd28f1959d93ab3e228e9",
            "result": "artifacts/context-compaction-paired-v1/pilot-results.json",
            "resultSha256": "b64f049c6e5a151d12a9845cedac834d186d3ab10429b99e0f9135950f507575",
            "failedCanonicalCases": ["port-normalization-r1-canonical"],
            "retainedAsFailureEvidence": True,
            "repair": "bounded_deterministic_critical_state_anchors_v1",
            "protocolRepair": "split_marker_recall_oracles_v1",
        },
        "cases": cases,
    }


def build_pilot_manifest() -> dict[str, Any]:
    cases = [
        _case(spec, replicate=0, condition=condition)
        for spec in _family_specs()[:2]
        for condition in ("canonical", "high-window-control")
    ]
    document = build_manifest()
    document.update(
        {
            "suiteId": PILOT_SUITE_ID,
            "description": (
                "Independent four-case operational pilot for the bounded "
                "critical-state-anchor repair and split recall oracles."
            ),
            "caseCount": len(cases),
            "taskCount": sum(len(case["turns"]) for case in cases),
            "pairCount": len(cases) // 2,
            "cases": cases,
        }
    )
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pilot", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            build_pilot_manifest() if args.pilot else build_manifest(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
