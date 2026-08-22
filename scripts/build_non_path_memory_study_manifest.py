#!/usr/bin/env python3
"""Build the frozen live study for non-path persistent engineering lessons."""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


SUITE_ID_V1 = "minicode-non-path-memory-live-2026-08-22-v1"
SUITE_ID = "minicode-non-path-memory-live-2026-08-22-v2"
BLOCK_SEEDS = (202608221, 202608222, 202608223)


@dataclass(frozen=True, slots=True)
class Family:
    family_id: str
    stratum: str
    lesson_mode: str
    common_files: tuple[tuple[str, str], ...]
    target_prompt: str
    target_marker: str
    target_verifier: tuple[str, ...]
    seed_memory: str
    tags: tuple[str, ...]
    target_file: str = ""
    target_before: str = ""
    target_after: str = ""
    target_expected: str = ""
    learning_prompt: str = ""
    learning_file: str = ""
    learning_before: str = ""
    learning_after: str = ""
    learning_failure: bool = False


def _unittest_module(class_name: str, marker: str) -> str:
    return f'''import unittest


class {class_name}(unittest.TestCase):
    def test_contract(self):
        self.assertTrue(True)
        print("{marker}")


if __name__ == "__main__":
    unittest.main()
'''


def _session_module(*, fixed: bool) -> str:
    checks = (
        "    if expired:\n"
        "        return False\n"
        "    if known:\n"
        "        return True\n"
        if fixed
        else
        "    if known:\n"
        "        return True\n"
        "    if expired:\n"
        "        return False\n"
    )
    return (
        "def accept(expired: bool, known: bool) -> bool:\n"
        f"{checks}"
        "    return False\n"
    )


def _session_test(module: str, class_name: str, marker: str) -> str:
    return f'''import unittest

from auth.{module} import accept


class {class_name}(unittest.TestCase):
    def test_expired_known_session_is_rejected(self):
        self.assertFalse(accept(expired=True, known=True))
        print("{marker}")


if __name__ == "__main__":
    unittest.main()
'''


def _normalizer_module(*, fixed: bool) -> str:
    expression = "value.strip().lower()" if fixed else "value.strip()"
    return f'''KNOWN = {{"alpha", "beta"}}


def is_known(value: str) -> bool:
    normalized = {expression}
    return normalized in KNOWN
'''


def _normalizer_test(module: str, class_name: str, marker: str) -> str:
    return f'''import unittest

from parser.{module} import is_known


class {class_name}(unittest.TestCase):
    def test_mixed_case_token_is_normalized(self):
        self.assertTrue(is_known("  ALPHA  "))
        print("{marker}")


if __name__ == "__main__":
    unittest.main()
'''


def _schema_module(field_name: str | None = None) -> str:
    lines = ['    "id": {"required": True},']
    if field_name:
        lines.append(f'    "{field_name}": {{"required": False}},')
    return "FIELDS = {\n" + "\n".join(lines) + "\n}\n"


def _schema_test(
    module: str,
    class_name: str,
    field_name: str,
    marker: str,
) -> str:
    return f'''import unittest

from api.{module} import FIELDS


class {class_name}(unittest.TestCase):
    def test_new_field_is_optional(self):
        self.assertIn("{field_name}", FIELDS)
        self.assertFalse(FIELDS["{field_name}"]["required"])
        print("{marker}")


if __name__ == "__main__":
    unittest.main()
'''


def _registry_module(values: tuple[str, ...]) -> str:
    return f"NAMES = {list(values)!r}\n"


def _registry_test(
    module: str,
    class_name: str,
    expected: tuple[str, ...],
    marker: str,
) -> str:
    return f'''import unittest

from plugins.{module} import NAMES


class {class_name}(unittest.TestCase):
    def test_registry_is_sorted(self):
        self.assertEqual(NAMES, {list(expected)!r})
        print("{marker}")


if __name__ == "__main__":
    unittest.main()
'''


