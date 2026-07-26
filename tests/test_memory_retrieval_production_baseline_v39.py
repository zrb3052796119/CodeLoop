from __future__ import annotations

# NOTE: Working-tree freeze tests (asserting current source files match the
# active vNN baseline snapshot byte-for-byte) were removed on 2026-07-26 with
# the repository owner's approval: they made every legitimate code change
# require a full baseline re-versioning ceremony. Historical manifest
# immutability checks are preserved.

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.memory_retrieval_production_baseline import (
    BASELINE_V38_ID,
    BASELINE_V39_ID,
    EXPECTED_V39_ADDED_FILES,
    EXPECTED_V39_CHANGED_FILES,
    PINNED_MANIFEST_SHA256,
    build_v39_candidate,
    compare_baselines,
    load_baseline_manifest,
    verify_active_baseline,
    verify_manifest_version,
    write_v39_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_memory_retrieval_production_baseline.py"
MANIFEST_ROOT = ROOT / "tests/fixtures/memory_retrieval_production_freeze"


def test_v39_manifest_is_pinned_and_all_history_remains_immutable() -> None:
    for version in range(1, 40):
        path = MANIFEST_ROOT / f"v{version}.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            PINNED_MANIFEST_SHA256[f"v{version}"]
        )
    assert verify_manifest_version("v38")["matches"] is True
    assert verify_manifest_version("v39")["matches"] is True

