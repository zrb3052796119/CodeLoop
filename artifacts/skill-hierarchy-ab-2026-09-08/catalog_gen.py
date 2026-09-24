"""Deterministic synthetic skill-catalog generator for the hierarchical A/B harness.

Writes a ``.mini-code/skills/`` tree plus a ``catalog-manifest.json`` into a
workspace directory. Two scales:

- ``S100``: 12 core directories x ~8 skills + 4 root-level skills (~100 skills
  before decoys). Every directory shares 6 common patterns (retry-backoff,
  circuit-breaker, pagination, idempotency, rate-limiting, timeout-tuning) so
  near-neighbour confusion is built in; directory-specific patterns are added
  so every task target exists, then deterministic filler tops each directory
  up to 8 skills.
- ``S1000``: 50 directories x 20 patterns + 10 root-level skills (~1010).

Confusability is structural: the same pattern (e.g. retry-backoff) exists in
many directories with descriptions that share most of their vocabulary and
differ only in the domain object. On top of that, every positive task in
``tasks.json`` gets one hand-authored plausible-but-wrong decoy skill.

Variants:
- ``base``: as generated.
- ``wrong-placement``: each task's target skill is moved into a
  wrong-but-plausible directory (per-task mapping below), displacing any
  resident skill of the same name. The moved skill keeps its original
  (correct-domain) description and body.
- ``stale-summary``: SKILL_DIR.md descriptions of directories that contain a
  task target are rewritten to mention only non-target topics.

Everything is deterministic for a given ``--seed``. This file is stdlib-only
and never imports minicode.

Usage:
    python catalog_gen.py --scale S100 --variant base --tasks tasks.json --out <workspace>
    python catalog_gen.py --all --tasks tasks.json --out-root <dir>
    python catalog_gen.py --validate --tasks tasks.json --manifest <workspace>/catalog-manifest.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

DEFAULT_SEED = 20260908

# ---------------------------------------------------------------------------
# Domain and pattern spec
# ---------------------------------------------------------------------------

# Core 12 directories: name -> domain object phrase used in descriptions.
CORE_DOMAINS: dict[str, str] = {
    "http-client": "the outbound HTTP client layer",
    "message-queue": "the message queue brokers and consumers",
    "database": "the relational database access layer",
    "job-scheduler": "the cron/background job scheduler",
    "graphql-api": "the GraphQL API server",
    "object-storage": "the object/blob storage service",
    "search-index": "the full-text search cluster",
    "auth-service": "the authentication and token service",
    "payment-gateway": "the payment provider integration",
    "notification-service": "the email/SMS/push notification service",
    "ci-pipeline": "the CI/CD pipeline",
    "cache-layer": "the application cache layer",
}

# 38 extra directories for S1000: name -> domain object phrase.
EXTRA_DOMAINS: dict[str, str] = {
    "api-gateway": "the edge API gateway",
    "service-mesh": "the service mesh data plane",
    "config-service": "the centralized configuration service",
    "secrets-manager": "the secrets management backend",
    "identity-provider": "the upstream identity provider",
    "session-store": "the distributed session store",
    "tenant-manager": "the multi-tenant management service",
    "billing-service": "the billing and invoicing service",
    "subscription-billing": "the subscription billing engine",
    "fraud-detection": "the fraud detection pipeline",
    "order-processing": "the order processing pipeline",
    "inventory-service": "the inventory tracking service",
    "shipping-integration": "the shipping carrier integrations",
    "crm-connector": "the CRM synchronization connector",
    "user-directory": "the user directory service",
    "feature-flags": "the feature flag platform",
    "audit-log": "the audit logging pipeline",
    "analytics-pipeline": "the product analytics pipeline",
    "data-warehouse": "the analytical data warehouse",
    "etl-jobs": "the ETL job fleet",
    "report-generator": "the scheduled report generator",
    "document-parser": "the document parsing service",
    "image-pipeline": "the image processing pipeline",
    "video-transcoding": "the video transcoding farm",
    "email-templates": "the transactional email template system",
    "sms-gateway": "the SMS gateway integration",
    "push-service": "the mobile push notification service",
    "webhook-dispatcher": "the outbound webhook dispatcher",
    "recommendation-engine": "the recommendation serving engine",
    "ab-testing": "the A/B experiment platform",
    "logging-pipeline": "the log collection pipeline",
    "metrics-collector": "the metrics collection stack",
    "tracing-instrumentation": "the distributed tracing instrumentation",
    "alert-manager": "the alert routing manager",
    "incident-response": "the incident response tooling",
    "backup-restore": "the backup and restore system",
    "disaster-recovery": "the disaster recovery tooling",
    "capacity-planning": "the capacity planning toolkit",
}

# Pattern name -> (description template, short topic phrase, keywords).
PATTERNS: dict[str, tuple[str, str, list[str]]] = {
    "retry-backoff": (
        "Configure retry and exponential backoff for {obj}; covers jitter, "
        "attempt budgets, and which errors are safe to retry.",
        "retry and backoff",
        ["retry", "backoff", "jitter", "transient-errors"],
    ),
    "circuit-breaker": (
        "Add a circuit breaker around calls from {obj}; covers failure "
        "thresholds, half-open probing, and fallback behavior.",
        "circuit breaking",
        ["circuit-breaker", "fail-fast", "fallback"],
    ),
    "connection-pooling": (
        "Size and tune connection pools for {obj}; covers pool exhaustion, "
        "idle reaping, and leak detection.",
        "connection pooling",
        ["pool", "connections", "leaks"],
    ),
    "pagination": (
        "Implement pagination over list APIs of {obj}; covers cursor versus "
        "offset trade-offs and stable ordering.",
        "pagination",
        ["pagination", "cursor", "offset", "ordering"],
    ),
    "idempotency": (
        "Make operations in {obj} idempotent; covers idempotency keys, dedup "
        "windows, and safe replay.",
        "idempotency",
        ["idempotency", "dedup", "replay", "exactly-once"],
    ),
    "rate-limiting": (
        "Apply rate limiting and throttling to {obj}; covers token buckets, "
        "quota tiers, and backoff signaling.",
        "rate limiting",
        ["rate-limit", "throttle", "quota", "token-bucket"],
    ),
    "caching-strategy": (
        "Design the caching policy for {obj}; covers TTL selection, "
        "invalidation, and stampede protection.",
        "caching policy",
        ["cache", "ttl", "invalidation", "stampede"],
    ),
    "timeout-tuning": (
        "Set timeout budgets for {obj}; covers connect/read splits, deadline "
        "propagation, and slow-call diagnostics.",
        "timeout budgets",
        ["timeout", "deadline", "slow-calls"],
    ),
    "batch-processing": (
        "Batch operations in {obj} for throughput; covers batch sizing, "
        "partial-failure handling, and ordering guarantees.",
        "batching",
        ["batch", "throughput", "partial-failure"],
    ),
    "error-mapping": (
        "Map errors from {obj} to application-level exceptions; covers error "
        "taxonomies and retryable-versus-fatal classification.",
        "error mapping",
        ["errors", "taxonomy", "classification"],
    ),
    "observability": (
        "Instrument {obj} with metrics, logs, and traces; covers RED metrics "
        "and alert thresholds.",
        "observability",
        ["metrics", "logs", "traces", "alerts"],
    ),
    "schema-migration": (
        "Plan and execute schema or contract migrations for {obj}; covers "
        "expand-contract, backfills, and rollback.",
        "schema migrations",
        ["migration", "backfill", "rollback", "expand-contract"],
    ),
    "dead-letter-handling": (
        "Handle poison or dead-lettered items in {obj}; covers quarantine, "
        "replay, and alerting.",
        "dead-letter handling",
        ["dead-letter", "poison", "quarantine", "replay"],
    ),
    "backpressure": (
        "Apply backpressure in {obj} pipelines; covers bounded queues, load "
        "shedding, and consumer lag.",
        "backpressure",
        ["backpressure", "load-shedding", "bounded-queue"],
    ),
    "testing-strategy": (
        "Build the test strategy for {obj}; covers contract tests, fakes "
        "versus live instances, and flake control.",
        "testing strategy",
        ["testing", "contract-tests", "fakes", "flakes"],
    ),
    "debugging-playbook": (
        "Debug production issues in {obj}; covers symptom-to-cause checklists "
        "and safe inspection commands.",
        "debugging",
        ["debugging", "playbook", "production", "checklist"],
    ),
    "performance-tuning": (
        "Profile and tune the performance of {obj}; covers bottleneck "
        "isolation, benchmark harnesses, and regression guards.",
        "performance tuning",
        ["performance", "profiling", "bottleneck", "benchmark"],
    ),
    "security-hardening": (
        "Harden {obj} against abuse; covers secret handling, input "
        "validation, and least-privilege access.",
        "security hardening",
        ["security", "hardening", "least-privilege", "validation"],
    ),
    "config-management": (
        "Manage configuration of {obj} across environments; covers "
        "validation, defaults, and safe rollout of changes.",
        "configuration",
        ["config", "environments", "rollout", "defaults"],
    ),
    "upgrade-runbook": (
        "Upgrade the dependencies of {obj} safely; covers compatibility "
        "checks, staged rollout, and rollback.",
        "upgrades",
        ["upgrade", "compatibility", "staged-rollout"],
    ),
}

PATTERN_ORDER = list(PATTERNS)

# Patterns every S100 directory carries -> structural near-neighbours.
S100_COMMON = [
    "retry-backoff",
    "circuit-breaker",
    "pagination",
    "idempotency",
    "rate-limiting",
    "timeout-tuning",
]
S100_DIR_SIZE = 8
S1000_DIR_SIZE = 20

ROOT_SKILLS_S100: dict[str, str] = {
    "commit-message-format": (
        "Format git commit messages with conventional-commit prefixes, scope "
        "rules, and body wrapping."
    ),
    "code-review-checklist": (
        "Checklist for reviewing pull requests: correctness, tests, "
        "readability, and rollback safety passes."
    ),
    "readme-authoring": (
        "Write or refresh a project README with setup, usage, and "
        "architecture sections."
    ),
    "incident-postmortem": (
        "Structure a blameless incident postmortem with timeline, root "
        "cause, and action items."
    ),
}

ROOT_SKILLS_S1000_EXTRA: dict[str, str] = {
    "oncall-handoff": (
        "Run a structured oncall handoff: open incidents, silenced alerts, "
        "and pending follow-ups."
    ),
    "dependency-audit": (
        "Audit third-party dependencies for known vulnerabilities and "
        "license drift."
    ),
    "api-design-review": (
        "Review a proposed API change for consistency, versioning, and "
        "backward compatibility."
    ),
    "release-checklist": (
        "Pre-release checklist: changelog, version bump, migration dry-run, "
        "and rollback plan."
    ),
    "adr-writing": (
        "Write an architecture decision record with context, options, and "
        "consequences."
    ),
    "database-change-review": (
        "Review a proposed database change for lock risk, index coverage, "
        "and rollback safety."
    ),
}

# wrong-placement: task id -> wrong-but-plausible destination directory.
# All destinations are core directories (present at both scales). Moves are
# SWAPS: the target's content trades places with the resident same-named
# skill (if any), so every qualified name stays valid in every variant. No
# two moved targets may share a destination slot; the generator asserts it.
WRONG_PLACEMENT: dict[str, str] = {
    "T01": "message-queue",
    "T02": "job-scheduler",
    "T03": "database",
    "T04": "database",
    "T05": "http-client",
    "T06": "database",
    "T07": "message-queue",
    "T08": "message-queue",
    "T09": "payment-gateway",
    "T10": "message-queue",
    "T11": "ci-pipeline",
    "T12": "job-scheduler",
    "T13": "http-client",
    "T14": "database",
    "T15": "message-queue",
    "T16": "job-scheduler",
    "T17": "graphql-api",
    "T18": "graphql-api",
    "T19": "cache-layer",
}

# Extra patterns a directory must carry at S100 so every task target exists
# (on top of S100_COMMON). Derived by hand from tasks.json; --validate
# re-checks it against the actual task set.
S100_REQUIRED_EXTRA: dict[str, list[str]] = {
    "database": ["schema-migration"],
    "ci-pipeline": ["debugging-playbook"],
    "cache-layer": ["caching-strategy", "debugging-playbook", "performance-tuning"],
    "search-index": ["backpressure"],
    "auth-service": ["security-hardening"],
    "payment-gateway": ["performance-tuning", "debugging-playbook"],
    "notification-service": ["observability", "debugging-playbook"],
}

# Filler pool for topping S100 directories up to 8 skills (patterns not in
# S100_COMMON), in deterministic rotation order.
S100_FILLER_POOL = [p for p in PATTERN_ORDER if p not in S100_COMMON]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _title(words: str) -> str:
    return " ".join(part.capitalize() for part in words.replace("-", " ").split())


def _skill_markdown(
    name: str,
    description: str,
    *,
    directory: str,
    domain_obj: str,
    topic: str,
    keywords: list[str],
) -> str:
    """Render one deterministic SKILL.md (frontmatter + plausible body)."""
    domain_words = directory.replace("-", " ") if directory else "general"
    all_keywords = sorted(set(keywords + [w for w in domain_words.split() if len(w) > 3]))
    frontmatter = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "keywords: [" + ", ".join(all_keywords) + "]",
    ]
    if directory:
        frontmatter.append(f"domains: [{domain_words}]")
    frontmatter.append("---")
    title = _title(name) if not directory else f"{_title(domain_words)}: {_title(name)}"
    body = f"""
