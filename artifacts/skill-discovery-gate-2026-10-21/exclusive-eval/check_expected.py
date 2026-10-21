"""Recompute every E-case expected value from the authored constants.

Catches authoring arithmetic errors before any live trial runs. Each check
re-derives the expected value from EXCLUSIVE_SKILLS constants and the
evidence literals, then compares with cases-exclusive.json.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    dataset = json.loads((HERE / "cases-exclusive.json").read_text(encoding="utf-8"))
    expected_by_id = {c["id"]: c["expected"]["task"]["checks"] for c in dataset["cases"]}

    failures = []

    def expect(case_id: str, pointer: str, actual) -> None:
        (check,) = [c for c in expected_by_id[case_id] if c["pointer"] == pointer]
        if check["equals"] != actual:
            failures.append(f"{case_id}{pointer}: expected {check['equals']!r}, derived {actual!r}")

    # E01: budget = attempts_limit(4) x (timeout 480 + jitter 250); allowed >= 2000
    attempts_limit, jitter, timeout = 4, 250, 480
    budget = attempts_limit * (timeout + jitter)
    expect("E01", "/replay_budget_ms", budget)
    expect("E01", "/replay_allowed", budget >= 2000)
    expect("E01", "/attempts_limit", attempts_limit)

    # E02: shop.<resource>.<action>; wildcard never covers export
    expect("E02", "/minimal_missing_scope", "shop.orders.export")
    expect("E02", "/deny_expected", True)

    # E03: holdoff 90s; extension 45s from later bump (10:01:10)
    from datetime import datetime, timedelta, timezone
    bumps = [datetime(2026, 11, 2, 10, 0, 0, tzinfo=timezone.utc),
             datetime(2026, 11, 2, 10, 1, 10, tzinfo=timezone.utc)]
    window_end = bumps[0] + timedelta(seconds=90)
    if bumps[1] < window_end:
        window_end = bumps[1] + timedelta(seconds=45)
    purge = datetime(2026, 11, 2, 10, 2, 0, tzinfo=timezone.utc)
    expect("E03", "/holdoff_active", purge < window_end)
    expect("E03", "/holdoff_ends_utc", window_end.strftime("%Y-%m-%dT%H:%M:%SZ"))

    # E04: A<=7d intact 0.95; B<=14d intact 0.90; damaged cap C 0.70; >14d refused 0
    grades, credit = [], 0
    for days, packaging, price in [(5, "intact", 8000), (11, "intact", 5000),
                                   (3, "damaged", 10000), (20, "intact", 6000)]:
        if days > 14:
            grades.append("refused")
        elif packaging == "damaged":
            grades.append("C")
            credit += int(price * 0.70)
        elif days <= 7:
            grades.append("A")
            credit += int(price * 0.95)
        else:
            grades.append("B")
            credit += int(price * 0.90)
    expect("E04", "/grades", grades)
    expect("E04", "/total_credit_cents", credit)

    # E05: skew 300s; nonce window 600s; 401 skew, 409 replay, else 200
    def ts(text):
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    statuses = []
    for sent, received, first_seen in [
        ("2026-11-03T08:00:00Z", "2026-11-03T08:03:50Z", None),
        ("2026-11-03T08:00:00Z", "2026-11-03T08:07:00Z", None),
        ("2026-11-03T08:05:00Z", "2026-11-03T08:05:30Z", "2026-11-03T08:02:00Z"),
    ]:
        if abs((ts(received) - ts(sent)).total_seconds()) > 300:
            statuses.append(401)
        elif first_seen and 0 <= (ts(received) - ts(first_seen)).total_seconds() <= 600:
            statuses.append(409)
        else:
            statuses.append(200)
    expect("E05", "/statuses", statuses)

    # E06: alert iff ratio >= 50 AND returned >= 8; equality, range, order-by
    ratio = 61000 // 7
    expect("E06", "/scan_to_result_ratio", ratio)
    expect("E06", "/alert", ratio >= 50 and 7 >= 8)
    expect("E06", "/index_columns", ["tenant_id", "created_at", "id"])

    # E07: shares 30/45/25 of 1200; breach when p95 > own budget
    budgets = {"gateway": int(1200 * 0.30), "payment_api": int(1200 * 0.45),
               "presentation": int(1200 * 0.25)}
    p95 = {"gateway": 310, "payment_api": 580, "presentation": 260}
    expect("E07", "/budgets_ms", budgets)
    expect("E07", "/breached", sorted(c for c, b in p95.items() if b > budgets[c]))

    # E08: deadline 45s minus margin 10s
    first = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
    effective = first + timedelta(seconds=45 - 10)
    expect("E08", "/commit_deadline_utc", effective.strftime("%Y-%m-%dT%H:%M:%SZ"))
    commits = {"m1": "2026-11-04T12:00:30Z", "m2": "2026-11-04T12:00:40Z",
               "m3": "2026-11-04T12:01:20Z"}
    expect("E08", "/late_ids", sorted(m for m, c in commits.items() if ts(c) > effective))

    if failures:
        print("FAILURES:")
        for line in failures:
            print(" ", line)
        raise SystemExit(1)
    print("all E-case expected values re-derived correctly")


if __name__ == "__main__":
    main()
