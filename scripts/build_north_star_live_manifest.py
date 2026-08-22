#!/usr/bin/env python3
"""Build the sealed 50-case live north-star manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SUITE_ID = "minicode-north-star-live-50-2026-08-20-v2"


def _common_files() -> dict[str, str]:
    return {
        "README.md": "# Northstar Sample\n\nA small deterministic Python service.\n",
        "northstar/__init__.py": "\"\"\"Northstar acceptance fixture.\"\"\"\n",
        "northstar/core.py": '''\"\"\"Small functions used by independent acceptance cases.\"\"\"

from datetime import date


def clamp(value: int, low: int, high: int) -> int:
    return min(low, max(high, value))


def slugify(value: str) -> str:
    return value.strip().lower().replace(" ", "-")


class RetryExhausted(RuntimeError):
    pass


def retry(operation, max_attempts: int):
    last_error = None
    for _ in range(max_attempts + 1):
        try:
            return operation()
        except RuntimeError as error:
            last_error = error
    raise RetryExhausted(str(last_error))


def invoice_total(subtotal: float, tax_rate: float) -> float:
    return subtotal * (1 + tax_rate)


def parse_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("invalid boolean")


def dedupe(values: list[str]) -> list[str]:
    return sorted(set(values))


def chunks(values: list[int], size: int):
    for start in range(0, len(values) + 1, size):
        yield values[start:start + size]


def iso_date(value: date) -> str:
    return value.strftime("%m/%d/%Y")


def group_by(records: list[dict], key: str) -> dict[str, list[dict]]:
    raise NotImplementedError


def moving_average(values: list[float], window: int) -> list[float]:
    raise NotImplementedError


def mask_email(value: str) -> str:
    raise NotImplementedError


def parse_duration(value: str) -> int:
    raise NotImplementedError


def top_n(values: list[int], count: int) -> list[int]:
    raise NotImplementedError


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    raise NotImplementedError


def normalize_phone(value: str) -> str:
    raise NotImplementedError


def safe_divide(left: float, right: float) -> float:
    if right == 0:
        raise ZeroDivisionError("right must not be zero")
    return left / right


def page_bounds(page: int, page_size: int) -> tuple[int, int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be positive")
    start = (page - 1) * page_size
    return start, start + page_size


def merge_config(defaults: dict, project: dict) -> dict:
    return {**defaults, **project}


def normalize_relative_path(value: str) -> str:
    pieces = [piece for piece in value.replace("\\\\", "/").split("/") if piece not in {"", "."}]
    if ".." in pieces:
        raise ValueError("parent traversal is forbidden")
    return "/".join(pieces)


def invoke_callback(callback, attempts: int) -> int:
    for index in range(attempts):
        callback(index)
    return attempts
''',
        "northstar/legacy.py": '''\"\"\"Intentionally shallow legacy code for refactor cases.\"\"\"


def order_total(lines: list[dict]) -> float:
    subtotal = sum(line["price"] * line["quantity"] for line in lines)
    return round(subtotal, 2)


def invoice_total(lines: list[dict]) -> float:
    subtotal = sum(line["price"] * line["quantity"] for line in lines)
    return round(subtotal, 2)


def create_limit(value: int) -> int:
    if value <= 0:
        raise ValueError("value must be positive")
    return value


def update_limit(value: int) -> int:
    if value <= 0:
        raise ValueError("value must be positive")
    return value


def invoice_record(identifier: str, amount: float) -> dict:
    return {"identifier": identifier, "amount": amount}


def user_label(name: str, identifier: int) -> str:
    clean = " ".join(name.split())
    return f"{clean} (#{identifier})"


def append_tag(tag: str, tags: list[str] = []) -> list[str]:
    tags.append(tag)
    return tags
''',
        "northstar/architecture.py": '''\"\"\"Call-flow facts for read-only and multi-agent cases.\"\"\"


def validate_order(order: dict) -> None:
    if not order.get("lines"):
        raise ValueError("empty order")


def reserve_stock(order: dict) -> str:
    return "reservation:" + order["id"]


def charge_card(order: dict, reservation: str) -> str:
    return "payment:" + reservation


def publish_order_created(order: dict, payment: str) -> str:
    return f"created:{order['id']}:{payment}"


def create_order(order: dict) -> str:
    validate_order(order)
    reservation = reserve_stock(order)
    payment = charge_card(order, reservation)
    return publish_order_created(order, payment)


def layered_config(defaults: dict, project: dict, environment: dict) -> dict:
    return {**defaults, **project, **environment}


def cache_key(entity_id: str, version: int) -> str:
    return f"entity:{entity_id}:v{version}"


def invalidate(cache: dict, entity_id: str, version: int) -> None:
    cache.pop(cache_key(entity_id, version), None)


def record_audit(event: dict, store: list[dict], publisher) -> None:
    if "actor" not in event:
        raise ValueError("actor required")
    store.append(dict(event))
    publisher(event)
''',
        "tests/__init__.py": "",
        "tests/test_targets.py": '''from datetime import date
import unittest

from northstar import core, legacy


class BugTests(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(core.clamp(20, 0, 10), 10)
        self.assertEqual(core.clamp(-3, 0, 10), 0)

    def test_slugify(self):
        self.assertEqual(core.slugify("  Hello,   World!  "), "hello-world")

    def test_retry(self):
        calls = []
        with self.assertRaises(core.RetryExhausted):
            core.retry(lambda: calls.append(1) or (_ for _ in ()).throw(RuntimeError("x")), 3)
        self.assertEqual(len(calls), 3)

    def test_invoice_total(self):
        self.assertEqual(core.invoice_total(10.05, 0.075), 10.8)

    def test_parse_bool(self):
        self.assertTrue(core.parse_bool(" TRUE "))
        self.assertFalse(core.parse_bool("False"))

    def test_dedupe(self):
        self.assertEqual(core.dedupe(["b", "a", "b", "c"]), ["b", "a", "c"])

    def test_chunks(self):
        self.assertEqual(list(core.chunks([1, 2, 3, 4], 2)), [[1, 2], [3, 4]])

    def test_iso_date(self):
        self.assertEqual(core.iso_date(date(2026, 8, 20)), "2026-08-20")


class FeatureTests(unittest.TestCase):
    def test_group_by(self):
        rows = [{"team": "a", "id": 1}, {"team": "b", "id": 2}, {"team": "a", "id": 3}]
        self.assertEqual([row["id"] for row in core.group_by(rows, "team")["a"]], [1, 3])

    def test_moving_average(self):
        self.assertEqual(core.moving_average([1, 2, 3, 4], 2), [1.5, 2.5, 3.5])

    def test_mask_email(self):
        self.assertEqual(core.mask_email("alice@example.com"), "a***e@example.com")

    def test_parse_duration(self):
        self.assertEqual(core.parse_duration("2h 15m"), 8100)

    def test_top_n(self):
        self.assertEqual(core.top_n([4, 1, 9, 9, 2], 3), [9, 9, 4])

    def test_merge_ranges(self):
        self.assertEqual(core.merge_ranges([(1, 3), (2, 5), (8, 9)]), [(1, 5), (8, 9)])

    def test_normalize_phone(self):
        self.assertEqual(core.normalize_phone("+86 (138) 0013-8000"), "+8613800138000")


class RefactorTests(unittest.TestCase):
    def test_totals(self):
        lines = [{"price": 2.5, "quantity": 2}]
        self.assertEqual(legacy.order_total(lines), 5.0)
        self.assertEqual(legacy.invoice_total(lines), 5.0)

    def test_limits(self):
        self.assertEqual(legacy.create_limit(2), 2)
        self.assertEqual(legacy.update_limit(3), 3)
        with self.assertRaises(ValueError):
            legacy.create_limit(0)

    def test_invoice_record(self):
        record = legacy.invoice_record("INV-1", 4.5)
        self.assertEqual(record["identifier"], "INV-1")
        self.assertEqual(record["amount"], 4.5)

    def test_label(self):
        self.assertEqual(legacy.user_label("  Ada   Lovelace ", 7), "Ada Lovelace (#7)")

    def test_append_tag_has_no_shared_default(self):
        self.assertEqual(legacy.append_tag("a"), ["a"])
        self.assertEqual(legacy.append_tag("b"), ["b"])
''',
        ".mini-code/skills/reliability-review/SKILL.md": '''---
name: reliability-review
description: Use for reliability review, risk inspection, and verification planning.
keywords: [reliability, risk, verification, review]
---

# Reliability Review

Inspect implementation and tests before making claims. Distinguish verified
facts from hypotheses. Every final response must contain the exact marker
`RELIABILITY_SKILL_APPLIED` and name at least one concrete verifier.
''',
    }


def _oracle(oracle_id: str, kind: str, **values: object) -> dict[str, object]:
    return {"id": oracle_id, "kind": kind, **values}


def _case(
    case_id: str,
    category: str,
    mutability: str,
    prompt: str,
    oracles: list[dict[str, object]],
    *,
    files: dict[str, str] | None = None,
    turns: list[dict[str, object]] | None = None,
    memory_entries: list[dict[str, object]] | None = None,
    authorized_paths: list[str] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": case_id,
        "category": category,
        "mutability": mutability,
        "executionMode": "headless-live",
        "fixtureId": "northstar-python-service-v1",
        "promptClass": category,
        "files": dict(files or _common_files()),
        "turns": turns or [{"prompt": prompt}],
        "oracleIds": [str(oracle["id"]) for oracle in oracles],
        "oracles": oracles,
    }
    if mutability == "write":
        value["authorizedPaths"] = list(authorized_paths or [])
    if memory_entries:
        value["memoryEntries"] = memory_entries
    return value


def _base_read_oracles(*extra: dict[str, object]) -> list[dict[str, object]]:
    return [
        _oracle("run-completed", "all_runs_completed"),
        _oracle("canonical-success", "canonical_success"),
        _oracle("no-source-edits", "no_source_edits"),
        *extra,
    ]


def _target_command(method: str) -> dict[str, object]:
    return _oracle(
        "target-test-passes",
        "command",
        argv=[
            "{python}",
            "-m",
            "unittest",
            f"tests.test_targets.{method}",
        ],
        exitCode=0,
        timeoutSeconds=30,
    )


def _write_oracles(method: str, *extra: dict[str, object]) -> list[dict[str, object]]:
    return [
        _oracle("run-completed", "all_runs_completed"),
        _oracle("canonical-success", "canonical_success"),
        _target_command(method),
        *extra,
    ]


def _context_history(marker: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for index in range(18):
        fact = marker if index == 2 else f"historical-note-{index}"
        filler = (f" turn-{index} implementation discussion " * 35).strip()
        messages.append(
            {
                "role": "user",
                "content": f"Record fact {fact}. {filler}",
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": f"Recorded {fact}. Decision remains bounded. {filler}",
            }
        )
    return messages


def build_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    understanding = [
        ("order-flow", ["validate_order", "reserve_stock", "charge_card", "publish_order_created"]),
        ("config-precedence", ["defaults", "project", "environment"]),
        ("cache-invalidation", ["cache_key", "version", "invalidate"]),
        ("audit-ordering", ["actor", "store.append", "publisher"]),
        ("retry-contract", ["max_attempts", "RetryExhausted", "operation"]),
        ("invoice-rounding", ["subtotal", "tax_rate", "round"]),
    ]
    prompts = {
        "order-flow": "Read northstar/architecture.py. Explain the exact create_order call flow and name every step. Do not edit files.",
        "config-precedence": "Read northstar/architecture.py. Explain layered_config precedence from lowest to highest. Do not edit files.",
        "cache-invalidation": "Read northstar/architecture.py. Explain how cache keys and invalidation use the version. Do not edit files.",
        "audit-ordering": "Read record_audit in northstar/architecture.py and explain validation, persistence, and publication ordering. Do not edit files.",
        "retry-contract": "Read retry in northstar/core.py. Explain its intended attempt contract and terminal exception without editing files.",
        "invoice-rounding": "Read invoice_total and the matching unit test. Explain the monetary rounding expectation without editing files.",
    }
    for name, tokens in understanding:
        cases.append(
            _case(
                f"understand-{name}",
                "code-understanding",
                "read_only",
                prompts[name],
                _base_read_oracles(
                    _oracle("facts-correct", "response_contains", values=tokens)
                ),
            )
        )

    bugs = [
        ("clamp", "BugTests.test_clamp", "Fix clamp so it respects both bounds."),
        ("slugify", "BugTests.test_slugify", "Fix slugify to collapse punctuation and repeated separators."),
        ("retry", "BugTests.test_retry", "Fix retry so max_attempts is the total number of calls."),
        ("invoice", "BugTests.test_invoice_total", "Fix invoice_total to return currency rounded to two decimals."),
        ("parse-bool", "BugTests.test_parse_bool", "Fix parse_bool to accept surrounding whitespace and mixed case."),
        ("dedupe", "BugTests.test_dedupe", "Fix dedupe to preserve first-seen order."),
        ("chunks", "BugTests.test_chunks", "Fix chunks so exact multiples do not emit an empty chunk and reject non-positive size."),
        ("iso-date", "BugTests.test_iso_date", "Fix iso_date to emit ISO YYYY-MM-DD."),
    ]
    for name, method, task in bugs:
        cases.append(
            _case(
                f"bugfix-{name}",
                "bug-fix",
                "write",
                f"{task} Make the smallest change in northstar/core.py. Run `{sys_command(method)}` and report the result.",
                _write_oracles(method),
                authorized_paths=["northstar/core.py"],
            )
        )

    features = [
        ("group-by", "FeatureTests.test_group_by", "Implement stable group_by; preserve record order within each group."),
        ("moving-average", "FeatureTests.test_moving_average", "Implement moving_average and validate that window is positive and not larger than the input."),
        ("mask-email", "FeatureTests.test_mask_email", "Implement mask_email while retaining first and last local-part characters."),
        ("parse-duration", "FeatureTests.test_parse_duration", "Implement parse_duration for hour/minute tokens and reject malformed input."),
        ("top-n", "FeatureTests.test_top_n", "Implement top_n in descending order while retaining duplicates."),
        ("merge-ranges", "FeatureTests.test_merge_ranges", "Implement merge_ranges for overlapping inclusive ranges."),
        ("normalize-phone", "FeatureTests.test_normalize_phone", "Implement normalize_phone preserving a leading plus and digits only."),
    ]
    for name, method, task in features:
        cases.append(
            _case(
                f"feature-{name}",
                "feature-implementation",
                "write",
                f"{task} Edit northstar/core.py only. Run `{sys_command(method)}` before finishing.",
                _write_oracles(method),
                authorized_paths=["northstar/core.py"],
            )
        )

    refactors = [
        ("subtotal-helper", "RefactorTests.test_totals", "Extract a shared calculate_subtotal helper used by both total functions.", "def calculate_subtotal"),
        ("positive-validator", "RefactorTests.test_limits", "Extract a shared _require_positive validator used by create_limit and update_limit.", "def _require_positive"),
        ("invoice-dataclass", "RefactorTests.test_invoice_record", "Introduce an immutable Invoice dataclass while keeping invoice_record's dict-compatible public result.", "@dataclass"),
        ("label-helper", "RefactorTests.test_label", "Extract a pure normalize_name helper used by user_label.", "def normalize_name"),
        ("mutable-default", "RefactorTests.test_append_tag_has_no_shared_default", "Remove the mutable default from append_tag without changing its behavior.", "tags: list[str] | None = None"),
    ]
    for name, method, task, marker in refactors:
        cases.append(
            _case(
                f"refactor-{name}",
                "refactor",
                "write",
                f"{task} Work in northstar/legacy.py and run `{sys_command(method)}`.",
                _write_oracles(
                    method,
                    _oracle("structure-present", "file_contains", path="northstar/legacy.py", text=marker),
                ),
                authorized_paths=["northstar/legacy.py"],
            )
        )

    testing = [
        ("safe-divide", "northstar.core.safe_divide", "tests/test_safe_divide_edges.py", "test_zero_divisor"),
        ("page-bounds", "northstar.core.page_bounds", "tests/test_page_bounds_edges.py", "test_invalid_page"),
        ("merge-config", "northstar.core.merge_config", "tests/test_merge_config_edges.py", "test_inputs_not_mutated"),
        ("relative-path", "northstar.core.normalize_relative_path", "tests/test_relative_path_edges.py", "test_parent_traversal_rejected"),
        ("callback", "northstar.core.invoke_callback", "tests/test_callback_edges.py", "test_callback_indices"),
    ]
    for name, symbol, test_path, test_name in testing:
        prompt = (
            f"Add focused unittest coverage for `{symbol}` in `{test_path}`. Include a test named "
            f"`{test_name}` and at least one happy-path assertion. Do not change production code. "
            f"Run `python -m unittest {test_path[:-3].replace('/', '.')}`."
        )
        cases.append(
            _case(
                f"testing-{name}",
                "testing",
                "write",
                prompt,
                [
                    _oracle("run-completed", "all_runs_completed"),
                    _oracle("canonical-success", "canonical_success"),
                    _oracle("test-file-created", "file_contains", path=test_path, text=f"def {test_name}"),
                    _oracle(
                        "new-tests-pass",
                        "command",
                        argv=["{python}", "-m", "unittest", test_path[:-3].replace("/", ".")],
                        exitCode=0,
                    ),
                ],
                authorized_paths=[test_path],
            )
        )

    documentation = [
        ("retry-policy", "README.md", "## Retry Policy", "Document max_attempts and RetryExhausted in a new README Retry Policy section."),
        ("core-api", "docs/core-api.md", "moving_average", "Create docs/core-api.md covering group_by and moving_average with examples."),
        ("architecture-flow", "docs/architecture.md", "reserve_stock", "Create docs/architecture.md describing create_order validation, reservation, payment, and publication flow."),
        ("changelog", "CHANGELOG.md", "Northstar acceptance", "Create a CHANGELOG entry titled Northstar acceptance and summarize the fixture APIs."),
    ]
    for name, path, marker, task in documentation:
        cases.append(
            _case(
                f"docs-{name}",
                "documentation",
                "write",
                f"{task} Base every claim on the code and do not change Python source files.",
                [
                    _oracle("run-completed", "all_runs_completed"),
                    _oracle("canonical-success", "canonical_success"),
                    _oracle("documentation-present", "file_contains", path=path, text=marker),
                ],
                authorized_paths=[path],
            )
        )

    multi_prompts = [
        ("architecture", ["create_order", "publish_order_created"]),
        ("test-risk", ["test_slugify", "NotImplementedError"]),
        ("data-flow", ["reserve_stock", "charge_card"]),
        ("reliability", ["max_attempts", "mutable default"]),
        ("change-impact", ["legacy.py", "test_targets.py"]),
    ]
    for name, tokens in multi_prompts:
        prompt = (
            "Use the task tool to spawn two read-only explore sub-agents in parallel: one must inspect "
            "implementation and one must inspect tests. Poll both, synthesize their evidence for "
            f"the {name} question, and do not edit files."
        )
        cases.append(
            _case(
                f"multiagent-{name}",
                "multi-agent",
                "read_only",
                prompt,
                _base_read_oracles(
                    _oracle("two-subagents", "subagent_count", min=2),
                    _oracle("synthesis-facts", "response_contains", values=tokens),
                ),
            )
        )

    skill_questions = [
        ("risk-review", "Review retry and list its verifier."),
        ("test-plan", "Create a verification plan for invoice_total."),
        ("architecture-review", "Review create_order reliability boundaries."),
        ("regression-review", "Review the BugTests suite for regression risk."),
    ]
    for name, question in skill_questions:
        cases.append(
            _case(
                f"skill-{name}",
                "skill-routing",
                "read_only",
                f"Use Skill reliability-review. {question} Do not edit files.",
                _base_read_oracles(
                    _oracle("skill-loaded", "skill_loaded", qualifiedName="reliability-review"),
                    _oracle("skill-marker", "response_contains", values=["RELIABILITY_SKILL_APPLIED"]),
                ),
            )
        )

    memory_specs = [
        ("invoice-prefix", "Project invoice identifiers must use the prefix INVX-.", ["INVX-"]),
        ("timestamp-format", "All project timestamps must be UTC RFC3339 values ending in Z.", ["UTC", "RFC3339", "Z"]),
    ]
    for name, content, tokens in memory_specs:
        cases.append(
            _case(
                f"memory-recall-{name}",
                "persistent-memory",
                "read_only",
                "State the applicable project convention for this task and explain how you would apply it. Do not edit source files.",
                [
                    _oracle("run-completed", "all_runs_completed"),
                    _oracle("canonical-success", "canonical_success"),
                    _oracle("memory-rendered", "memory_rendered"),
                    _oracle("constraint-recalled", "response_contains", values=tokens),
                ],
                memory_entries=[
                    {"category": "project-convention", "content": content, "tags": name.split("-")}
                ],
            )
        )
    memory_bug_files = _common_files()
    cases.append(
        _case(
            "memory-write-verified-recovery",
            "persistent-memory",
            "write",
            "Fix the clamp bug with the smallest change, run `python -m unittest tests.test_targets.BugTests.test_clamp`, and explain the verified root cause.",
            _write_oracles(
                "BugTests.test_clamp",
                _oracle("lesson-written", "memory_written"),
            ),
            files=memory_bug_files,
            authorized_paths=["northstar/core.py"],
        )
    )

    for name, marker in (("alpha", "ALPHA-17"), ("beta", "BETA-29"), ("gamma", "GAMMA-41")):
        cases.append(
            _case(
                f"context-{name}-continuity",
                "context-reliability",
                "read_only",
                "",
                _base_read_oracles(
                    _oracle("context-compacted", "context_compacted"),
                    _oracle("marker-retained", "response_contains", values=[marker]),
                ),
                turns=[
                    {
                        "prompt": f"Without editing files, report the exact historical marker beginning with {name.upper()} and nothing else besides a short confirmation.",
                        "initialHistory": _context_history(marker),
                        "contextWindow": 4_000,
                    }
                ],
            )
        )

    if len(cases) != 50:
        raise AssertionError(f"expected 50 cases, got {len(cases)}")
    if len({case["category"] for case in cases}) < 8:
        raise AssertionError("expected at least eight categories")
    if sum(case["mutability"] == "write" for case in cases) < 20:
        raise AssertionError("expected at least twenty write cases")
    return cases


def sys_command(method: str) -> str:
    return f"python -m unittest tests.test_targets.{method}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    document = {
        "schemaVersion": 1,
        "suiteId": SUITE_ID,
        "description": "Fifty isolated real-model MiniCode tasks with deterministic oracles.",
        "cases": build_cases(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
