#!/usr/bin/env python3
"""Build the frozen large-scale live study for persistent Memory reuse."""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


SUITE_ID_V1 = "minicode-persistent-memory-large-live-2026-08-21-v1"
SUITE_ID_V2 = "minicode-persistent-memory-large-live-2026-08-21-v2"
SUITE_ID = "minicode-persistent-memory-large-live-2026-08-21-v3"
BLOCK_SEEDS = (202608211, 202608212, 202608213)


@dataclass(frozen=True, slots=True)
class Family:
    family_id: str
    stratum: str
    lesson_mode: str
    subject: str
    failed_path: str
    corrected_path: str
    marker: str
    marker_label: str
    target_prompt: str
    tags: tuple[str, ...]


FAMILIES_V1 = (
    Family(
        "auth-policy",
        "application-security",
        "learned",
        "gateway authentication policy",
        "src/auth_policy.py",
        "backend/src/auth_policy.py",
        "AUTH-POLICY-731",
        "authentication policy marker",
        "Read the gateway authentication policy and report its exact policy marker. Do not edit files.",
        ("authentication", "gateway", "policy"),
    ),
    Family(
        "billing-rules",
        "application-security",
        "learned",
        "billing validation rules",
        "config/billing_rules.yaml",
        "services/billing/config/rules.yaml",
        "BILLING-RULE-284",
        "billing validation marker",
        "Inspect the billing validation rules and return their exact validation marker. Do not edit files.",
        ("billing", "validation", "rules"),
    ),
    Family(
        "feature-flags",
        "application-security",
        "seeded",
        "feature flag registry",
        "config/features.toml",
        "platform/config/feature_flags.toml",
        "FEATURE-FLAG-915",
        "feature flag marker",
        "Locate the feature flag registry and report its exact registry marker. Do not edit files.",
        ("feature", "flags", "registry"),
    ),
    Family(
        "api-contract",
        "application-security",
        "seeded",
        "public API contract",
        "docs/api.yaml",
        "services/gateway/contracts/public_api.yaml",
        "API-CONTRACT-642",
        "API contract marker",
        "Find the public API contract and return its exact contract marker. Do not edit files.",
        ("public", "api", "contract"),
    ),
    Family(
        "runtime-config",
        "operations",
        "learned",
        "application runtime configuration",
        "config/runtime.toml",
        "app/config/runtime.toml",
        "RUNTIME-CONFIG-463",
        "runtime recovery marker",
        "Inspect the runtime configuration used by this application and return its exact recovery marker. Do not edit files.",
        ("runtime", "application", "configuration"),
    ),
    Family(
        "observability",
        "operations",
        "learned",
        "telemetry collector configuration",
        "config/telemetry.yaml",
        "infra/observability/collector.yaml",
        "TELEMETRY-857",
        "telemetry collector marker",
        "Locate the telemetry collector configuration and report its exact collector marker. Do not edit files.",
        ("telemetry", "collector", "observability"),
    ),
    Family(
        "deploy-runbook",
        "operations",
        "seeded",
        "deployment rollback runbook",
        "docs/deploy.md",
        "ops/runbooks/deploy.md",
        "DEPLOY-ROLLBACK-812",
        "rollback marker",
        "Locate the deployment rollback runbook and report its exact rollback marker. Do not edit files.",
        ("deployment", "rollback", "runbook"),
    ),
    Family(
        "queue-policy",
        "operations",
        "seeded",
        "worker queue policy",
        "config/queue.toml",
        "services/worker/config/queue.toml",
        "QUEUE-POLICY-394",
        "queue policy marker",
        "Inspect the worker queue policy and return its exact policy marker. Do not edit files.",
        ("worker", "queue", "policy"),
    ),
    Family(
        "migration-plan",
        "data-governance",
        "learned",
        "database migration runbook",
        "docs/migration.md",
        "data/migrations/runbook.md",
        "MIGRATION-PLAN-526",
        "migration plan marker",
        "Find the database migration runbook and report its exact plan marker. Do not edit files.",
        ("database", "migration", "runbook"),
    ),
    Family(
        "retention-policy",
        "data-governance",
        "learned",
        "data retention policy",
        "config/retention.yaml",
        "governance/data/retention.yaml",
        "RETENTION-748",
        "retention policy marker",
        "Read the data retention policy and return its exact policy marker. Do not edit files.",
        ("data", "retention", "governance"),
    ),
    Family(
        "schema-contract",
        "data-governance",
        "seeded",
        "data schema contract",
        "schema/contract.yaml",
        "services/data/schema_contract.yaml",
        "SCHEMA-COMPAT-623",
        "schema compatibility marker",
        "Find the data schema contract and return its exact compatibility marker. Do not edit files.",
        ("data", "schema", "contract"),
    ),
    Family(
        "cache-policy",
        "data-governance",
        "seeded",
        "cache eviction policy",
        "config/cache.yaml",
        "platform/cache/eviction.yaml",
        "CACHE-EVICT-269",
        "cache eviction marker",
        "Locate the cache eviction policy and report its exact eviction marker. Do not edit files.",
        ("cache", "eviction", "policy"),
    ),
    Family(
        "test-matrix",
        "developer-platform",
        "learned",
        "quality assurance test matrix",
        "tests/matrix.yaml",
        "qa/config/test_matrix.yaml",
        "TEST-MATRIX-481",
        "test matrix marker",
        "Inspect the quality assurance test matrix and return its exact matrix marker. Do not edit files.",
        ("quality", "test", "matrix"),
    ),
    Family(
        "package-map",
        "developer-platform",
        "learned",
        "build package map",
        "packages/map.json",
        "build/metadata/package_map.json",
        "PACKAGE-MAP-357",
        "package map marker",
        "Locate the build package map and report its exact package marker. Do not edit files.",
        ("build", "package", "metadata"),
    ),
    Family(
        "plugin-registry",
        "developer-platform",
        "seeded",
        "extension plugin registry",
        "config/plugins.json",
        "extensions/registry/plugins.json",
        "PLUGIN-REGISTRY-936",
        "plugin registry marker",
        "Find the extension plugin registry and return its exact registry marker. Do not edit files.",
        ("extension", "plugin", "registry"),
    ),
    Family(
        "model-routing",
        "developer-platform",
        "seeded",
        "AI model routing configuration",
        "config/models.yaml",
        "ai/runtime/model_routes.yaml",
        "MODEL-ROUTE-174",
        "model routing marker",
        "Inspect the AI model routing configuration and report its exact routing marker. Do not edit files.",
        ("model", "routing", "configuration"),
    ),
)