# {title}

## When to use

Use this skill when work touches {topic} in {domain_obj}. Skip it for
unrelated subsystems; the steps below assume the {domain_words} runtime
conventions.

## Procedure

1. Identify the {topic} surface in {domain_obj}: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the {topic} changes incrementally, one knob at a time, keeping the
   public interface of {domain_obj} unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic {topic} advice without checking the {domain_words}
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard {domain_words} smoke checks.
"""
    return "\n".join(frontmatter) + "\n" + body.lstrip("\n")


def _dir_marker(name: str, description: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n\n"
        f"# {_title(name)}\n"
    )


def _base_dir_description(name: str, obj: str, topics: list[str]) -> str:
    shown = ", ".join(topics[:3])
    return (
        f"Skills for operating and troubleshooting {obj}: {shown}, and more."
    )


def _stale_dir_description(
    name: str, obj: str, topics: list[str], target_topics: set[str]
) -> str:
    """Rewrite that omits every target topic (the 'stale summary' variant)."""
    kept = [t for t in topics if t not in target_topics][:2]
    if kept:
        shown = ", ".join(kept)
        return (
            f"Operational utilities for {obj}: {shown}, and related "
            "housekeeping."
        )
    return f"Operational utilities and housekeeping runbooks for {obj}."


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _s100_patterns_for(directory: str, index: int, rng: random.Random) -> list[str]:
    required = list(S100_COMMON) + [
        p for p in S100_REQUIRED_EXTRA.get(directory, []) if p not in S100_COMMON
    ]
    pool = [p for p in S100_FILLER_POOL if p not in required]
    # Seeded rotation so filler differs per directory but is reproducible.
    offset = rng.randrange(len(pool)) if pool else 0
    rotated = pool[offset:] + pool[:offset]
    chosen = list(required)
    for pattern in rotated:
        if len(chosen) >= S100_DIR_SIZE:
            break
        chosen.append(pattern)
    return sorted(chosen)


def _positive_tasks(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if not t.get("negative") and t.get("target")]


def build_catalog(
    scale: str,
    variant: str,
    tasks: list[dict],
    seed: int = DEFAULT_SEED,
) -> dict:
    """Build the full in-memory catalog spec for one scale/variant."""
    if scale not in {"S100", "S1000"}:
        raise ValueError(f"unknown scale {scale!r}")
    if variant not in {"base", "wrong-placement", "stale-summary"}:
        raise ValueError(f"unknown variant {variant!r}")
    rng = random.Random(f"{seed}:{scale}:{variant}")

    domains = dict(CORE_DOMAINS)
    if scale == "S1000":
        domains.update(EXTRA_DOMAINS)

    positives = _positive_tasks(tasks)
    task_by_id = {t["id"]: t for t in positives}

    # directory -> {skill_name: {"description", "kind", "task_ids", "domain_obj", "topic", "keywords"}}
    layout: dict[str, dict[str, dict]] = {}
    for index, (directory, obj) in enumerate(sorted(domains.items())):
        if scale == "S100":
            patterns = _s100_patterns_for(directory, index, rng)
        else:
            patterns = sorted(PATTERN_ORDER)[:S1000_DIR_SIZE]
        entries: dict[str, dict] = {}
        for pattern in patterns:
            template, topic, keywords = PATTERNS[pattern]
            entries[pattern] = {
                "description": template.format(obj=obj),
                "kind": "core",
                "task_ids": [],
                "domain_obj": obj,
                "topic": topic,
                "keywords": list(keywords),
            }
        layout[directory] = entries

    root_skills = dict(ROOT_SKILLS_S100)
    if scale == "S1000":
        root_skills.update(ROOT_SKILLS_S1000_EXTRA)

    # Tag task targets.
    for task in positives:
        target = task["target"]["qualified_name"]
        directory, _, name = target.partition("/")
        if directory not in layout or name not in layout[directory]:
            raise ValueError(
                f"task {task['id']} target {target!r} is not in the {scale} catalog"
            )
        layout[directory][name]["task_ids"].append(task["id"])

    # Materialize per-task decoys (skip decoys whose directory is not in scale).
    skipped_decoys: list[dict] = []
    for task in positives:
        decoy = task.get("decoy")
        if not decoy:
            continue
        qualified = decoy["qualified_name"]
        directory, _, name = qualified.partition("/")
        if directory not in layout:
            skipped_decoys.append(
                {"task_id": task["id"], "qualified_name": qualified,
                 "reason": f"directory {directory!r} not in {scale}"}
            )
            continue
        if name in layout[directory]:
            raise ValueError(f"decoy {qualified!r} collides with an existing skill")
        obj = domains[directory]
        layout[directory][name] = {
            "description": decoy["description"],
            "kind": "decoy",
            "task_ids": [],
            "decoy_for": task["id"],
            "domain_obj": obj,
            "topic": "specialized helper",
            "keywords": sorted(set(name.split("-"))),
        }

    displaced: list[dict] = []
    task_targets: dict[str, dict | None] = {
        t["id"]: {
            "qualified_name": t["target"]["qualified_name"],
            "directory": t["target"]["directory"],
        }
        for t in positives
    }
    for task in tasks:
        if task.get("negative"):
            task_targets[task["id"]] = None

    if variant == "wrong-placement":
        for task in positives:
            task_id = task["id"]
            destination = WRONG_PLACEMENT.get(task_id)
            if destination is None:
                raise ValueError(f"no wrong-placement mapping for {task_id}")
            source_dir = task["target"]["directory"]
            _, _, name = task["target"]["qualified_name"].partition("/")
            entry = layout[source_dir].get(name)
            if entry is None or task_id not in entry["task_ids"]:
                raise ValueError(
                    f"{task_id}: home slot {source_dir}/{name} no longer holds "
                    "this task's target (destination-slot collision?)"
                )
            resident = layout[destination].get(name)
            if resident is not None and resident["task_ids"]:
                raise ValueError(
                    f"{task_id}: destination slot {destination}/{name} holds "
                    f"another task's target ({resident['task_ids']})"
                )
            layout[source_dir].pop(name)
            layout[destination][name] = entry
            if resident is not None:
                layout[source_dir][name] = resident
                displaced.append({
                    "qualified_name": f"{source_dir}/{name}",
                    "reason": f"swapped with moved target of {task_id} "
                              f"(now at {destination}/{name})",
                })
            task_targets[task_id] = {
                "qualified_name": f"{destination}/{name}",
                "directory": destination,
            }

    # Directory descriptions (after moves, so stale-summary sees final layout).
    target_pattern_by_dir: dict[str, set[str]] = {}
    if variant == "stale-summary":
        for task_id, target in task_targets.items():
            if target is None:
                continue
            directory = target["directory"]
            _, _, name = target["qualified_name"].partition("/")
            entry = layout[directory].get(name)
            if entry is not None and entry.get("kind") == "core":
                target_pattern_by_dir.setdefault(directory, set()).add(
                    entry["topic"]
                )

    dir_descriptions: dict[str, str] = {}
    for directory in sorted(layout):
        obj = domains[directory]
        core_topics = [
            entry["topic"]
            for name, entry in sorted(layout[directory].items())
            if entry["kind"] == "core"
        ]
        if variant == "stale-summary" and directory in target_pattern_by_dir:
            dir_descriptions[directory] = _stale_dir_description(
                directory, obj, core_topics, target_pattern_by_dir[directory]
            )
        else:
            dir_descriptions[directory] = _base_dir_description(
                directory, obj, core_topics
            )

    # Flatten.
    skills: list[dict] = []
    for directory in sorted(layout):
        for name, entry in sorted(layout[directory].items()):
            skills.append({
                "qualified_name": f"{directory}/{name}",
                "name": name,
                "directory": directory,
                "description": entry["description"],
                "kind": entry["kind"],
                "task_ids": entry["task_ids"],
                "decoy_for": entry.get("decoy_for"),
                "domain_obj": entry["domain_obj"],
                "topic": entry["topic"],
                "keywords": entry["keywords"],
            })
    for name, description in sorted(root_skills.items()):
        skills.append({
            "qualified_name": name,
            "name": name,
            "directory": "",
            "description": description,
            "kind": "root",
            "task_ids": [],
            "decoy_for": None,
            "domain_obj": "general project work",
            "topic": name.replace("-", " "),
            "keywords": sorted(set(name.split("-"))),
        })

    manifest = {
        "scale": scale,
        "variant": variant,
        "seed": seed,
        "skills": [
            {k: s[k] for k in (
                "qualified_name", "name", "directory", "description",
                "kind", "task_ids", "decoy_for",
            )}
            for s in skills
        ],
        "directories": [
            {"name": d, "description": dir_descriptions[d],
             "skill_count": len(layout[d])}
            for d in sorted(layout)
        ],
        "task_targets": task_targets,
        "displaced": displaced,
        "skipped_decoys": skipped_decoys,
        "stats": {
            "total_skills": len(skills),
            "directories": len(layout),
            "root_skills": len(root_skills),
            "decoys": sum(1 for s in skills if s["kind"] == "decoy"),
        },
        # Internal rendering data (not needed by consumers of the manifest).
        "_render": {
            "dir_descriptions": dir_descriptions,
            "skills": {
                s["qualified_name"]: {
                    "domain_obj": s["domain_obj"],
                    "topic": s["topic"],
                    "keywords": s["keywords"],
                    "directory": s["directory"],
                    "name": s["name"],
                    "description": s["description"],
                }
                for s in skills
            },
        },
    }
    return manifest


def write_catalog(manifest: dict, out_dir: Path) -> None:
    """Materialize the in-memory catalog as a .mini-code/skills tree."""
    skills_root = out_dir / ".mini-code" / "skills"
    if skills_root.exists():
        raise FileExistsError(
            f"{skills_root} already exists; remove it or choose a fresh --out"
        )
    render = manifest["_render"]
    dir_desc = render["dir_descriptions"]
    for directory in sorted(dir_desc):
        dir_path = skills_root / directory
        dir_path.mkdir(parents=True)
        (dir_path / "SKILL_DIR.md").write_text(
            _dir_marker(directory, dir_desc[directory]), encoding="utf-8"
        )
    for qualified_name, spec in sorted(render["skills"].items()):
        directory = spec["directory"]
        name = spec["name"]
        skill_dir = (
            skills_root / directory / name if directory else skills_root / name
        )
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _skill_markdown(
                name,
                spec["description"],
                directory=directory,
                domain_obj=spec["domain_obj"],
                topic=spec["topic"],
                keywords=spec["keywords"],
            ),
            encoding="utf-8",
        )
    public_manifest = {k: v for k, v in manifest.items() if not k.startswith("_")}
    (out_dir / "catalog-manifest.json").write_text(
        json.dumps(public_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_manifest(manifest: dict, tasks: list[dict]) -> list[str]:
    """Check that every task reference resolves inside this manifest."""
    errors: list[str] = []
    by_qname = {s["qualified_name"]: s for s in manifest["skills"]}
    targets = manifest["task_targets"]
    for task in tasks:
        task_id = task["id"]
        if task_id not in targets:
            errors.append(f"{task_id}: missing from task_targets")
            continue
        target = targets[task_id]
        if task.get("negative"):
            if target is not None:
                errors.append(f"{task_id}: negative task has a target")
            continue
        if target is None:
            errors.append(f"{task_id}: positive task has no target")
            continue
        entry = by_qname.get(target["qualified_name"])
        if entry is None:
            errors.append(
                f"{task_id}: target {target['qualified_name']!r} not in catalog"
            )
            continue
        if task_id not in entry["task_ids"]:
            errors.append(
                f"{task_id}: target {target['qualified_name']!r} not tagged "
                "with this task id"
            )
        for alt in task.get("acceptable_alternatives", []):
            if alt not in by_qname:
                errors.append(f"{task_id}: alternative {alt!r} not in catalog")
        decoy = task.get("decoy")
        if decoy:
            skipped = {
                s["qualified_name"] for s in manifest["skipped_decoys"]
            }
            qname = decoy["qualified_name"]
            if qname not in by_qname and qname not in skipped:
                errors.append(f"{task_id}: decoy {qname!r} neither present nor skipped")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scale", choices=["S100", "S1000"])
    parser.add_argument(
        "--variant", choices=["base", "wrong-placement", "stale-summary"]
    )
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="workspace dir for one catalog")
    parser.add_argument(
        "--out-root", type=Path,
        help="with --all: write <out-root>/<scale>-<variant>/ per combo",
    )
    parser.add_argument("--all", action="store_true", help="build all 6 combos")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--validate", action="store_true",
        help="validate --tasks against an existing --manifest (no generation)",
    )
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)

    tasks_doc = json.loads(args.tasks.read_text(encoding="utf-8"))
    tasks = tasks_doc["tasks"]

    if args.validate:
        if not args.manifest:
            parser.error("--validate requires --manifest")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        errors = validate_manifest(manifest, tasks)
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        if errors:
            return 1
        stats = manifest["stats"]
        print(
            f"OK: {args.manifest} ({manifest['scale']}/{manifest['variant']}) "
            f"validates against {len(tasks)} tasks; "
            f"{stats['total_skills']} skills, {stats['directories']} dirs, "
            f"{stats['decoys']} decoys"
        )
        return 0

    if args.all:
        if not args.out_root:
            parser.error("--all requires --out-root")
        for scale in ("S100", "S1000"):
            for variant in ("base", "wrong-placement", "stale-summary"):
                out = args.out_root / f"{scale}-{variant}"
                manifest = build_catalog(scale, variant, tasks, seed=args.seed)
                write_catalog(manifest, out)
                errors = validate_manifest(manifest, tasks)
                if errors:
                    for error in errors:
                        print(f"ERROR {scale}/{variant}: {error}", file=sys.stderr)
                    return 1
                print(f"built {out} ({manifest['stats']['total_skills']} skills)")
        return 0

    if not args.scale or not args.variant or not args.out:
        parser.error("single build requires --scale, --variant and --out")
    manifest = build_catalog(args.scale, args.variant, tasks, seed=args.seed)
    write_catalog(manifest, args.out)
    errors = validate_manifest(manifest, tasks)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    stats = manifest["stats"]
    print(
        f"built {args.out}: {stats['total_skills']} skills in "
        f"{stats['directories']} directories + {stats['root_skills']} root, "
        f"{stats['decoys']} decoys"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
