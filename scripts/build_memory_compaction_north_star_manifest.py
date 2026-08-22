#!/usr/bin/env python3
"""Build the pre-registered 20-task Memory/compaction live manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SUITE_ID = "minicode-memory-compaction-live-20-2026-08-21-v3"
ADDENDUM_SUITE_ID = "minicode-memory-compaction-cross-boundary-4-2026-08-21-v1"


def _oracle(oracle_id: str, kind: str, **values: object) -> dict[str, object]:
    return {"id": oracle_id, "kind": kind, **values}


def _read_oracles(*extra: dict[str, object]) -> list[dict[str, object]]:
    return [
        _oracle("run-completed", "all_runs_completed"),
        _oracle("canonical-success", "canonical_success"),
        _oracle("no-source-edits", "no_source_edits"),
        *extra,
    ]


def _memory_oracles(*extra: dict[str, object]) -> list[dict[str, object]]:
    return _read_oracles(
        _oracle(
            "source-read",
            "tool_succeeded",
            toolName="read_file",
            min=1,
            everyTurn=True,
        ),
        *extra,
    )


def _case(
    case_id: str,
    category: str,
    *,
    files: dict[str, str],
    turns: list[dict[str, object]],
    oracles: list[dict[str, object]],
    memory_entries: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": case_id,
        "category": category,
        "promptClass": category,
        "executionMode": "headless-live",
        "fixtureId": "memory-compaction-synthetic-v1",
        "mutability": "read_only",
        "authorizedPaths": [],
        "files": {"README.md": "# Memory and Compaction Acceptance\n", **files},
        "turns": turns,
        "oracles": oracles,
        "oracleIds": [str(oracle["id"]) for oracle in oracles],
    }
    if memory_entries:
        value["memoryEntries"] = memory_entries
    return value


def _long_history(*statements: tuple[int, str]) -> list[dict[str, str]]:
    """Create long, non-secret history with facts at pre-registered positions."""
    by_position = dict(statements)
    history: list[dict[str, str]] = []
    filler = " bounded implementation discussion" * 42
    for index in range(18):
        statement = by_position.get(index, f"Historical note {index} is non-authoritative.")
        history.append(
            {
                "role": "user",
                "content": f"Record this project statement: {statement}.{filler}",
            }
        )
        history.append(
            {
                "role": "assistant",
                "content": f"Recorded statement {index}. No files changed.{filler}",
            }
        )
    return history


def _memory_cases() -> list[dict[str, object]]:
    auth_files = {
        "backend/src/auth_policy.py": (
            '"""Gateway authentication policy."""\n'
            'POLICY_MARKER = "AUTH-POLICY-73"\n'
        )
    }
    runtime_files = {
        "app/config/runtime.toml": (
            'mode = "safe"\nrecovery_marker = "RUNTIME-CONFIG-46"\n'
        )
    }
    deploy_files = {
        "ops/runbooks/deploy.md": (
            "# Deployment rollback\n\nExact rollback marker: DEPLOY-ROLLBACK-81.\n"
        )
    }
    schema_files = {
        "services/data/schema_contract.yaml": (
            "version: 3\ncompatibility_marker: SCHEMA-COMPAT-62\n"
        )
    }
    return [
        _case(
            "memory-chain-auth",
            "persistent-memory-learning",
            files=auth_files,
            turns=[
                {
                    "prompt": (
                        "Your first tool call must be read_file with exactly the path "
                        "src/auth_policy.py; do not inspect or list files before that "
                        "call. This pre-registered path is intentionally absent. After "
                        "the tool reports the failure, inspect the workspace, recover "
                        "to the real file, and report its exact authentication policy "
                        "marker. Do not edit files."
                    )
                },
                {
                    "prompt": (
                        "Re-check the gateway authentication policy in this project and "
                        "return its exact policy marker. Do not edit files."
                    )
                },
            ],
            oracles=_memory_oracles(
                _oracle(
                    "source-failure",
                    "tool_failed",
                    toolName="read_file",
                    min=1,
                ),
                _oracle("lesson-written", "memory_written"),
                _oracle("lesson-injected", "memory_injected", min=1),
                _oracle(
                    "marker-retained",
                    "response_contains",
                    values=["AUTH-POLICY-73"],
                ),
            ),
        ),
        _case(
            "memory-cold-auth",
            "persistent-memory-control",
            files=auth_files,
            turns=[
                {
                    "prompt": (
                        "Re-check the gateway authentication policy in this project and "
                        "return its exact policy marker. Do not edit files."
                    )
                }
            ],
            oracles=_memory_oracles(
                _oracle(
                    "marker-found",
                    "response_contains",
                    values=["AUTH-POLICY-73"],
                )
            ),
        ),
        _case(
            "memory-chain-runtime",
            "persistent-memory-learning",
            files=runtime_files,
            turns=[
                {
                    "prompt": (
                        "Your first tool call must be read_file with exactly the path "
                        "config/runtime.toml; do not inspect or list files before that "
                        "call. This pre-registered path is intentionally absent. After "
                        "the tool reports the failure, inspect the workspace, recover "
                        "to the real file, and report its exact runtime recovery marker. "
                        "Do not edit files."
                    )
                },
                {
                    "prompt": (
                        "Inspect the runtime configuration used by this application and "
                        "return its exact recovery marker. Do not edit files."
                    )
                },
            ],
            oracles=_memory_oracles(
                _oracle(
                    "source-failure",
                    "tool_failed",
                    toolName="read_file",
                    min=1,
                ),
                _oracle("lesson-written", "memory_written"),
                _oracle("lesson-injected", "memory_injected", min=1),
                _oracle(
                    "marker-retained",
                    "response_contains",
                    values=["RUNTIME-CONFIG-46"],
                ),
            ),
        ),
        _case(
            "memory-cold-runtime",
            "persistent-memory-control",
            files=runtime_files,
            turns=[
                {
                    "prompt": (
                        "Inspect the runtime configuration used by this application and "
                        "return its exact recovery marker. Do not edit files."
                    )
                }
            ],
            oracles=_memory_oracles(
                _oracle(
                    "marker-found",
                    "response_contains",
                    values=["RUNTIME-CONFIG-46"],
                )
            ),
        ),
        _case(
            "memory-warm-deploy",
            "persistent-memory-warm",
            files=deploy_files,
            turns=[
                {
                    "prompt": (
                        "Locate the deployment rollback runbook and report its exact "
                        "rollback marker. Do not edit files."
                    )
                }
            ],
            memory_entries=[
                {
                    "category": "verified-recovery",
                    "content": (
                        "Verified recovery for the deployment rollback runbook: "
                        "docs/deploy.md is absent; read ops/runbooks/deploy.md directly."
                    ),
                    "tags": ["deployment", "rollback", "runbook"],
                }
            ],
            oracles=_memory_oracles(
                _oracle("lesson-injected", "memory_injected", min=1),
                _oracle(
                    "marker-found",
                    "response_contains",
                    values=["DEPLOY-ROLLBACK-81"],
                ),
            ),
        ),
        _case(
            "memory-cold-deploy",
            "persistent-memory-control",
            files=deploy_files,
            turns=[
                {
                    "prompt": (
                        "Locate the deployment rollback runbook and report its exact "
                        "rollback marker. Do not edit files."
                    )
                }
            ],
            oracles=_memory_oracles(
                _oracle(
                    "marker-found",
                    "response_contains",
                    values=["DEPLOY-ROLLBACK-81"],
                )
            ),
        ),
        _case(
            "memory-warm-schema",
            "persistent-memory-warm",
            files=schema_files,
            turns=[
                {
                    "prompt": (
                        "Find the data schema contract and return its exact compatibility "
                        "marker. Do not edit files."
                    )
                }
            ],
            memory_entries=[
                {
                    "category": "verified-recovery",
                    "content": (
                        "Verified recovery for the data schema contract: "
                        "schema/contract.yaml is absent; use "
                        "services/data/schema_contract.yaml."
                    ),
                    "tags": ["data", "schema", "contract"],
                }
            ],
            oracles=_memory_oracles(
                _oracle("lesson-injected", "memory_injected", min=1),
                _oracle(
                    "marker-found",
                    "response_contains",
                    values=["SCHEMA-COMPAT-62"],
                ),
            ),
        ),
        _case(
            "memory-cold-schema",
            "persistent-memory-control",
            files=schema_files,
            turns=[
                {
                    "prompt": (
                        "Find the data schema contract and return its exact compatibility "
                        "marker. Do not edit files."
                    )
                }
            ],
            oracles=_memory_oracles(
                _oracle(
                    "marker-found",
                    "response_contains",
                    values=["SCHEMA-COMPAT-62"],
                )
            ),
        ),
    ]


def _context_case(
    case_id: str,
    marker_values: list[str],
    prompt: str,
    *,
    history: list[dict[str, str]],
    files: dict[str, str] | None = None,
    context_window: int = 4_000,
    extra_oracles: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return _case(
        case_id,
        "context-compaction",
        files=files or {},
        turns=[
            {
                "prompt": prompt,
                "initialHistory": history,
                "contextWindow": context_window,
            }
        ],
        oracles=_read_oracles(
            _oracle("context-compacted", "context_compaction_count", min=1),
            _oracle("critical-state-retained", "response_contains", values=marker_values),
            *(extra_oracles or []),
        ),
    )


def _context_cases() -> list[dict[str, object]]:
    large_file = (
        "ordinary-record\n" * 700
        + "critical middle marker FILE-MIDDLE-64\n"
        + "ordinary-record\n" * 700
    )
    skill_body = (
        "Follow bounded review discipline.\n" * 360
        + "The exact protected skill marker is SKILL-PINNED-77.\n"
        + "Continue following bounded review discipline.\n" * 360
    )
    cases = [
        _context_case(
            "context-goal-retention",
            ["GOAL-NOVA-31"],
            "Return the exact release goal codename from the earlier discussion.",
            history=_long_history((0, "The release goal codename is GOAL-NOVA-31")),
        ),
        _context_case(
            "context-fact-retention",
            ["FACT-CACHE-42"],
            "Return the exact cache fact marker that earlier verification established.",
            history=_long_history((8, "Verified cache namespace fact: FACT-CACHE-42")),
        ),
        _context_case(
            "context-rejected-retention",
            ["REJECT-SQLITE-58"],
            "Return the exact marker for the approach that was explicitly rejected.",
            history=_long_history(
                (3, "SQLite was rejected due to lock contention: REJECT-SQLITE-58")
            ),
        ),
        _context_case(
            "context-constraint-retention",
            ["CONSTRAINT-NETZERO-69"],
            "Return the exact non-negotiable constraint marker from the prior discussion.",
            history=_long_history(
                (15, "Non-negotiable constraint: no network writes, CONSTRAINT-NETZERO-69")
            ),
        ),
        _context_case(
            "context-decision-retention",
            ["DECISION-NATS-22"],
            "Return the exact accepted architecture decision marker.",
            history=_long_history(
                (11, "Accepted architecture decision: use the queue, DECISION-NATS-22")
            ),
        ),
        _context_case(
            "context-combined-retention",
            ["GOAL-TRIO-14", "FACT-TRIO-27", "REJECT-TRIO-39"],
            (
                "Return exactly the earlier goal, verified-fact, and rejected-approach "
                "markers in one short line."
            ),
            history=_long_history(
                (0, "Goal marker: GOAL-TRIO-14"),
                (7, "Verified fact marker: FACT-TRIO-27"),
                (14, "Rejected approach marker: REJECT-TRIO-39"),
            ),
        ),
        _context_case(
            "context-large-file-result",
            ["FILE-MIDDLE-64"],
            (
                "Read data/large_record.txt, find its exact critical middle marker, "
                "and return that marker. Do not edit files."
            ),
            history=_long_history((1, "The active task requires inspecting the large record")),
            files={"data/large_record.txt": large_file},
            context_window=2_000,
        ),
        _context_case(
            "context-loaded-skill-result",
            ["SKILL-PINNED-77"],
            (
                "Use Skill compaction-guardian. Return the exact protected marker from "
                "that Skill and do not edit files."
            ),
            history=_long_history((2, "The required workflow is a compaction review")),
            files={
                ".mini-code/skills/compaction-guardian/SKILL.md": (
                    "---\n"
                    "name: compaction-guardian\n"
                    "description: Use for compaction review and protected context checks.\n"
                    "keywords: [compaction, context, review]\n"
                    "---\n\n"
                    "# Compaction Guardian\n\n"
                    + skill_body
                )
            },
            context_window=2_000,
            extra_oracles=[
                _oracle(
                    "skill-loaded",
                    "skill_loaded",
                    qualifiedName="compaction-guardian",
                )
            ],
        ),
    ]
    multi_turn = _case(
        "context-two-round-continuity",
        "context-compaction",
        files={},
        turns=[
            {
                "prompt": "Recover and return the exact earlier release marker.",
                "initialHistory": _long_history(
                    (5, "The exact release marker is ROUND-CHAIN-91")
                ),
                "contextWindow": 4_000,
                "carryHistory": True,
            },
            {
                "prompt": (
                    "Without reading files, repeat the exact release marker recovered "
                    "in the prior task."
                ),
                "contextWindow": 2_000,
            },
        ],
        oracles=_read_oracles(
            _oracle("two-compactions", "context_compaction_count", min=2),
            _oracle(
                "cross-round-marker-retained",
                "response_contains",
                values=["ROUND-CHAIN-91"],
            ),
        ),
    )
    return [*cases, multi_turn]


def build_manifest() -> dict[str, Any]:
    cases = [*_memory_cases(), *_context_cases()]
    task_count = sum(len(case["turns"]) for case in cases)
    if len(cases) != 17 or task_count != 20:
        raise AssertionError(
            f"expected 17 cases / 20 tasks, got {len(cases)} / {task_count}"
        )
    return {
        "schemaVersion": 1,
        "suiteId": SUITE_ID,
        "description": (
            "Twenty real-model tasks pre-registered for persistent Memory lessons "
            "and context-compaction retention."
        ),
        "caseCount": len(cases),
        "taskCount": task_count,
        "cases": cases,
    }


def build_addendum_manifest() -> dict[str, Any]:
    """Freeze four tasks that carry tool results through a later compaction."""
    skill_body = (
        "Preserve bounded context evidence.\n" * 260
        + "The after-compaction marker is SKILL-AFTER-COMPACT-88.\n"
        + "For the first task, respond with exactly READY-SKILL-12.\n"
        + "Continue preserving bounded context evidence.\n" * 260
    )
    file_body = (
        "ordinary cross-boundary record\n" * 260
        + "hidden_after_compaction: FILE-AFTER-COMPACT-74\n"
        + "ordinary cross-boundary record\n" * 260
    )
    cases = [
        _case(
            "context-skill-cross-boundary",
            "context-compaction-addendum",
            files={
                ".mini-code/skills/compaction-cross/SKILL.md": (
                    "---\n"
                    "name: compaction-cross\n"
                    "description: Use for cross-boundary compaction instruction tests.\n"
                    "keywords: [compaction, boundary, instruction]\n"
                    "---\n\n"
                    "# Cross-boundary Skill\n\n"
                    + skill_body
                )
            },
            turns=[
                {
                    "prompt": (
                        "Use Skill compaction-cross. Follow its first-task response "
                        "instruction exactly and do not edit files."
                    ),
                    "initialHistory": _long_history(
                        (4, "This task validates protected instruction continuity")
                    ),
                    "contextWindow": 2_000,
                    "carryHistory": True,
                },
                {
                    "prompt": (
                        "Do not call tools. Return the exact after-compaction marker "
                        "that appeared only in the previously loaded instructions."
                    ),
                    "contextWindow": 2_000,
                },
            ],
            oracles=_read_oracles(
                _oracle("two-compactions", "context_compaction_count", min=2),
                _oracle(
                    "skill-loaded",
                    "skill_loaded",
                    qualifiedName="compaction-cross",
                ),
                _oracle(
                    "skill-result-survived",
                    "response_contains",
                    values=["SKILL-AFTER-COMPACT-88"],
                ),
            ),
        ),
        _case(
            "context-file-cross-boundary",
            "context-compaction-addendum",
            files={"data/cross_boundary.txt": file_body},
            turns=[
                {
                    "prompt": (
                        "Read data/cross_boundary.txt, but for this first task respond "
                        "with exactly READY-FILE-15 and do not reveal its hidden marker."
                    ),
                    "initialHistory": _long_history(
                        (6, "This task validates tool-result continuity")
                    ),
                    "contextWindow": 2_000,
                    "carryHistory": True,
                },
                {
                    "prompt": (
                        "Do not call tools. Return the exact hidden_after_compaction "
                        "marker from the file content observed in the prior task."
                    ),
                    "contextWindow": 2_000,
                },
            ],
            oracles=_read_oracles(
                _oracle("two-compactions", "context_compaction_count", min=2),
                _oracle(
                    "file-result-survived",
                    "response_contains",
                    values=["FILE-AFTER-COMPACT-74"],
                ),
            ),
        ),
    ]
    task_count = sum(len(case["turns"]) for case in cases)
    if len(cases) != 2 or task_count != 4:
        raise AssertionError("expected two addendum cases / four tasks")
    return {
        "schemaVersion": 1,
        "suiteId": ADDENDUM_SUITE_ID,
        "description": (
            "Four supplemental real-model tasks carrying Skill/file tool results "
            "through a subsequent compaction boundary."
        ),
        "caseCount": len(cases),
        "taskCount": task_count,
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--addendum", action="store_true")
    args = parser.parse_args()
    document = build_addendum_manifest() if args.addendum else build_manifest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