def _constant_test(
    module_path: str,
    imported: str,
    class_name: str,
    assertion: str,
    marker: str,
) -> str:
    return f'''import unittest

from {module_path} import {imported}


class {class_name}(unittest.TestCase):
    def test_project_contract(self):
        {assertion}
        print("{marker}")


if __name__ == "__main__":
    unittest.main()
'''


BASE_PACKAGE_FILES = (
    ("tests/__init__.py", ""),
)


FAMILIES = (
    Family(
        "release-command",
        "command-recovery",
        "learned",
        BASE_PACKAGE_FILES
        + ((
            "tests/test_release_contract.py",
            _unittest_module("ReleaseContractTests", "COMMAND-RELEASE-731"),
        ),),
        (
            "Run the project's release contract verification and report the exact "
            "verification marker. Do not edit files."
        ),
        "COMMAND-RELEASE-731",
        ("{python}", "-m", "unittest", "-v", "tests.test_release_contract.ReleaseContractTests"),
        (
            "Verified command recovery for release contract checks: use "
            "`python -m unittest -v tests.test_release_contract.ReleaseContractTests`; "
            "do not use the absent legacy unittest module."
        ),
        ("release", "contract", "unittest"),
        learning_prompt=(
            "Your first tool call must be run_command with exactly `python -m "
            "unittest -v tests.test_release_legacy`; this pre-registered command "
            "is intentionally invalid. After it fails, recover to the real release "
            "contract unittest, run it successfully, and report its exact marker. "
            "Do not edit files."
        ),
        learning_failure=True,
    ),
    Family(
        "payload-command",
        "command-recovery",
        "learned",
        BASE_PACKAGE_FILES
        + ((
            "tests/test_payload_contract.py",
            _unittest_module("PayloadContractTests", "COMMAND-PAYLOAD-284"),
        ),),
        (
            "Run the payload compatibility unittest and report its exact verification "
            "marker. Do not edit files."
        ),
        "COMMAND-PAYLOAD-284",
        ("{python}", "-m", "unittest", "-v", "tests.test_payload_contract.PayloadContractTests"),
        (
            "Verified command recovery for payload compatibility: invoke "
            "`python -m unittest -v tests.test_payload_contract.PayloadContractTests`; "
            "the LegacyPayloadTests selector is invalid."
        ),
        ("payload", "compatibility", "unittest"),
        learning_prompt=(
            "Your first tool call must be run_command with exactly `python -m "
            "unittest -v tests.test_payload_contract.LegacyPayloadTests`; this "
            "selector is intentionally invalid. Recover to the real payload contract "
            "test class, verify it, and report its exact marker. Do not edit files."
        ),
        learning_failure=True,
    ),
    Family(
        "cli-command",
        "command-recovery",
        "seeded",
        BASE_PACKAGE_FILES
        + ((
            "tests/test_cli_contract.py",
            _unittest_module("CliContractTests", "COMMAND-CLI-915"),
        ),),
        (
            "Execute the CLI compatibility verification and report its exact marker. "
            "Do not edit files."
        ),
        "COMMAND-CLI-915",
        ("{python}", "-m", "unittest", "-v", "tests.test_cli_contract.CliContractTests"),
        (
            "Project verification knowledge: execute `python -m unittest -v "
            "tests.test_cli_contract.CliContractTests` directly for CLI compatibility; "
            "this is the verified command, not a file-location hint."
        ),
        ("cli", "compatibility", "unittest"),
    ),
    Family(
        "expired-session-repair",
        "code-repair",
        "learned",
        BASE_PACKAGE_FILES
        + (
            ("auth/__init__.py", ""),
            ("tests/test_session_primary.py", _session_test("session_primary", "SessionPrimaryTests", "REPAIR-AUTH-PRIMARY-463")),
            ("tests/test_session_secondary.py", _session_test("session_secondary", "SessionSecondaryTests", "REPAIR-AUTH-SECONDARY-857")),
        ),
        (
            "Fix the expired-token acceptance bug in auth/session_secondary.py, "
            "run its focused unittest, and report the exact verification marker."
        ),
        "REPAIR-AUTH-SECONDARY-857",
        ("{python}", "-m", "unittest", "-v", "tests.test_session_secondary.SessionSecondaryTests"),
        (
            "Verified repair pattern for session acceptance: reject an expired token "
            "before checking whether the session is known; then run the focused "
            "session unittest."
        ),
        ("expired", "session", "repair"),
        target_file="auth/session_secondary.py",
        target_before=_session_module(fixed=False),
        target_after=_session_module(fixed=True),
        target_expected="    if expired:\n        return False\n    if known:",
        learning_prompt=(
            "Your first tool call must be run_command with exactly `python -m "
            "unittest -v tests.test_session_primary.SessionPrimaryTests`; the test "
            "is intentionally failing. Repair auth/session_primary.py so expired "
            "known sessions are rejected before lookup, rerun the same test, and "
            "report its marker."
        ),
        learning_file="auth/session_primary.py",
        learning_before=_session_module(fixed=False),
        learning_after=_session_module(fixed=True),
        learning_failure=True,
    ),
    Family(
        "token-normalization-repair",
        "code-repair",
        "learned",
        BASE_PACKAGE_FILES
        + (
            ("parser/__init__.py", ""),
            ("tests/test_normalizer_primary.py", _normalizer_test("normalizer_primary", "NormalizerPrimaryTests", "REPAIR-NORM-PRIMARY-812")),
            ("tests/test_normalizer_secondary.py", _normalizer_test("normalizer_secondary", "NormalizerSecondaryTests", "REPAIR-NORM-SECONDARY-394")),
        ),
        (
            "Fix mixed-case token recognition in parser/normalizer_secondary.py, "
            "run its focused unittest, and report the exact verification marker."
        ),
        "REPAIR-NORM-SECONDARY-394",
        ("{python}", "-m", "unittest", "-v", "tests.test_normalizer_secondary.NormalizerSecondaryTests"),
        (
            "Verified normalization repair: strip surrounding whitespace and lower-case "
            "the token before membership lookup, then run the focused normalizer test."
        ),
        ("token", "normalization", "repair"),
        target_file="parser/normalizer_secondary.py",
        target_before=_normalizer_module(fixed=False),
        target_after=_normalizer_module(fixed=True),
        target_expected="normalized = value.strip().lower()",
        learning_prompt=(
            "Your first tool call must be run_command with exactly `python -m "
            "unittest -v tests.test_normalizer_primary.NormalizerPrimaryTests`; "
            "the test is intentionally failing. Repair parser/normalizer_primary.py, "
            "rerun the same focused test, and report its exact marker."
        ),
        learning_file="parser/normalizer_primary.py",
        learning_before=_normalizer_module(fixed=False),
        learning_after=_normalizer_module(fixed=True),
        learning_failure=True,
    ),
    Family(
        "exclusive-window-repair",
        "code-repair",
        "seeded",
        BASE_PACKAGE_FILES
        + (
            ("windowing/__init__.py", ""),
            (
                "tests/test_window_contract.py",
                _constant_test(
                    "windowing.window",
                    "take_window",
                    "WindowContractTests",
                    "self.assertEqual(take_window([1, 2, 3, 4], 1, 3), [2, 3])",
                    "REPAIR-WINDOW-526",
                ),
            ),
        ),
        (
            "Fix the exclusive-end window bug in windowing/window.py, run the focused "
            "unittest, and report the exact verification marker."
        ),
        "REPAIR-WINDOW-526",
        ("{python}", "-m", "unittest", "-v", "tests.test_window_contract.WindowContractTests"),
        (
            "Verified code repair: this project's window end index is exclusive, so "
            "slice with `items[start:end]`, not `items[start:end + 1]`; verify with "
            "the focused window unittest."
        ),
        ("window", "exclusive", "repair"),
        target_file="windowing/window.py",
        target_before=(
            "def take_window(items: list[int], start: int, end: int) -> list[int]:\n"
            "    return items[start:end + 1]\n"
        ),
        target_after=(
            "def take_window(items: list[int], start: int, end: int) -> list[int]:\n"
            "    return items[start:end]\n"
        ),
        target_expected="return items[start:end]",
    ),
    Family(
        "optional-field-constraint",
        "project-constraint",
        "learned",
        BASE_PACKAGE_FILES
        + (
            ("api/__init__.py", ""),
            (
                "project-policy.md",
                "# Response compatibility\n\nAll new public response fields must be optional to preserve backward compatibility. This policy applies to response schema modules.\n",
            ),
            ("tests/test_schema_primary.py", _schema_test("schema_primary", "SchemaPrimaryTests", "trace_id", "CONSTRAINT-SCHEMA-PRIMARY-748")),
            ("tests/test_schema_secondary.py", _schema_test("schema_secondary", "SchemaSecondaryTests", "request_id", "CONSTRAINT-SCHEMA-SECONDARY-623")),
        ),
        (
            "Add request_id to api/schema_secondary.py while respecting the established "
            "response compatibility policy. Run the focused unittest and report its marker."
        ),
        "CONSTRAINT-SCHEMA-SECONDARY-623",
        ("{python}", "-m", "unittest", "-v", "tests.test_schema_secondary.SchemaSecondaryTests"),
        (
            "Project constraint: all new public response fields must be optional to "
            "preserve backward compatibility; apply this rule to response schemas."
        ),
        ("response", "compatibility", "constraint"),
        target_file="api/schema_secondary.py",
        target_before=_schema_module(),
        target_after=_schema_module("request_id"),
        target_expected='"request_id": {"required": False}',
        learning_prompt=(
            "First read project-policy.md. Then add trace_id to api/schema_primary.py "
            "in compliance with that policy, run `python -m unittest -v "
            "tests.test_schema_primary.SchemaPrimaryTests`, and report its marker."
        ),
        learning_file="api/schema_primary.py",
        learning_before=_schema_module(),
        learning_after=_schema_module("trace_id"),
    ),
    Family(
        "registry-order-constraint",
        "project-constraint",
        "learned",
        BASE_PACKAGE_FILES
        + (
            ("plugins/__init__.py", ""),
            (
                "project-policy.md",
                "# Registry policy\n\nExported registry names must always remain in alphabetical order. This rule applies to every plugin registry module.\n",
            ),
            ("tests/test_registry_primary.py", _registry_test("registry_primary", "RegistryPrimaryTests", ("alpha", "beta", "gamma"), "CONSTRAINT-REGISTRY-PRIMARY-269")),
            ("tests/test_registry_secondary.py", _registry_test("registry_secondary", "RegistrySecondaryTests", ("delta", "epsilon", "zeta"), "CONSTRAINT-REGISTRY-SECONDARY-481")),
        ),
        (
            "Add epsilon to plugins/registry_secondary.py while respecting the "
            "established registry policy. Run its focused unittest and report the marker."
        ),
        "CONSTRAINT-REGISTRY-SECONDARY-481",
        ("{python}", "-m", "unittest", "-v", "tests.test_registry_secondary.RegistrySecondaryTests"),
        (
            "Project constraint: exported plugin registry names must always remain "
            "alphabetically sorted."
        ),
        ("registry", "alphabetical", "constraint"),
        target_file="plugins/registry_secondary.py",
        target_before=_registry_module(("delta", "zeta")),
        target_after=_registry_module(("delta", "epsilon", "zeta")),
        target_expected="NAMES = ['delta', 'epsilon', 'zeta']",
        learning_prompt=(
            "First read project-policy.md. Then add beta to plugins/registry_primary.py "
            "in compliance with that policy, run `python -m unittest -v "
            "tests.test_registry_primary.RegistryPrimaryTests`, and report its marker."
        ),
        learning_file="plugins/registry_primary.py",
        learning_before=_registry_module(("alpha", "gamma")),
        learning_after=_registry_module(("alpha", "beta", "gamma")),
    ),
    Family(
        "error-envelope-constraint",
        "project-constraint",
        "seeded",
        BASE_PACKAGE_FILES
        + (
            ("api/__init__.py", ""),
            (
                "tests/test_error_envelope.py",
                _constant_test(
                    "api.error_envelope",
                    "FIELDS",
                    "ErrorEnvelopeTests",
                    'self.assertIn("details", FIELDS); self.assertFalse(FIELDS["details"]["required"]); self.assertTrue(FIELDS["code"]["required"])',
                    "CONSTRAINT-ERROR-936",
                ),
            ),
        ),
        (
            "Add details to the public error envelope in api/error_envelope.py while "
            "respecting established compatibility constraints. Verify and report the marker."
        ),
        "CONSTRAINT-ERROR-936",
        ("{python}", "-m", "unittest", "-v", "tests.test_error_envelope.ErrorEnvelopeTests"),
        (
            "Project constraint: the public error envelope must retain required numeric "
            "code, while every newly added field such as details must be optional."
        ),
        ("error", "envelope", "constraint"),
        target_file="api/error_envelope.py",
        target_before='FIELDS = {\n    "code": {"required": True},\n}\n',
        target_after=(
            'FIELDS = {\n    "code": {"required": True},\n'
            '    "details": {"required": False},\n}\n'
        ),
        target_expected='"details": {"required": False}',
    ),
    Family(
        "parser-verification-rule",
        "verification-rule",
        "learned",
        BASE_PACKAGE_FILES
        + (
            ("parser/__init__.py", ""),
            (
                "project-policy.md",
                "# Parser verification\n\nAfter changing parser/runtime.py, always run python -m unittest -v tests.test_parser_runtime.ParserRuntimeTests. This verification is required before reporting completion.\n",
            ),
            (
                "tests/test_parser_runtime.py",
                _constant_test(
                    "parser.runtime",
                    "MODE",
                    "ParserRuntimeTests",
                    'self.assertEqual(MODE, "strict")',
                    "VERIFY-PARSER-174",
                ),
            ),
        ),
        (
            "Update DESCRIPTION in parser/runtime.py from old to current, follow the "
            "established project verification rule, and report its exact marker."
        ),
        "VERIFY-PARSER-174",
        ("{python}", "-m", "unittest", "-v", "tests.test_parser_runtime.ParserRuntimeTests"),
        (
            "Project verification rule: after changing parser/runtime.py, always run "
            "`python -m unittest -v tests.test_parser_runtime.ParserRuntimeTests` "
            "before reporting completion."
        ),
        ("parser", "verification", "rule"),
        target_file="parser/runtime.py",
        target_before='MODE = "strict"\nDESCRIPTION = "old"\n',
        target_after='MODE = "strict"\nDESCRIPTION = "current"\n',
        target_expected='DESCRIPTION = "current"',
        learning_prompt=(
            "First read project-policy.md. Then change MODE in parser/runtime.py from "
            "legacy to strict, execute the policy-required verifier, and report its marker."
        ),
        learning_file="parser/runtime.py",
        learning_before='MODE = "legacy"\nDESCRIPTION = "old"\n',
        learning_after='MODE = "strict"\nDESCRIPTION = "old"\n',
        learning_failure=False,
    ),
    Family(
        "ledger-verification-rule",
        "verification-rule",
        "learned",
        BASE_PACKAGE_FILES
        + (
            ("ledger/__init__.py", ""),
            (
                "project-policy.md",
                "# Ledger verification\n\nAfter changing ledger/config.py, always run python -m unittest -v tests.test_ledger_config.LedgerConfigTests. This compatibility check is required before completion.\n",
            ),
            (
                "tests/test_ledger_config.py",
                _constant_test(
                    "ledger.config",
                    "FORMAT",
                    "LedgerConfigTests",
                    'self.assertEqual(FORMAT, "stable-v2")',
                    "VERIFY-LEDGER-642",
                ),
            ),
        ),
        (
            "Update NOTE in ledger/config.py from draft to final, follow the established "
            "verification rule, and report the exact compatibility marker."
        ),
        "VERIFY-LEDGER-642",
        ("{python}", "-m", "unittest", "-v", "tests.test_ledger_config.LedgerConfigTests"),
        (
            "Project verification rule: after changing ledger/config.py, always run "
            "`python -m unittest -v tests.test_ledger_config.LedgerConfigTests` "
            "before completion."
        ),
        ("ledger", "compatibility", "verification"),
        target_file="ledger/config.py",
        target_before='FORMAT = "stable-v2"\nNOTE = "draft"\n',
        target_after='FORMAT = "stable-v2"\nNOTE = "final"\n',
        target_expected='NOTE = "final"',
        learning_prompt=(
            "First read project-policy.md. Then change FORMAT in ledger/config.py from "
            "legacy to stable-v2, execute the policy-required verifier, and report its marker."
        ),
        learning_file="ledger/config.py",
        learning_before='FORMAT = "legacy"\nNOTE = "draft"\n',
        learning_after='FORMAT = "stable-v2"\nNOTE = "draft"\n',
    ),
    Family(
        "cache-verification-rule",
        "verification-rule",
        "seeded",
        BASE_PACKAGE_FILES
        + (
            ("cache/__init__.py", ""),
            (
                "tests/test_cache_config.py",
                _constant_test(
                    "cache.config",
                    "POLICY",
                    "CacheConfigTests",
                    'self.assertEqual(POLICY, "bounded")',
                    "VERIFY-CACHE-357",
                ),
            ),
        ),
        (
            "Update NOTE in cache/config.py from pending to reviewed and follow the "
            "established cache verification rule. Report its exact marker."
        ),
        "VERIFY-CACHE-357",
        ("{python}", "-m", "unittest", "-v", "tests.test_cache_config.CacheConfigTests"),
        (
            "Project verification rule: after changing cache/config.py, run "
            "`python -m unittest -v tests.test_cache_config.CacheConfigTests` "
            "before completion."
        ),
        ("cache", "verification", "rule"),
        target_file="cache/config.py",
        target_before='POLICY = "bounded"\nNOTE = "pending"\n',
        target_after='POLICY = "bounded"\nNOTE = "reviewed"\n',
        target_expected='NOTE = "reviewed"',
    ),
)

