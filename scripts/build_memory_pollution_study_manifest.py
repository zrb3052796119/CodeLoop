#!/usr/bin/env python3
"""Build the frozen manifest for the memory-pollution self-correction study.

Suite: minicode-memory-pollution-self-correction-2026-09-09-v1

Design (docs/superpowers/specs/2026-09-09-memory-pollution-self-correction-experiment-design.md):

- 6 seeded families (3 path recovery, 3 command recovery), no learning turns.
- 3 arms per family: ``clean`` (correct lesson only), ``polluted-off``
  (correct + stale + irrelevant, contribution review disabled) and
  ``polluted-rule`` (same pool, review enabled). The arm difference is
  carried by the runner's case-level env whitelist.
- 3 sequential target turns per case (fresh session each round; memory
  persists on disk between rounds).
- 18 cases / 54 target turns, single block. Statistical unit: family.

The stale lessons are lexically close to the correct ones (they compete for
retrieval) but their claims fail in this workspace: the stale path does not
exist / the stale unittest module does not exist.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SUITE_ID = "minicode-memory-pollution-self-correction-2026-09-09-v1"
BLOCK_SEEDS = (202609091,)
ROUND_COUNT = 3
# Credential-free wire-route contract frozen from the host runtime
# configuration on 2026-09-09 (model glm-5.3-flash via the bigmodel
# OpenAI-compatible endpoint). The runner binds every turn to this route
# before execution.
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
class PathFamily:
    family_id: str
    subject: str
    target_path: str
    stale_path: str
    decoy_path: str
    marker: str
    tags: tuple[str, ...]
    irrelevant: str


@dataclass(frozen=True, slots=True)
class CommandFamily:
    family_id: str
    subject: str
    module: str
    stale_module: str
    marker: str
    tags: tuple[str, ...]
    irrelevant: str


_PATH_FAMILIES: tuple[PathFamily, ...] = (
    PathFamily(
        family_id="p1-auth-policy",
        subject="auth policy document",
        target_path="docs/policies/auth-policy.md",
        stale_path="policies/auth-policy.md",
        decoy_path="docs/policies/data-retention.md",
        marker="AUTH-POLICY-771",
        tags=("auth", "policy"),
        irrelevant=(
            "Team convention: keep policy verification tests small and "
            "focused; prefer fast unit checks over broad suites for policy "
            "changes."
        ),
    ),
    PathFamily(
        family_id="p2-invoice-schema",
        subject="invoice schema definition",
        target_path="services/billing/invoice_schema.yaml",
        stale_path="services/invoice/schema.yaml",
        decoy_path="services/billing/tax_tables.yaml",
        marker="INVOICE-SCHEMA-623",
        tags=("billing", "schema"),
        irrelevant=(
            "Billing note: invoice totals are computed before tax "
            "adjustments; verify rounding behaviour with unit tests."
        ),
    ),
    PathFamily(
        family_id="p3-release-manifest",
        subject="release manifest",
        target_path="build/release-manifest.json",
        stale_path="release/manifest.json",
        decoy_path="build/artifact-index.json",
        marker="RELEASE-MANIFEST-509",
        tags=("release", "build"),
        irrelevant=(
            "Release process note: tag builds only after the verification "
            "suite passes; keep release notes brief and factual."
        ),
    ),
)

_COMMAND_FAMILIES: tuple[CommandFamily, ...] = (
    CommandFamily(
        family_id="c1-ledger-verify",
        subject="ledger configuration verification suite",
        module="ledgerlib.tests.test_config",
        stale_module="tests.test_config",
        marker="VERIFY-LEDGER-642",
        tags=("ledger", "verification"),
        irrelevant=(
            "Testing convention: prefer focused unit tests over broad "
            "integration runs when changing configuration handling."
        ),
    ),
    CommandFamily(
        family_id="c2-normalizer-verify",
        subject="data normalizer verification suite",
        module="datalib.tests.test_normalizer",
        stale_module="test_normalizer",
        marker="VERIFY-NORMALIZER-773",
        tags=("normalizer", "verification"),
        irrelevant=(
            "Data pipeline note: normalizer changes should ship with small "
            "regression tests before merge."
        ),
    ),
    CommandFamily(
        family_id="c3-registry-verify",
        subject="registry contract verification suite",
        module="registrylib.tests.test_registry",
        stale_module="registrylib.test_registry",
        marker="VERIFY-REGISTRY-824",
        tags=("registry", "contract"),
        irrelevant=(
            "Contract testing note: registry schemas evolve; keep contract "
            "fixtures minimal and deterministic."
        ),
    ),
)

_ROUND_PROMPTS = (
    "In this repository, locate the {subject} and report its verification marker.",
    "A follow-up session: find the current {subject} in this codebase and report the verification marker it contains.",
    "New conversation, same workspace: where does the {subject} live? Report its verification marker.",
)

_COMMAND_PROMPTS = (
    "Run this project's {subject} and report the marker it prints.",
    "A follow-up session: execute the {subject} for this repository and report the printed marker.",
    "New conversation, same workspace: run the {subject} and report the marker it prints.",
)


def _path_correct_lesson(family: PathFamily) -> str:
    return (
        f"Verified location for this repository: the {family.subject} lives "
        f"at {family.target_path} and reports its verification marker there."
    )


def _path_stale_lesson(family: PathFamily) -> str:
    return (
        f"Reference note: the {family.subject} used to live at "
        f"{family.stale_path}; check there first."
    )


def _command_correct_lesson(family: CommandFamily) -> str:
    return (
        f"Verified command for this repository: the {family.subject} runs "
        f"with python -m unittest {family.module} from the repository root "
        "and prints its verification marker."
    )


def _command_stale_lesson(family: CommandFamily) -> str:
    return (
        f"Recovery note: the {family.subject} can be run with "
        f"python -m unittest {family.stale_module}; use that command when "
        "verification is needed."
    )


def _path_files(family: PathFamily) -> dict[str, str]:
    return {
        "README.md": (
            "# Synthetic workspace\n\nA small synthetic project used by the "
            "memory-pollution study. Documents and verification suites exist "
            "somewhere in the tree.\n"
        ),
        family.target_path: (
            f"# Synthetic {family.subject}\n\nmarker: {family.marker}\n"
        ),
        family.decoy_path: (
            f"# Synthetic neighbour of {family.subject}\n\nNo verification "
            "marker is recorded here.\n"
        ),
    }


def _command_files(family: CommandFamily) -> dict[str, str]:
    package = family.module.split(".")[0]
    test_path = family.module.replace(".", "/") + ".py"
    return {
        "README.md": (
            "# Synthetic workspace\n\nA small synthetic project used by the "
            "memory-pollution study. Verification suites live inside their "
            "packages.\n"
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


def _case_for_arm(
    *,
    case_id: str,
    stratum: str,
    family_id: str,
    marker: str,
    turns: list[dict[str, object]],
    files: dict[str, str],
    memory_entries: list[dict[str, object]],
    review_mode: str,
    verifier_argv: list[str] | None,
    tags: tuple[str, ...],
) -> dict[str, object]:
    oracles: list[dict[str, object]] = [
        _oracle("run-completed", "all_runs_completed"),
        _oracle("canonical-success", "canonical_success"),
        _oracle("no-source-edits", "no_source_edits"),
        _oracle("memory-injected", "memory_injected", min=1),
    ]
    if verifier_argv is not None:
        oracles.extend(
            [
                _oracle(
                    "target-verifier",
                    "command",
                    argv=verifier_argv,
                    exitCode=0,
                    timeoutSeconds=30,
                ),
            ]
        )
    oracles.append(_oracle("target-marker", "response_contains", values=[marker]))
    case: dict[str, object] = {
        "id": case_id,
        "category": f"memory-pollution-{stratum}",
        "promptClass": "memory-pollution-study",
        "executionMode": "headless-live",
        "fixtureId": "memory-pollution-synthetic-v1",
        "mutability": "read_only",
        "authorizedPaths": [],
        "env": {"MINI_CODE_MEMORY_CONTRIBUTION_REVIEW": review_mode},
        "files": files,
        "turns": turns,
        "memoryEntries": memory_entries,
        "oracles": oracles,
        "oracleIds": [str(oracle["id"]) for oracle in oracles],
        "study": {
            "familyId": family_id,
            "stratum": stratum,
            "arm": (
                "clean"
                if len(memory_entries) == 1
                else ("polluted-off" if review_mode == "off" else "polluted-rule")
            ),
            "roundCount": len(turns),
            "targetMarker": marker,
            "seedRoles": (
                ["correct"]
                if len(memory_entries) == 1
                else ["correct", "stale", "irrelevant"]
            ),
        },
    }
    del tags  # tags flow into memoryEntries, not the case itself
    return case


def _memory_entries(
    items: list[tuple[str, str, tuple[str, ...]]]
) -> list[dict[str, object]]:
    return [
        {"category": "lesson", "content": content, "tags": list(tags)}
        for _role, content, tags in items
    ]


def build_manifest() -> dict[str, object]:
    cases: list[dict[str, object]] = []
    for family in _PATH_FAMILIES:
        turns = [
            {"prompt": prompt.format(subject=family.subject)}
            for prompt in _ROUND_PROMPTS
        ]
        files = _path_files(family)
        assert family.stale_path not in files
        common = dict(
            stratum="path-recovery",
            family_id=family.family_id,
            marker=family.marker,
            turns=turns,
            files=files,
            verifier_argv=None,
            tags=family.tags,
        )
        cases.append(
            _case_for_arm(
                case_id=f"mpol-{family.family_id}-clean",
                memory_entries=_memory_entries(
                    [
                        (
                            "correct",
                            _path_correct_lesson(family),
                            family.tags,
                        )
                    ]
                ),
                review_mode="rule",
                **common,  # type: ignore[arg-type]
            )
        )
        polluted = _memory_entries(
            [
                ("correct", _path_correct_lesson(family), family.tags),
                ("stale", _path_stale_lesson(family), family.tags),
                ("irrelevant", family.irrelevant, family.tags),
            ]
        )
        cases.append(
            _case_for_arm(
                case_id=f"mpol-{family.family_id}-polluted-off",
                memory_entries=polluted,
                review_mode="off",
                **common,  # type: ignore[arg-type]
            )
        )
        cases.append(
            _case_for_arm(
                case_id=f"mpol-{family.family_id}-polluted-rule",
                memory_entries=list(polluted),
                review_mode="rule",
                **common,  # type: ignore[arg-type]
            )
        )
    for family in _COMMAND_FAMILIES:
        turns = [
            {"prompt": prompt.format(subject=family.subject)}
            for prompt in _COMMAND_PROMPTS
        ]
        files = _command_files(family)
        common = dict(
            stratum="command-recovery",
            family_id=family.family_id,
            marker=family.marker,
            turns=turns,
            files=files,
            verifier_argv=["{python}", "-m", "unittest", family.module],
            tags=family.tags,
        )
        cases.append(
            _case_for_arm(
                case_id=f"mpol-{family.family_id}-clean",
                memory_entries=_memory_entries(
                    [
                        (
                            "correct",
                            _command_correct_lesson(family),
                            family.tags,
                        )
                    ]
                ),
                review_mode="rule",
                **common,  # type: ignore[arg-type]
            )
        )
        polluted = _memory_entries(
            [
                ("correct", _command_correct_lesson(family), family.tags),
                ("stale", _command_stale_lesson(family), family.tags),
                ("irrelevant", family.irrelevant, family.tags),
            ]
        )
        cases.append(
            _case_for_arm(
                case_id=f"mpol-{family.family_id}-polluted-off",
                memory_entries=polluted,
                review_mode="off",
                **common,  # type: ignore[arg-type]
            )
        )
        cases.append(
            _case_for_arm(
                case_id=f"mpol-{family.family_id}-polluted-rule",
                memory_entries=list(polluted),
                review_mode="rule",
                **common,  # type: ignore[arg-type]
            )
        )

    assert len(cases) == 18, f"expected 18 cases, built {len(cases)}"
    assert sum(len(case["turns"]) for case in cases) == 54  # type: ignore[index]
    return {
        "schemaVersion": 1,
        "suiteId": SUITE_ID,
        "description": (
            "Memory pollution & self-correction: clean vs polluted pools "
            "with the contribution reviewer on/off, three sequential target "
            "rounds per case, single block."
        ),
        "familyCount": 6,
        "armCount": 3,
        "roundCount": ROUND_COUNT,
        "pairCount": 6,
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
        default=Path(
            "artifacts/memory-pollution-study-v1/manifest.json"
        ),
    )
    args = parser.parse_args(argv)
    manifest = build_manifest()
    # Validate against the production runner contract before freezing.
    from scripts.run_north_star_live import _validate_manifest

    _validate_manifest(manifest)
    _write_frozen(args.output, manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
