"""Batch-only analysis of the Hybrid Memory admission challenger."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from minicode.memory_hybrid import HYBRID_CHALLENGER_SYSTEM_PROMPT
from minicode.memory_hybrid_runtime import _parse_audits, _stable_json


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--verifier-model", default="qwen3.7-plus")
    parser.add_argument("--case-id", action="append", default=[])
    args = parser.parse_args()
    dataset = json.loads((args.fixture / "holdout.json").read_text(encoding="utf-8"))

    try:
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass
    from minicode.config import load_runtime_config
    from minicode.env_file import apply_env_file
    from minicode.model_registry import create_dedicated_model_adapter

    apply_env_file([args.env_file])
    runtime = dict(
        load_runtime_config(ROOT),
        maxOutputTokens=5000,
        temperature=0,
        modelMaxRetries=1,
        modelTimeoutSeconds=90,
    )
    model = create_dedicated_model_adapter(args.verifier_model, None, runtime)
    selected_ids = set(args.case_id)
    selected_cases = [
        case
        for case in dataset["cases"]
        if not selected_ids or case["case_id"] in selected_ids
    ]
    if selected_ids and selected_ids != {
        case["case_id"] for case in selected_cases
    }:
        raise SystemExit("unknown development case ID")
    pairs = [
        {
            "id": case["case_id"],
            "query": case["query"],
            "currentFiles": case["current_files"],
            "activeDomains": case["active_domains"],
            "memory": case["entry"],
        }
        for case in selected_cases
    ]
    audits = []
    for start in range(0, len(pairs), 20):
        batch = pairs[start : start + 20]
        step = model.next(
            [
                {"role": "system", "content": HYBRID_CHALLENGER_SYSTEM_PROMPT},
                {"role": "user", "content": _stable_json({"pairs": batch})},
            ]
        )
        audits.extend(_parse_audits(step.content, [pair["id"] for pair in batch]))
    by_id = {audit["id"]: audit for audit in audits}
    rows = [
        {
            "id": case["case_id"],
            "polarity": case["polarity"],
            "relation": case["relation"],
            **by_id[case["case_id"]],
        }
        for case in selected_cases
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