_V2_CORRECTED_PATHS = {
    "billing-rules": "services/billing/config/billing_rules.yaml",
    "observability": "infra/observability/telemetry.yaml",
    "migration-plan": "data/migrations/migration.md",
    "test-matrix": "qa/config/matrix.yaml",
    "package-map": "build/metadata/map.json",
}
FAMILIES_V2 = tuple(
    replace(
        family,
        corrected_path=_V2_CORRECTED_PATHS.get(
            family.family_id,
            family.corrected_path,
        ),
    )
    for family in FAMILIES_V1
)

_V3_CORRECTED_PATHS = {
    "observability": "infra/observability/config/telemetry.yaml",
    "migration-plan": "data/migrations/docs/migration.md",
    "retention-policy": "governance/data/config/retention.yaml",
    "test-matrix": "qa/config/tests/matrix.yaml",
    "package-map": "build/metadata/packages/map.json",
}
FAMILIES = tuple(
    replace(
        family,
        corrected_path=_V3_CORRECTED_PATHS.get(
            family.family_id,
            family.corrected_path,
        ),
    )
    for family in FAMILIES_V2
)


def _oracle(oracle_id: str, kind: str, **values: object) -> dict[str, object]:
    return {"id": oracle_id, "kind": kind, **values}


def _oracles(*extra: dict[str, object]) -> list[dict[str, object]]:
    return [
        _oracle("run-completed", "all_runs_completed"),
        _oracle("canonical-success", "canonical_success"),
        _oracle("no-source-edits", "no_source_edits"),
        _oracle(
            "source-read",
            "tool_succeeded",
            toolName="read_file",
            min=1,
            everyTurn=True,
        ),
        *extra,
    ]


def _file_content(family: Family) -> str:
    return (
        f"# Synthetic {family.subject}\n"
        f"{family.marker_label}: {family.marker}\n"
    )


def _learning_prompt(family: Family) -> str:
    return (
        "Your first tool call must be read_file with exactly the path "
        f"{family.failed_path}; do not inspect or list files before that call. "
        "This pre-registered path is intentionally absent. After the tool reports "
        f"the failure, inspect the workspace, recover to the real {family.subject}, "
        f"and report its exact {family.marker_label}. Do not edit files."
    )


def _seed_memory(family: Family) -> dict[str, object]:
    return {
        "category": "verified-recovery",
        "content": (
            f"Verified recovery for the {family.subject}: `{family.failed_path}` "
            "is the failed target and must not be retried. The corrected, verified "
            f"target is `{family.corrected_path}`; read it directly before any "
            "repository discovery."
        ),
        "tags": list(family.tags),
    }


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
        turns.insert(0, {"prompt": _learning_prompt(family)})
    extras: list[dict[str, object]] = [
        _oracle("marker-found", "response_contains", values=[family.marker])
    ]
    if warm:
        extras.insert(0, _oracle("lesson-injected", "memory_injected", min=1))
    if warm and learned:
        extras[0:0] = [
            _oracle("source-failure", "tool_failed", toolName="read_file", min=1),
            _oracle("lesson-written", "memory_written"),
        ]
    oracles = _oracles(*extras)
    case: dict[str, object] = {
        "id": f"pmem-b{block}-{family.family_id}-{condition}",
        "category": (
            "persistent-memory-learning"
            if warm and learned
            else "persistent-memory-warm"
            if warm
            else "persistent-memory-control"
        ),
        "promptClass": "persistent-memory-large-study",
        "executionMode": "headless-live",
        "fixtureId": "persistent-memory-large-synthetic-v1",
        "mutability": "read_only",
        "authorizedPaths": [],
        "files": {
            "README.md": "# Persistent Memory Large Study\n",
            family.corrected_path: _file_content(family),
        },
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
            "failedPath": family.failed_path,
            "correctedPath": family.corrected_path,
            "marker": family.marker,
        },
    }
    if warm and not learned:
        case["memoryEntries"] = [_seed_memory(family)]
    return case


def build_manifest(*, suite_version: str = "v3") -> dict[str, Any]:
    if suite_version == "v1":
        suite_id = SUITE_ID_V1
        study_families = FAMILIES_V1
    elif suite_version == "v2":
        suite_id = SUITE_ID_V2
        study_families = FAMILIES_V2
    elif suite_version == "v3":
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
    if len(cases) != 96 or task_count != 120:
        raise AssertionError(
            f"expected 96 cases / 120 turns, got {len(cases)} / {task_count}"
        )
    return {
        "schemaVersion": 1,
        "suiteId": suite_id,
        "description": (
            "Pre-registered blocked warm/cold study of project-scoped persistent "
            "Memory reuse across sixteen synthetic task families."
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
        choices=("v1", "v2", "v3"),
        default="v3",
    )
    args = parser.parse_args()
    document = build_manifest(suite_version=args.suite_version)
    _write_frozen(args.output, document)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
