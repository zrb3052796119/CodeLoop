#!/usr/bin/env python3
"""Build eight synthetic trigger tasks and prove their behavior offline.

Public sources and compatibility tests are separate from reference repairs and
hidden checks. This module does not import production MiniCode or call models.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[1]
GENERATION = "trigger-v1"
CASES = (
    "trigger-record-pagination", "trigger-import-retry", "trigger-cart-discounts",
    "trigger-stock-allocation", "trigger-report-timezones", "trigger-config-overlay",
    "trigger-limit-clamp", "trigger-extension-case",
)
SHARED_FILES = {"minicode/__init__.py": "", "pytest.ini": "[pytest]\naddopts =\n"}


def source(text: str) -> str:
    return textwrap.dedent(text).strip() + "\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def oracle(body: str) -> str:
    return source('''
        from copy import deepcopy
        import json
        from pathlib import Path
        import sys

        workspace = Path(sys.argv[1]).resolve()
        sys.path.insert(0, str(workspace))
        import minicode
        assert Path(minicode.__file__).resolve().is_relative_to(workspace)
        checks = []

        def check(label, actual, expected):
            checks.append({"label": label, "actual": deepcopy(actual), "expected": deepcopy(expected),
                           "passed": actual == expected})

        def raises_value_error(function):
            try:
                function()
            except ValueError:
                return True
            return False
    ''') + "\n" + source(body) + "\n" + source('''
        for name, module in tuple(sys.modules.items()):
            if name.startswith("minicode.") and getattr(module, "__file__", None):
                assert Path(module.__file__).resolve().is_relative_to(workspace), name
        errors = [item["label"] for item in checks if not item["passed"]]
        print(json.dumps({"passed": not errors, "errors": errors, "checks": checks},
                         ensure_ascii=False, sort_keys=True))
        raise SystemExit(1 if errors else 0)
    ''')


def case(name: str, category: str, contract: str, boundaries: list[str],
         baseline: dict[str, str], reference: dict[str, str], public: str,
         hidden: str, *, negative: bool = False) -> tuple[dict, dict]:
    paths = list(baseline)
    assert set(paths) == set(reference)
    prompt = (contract.strip() + "\nPreserve the existing public APIs and avoid modifying inputs. "
              "Add regression tests for the requested behavior in tests/test_eval_regression.py. "
              "Run tests/test_public.py and your new regression tests before finishing. "
              "Only the following files may change: "
              + ", ".join([*paths, "tests/test_eval_regression.py"]) + ".")
    value = {
        "id": name, "category": category, "prompt": prompt,
        "files": {**{path: source(text) for path, text in baseline.items()},
                  "tests/test_public.py": source(public)},
        "authorized_paths": [*paths, "tests/test_eval_regression.py"],
        "verification_argv": ["python3", "-m", "pytest", "-q", "tests/test_public.py"],
        "sourcePaths": paths, "oracle_source": oracle(hidden),
        "triggerExpectation": "negative" if negative else "positive",
        "provenance": {"origin": "synthetic", "generation": GENERATION,
                       "behaviorBoundaries": boundaries,
                       "expectationRationale": (
                           "Known file and function; local repair with no independent investigation needed."
                           if negative else
                           "Distinct contracts can be investigated separately; direct execution remains valid."
                       ),
                       "notHistoricalReplay": True,
                       "distinctFromGen2": "No source-coordinate, registry-lifetime, or model-request-budget defect."},
    }
    return value, {path: source(text) for path, text in reference.items()}


def pagination() -> tuple[dict, dict]:
    baseline = {
        "minicode/records.py": '''
            def ordered_records(records):
                """Return records in feed order without changing the caller's list."""
                return sorted(records, key=lambda row: row["created"], reverse=True)
        ''',
        "minicode/cursors.py": '''
            import json

            def encode_cursor(record):
                return json.dumps([record["created"], record["id"]])

            def is_after_cursor(record, cursor):
                if cursor is None:
                    return True
                created, identity = json.loads(cursor)
                return record["created"] < created
        ''',
        "minicode/feed.py": '''
            from minicode.records import ordered_records
            from minicode.cursors import encode_cursor, is_after_cursor

            def page(records, limit, cursor=None):
                if limit <= 0:
                    raise ValueError("limit must be positive")
                remaining = [row for row in ordered_records(records)
                             if is_after_cursor(row, cursor)]
                selected = remaining[:limit]
                return {"items": selected,
                        "next": encode_cursor(selected[-1]) if len(remaining) > limit else None}
        ''',
    }
    reference = {**baseline,
        "minicode/records.py": baseline["minicode/records.py"].replace(
            'row["created"], reverse=True', '(row["created"], row["id"]), reverse=True'),
        "minicode/cursors.py": baseline["minicode/cursors.py"].replace(
            'record["created"] < created', '(record["created"], record["id"]) < (created, identity)'),
    }
    return case(CASES[0], "feed-pagination", """
The activity-feed API loses records when several records share a timestamp, and
their order can change with input order. Investigate ordering, cursor comparison,
and page composition in minicode/records.py, cursors.py and feed.py, then repair
their shared contract. Each record has a unique string id and integer created.
Feed order must be descending by (created, id), including lexicographic id ties.
An encoded cursor identifies an exclusive position in that same order: later
pages must neither repeat nor omit records with the same created value. page()
returns at most limit items and a next cursor only when additional items remain;
empty pages have next=None. Preserve positive-limit validation and cursor format.
""", ["repository total order", "exclusive cursor position", "page continuation"],
        baseline, reference, '''
            import pytest
            from minicode.feed import page

            def test_unique_timestamps_and_end():
                rows = [{"id": "b", "created": 2}, {"id": "a", "created": 1}]
                first = page(rows, 1)
                assert first["items"] == rows[:1]
                assert page(rows, 1, first["next"]) == {"items": rows[1:], "next": None}

            def test_empty_and_invalid_limit():
                assert page([], 2) == {"items": [], "next": None}
                with pytest.raises(ValueError):
                    page([], 0)
        ''', '''
            from copy import deepcopy
            from minicode.feed import page
            from minicode.cursors import encode_cursor, is_after_cursor
            from minicode.records import ordered_records
            rows = [{"id": "b", "created": 8}, {"id": "d", "created": 9},
                    {"id": "a", "created": 8}, {"id": "c", "created": 8},
                    {"id": "z", "created": 7}]
            before = deepcopy(rows)
            expected = ["d", "c", "b", "a", "z"]
            check("total_order", [r["id"] for r in ordered_records(rows)], expected)
            check("input_order_independent", [r["id"] for r in ordered_records(list(reversed(rows)))], expected)
            for limit in (1, 2, 3, 5, 9):
                cursor, found = None, []
                for _ in range(len(rows) + 2):
                    result = page(rows, limit, cursor)
                    found.extend(r["id"] for r in result["items"])
                    cursor = result["next"]
                    if cursor is None:
                        break
                check("complete_pages_" + str(limit), found, expected)
                check("terminal_cursor_" + str(limit), cursor, None)
            check("exclusive_tie_position", [r["id"] for r in page(rows, 9, encode_cursor({"created": 8, "id": "b"}))["items"]], ["a", "z"])
            position = encode_cursor({"created": 8, "id": "b"})
            check("cursor_tie_after", is_after_cursor({"created": 8, "id": "a"}, position), True)
            check("cursor_tie_before", is_after_cursor({"created": 8, "id": "c"}, position), False)
            check("cursor_position_excluded", is_after_cursor({"created": 8, "id": "b"}, position), False)
            check("empty_page", page([], 3), {"items": [], "next": None})
            check("invalid_limit", raises_value_error(lambda: page(rows, 0)), True)
            check("input_unchanged", rows, before)
        ''')


def import_retry() -> tuple[dict, dict]:
    baseline = {
        "minicode/event_store.py": '''
            class EventStore:
                def __init__(self):
                    self.totals = {}
                    self.applied_ids = set()

                def apply(self, event):
                    key = event["account"]
                    self.totals[key] = self.totals.get(key, 0) + event["amount"]
                    self.applied_ids.add(event["id"])
                    return True
        ''',
        "minicode/importer.py": '''
            def import_events(store, events):
                for event in events:
                    store.apply(event)
                return {"received": len(events), "applied": len(events)}
        ''',
        "minicode/retry.py": '''
            from minicode.importer import import_events

            def retry_job(store, job):
                attempt = job["attempt"] + 1
                events = [{**event, "id": event["id"] + ":" + str(attempt)}
                          for event in job["events"]]
                return {"attempt": attempt, **import_events(store, events)}
        ''',
    }
    reference = {
        "minicode/event_store.py": baseline["minicode/event_store.py"].replace(
            'key = event["account"]', 'if event["id"] in self.applied_ids:\n                        return False\n                    key = event["account"]'),
        "minicode/importer.py": '''
            def import_events(store, events):
                applied = sum(bool(store.apply(event)) for event in events)
                return {"received": len(events), "applied": applied}
        ''',
        "minicode/retry.py": '''
            from minicode.importer import import_events

            def retry_job(store, job):
                return {"attempt": job["attempt"] + 1,
                        **import_events(store, job["events"])}
        ''',
    }
    return case(CASES[1], "event-import-retry", """
Retrying an account-event import currently changes balances twice. Trace how
event identity crosses import_events(), EventStore.apply() and retry_job(), and
make retries idempotent for one EventStore lifetime. Each event contains string
id/account and integer amount; repeated ids always carry the same payload.
An id must affect totals once even when repeated inside a batch, in a later
batch, or in a retry. Distinct ids with equal amounts are distinct events.
EventStore.apply returns True only for a newly applied event; import_events
reports received as the number submitted and applied as the number newly used.
retry_job must preserve event ids, return attempt=job['attempt']+1 with those
same counts, and leave job/events unchanged. Negative and zero amounts are valid.
""", ["store idempotency boundary", "batch accounting", "retry identity preservation"],
        baseline, reference, '''
            from minicode.event_store import EventStore
            from minicode.importer import import_events
            from minicode.retry import retry_job

            def test_first_batch():
                store = EventStore()
                assert import_events(store, [{"id": "x", "account": "a", "amount": 4}]) == {"received": 1, "applied": 1}
                assert store.totals == {"a": 4}

            def test_empty_retry():
                assert retry_job(EventStore(), {"attempt": 2, "events": []}) == {"attempt": 3, "received": 0, "applied": 0}
        ''', '''
            from copy import deepcopy
            from minicode.event_store import EventStore
            from minicode.importer import import_events
            from minicode.retry import retry_job
            first = {"id": "x", "account": "a", "amount": 7}
            second = {"id": "y", "account": "a", "amount": 7}
            debit = {"id": "z", "account": "a", "amount": -3}
            zero = {"id": "zero", "account": "b", "amount": 0}
            store = EventStore()
            check("within_batch_counts", import_events(store, [first, first, second]), {"received": 3, "applied": 2})
            check("within_batch_total", store.totals, {"a": 14})
            check("later_batch_counts", import_events(store, [first, debit, zero]), {"received": 3, "applied": 2})
            job = {"attempt": 4, "events": [first, second, debit, zero]}
            before = deepcopy(job)
            check("retry_counts", retry_job(store, job), {"attempt": 5, "received": 4, "applied": 0})
            check("retry_keeps_totals", store.totals, {"a": 11, "b": 0})
            check("repeat_retry", retry_job(store, job), {"attempt": 5, "received": 4, "applied": 0})
            check("stable_identity_set", sorted(store.applied_ids), ["x", "y", "z", "zero"])
            check("apply_duplicate_returns_false", store.apply(first), False)
            check("job_unchanged", job, before)
        ''')


def cart_discounts() -> tuple[dict, dict]:
    baseline = {
        "minicode/discount_rules.py": '''
            def eligible(line, coupon):
                categories = coupon.get("categories", [])
                return not categories or line["category"] in categories
        ''',
        "minicode/pricing.py": '''
            from minicode.discount_rules import eligible

            def price_lines(lines, coupon):
                result = []
                for line in lines:
                    gross = line["unit_cents"] * line["quantity"]
                    discount = (line["unit_cents"] * coupon["percent"] // 100) * line["quantity"] if eligible(line, coupon) else 0
                    cap = coupon.get("cap_cents")
                    if cap is not None:
                        discount = min(discount, cap)
                    result.append({"sku": line["sku"], "gross": gross,
                                   "discount": discount, "net": gross - discount})
                return result
        ''',
        "minicode/checkout.py": '''
            from minicode.pricing import price_lines

            def receipt(lines, coupon):
                priced = price_lines(lines, coupon)
                return {"lines": priced,
                        "gross": sum(line["gross"] for line in priced),
                        "discount": sum(line["discount"] for line in priced),
                        "net": sum(line["net"] for line in priced)}
        ''',
    }
    reference = {**baseline,
        "minicode/discount_rules.py": baseline["minicode/discount_rules.py"].replace(
            'return not categories or line["category"] in categories',
            'return (not categories or line["category"] in categories) and line["sku"] not in coupon.get("excluded_skus", [])'),
        "minicode/pricing.py": '''
            from minicode.discount_rules import eligible

            def price_lines(lines, coupon):
                result = []
                remaining = coupon.get("cap_cents")
                for line in lines:
                    gross = line["unit_cents"] * line["quantity"]
                    discount = gross * coupon["percent"] // 100 if eligible(line, coupon) else 0
                    if remaining is not None:
                        discount = min(discount, remaining)
                        remaining -= discount
                    result.append({"sku": line["sku"], "gross": gross,
                                   "discount": discount, "net": gross - discount})
                return result
        ''',
    }
    return case(CASES[2], "cart-discount-allocation", """
Repair coupon handling across discount_rules, pricing and checkout. A line has
sku/category plus nonnegative integer unit_cents and quantity. A coupon has an
integer percent in 0..100, optional categories and excluded_skus, and optional
cap_cents. Empty/missing categories allow every category; excluded skus never
receive a discount. Compute each eligible line's discount by flooring the
percentage of the whole line gross (unit_cents * quantity), not each unit.
cap_cents is a single order-wide limit allocated in input line order; None or
missing means unlimited and zero means no discount. Ineligible lines do not
consume this cap. Preserve the existing line and receipt shapes; receipt totals
must equal the sums of its lines. Zero-price/quantity lines and empty carts work.
""", ["coupon eligibility", "line rounding and order-wide cap", "receipt reconciliation"],
        baseline, reference, '''
            from minicode.checkout import receipt

            def test_simple_coupon_and_empty_cart():
                line = {"sku": "a", "category": "book", "unit_cents": 100, "quantity": 1}
                result = receipt([line], {"percent": 10})
                assert (result["gross"], result["discount"], result["net"]) == (100, 10, 90)
                assert receipt([], {"percent": 10}) == {"lines": [], "gross": 0, "discount": 0, "net": 0}

            def test_non_matching_category():
                line = {"sku": "a", "category": "book", "unit_cents": 100, "quantity": 1}
                assert receipt([line], {"percent": 25, "categories": ["food"]})["discount"] == 0
        ''', '''
            from copy import deepcopy
            from minicode.checkout import receipt
            from minicode.discount_rules import eligible
            from minicode.pricing import price_lines
            lines = [{"sku": "a", "category": "book", "unit_cents": 3, "quantity": 3},
                     {"sku": "blocked", "category": "book", "unit_cents": 100, "quantity": 1},
                     {"sku": "b", "category": "book", "unit_cents": 9, "quantity": 1},
                     {"sku": "food", "category": "food", "unit_cents": 20, "quantity": 1}]
            coupon = {"percent": 50, "categories": ["book"], "excluded_skus": ["blocked"], "cap_cents": 6}
            before = deepcopy((lines, coupon))
            result = receipt(lines, coupon)
            check("excluded_sku", eligible(lines[1], coupon), False)
            check("line_rounding_and_shared_cap", [r["discount"] for r in result["lines"]], [4, 0, 2, 0])
            check("pricing_interface", price_lines(lines, coupon), [
                {"sku": "a", "gross": 9, "discount": 4, "net": 5},
                {"sku": "blocked", "gross": 100, "discount": 0, "net": 100},
                {"sku": "b", "gross": 9, "discount": 2, "net": 7},
                {"sku": "food", "gross": 20, "discount": 0, "net": 20}])
            check("receipt_totals", [result[k] for k in ("gross", "discount", "net")], [138, 6, 132])
            check("unlimited_cap", receipt([lines[0], lines[2]], {"percent": 50})["discount"], 8)
            check("explicit_none_cap", receipt([lines[0], lines[2]], {"percent": 50, "cap_cents": None})["discount"], 8)
            check("zero_cap", receipt(lines, {"percent": 50, "cap_cents": 0})["discount"], 0)
            check("zero_percent", receipt(lines, {"percent": 0})["discount"], 0)
            check("empty_categories_allow_all", receipt([lines[3]], {"percent": 50, "categories": []})["discount"], 10)
            check("full_discount", receipt([lines[0]], {"percent": 100})["net"], 0)
            check("zero_quantity", receipt([{**lines[0], "quantity": 0}], {"percent": 50})["net"], 0)
            check("inputs_unchanged", (lines, coupon), before)
        ''')


def stock_allocation() -> tuple[dict, dict]:
    baseline = {
        "minicode/stock.py": '''
            def available_stock(bins):
                return {item["sku"]: item["quantity"] for item in bins}
        ''',
        "minicode/allocation.py": '''
            from minicode.stock import available_stock

            def allocate(bins, requests):
                stock = available_stock(bins)
                result = []
                for request in requests:
                    sku, quantity = request["sku"], request["quantity"]
                    allocated = min(quantity, stock.get(sku, 0))
                    result.append({"sku": sku, "requested": quantity,
                                   "allocated": allocated, "shortfall": quantity - allocated})
                return result
        ''',
        "minicode/allocation_report.py": '''
            def summarize(allocations):
                return {row["sku"]: {key: row[key] for key in ("requested", "allocated", "shortfall")}
                        for row in allocations}
        ''',
    }
    reference = {
        "minicode/stock.py": '''
            def available_stock(bins):
                result = {}
                for item in bins:
                    result[item["sku"]] = result.get(item["sku"], 0) + item["quantity"]
                return result
        ''',
        "minicode/allocation.py": baseline["minicode/allocation.py"].replace(
            'allocated = min(quantity, stock.get(sku, 0))',
            'allocated = min(quantity, stock.get(sku, 0))\n                    stock[sku] = stock.get(sku, 0) - allocated'),
        "minicode/allocation_report.py": '''
            def summarize(allocations):
                result = {}
                for row in allocations:
                    totals = result.setdefault(row["sku"], {"requested": 0, "allocated": 0, "shortfall": 0})
                    for key in totals:
                        totals[key] += row[key]
                return result
        ''',
    }
    return case(CASES[3], "stock-allocation-report", """
Make warehouse allocation and its summary agree when a SKU occurs more than
once. Bins and requests contain string sku and nonnegative integer quantity.
available_stock must sum all bins for the same SKU. allocate must process
request lines in input order, consuming that shared available stock, retaining
one output row per request, with requested, allocated and shortfall values.
Unknown SKUs allocate zero; zero quantities and empty inputs are valid.
summarize must aggregate all allocation rows by SKU, adding each of requested,
allocated and shortfall; it must not overwrite earlier request lines. Preserve
public function signatures and return shapes. Allocation must never overdraw
stock or modify the supplied bins or requests.
""", ["bin-to-stock aggregation", "sequential stock consumption", "per-SKU reporting"],
        baseline, reference, '''
            from minicode.stock import available_stock
            from minicode.allocation import allocate
            from minicode.allocation_report import summarize

            def test_single_bin_request():
                bins = [{"sku": "a", "quantity": 5}]
                assert available_stock(bins) == {"a": 5}
                rows = allocate(bins, [{"sku": "a", "quantity": 2}])
                assert summarize(rows) == {"a": {"requested": 2, "allocated": 2, "shortfall": 0}}

            def test_empty():
                assert allocate([], []) == []
                assert summarize([]) == {}
        ''', '''
            from copy import deepcopy
            from minicode.stock import available_stock
            from minicode.allocation import allocate
            from minicode.allocation_report import summarize
            bins = [{"sku": "a", "quantity": 3}, {"sku": "b", "quantity": 1}, {"sku": "a", "quantity": 4}]
            requests = [{"sku": "a", "quantity": 5}, {"sku": "b", "quantity": 2},
                        {"sku": "a", "quantity": 4}, {"sku": "missing", "quantity": 2}, {"sku": "a", "quantity": 0}]
            before = deepcopy((bins, requests))
            check("bin_aggregation", available_stock(bins), {"a": 7, "b": 1})
            rows = allocate(bins, requests)
            check("allocation_order_and_consumption", [r["allocated"] for r in rows], [5, 1, 2, 0, 0])
            check("shortfalls", [r["shortfall"] for r in rows], [0, 1, 2, 2, 0])
            check("summary_all_rows", summarize(rows), {"a": {"requested": 9, "allocated": 7, "shortfall": 2}, "b": {"requested": 2, "allocated": 1, "shortfall": 1}, "missing": {"requested": 2, "allocated": 0, "shortfall": 2}})
            explicit_rows = [{"sku": "x", "requested": 2, "allocated": 1, "shortfall": 1}, {"sku": "x", "requested": 4, "allocated": 3, "shortfall": 1}]
            check("independent_summary", summarize(explicit_rows), {"x": {"requested": 6, "allocated": 4, "shortfall": 2}})
            check("inputs_unchanged", (bins, requests), before)
            check("empty_allocation", allocate([], []), [])
        ''')


def report_timezones() -> tuple[dict, dict]:
    baseline = {
        "minicode/time_window.py": '''
            from datetime import datetime, timedelta, timezone

            def day_bounds(day, offset_minutes):
                start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
                return start, start + timedelta(days=1)
        ''',
        "minicode/daily_report.py": '''
            from datetime import datetime
            from minicode.time_window import day_bounds

            def daily_totals(events, day, offset_minutes):
                start, end = day_bounds(day, offset_minutes)
                selected = [event for event in events
                            if start <= datetime.fromisoformat(event["at"]) <= end]
                return {"ids": [event["id"] for event in selected],
                        "total": sum(event["amount"] for event in selected)}
        ''',
    }
    reference = {
        "minicode/time_window.py": baseline["minicode/time_window.py"].replace(
            'datetime.fromisoformat(day).replace(tzinfo=timezone.utc)',
            'datetime.fromisoformat(day).replace(tzinfo=timezone(timedelta(minutes=offset_minutes))).astimezone(timezone.utc)'),
        "minicode/daily_report.py": baseline["minicode/daily_report.py"].replace('event["at"]) <= end', 'event["at"]) < end'),
    }
    return case(CASES[4], "local-day-report", """
Correct daily reporting across time_window and daily_report. day is an ISO
calendar date; offset_minutes is a fixed UTC offset, not a timezone with DST.
day_bounds must return timezone-aware UTC instants for local midnight and the
next local midnight. daily_totals includes events in [start, end), so an event
at the next day's midnight belongs only to that next day. Event at values are
ISO strings with explicit offsets (possibly different from the report offset);
compare their instants correctly. Offsets may be positive, negative or zero,
including half-hour offsets, and days may cross month/year boundaries. Preserve
input order in the ids result, sum integer amounts including negatives/zero,
and return ids=[]/total=0 for no matching events.
""", ["local calendar day to UTC interval", "event-offset normalization and exclusive end"],
        baseline, reference, '''
            from minicode.daily_report import daily_totals

            def test_utc_noon_and_empty():
                events = [{"id": "a", "at": "2026-01-02T12:00:00+00:00", "amount": 4}]
                assert daily_totals(events, "2026-01-02", 0) == {"ids": ["a"], "total": 4}
                assert daily_totals([], "2026-01-02", 0) == {"ids": [], "total": 0}
        ''', '''
            from copy import deepcopy
            from datetime import datetime, timedelta, timezone
            from minicode.time_window import day_bounds
            from minicode.daily_report import daily_totals
            for day, offset in (("2026-01-01", 330), ("2026-03-01", -420), ("2026-12-31", 0)):
                expected_start = datetime.fromisoformat(day).replace(tzinfo=timezone(timedelta(minutes=offset))).astimezone(timezone.utc)
                expected_end = expected_start + timedelta(days=1)
                start, end = day_bounds(day, offset)
                check("utc_bounds_" + str(offset), [start.isoformat(), end.isoformat()], [expected_start.isoformat(), expected_end.isoformat()])
                events = [{"id": "end", "at": expected_end.isoformat(), "amount": 100},
                          {"id": "middle", "at": (expected_start + timedelta(hours=12)).astimezone(timezone(timedelta(hours=-2))).isoformat(), "amount": -2},
                          {"id": "start", "at": expected_start.isoformat(), "amount": 5},
                          {"id": "before", "at": (expected_start - timedelta(seconds=1)).isoformat(), "amount": 100}]
                before = deepcopy(events)
                check("half_open_and_order_" + str(offset), daily_totals(events, day, offset), {"ids": ["middle", "start"], "total": 3})
                check("events_unchanged_" + str(offset), events, before)
            check("empty_report", daily_totals([], "2026-02-01", 0), {"ids": [], "total": 0})
        ''')


def config_overlay() -> tuple[dict, dict]:
    baseline = {
        "minicode/config_merge.py": '''
            from copy import deepcopy

            def merge_settings(base, override):
                result = deepcopy(base)
                for key, value in override.items():
                    if value:
                        result[key] = deepcopy(value)
                return result
        ''',
        "minicode/settings.py": '''
            from minicode.config_merge import merge_settings

            def load_settings(defaults, file_values, environment):
                overrides = {}
                if "APP_ENABLED" in environment:
                    overrides["enabled"] = bool(environment["APP_ENABLED"])
                if "APP_LIMIT" in environment:
                    overrides["limit"] = int(environment["APP_LIMIT"])
                if "APP_TAGS" in environment:
                    overrides["tags"] = environment["APP_TAGS"].split(",")
                return merge_settings(merge_settings(defaults, file_values), overrides)
        ''',
    }
    reference = {
        "minicode/config_merge.py": '''
            from copy import deepcopy

            def merge_settings(base, override):
                result = deepcopy(base)
                for key, value in override.items():
                    if value is None:
                        continue
                    if isinstance(value, dict) and value:
                        previous = result.get(key)
                        result[key] = merge_settings(previous if isinstance(previous, dict) else {}, value)
                    else:
                        result[key] = deepcopy(value)
                return result
        ''',
        "minicode/settings.py": '''
            from minicode.config_merge import merge_settings

            def load_settings(defaults, file_values, environment):
                overrides = {}
                if "APP_ENABLED" in environment:
                    value = environment["APP_ENABLED"].strip().lower()
                    if value not in {"true", "false"}:
                        raise ValueError("APP_ENABLED must be true or false")
                    overrides["enabled"] = value == "true"
                if "APP_LIMIT" in environment:
                    overrides["limit"] = int(environment["APP_LIMIT"])
                if "APP_TAGS" in environment:
                    overrides["tags"] = [tag.strip() for tag in environment["APP_TAGS"].split(",") if tag.strip()]
                return merge_settings(merge_settings(defaults, file_values), overrides)
        ''',
    }
    return case(CASES[5], "settings-overlay", """
Repair the configuration overlay contract shared by config_merge and settings.
Precedence is defaults, then file_values, then recognized environment variables.
merge_settings recursively merges nonempty dictionaries, retaining unspecified
nested defaults; an explicit empty dictionary clears that value. None means
'unset, keep the previous value'. False, 0, empty strings and empty lists are
explicit values and must override defaults. Return independent data without
modifying or sharing mutable nested objects with either input.
Recursive rules also apply to newly introduced dictionaries: a None entry with
no previous value is omitted, while explicit empty dictionaries still clear.
load_settings recognizes APP_ENABLED (trimmed, case-insensitive true/false;
other values raise ValueError), APP_LIMIT (an integer string, including zero),
and APP_TAGS (comma-separated trimmed tags, dropping empty entries; blank means
an empty list). Ignore unknown environment names. Preserve these public APIs.
""", ["recursive value-preserving merge", "environment decoding and layer precedence"],
        baseline, reference, '''
            from minicode.config_merge import merge_settings
            from minicode.settings import load_settings

            def test_ordinary_values_and_precedence():
                assert merge_settings({"limit": 3}, {"limit": 5}) == {"limit": 5}
                assert load_settings({"enabled": False, "limit": 3}, {"limit": 5}, {"APP_ENABLED": "true", "APP_LIMIT": "8"}) == {"enabled": True, "limit": 8}

            def test_missing_values_keep_defaults():
                assert load_settings({"limit": 3}, {}, {"UNRELATED": "9"}) == {"limit": 3}
        ''', '''
            from copy import deepcopy
            from minicode.config_merge import merge_settings
            from minicode.settings import load_settings
            base = {"enabled": True, "limit": 10, "tags": ["default"], "name": "demo", "db": {"host": "db", "timeout": 3}, "clear": {"old": 1}}
            override = {"enabled": False, "limit": 0, "tags": [], "name": "", "db": {"timeout": 0}, "clear": {}}
            before = deepcopy((base, override))
            expected = {"enabled": False, "limit": 0, "tags": [], "name": "", "db": {"host": "db", "timeout": 0}, "clear": {}}
            check("falsey_recursive_values", merge_settings(base, override), expected)
            check("none_keeps_value", merge_settings(base, {"limit": None, "db": {"host": None}}), base)
            check("new_dictionary_none_unset", merge_settings({}, {"new": {"unset": None, "zero": 0}}), {"new": {"zero": 0}})
            env = {"APP_ENABLED": " FaLsE ", "APP_LIMIT": "0", "APP_TAGS": " ", "UNKNOWN": "ignored"}
            check("environment_zero_false_empty", load_settings(base, {"limit": 4}, env), {**base, "enabled": False, "limit": 0, "tags": []})
            check("tag_whitespace", load_settings({}, {}, {"APP_TAGS": " red, , blue,, "}), {"tags": ["red", "blue"]})
            check("invalid_boolean", raises_value_error(lambda: load_settings({}, {}, {"APP_ENABLED": "yes"})), True)
            merged = merge_settings(base, {"db": {"port": 9000}})
            merged["db"]["host"] = "changed"
            merged["tags"].append("changed")
            check("inputs_unchanged_and_unaliased", (base, override), before)
            new_values = {"new": {"items": [1]}}
            copied_values = deepcopy(new_values)
            from_override = merge_settings({}, new_values)
            from_override["new"]["items"].append(2)
            check("override_nested_unaliased", new_values, copied_values)
        ''')


def limit_clamp() -> tuple[dict, dict]:
    baseline = {"minicode/limits.py": '''
        def clamp_limit(value, minimum, maximum):
            """Clamp an integer setting to the inclusive supported interval."""
            if minimum > maximum:
                raise ValueError("minimum exceeds maximum")
            return max(minimum, value)
    '''}
    reference = {"minicode/limits.py": baseline["minicode/limits.py"].replace(
        'max(minimum, value)', 'min(maximum, max(minimum, value))')}
    return case(CASES[6], "known-function-clamp", """
Fix clamp_limit(value, minimum, maximum) in minicode/limits.py. Values above
maximum currently escape the documented upper bound. All inputs are integers;
return the nearest bound outside the inclusive interval, otherwise value itself.
Equal bounds are valid and minimum > maximum must still raise ValueError. This
is a local function correction; its location and required behavior are known.
""", ["known clamp_limit function"], baseline, reference, '''
        import pytest
        from minicode.limits import clamp_limit

        def test_within_and_below_interval():
            assert clamp_limit(4, 2, 8) == 4
            assert clamp_limit(0, 2, 8) == 2

        def test_invalid_interval():
            with pytest.raises(ValueError):
                clamp_limit(4, 8, 2)
    ''', '''
        from minicode.limits import clamp_limit
        for value, low, high, expected in ((9, 2, 8, 8), (2, 2, 8, 2), (8, 2, 8, 8),
                                          (-1, -8, -3, -3), (-10, -8, -3, -8),
                                          (20, 5, 5, 5), (0, 5, 5, 5)):
            check("clamp_" + str((value, low, high)), clamp_limit(value, low, high), expected)
        check("invalid_interval", raises_value_error(lambda: clamp_limit(0, 2, 1)), True)
    ''', negative=True)


def extension_case() -> tuple[dict, dict]:
    baseline = {"minicode/formats.py": '''
        from pathlib import PurePosixPath

        def accepts_extension(filename, allowed):
            """Match a nonempty final filename extension against allowed names."""
            suffix = PurePosixPath(filename).suffix.lstrip(".")
            return bool(suffix) and suffix in allowed
    '''}
    reference = {"minicode/formats.py": baseline["minicode/formats.py"].replace(
        'suffix in allowed', 'suffix.casefold() in {item.casefold() for item in allowed}')}
    return case(CASES[7], "known-function-extension", """
Fix accepts_extension(filename, allowed) in minicode/formats.py so extension
matching is case-insensitive on both the filename and allowed names. allowed
contains extension names without a leading dot. Only the final extension counts
(archive.TAR.GZ is gz); a filename with no nonempty extension, a dotfile such as
.env, or an empty allowed list returns False. Paths use POSIX separators.
The problem is confined to this known function; preserve its boolean API and
do not change the supplied allowed collection.
""", ["known accepts_extension function"], baseline, reference, '''
        from minicode.formats import accepts_extension

        def test_lowercase_and_absent_suffix():
            assert accepts_extension("report.txt", ["txt"])
            assert not accepts_extension("README", ["txt"])
            assert not accepts_extension("report.csv", ["txt"])
    ''', '''
        from minicode.formats import accepts_extension
        allowed = ["TxT", "gz"]
        for filename, expected in (("report.TXT", True), ("report.txt", True),
                                   ("folder/archive.TAR.GZ", True), ("archive.gz.zip", False),
                                   ("README", False), (".env", False), ("trailing.", False)):
            check(filename, accepts_extension(filename, allowed), expected)
        check("empty_allowlist", accepts_extension("report.txt", []), False)
        check("no_empty_extension", accepts_extension("README", [""]), False)
        check("allowed_unchanged", allowed, ["TxT", "gz"])
    ''', negative=True)


def build_cases() -> list[tuple[dict, dict]]:
    result = [factory() for factory in (pagination, import_retry, cart_discounts,
              stock_allocation, report_timezones, config_overlay, limit_clamp, extension_case)]
    assert tuple(item[0]["id"] for item in result) == CASES
    return result


def materialize(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def run(command: list[str], workspace: Path) -> dict:
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONPATH": str(workspace), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    for name in ("PYTEST_ADDOPTS", "PYTHONHOME"):
        environment.pop(name, None)
    result = subprocess.run(command, cwd=workspace, env=environment,
                            capture_output=True, text=True, timeout=45)
    return {"argv": command, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def target_failure_proven(baseline: dict, reference: dict) -> bool:
    """Require named behavioral checks, not an import failure or arbitrary exit."""
    try:
        before, after = (json.loads(item["stdout"]) for item in (baseline, reference))
        def valid_receipt(receipt: dict) -> bool:
            checks = receipt.get("checks")
            if not isinstance(checks, list) or not checks:
                return False
            if any(not isinstance(item, dict) or not isinstance(item.get("label"), str)
                   or type(item.get("passed")) is not bool
                   or "actual" not in item or "expected" not in item for item in checks):
                return False
            if any(item["passed"] is not (item["actual"] == item["expected"])
                   for item in checks):
                return False
            errors = [item["label"] for item in checks if not item["passed"]]
            return receipt.get("errors") == errors and receipt.get("passed") is (not errors)
        return (baseline["returncode"] == 1 and reference["returncode"] == 0
                and valid_receipt(before) and valid_receipt(after)
                and before["passed"] is False and after["passed"] is True
                and [item["label"] for item in before["checks"]]
                    == [item["label"] for item in after["checks"]])
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def prepare(output: Path) -> None:
    if output.exists():
        raise ValueError("output already exists; preserve offline evidence")
    built = build_cases()
    output.mkdir(parents=True)
    cases, references, proof = [], [], []
    for value, reference in built:
        cases.append(value)
        references.append({"id": value["id"], "files": reference})
        evidence = {"case": value["id"], "origin": "synthetic",
                    "oracleSha256": sha(value["oracle_source"].encode()),
                    "publicTestSha256": sha(value["files"]["tests/test_public.py"].encode())}
        with tempfile.TemporaryDirectory(prefix="trigger-proof-") as temporary:
            parent = Path(temporary)
            oracle_path = parent / "oracle.py"
            oracle_path.write_text(value["oracle_source"], encoding="utf-8")
            for label, files in (("baseline", value["files"]),
                                 ("reference", {**value["files"], **reference})):
                workspace = parent / label
                materialize(workspace, {**SHARED_FILES, **files})
                evidence[label + "Public"] = run(
                    [sys.executable, "-B", "-m", "pytest", "-q", "-o", "addopts=", "tests/test_public.py"], workspace)
                evidence[label + "Oracle"] = run(
                    [sys.executable, "-B", str(oracle_path), str(workspace), str(parent / "response.txt")], workspace)
            proof.append(evidence)
            write_json(output / "offline-proof.json", proof)
            if evidence["baselinePublic"]["returncode"] or evidence["referencePublic"]["returncode"]:
                raise ValueError(value["id"] + ": public compatibility failed")
            if not target_failure_proven(evidence["baselineOracle"], evidence["referenceOracle"]):
                raise ValueError(value["id"] + ": behavior counterexample not proven")
    write_json(output / "tasks.json", {"generation": GENERATION, "shared_files": SHARED_FILES, "cases": cases})
    write_json(output / "reference-fixes.json", {"generation": GENERATION, "cases": references})
    write_json(output / "source-manifest.json", {
        "schemaVersion": 1, "generation": GENERATION, "origin": "synthetic",
        "sharedFiles": {path: sha(text.encode()) for path, text in SHARED_FILES.items()},
        "cases": [{"id": value["id"], "triggerExpectation": value["triggerExpectation"],
                   "sourceBlobs": {path: {"baselineSha256": sha(value["files"][path].encode()),
                                          "referenceSha256": sha(reference[path].encode())}
                                   for path in value["sourcePaths"]},
                   "publicTestSha256": sha(value["files"]["tests/test_public.py"].encode()),
                   "oracleSha256": sha(value["oracle_source"].encode())}
                  for value, reference in built],
        "offlineProofSha256": sha((output / "offline-proof.json").read_bytes()),
        "taskSha256": sha((output / "tasks.json").read_bytes()),
        "referenceFixesSha256": sha((output / "reference-fixes.json").read_bytes()),
        "modelCalls": 0,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args().output.resolve())
