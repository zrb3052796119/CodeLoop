"""Decide whether another repair or review round can finish before the deadline.

The estimate is the slowest positive duration already observed for that kind
of round. The first round has no sample and is allowed to start.
"""

from __future__ import annotations


def estimate_round_seconds(samples: list[float]) -> float | None:
    """Return the slowest observed round, or None when nothing has finished."""

    positive: list[float] = []
    for sample in samples:
        if isinstance(sample, bool) or not isinstance(sample, (int, float)):
            continue
        if sample > 0:
            positive.append(float(sample))
    if not positive:
        return None
    return max(positive)


def round_fits(*, remaining_seconds: float | None, samples: list[float]) -> bool:
    """True when the deadline is open or the remaining time covers the estimate."""

    if remaining_seconds is None:
        return True
    estimate = estimate_round_seconds(samples)
    if estimate is None:
        return True
    return float(remaining_seconds) >= estimate


__all__ = ["estimate_round_seconds", "round_fits"]