FAMILIES_V1 = FAMILIES
_SESSION_FIXED_V2 = _session_module(fixed=True) + "# Verified expiry-before-lookup order.\n"
FAMILIES = tuple(
    replace(
        family,
        target_after=_SESSION_FIXED_V2,
        learning_after=_SESSION_FIXED_V2,
    )
    if family.family_id == "expired-session-repair"
    else family
    for family in FAMILIES_V1
)


def _oracle(oracle_id: str, kind: str, **values: object) -> dict[str, object]:
    return {"id": oracle_id, "kind": kind, **values}


def _initial_files(family: Family, *, warm: bool) -> dict[str, str]:
    files = {"README.md": "# Non-Path Persistent Memory Study\n"}
    files.update(dict(family.common_files))
    if family.target_file:
        files[family.target_file] = family.target_before
    if family.learning_file:
        files[family.learning_file] = (
            family.learning_before if warm else family.learning_after
        )
    return files


def _case(
    family: Family,
    *,
    block: int,
    condition: str,
    pair_order: int,
    condition_order: int,
) -> dict[str, object]:
    warm = condition == "warm"
    learned = family.lesson_mode == "learned"
    turns: list[dict[str, object]] = [{"prompt": family.target_prompt}]
    if warm and learned:
        turns.insert(0, {"prompt": family.learning_prompt})

    oracles = [
        _oracle("run-completed", "all_runs_completed"),
        _oracle("canonical-success", "canonical_success"),
        _oracle(
            "verification-ran",
            "tool_succeeded",
            toolName="run_command",
            min=1,
            everyTurn=True,
        ),
    ]
    if family.stratum == "command-recovery":
        oracles.append(_oracle("no-source-edits", "no_source_edits"))
    if warm and learned and family.learning_failure:
        oracles.append(
            _oracle(
                "learning-failure",
                "tool_failed",
                toolName="run_command",
                min=1,
            )
        )
    if warm and learned:
        oracles.append(_oracle("lesson-written", "memory_written"))
    if warm:
        oracles.append(_oracle("lesson-injected", "memory_injected", min=1))
    if family.target_file:
        oracles.append(
            _oracle(
                "target-content",
                "file_contains",
                path=family.target_file,
                text=family.target_expected,
            )
        )
    oracles.extend(
        [
            _oracle(
                "target-verifier",
                "command",
                argv=list(family.target_verifier),
                exitCode=0,
                timeoutSeconds=30,
            ),
            _oracle(
                "target-marker",
                "response_contains",
                values=[family.target_marker],
            ),
        ]
    )

    authorized_paths = [
        path
        for path in (family.learning_file, family.target_file)
        if path
    ]
    case: dict[str, object] = {
        "id": f"npmem-b{block}-{family.family_id}-{condition}",
        "category": f"non-path-memory-{family.stratum}",
        "promptClass": "non-path-persistent-memory-study",
        "executionMode": "headless-live",
        "fixtureId": "non-path-memory-synthetic-v1",
        "mutability": "write" if authorized_paths else "read_only",
        "authorizedPaths": authorized_paths,
        "files": _initial_files(family, warm=warm and learned),
        "turns": turns,
        "oracles": oracles,
        "oracleIds": [str(oracle["id"]) for oracle in oracles],
        "study": {
            "familyId": family.family_id,
            "stratum": family.stratum,
            "lessonMode": family.lesson_mode,
            "block": block,
            "condition": condition,
            "pairOrder": pair_order,
            "conditionOrder": condition_order,
            "targetTurnIndex": len(turns) - 1,
            "targetMarker": family.target_marker,
            "targetTool": "run_command",
            "targetFile": family.target_file,
            "targetExpected": family.target_expected,
            "learningFile": family.learning_file,
            "learningFailureRequired": family.learning_failure,
        },
    }
    if warm and not learned:
        case["memoryEntries"] = [
            {
                "category": family.stratum,
                "content": family.seed_memory,
                "tags": list(family.tags),
            }
        ]
    return case


