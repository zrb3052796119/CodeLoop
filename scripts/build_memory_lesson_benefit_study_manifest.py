#!/usr/bin/env python3
"""Build the frozen manifest for the memory-lesson-benefit study.

Suite: minicode-memory-lesson-benefit-2026-09-09-v1

Design (docs/superpowers/specs/2026-09-09-memory-lesson-benefit-experiment-design.md):

- 6 learned command-recovery families. Every warm case first runs a
  learning turn whose prompt misdirects to a unittest module that does not
  exist, so the model genuinely fails, discovers the correct module, and
  lets the verified recovery precipitate a lesson. (Path-recovery
  misdirection was probed and deliberately dropped: on the current
  precipitation gate a single-observation path error does not persist —
  see the design doc's revision note.)
- Paired warm/cold target turns: identical prompts and fixtures; warm reuses
  the workspace (lesson persists on disk), cold starts from an empty memory
  authority.
- The contribution reviewer runs in ``llm`` mode on every case (env
  whitelist); it is a no-op unless memories were injected.
- 2 blocks, 12 warm + 12 cold cases, 36 live turns. Statistical unit:
  family (n=6).

Oracles prove the full chain: the misdirection was really hit
(tool_failed), a lesson was written (memory_written), its CONTENT covers
the correct target (memory_lesson_covers), and the lesson written in turn 0
is the one rendered in turn 1 (memory_attributed, claim type recovery).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SUITE_ID = "minicode-memory-lesson-benefit-2026-09-09-v1"
BLOCK_SEEDS = (202609092, 202609093)
REVIEW_ENV: dict[str, str] = {"MINI_CODE_MEMORY_CONTRIBUTION_REVIEW": "llm"}
# Credential-free wire-route contract frozen from the host runtime
# configuration on 2026-09-09 (model glm-5.3-flash via the bigmodel
# OpenAI-compatible endpoint).
RUNTIME_PROFILE_CONTRACT: dict[str, object] = {
    "version": 2,
    "adapterType": "openai_compatible",
    "provider": "custom",
    "model": "glm-5.3-flash",
    "endpointUrl": "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
    "profileSha256": (
        "52f00559cb100637ac97877a59f6f1a35384bca223d07332492451435f61c2fb"
    ),
}


@dataclass(frozen=True, slots=True)
class CommandFamily:
    family_id: str
    subject: str
    module: str
    wrong_module: str
    marker: str
    tags: tuple[str, ...]


_COMMAND_FAMILIES: tuple[CommandFamily, ...] = (
    CommandFamily(
        family_id="c1-ledger-verify",
        subject="ledger configuration verification suite",
        module="ledgerlib.tests.test_config",
        wrong_module="tests.test_config",
        marker="VERIFY-LEDGER-642",
        tags=("ledger", "verification"),
    ),
    CommandFamily(
        family_id="c2-normalizer-verify",
        subject="data normalizer verification suite",
        module="datalib.tests.test_normalizer",
        wrong_module="test_normalizer",
        marker="VERIFY-NORMALIZER-773",
        tags=("normalizer", "verification"),
    ),
    CommandFamily(
        family_id="c3-registry-verify",
        subject="registry contract verification suite",
        module="registrylib.tests.test_registry",
        wrong_module="registrylib.test_registry",
        marker="VERIFY-REGISTRY-824",
        tags=("registry", "contract"),
    ),
    CommandFamily(
        family_id="c4-session-verify",
        subject="session expiry verification suite",
        module="sessionlib.tests.test_expiry",
        wrong_module="tests.test_expiry",
        marker="VERIFY-SESSION-851",
        tags=("session", "verification"),
    ),
    CommandFamily(
        family_id="c5-schema-verify",
        subject="schema compatibility verification suite",
        module="schemalib.tests.test_compat",
        wrong_module="test_compat",
        marker="VERIFY-SCHEMA-903",
        tags=("schema", "compat"),
    ),
    CommandFamily(
        family_id="c6-invoice-verify",
        subject="invoice rounding verification suite",
        module="invoicelib.tests.test_rounding",
        wrong_module="invoicelib.test_rounding",
        marker="VERIFY-ROUNDING-947",
        tags=("invoice", "rounding"),
    ),
)


def _command_files(family: CommandFamily) -> dict[str, str]:
    package = family.module.split(".")[0]
    test_path = family.module.replace(".", "/") + ".py"
    return {
        "README.md": (
            "# Synthetic workspace\n\nA small synthetic project used by the "
            "memory-lesson-benefit study. Verification suites live inside "
            "their packages.\n"
        ),
        f"{package}/__init__.py": "",
        f"{package}/tests/__init__.py": "",
        test_path: (
            "import unittest\n\n\n"
            f"class VerificationTests(unittest.TestCase):\n"
            "    def test_reports_marker(self):\n"
            f'        print("{family.marker}")\n'
            "        self.assertTrue(True)\n"
            "\n\nif __name__ == \"__main__\":\n"
            "    unittest.main()\n"
        ),
    }


def _oracle(oracle_id: str, kind: str, **extra: object) -> dict[str, object]:
    oracle: dict[str, object] = {"id": oracle_id, "kind": kind}
    oracle.update(extra)
    return oracle


def _learning_prompt_command(family: CommandFamily) -> str:
    return (
        f"A previous note says the {family.subject} runs with "
        f"python -m unittest {family.wrong_module} — start with that "
        "command and report the marker it prints."
    )


def _target_prompt_command(family: CommandFamily) -> str:
    return (
        f"Run this project's {family.subject} and report the marker it "
        "prints."
    )


def _base_oracles(
    *, stratum: str, marker: str, verifier_argv: list[str] | None
) -> list[dict[str, object]]:
    oracles: list[dict[str, object]] = [
        _oracle("run-completed", "all_runs_completed"),
        _oracle("canonical-success", "canonical_success"),
        _oracle("no-source-edits", "no_source_edits"),
    ]
    if verifier_argv is not None:
        oracles.append(
            _oracle(
                "target-verifier",
                "command",
                argv=verifier_argv,
                exitCode=0,
                timeoutSeconds=30,
            )
        )
    oracles.append(_oracle("target-marker", "response_contains", values=[marker]))
    return oracles


def _command_family_cases(
    family: CommandFamily, block: int, pair_order: int
) -> list[dict[str, object]]:
    files = _command_files(family)
    target_prompt = _target_prompt_command(family)
    verifier = ["{python}", "-m", "unittest", family.module]
    warm_oracles = _base_oracles(
        stratum="command-recovery", marker=family.marker, verifier_argv=verifier
    ) + [
        _oracle("source-failure", "tool_failed", toolName="run_command", min=1),
        _oracle("lesson-written", "memory_written"),
        _oracle(
            "lesson-covers-target",
            "memory_lesson_covers",
            text=family.module,
            claimType="recovery",
        ),
        _oracle(
            "lesson-attributed",
            "memory_attributed",
            source="written",
            sourceTurn=0,
            renderedTurn=1,
            claimType="recovery",
            min=1,
        ),
    ]
    cold_oracles = _base_oracles(
        stratum="command-recovery", marker=family.marker, verifier_argv=verifier
    )
    study_base = {
        "familyId": family.family_id,
        "stratum": "command-recovery",
        "block": block,
        "pairOrder": pair_order,
        "targetMarker": family.marker,
        "correctTarget": family.module,
        "wrongTarget": family.wrong_module,
    }
    warm = {
        "id": f"lb-b{block}-{family.family_id}-warm",
        "category": "memory-lesson-benefit-command-recovery",
        "promptClass": "memory-lesson-benefit-study",
        "executionMode": "headless-live",
        "fixtureId": "memory-lesson-benefit-synthetic-v1",
        "mutability": "read_only",
        "authorizedPaths": [],
        "env": dict(REVIEW_ENV),
        "files": files,
        "turns": [
            {"prompt": _learning_prompt_command(family)},
            {"prompt": target_prompt},
        ],
        "oracles": warm_oracles,
        "oracleIds": [str(item["id"]) for item in warm_oracles],
        "study": {**study_base, "condition": "warm", "warmFirst": True,
                  "targetTurnIndex": 1},
    }
    cold = {
        "id": f"lb-b{block}-{family.family_id}-cold",
        "category": "memory-lesson-benefit-command-recovery",
        "promptClass": "memory-lesson-benefit-study",
        "executionMode": "headless-live",
        "fixtureId": "memory-lesson-benefit-synthetic-v1",
        "mutability": "read_only",
        "authorizedPaths": [],
        "env": dict(REVIEW_ENV),
        "files": dict(files),
        "turns": [{"prompt": target_prompt}],
        "oracles": cold_oracles,
        "oracleIds": [str(item["id"]) for item in cold_oracles],
        "study": {**study_base, "condition": "cold", "warmFirst": False,
                  "targetTurnIndex": 0},
    }
    return [warm, cold]


def build_manifest() -> dict[str, object]:
    cases: list[dict[str, object]] = []
    for block in (1, 2):
        pair_order = 0
        for family in _COMMAND_FAMILIES:
            pair = _command_family_cases(family, block, pair_order)
            cases.extend(pair if (pair_order + block) % 2 == 0 else pair[::-1])
            pair_order += 1

    assert len(cases) == 24, f"expected 24 cases, built {len(cases)}"
    assert sum(len(case["turns"]) for case in cases) == 36
    warm_cases = [case for case in cases if case["study"]["condition"] == "warm"]
    cold_cases = [case for case in cases if case["study"]["condition"] == "cold"]
    assert len(warm_cases) == 12 and len(cold_cases) == 12
    for warm in warm_cases:
        cold = next(
            case
            for case in cold_cases
            if case["study"]["familyId"] == warm["study"]["familyId"]
            and case["study"]["block"] == warm["study"]["block"]
        )
        assert warm["turns"][-1]["prompt"] == cold["turns"][0]["prompt"]
        assert warm["files"] == cold["files"]
    return {
        "schemaVersion": 1,
        "suiteId": SUITE_ID,
        "description": (
            "Memory lesson benefit: paired warm/cold target turns; warm "
            "cases first learn from a genuinely failing misdirected "
            "episode; contribution reviewer in llm mode everywhere."
        ),
        "familyCount": 6,
        "conditionCount": 2,
        "blockSeeds": list(BLOCK_SEEDS),
        "primaryUnit": "family",
        "caseCount": len(cases),
        "runtimeProfileContract": dict(RUNTIME_PROFILE_CONTRACT),
        "cases": cases,
    }


def _write_frozen(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if path.exists() and path.read_text(encoding="utf-8") != encoded:
        raise SystemExit(f"refusing to overwrite frozen manifest: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(payload['cases'])} cases)")  # type: ignore[index]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/memory-lesson-benefit-study-v1/manifest.json"),
    )
    args = parser.parse_args(argv)
    manifest = build_manifest()
    from scripts.run_north_star_live import _validate_manifest

    _validate_manifest(manifest)
    _write_frozen(args.output, manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
