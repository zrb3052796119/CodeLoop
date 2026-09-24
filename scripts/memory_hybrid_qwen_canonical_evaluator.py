"""Retired v7 Qwen Hybrid Memory promotion entry point.

The v7 holdout and schema-2/protocol-v4 evidence remain in ``artifacts/`` as
historical records. They cannot authorize the current protocol. New promotion
runs must use :mod:`scripts.memory_hybrid_qwen_v2_evaluator`, whose independent
holdout, resolver pressure gate, runtime source freeze, and one-shot lifecycle
are part of the production contract.
"""

from __future__ import annotations

from typing import Any, NoReturn


_RETIRED_MESSAGE = (
    "the v7 Qwen canonical evaluator is retired; use "
    "python -m scripts.memory_hybrid_qwen_v2_evaluator"
)


def build_qwen_production_evidence(_report: dict[str, Any]) -> NoReturn:
    """Reject attempts to mint current evidence from the spent v7 holdout."""

    raise RuntimeError(_RETIRED_MESSAGE)


def main() -> NoReturn:
    raise SystemExit(_RETIRED_MESSAGE)


if __name__ == "__main__":
    main()