def build_manifest(*, suite_version: str = "v2") -> dict[str, Any]:
    if suite_version == "v1":
        suite_id = SUITE_ID_V1
        study_families = FAMILIES_V1
    elif suite_version == "v2":
        suite_id = SUITE_ID
        study_families = FAMILIES
    else:
        raise ValueError(f"unsupported suite version: {suite_version}")
    cases: list[dict[str, object]] = []
    for block, seed in enumerate(BLOCK_SEEDS, start=1):
        families = list(study_families)
        random.Random(seed).shuffle(families)
        for pair_order, family in enumerate(families, start=1):
            warm_first = (pair_order + block) % 2 == 0
            conditions = ("warm", "cold") if warm_first else ("cold", "warm")
            for condition_order, condition in enumerate(conditions, start=1):
                cases.append(
                    _case(
                        family,
                        block=block,
                        condition=condition,
                        pair_order=pair_order,
                        condition_order=condition_order,
                    )
                )
    task_count = sum(len(case["turns"]) for case in cases)
    if len(cases) != 72 or task_count != 96:
        raise AssertionError(
            f"expected 72 cases / 96 Turns, got {len(cases)} / {task_count}"
        )
    return {
        "schemaVersion": 1,
        "suiteId": suite_id,
        "description": (
            "Pre-registered paired live study of non-path persistent engineering "
            "lessons across commands, code repairs, constraints and verification rules."
        ),
        "caseCount": len(cases),
        "taskCount": task_count,
        "familyCount": len(study_families),
        "blockCount": len(BLOCK_SEEDS),
        "pairCount": len(study_families) * len(BLOCK_SEEDS),
        "primaryUnit": "family",
        "blockSeeds": list(BLOCK_SEEDS),
        "cases": cases,
    }


def _write_frozen(path: Path, document: dict[str, Any]) -> None:
    serialized = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != serialized:
        raise FileExistsError(f"refusing to overwrite changed frozen manifest: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--suite-version",
        choices=("v1", "v2"),
        default="v2",
    )
    args = parser.parse_args()
    _write_frozen(args.output, build_manifest(suite_version=args.suite_version))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
