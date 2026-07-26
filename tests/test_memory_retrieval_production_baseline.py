from __future__ import annotations

# NOTE: Working-tree freeze tests (asserting current source files match the
# active vNN baseline snapshot byte-for-byte) were removed on 2026-07-26 with
# the repository owner's approval: they made every legitimate code change
# require a full baseline re-versioning ceremony. Historical manifest
# immutability checks are preserved.

import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.memory_retrieval_production_baseline import (
    BaselineCertificationError,
    BASELINE_V1_ID,
    BASELINE_V2_ID,
    BASELINE_V3_ID,
    BASELINE_V4_ID,
    BASELINE_V5_ID,
    BASELINE_V6_ID,
    BASELINE_V7_ID,
    BASELINE_V8_ID,
    BASELINE_V9_ID,
    BASELINE_V10_ID,
    BASELINE_V11_ID,
    BASELINE_V12_ID,
    BASELINE_V13_ID,
    BASELINE_V14_ID,
    BASELINE_V15_ID,
    BASELINE_V16_ID,
    BASELINE_V17_ID,
    BASELINE_V18_ID,
    BASELINE_V19_ID,
    BASELINE_V20_ID,
    BASELINE_V21_ID,
    BASELINE_V22_ID,
    BASELINE_V23_ID,
    BASELINE_V24_ID,
    BASELINE_V25_ID,
    BASELINE_V26_ID,
    BASELINE_V27_ID,
    BASELINE_V28_ID,
    BASELINE_V29_ID,
    BASELINE_V30_ID,
    BASELINE_V31_ID,
    BASELINE_V32_ID,
    BASELINE_V33_ID,
    BASELINE_V39_ID,
    EXPECTED_ADDED_FILES,
    EXPECTED_CHANGED_FILES,
    EXPECTED_V3_CHANGED_FILES,
    EXPECTED_V4_ADDED_FILES,
    EXPECTED_V4_CHANGED_FILES,
    EXPECTED_V5_CHANGED_FILES,
    EXPECTED_V6_CHANGED_FILES,
    EXPECTED_V7_CHANGED_FILES,
    EXPECTED_V8_ADDED_FILES,
    EXPECTED_V8_CHANGED_FILES,
    EXPECTED_V9_ADDED_FILES,
    EXPECTED_V9_CHANGED_FILES,
    EXPECTED_V10_ADDED_FILES,
    EXPECTED_V10_CHANGED_FILES,
    EXPECTED_V11_ADDED_FILES,
    EXPECTED_V11_CHANGED_FILES,
    EXPECTED_V12_CHANGED_FILES,
    EXPECTED_V13_CHANGED_FILES,
    EXPECTED_V14_ADDED_FILES,
    EXPECTED_V14_CHANGED_FILES,
    EXPECTED_V15_ADDED_FILES,
    EXPECTED_V15_CHANGED_FILES,
    EXPECTED_V16_ADDED_FILES,
    EXPECTED_V16_CHANGED_FILES,
    EXPECTED_V17_CHANGED_FILES,
    EXPECTED_V18_ADDED_FILES,
    EXPECTED_V18_CHANGED_FILES,
    EXPECTED_V19_ADDED_FILES,
    EXPECTED_V19_CHANGED_FILES,
    EXPECTED_V20_CHANGED_FILES,
    EXPECTED_V21_ADDED_FILES,
    EXPECTED_V21_CHANGED_FILES,
    EXPECTED_V22_ADDED_FILES,
    EXPECTED_V22_CHANGED_FILES,
    EXPECTED_V23_ADDED_FILES,
    EXPECTED_V23_CHANGED_FILES,
    EXPECTED_V24_ADDED_FILES,
    EXPECTED_V24_CHANGED_FILES,
    EXPECTED_V25_ADDED_FILES,
    EXPECTED_V25_CHANGED_FILES,
    EXPECTED_V26_ADDED_FILES,
    EXPECTED_V26_CHANGED_FILES,
    EXPECTED_V27_ADDED_FILES,
    EXPECTED_V27_CHANGED_FILES,
    EXPECTED_V28_ADDED_FILES,
    EXPECTED_V28_CHANGED_FILES,
    EXPECTED_V29_ADDED_FILES,
    EXPECTED_V29_CHANGED_FILES,
    EXPECTED_V30_ADDED_FILES,
    EXPECTED_V30_CHANGED_FILES,
    EXPECTED_V31_ADDED_FILES,
    EXPECTED_V31_CHANGED_FILES,
    EXPECTED_V32_ADDED_FILES,
    EXPECTED_V32_CHANGED_FILES,
    EXPECTED_V33_ADDED_FILES,
    EXPECTED_V33_CHANGED_FILES,
    EXPECTED_V34_ADDED_FILES,
    EXPECTED_V34_CHANGED_FILES,
    EXPECTED_V35_ADDED_FILES,
    EXPECTED_V35_CHANGED_FILES,
    EXPECTED_V39_ADDED_FILES,
    EXPECTED_V39_CHANGED_FILES,
    EXPECTED_V1_FILES,
    build_v2_candidate,
    build_v3_candidate,
    build_v4_candidate,
    build_v5_candidate,
    build_v6_candidate,
    build_v7_candidate,
    build_v8_candidate,
    build_v9_candidate,
    build_v10_candidate,
    build_v11_candidate,
    build_v12_candidate,
    build_v13_candidate,
    build_v16_candidate,
    build_v17_candidate,
    build_v18_candidate,
    build_v19_candidate,
    build_v20_candidate,
    build_v21_candidate,
    build_v22_candidate,
    build_v23_candidate,
    build_v24_candidate,
    build_v25_candidate,
    build_v26_candidate,
    build_v27_candidate,
    build_v28_candidate,
    build_v29_candidate,
    build_v30_candidate,
    build_v31_candidate,
    build_v32_candidate,
    compare_baselines,
    load_baseline_manifest,
    validate_manifest,
    verify_active_baseline,
    verify_manifest_version,
    write_v5_manifest,
    write_v6_manifest,
    write_v7_manifest,
    write_v8_manifest,
    write_v9_manifest,
    write_v11_manifest,
    write_v12_manifest,
    write_v13_manifest,
    write_v14_manifest,
    write_v15_manifest,
    write_v16_manifest,
    write_v17_manifest,
    write_v18_manifest,
    write_v19_manifest,
    write_v20_manifest,
    write_v22_manifest,
    write_v23_manifest,
    write_v24_manifest,
    write_v25_manifest,
    write_v26_manifest,
    write_v27_manifest,
    write_v28_manifest,
    write_v29_manifest,
    write_v30_manifest,
    write_v31_manifest,
    write_v32_manifest,
    write_v33_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = PROJECT_ROOT / "scripts/generate_memory_retrieval_production_baseline.py"
EXPECTED_V1_TO_V16_MANIFEST_SHA256 = {
    "v1": "b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417",
    "v2": "15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab",
    "v3": "0722314faf0476e1566657578782a6437d8d218a779ef24bde474351a5b86522",
    "v4": "5034b342d68c9a8ef7b450fe2f4bcbda370204f92d3c87f05caf15eec1002e10",
    "v5": "70ece17f53ec7963395aadc3be2b104636c2804087928d45c707ee94a5e672ff",
    "v6": "623366c6d895d057ef03fc7e719d9d2c3dfdd6e4e1f394b355dc6441daaae89b",
    "v7": "120bec4ee33cbbee5d5d056024b96e3e331c1b3101cc6dbe36beaec8fd17ebf4",
    "v8": "13a70abaed1091d17bc137fcffab336349ab6d22cf7f503133bf6efd1cb37726",
    "v9": "3444072607489ec4cc2405b8fb09fe9bcb122f9427f4b94d25aa66b9aa52d4d0",
    "v10": "bc94fe753ba0a30a5b74f9e3d242d9ede4395244fbdebb8f0d1e9992d992dbdb",
    "v11": "c5d12d47e25db4ebd566f066420d398f7b04a53b518a407003784d8261371c71",
    "v12": "a8fba6ed9134b465167525f4b8c81de2369363ad0527f6368527de0369bd05a7",
    "v13": "ef295a3aa3dcfc522d4cc421310434de3013772122f3b913b6b137144a96fc2c",
    "v14": "c00bff9983800f3d1ae579aaa5ed20de2671b3e3162aa8942db709b91d5093ce",
    "v15": "f9e6254c59f8e7b4065c70aba28c20e8d53361e252866a1519264be92704df7a",
    "v16": "80fa4db12cb43f904a0d89cf0d32df7bd389fda1001c55b6447d7d1a5355decb",
}
EXPECTED_V17_MANIFEST_SHA256 = (
    "2ac1d7185488dd1008407e4711fc3777213dcc1cd405e104f44bf6ca20206857"
)
EXPECTED_V18_MANIFEST_SHA256 = (
    "515d3cacd96365bc09bfb608df59ff1bfcc4b0c10cff1d1e4e114cb8ef6ecee5"
)
EXPECTED_V19_MANIFEST_SHA256 = (
    "9c48c5c0f02f48c49a31411292b1d65b1e52de4667c2048477343ff64eaa82c6"
)
EXPECTED_V20_MANIFEST_SHA256 = (
    "4104965fd30bdfeb06910701be6b53d0a623607f3965b15ed8f9d80809baca05"
)
EXPECTED_V21_MANIFEST_SHA256 = (
    "5a6422b0ae18649166e3e8d28c990a9736f457093f105db661f7ff4b40d8a8ff"
)
EXPECTED_V22_MANIFEST_SHA256 = (
    "a47b1e5f203371e9ced01fed01e6df37947a2a0e891c1bee6c2ed43a51e59906"
)
EXPECTED_V23_MANIFEST_SHA256 = (
    "c6cab0e867db309f9ddfbaf3034e269f4f65ce7b1c66e155997c0697b3388aa8"
)
EXPECTED_V24_MANIFEST_SHA256 = (
    "f6022dec899fbf083db090385dd4358560673817e25764e469d97548e827307f"
)
EXPECTED_V25_MANIFEST_SHA256 = (
    "c431a30e03e12aab5085f49eab22a86aa57c99190fb93fb7fcb0c207c4a22aef"
)
EXPECTED_V26_MANIFEST_SHA256 = (
    "b44abf36befb98723b26036530296f8675a0d92ae59884956767b352445ed936"
)
EXPECTED_V27_MANIFEST_SHA256 = (
    "18ad99488f7a73e71bbe30011d9c86a8de6ab077b5d1be8790718c6ffac14013"
)
EXPECTED_V28_MANIFEST_SHA256 = (
    "75c71d1d740b35f530965d7f797f4bbe3ceafb019129be3ee4d73d9256b453e5"
)
EXPECTED_V29_MANIFEST_SHA256 = (
    "e43777832841629549d180e039d40ac54209c5f15a3581e9bdf09b308592d4d1"
)
EXPECTED_V30_MANIFEST_SHA256 = (
    "55654b2b979812440514686b44c5bf09b5a0527a59709d37907ffb7ffd9c5edd"
)
EXPECTED_V31_MANIFEST_SHA256 = (
    "d0ea9a10ccd45d6f8e7807f92acfc38afce801f22e8be0967897653aed82fbae"
)
EXPECTED_V32_MANIFEST_SHA256 = (
    "9680f6f4bb61d3489a98fd63cff01d99f6a5af2c98891befbfb6c513fc023fb1"
)
EXPECTED_V33_MANIFEST_SHA256 = (
    "a5a6c84205d68c6c30f85724f1091d06593cf203dc8390514731d1b65e995313"
)


def test_historical_v1_manifest_preserves_its_identity_and_complete_file_set() -> None:
    manifest = load_baseline_manifest("v1")

    assert manifest["schemaVersion"] == 1
    assert manifest["baselineId"] == BASELINE_V1_ID
    assert manifest["parentBaselineId"] is None
    assert manifest["files"] == EXPECTED_V1_FILES
    assert manifest["allowedChangesFromParent"] == {}
    assert manifest["addedFiles"] == {}


def test_v2_candidate_declares_only_the_certified_parent_differences() -> None:
    v1 = load_baseline_manifest("v1")
    v2 = build_v2_candidate()
    difference = compare_baselines(v1, v2)

    assert v2["baselineId"] == BASELINE_V2_ID
    assert v2["parentBaselineId"] == BASELINE_V1_ID
    assert set(difference["changedFiles"]) == EXPECTED_CHANGED_FILES
    assert set(difference["addedFiles"]) == EXPECTED_ADDED_FILES
    assert difference["removedFiles"] == []
    assert set(v2["allowedChangesFromParent"]) == EXPECTED_CHANGED_FILES
    assert set(v2["addedFiles"]) == EXPECTED_ADDED_FILES
    assert all(
        details["reasonCode"] == "lifecycle_observer_entrypoint"
        for details in v2["allowedChangesFromParent"].values()
    )
    assert all(
        details["reasonCode"] == "lifecycle_observer_dependency"
        for details in v2["addedFiles"].values()
    )


def test_all_manifests_remain_individually_verifiable() -> None:
    assert verify_manifest_version("v1")["matches"] is True
    assert verify_manifest_version("v2")["matches"] is True
    assert verify_manifest_version("v3")["matches"] is True
    assert verify_manifest_version("v4")["matches"] is True
    assert verify_manifest_version("v5")["matches"] is True
    assert verify_manifest_version("v6")["matches"] is True
    assert verify_manifest_version("v7")["matches"] is True
    assert verify_manifest_version("v8")["matches"] is True
    assert verify_manifest_version("v9")["matches"] is True
    assert verify_manifest_version("v10")["matches"] is True
    assert verify_manifest_version("v11")["matches"] is True
    assert verify_manifest_version("v12")["matches"] is True
    assert verify_manifest_version("v13")["matches"] is True
    assert verify_manifest_version("v14")["matches"] is True
    assert verify_manifest_version("v15")["matches"] is True
    assert verify_manifest_version("v16")["matches"] is True
    assert verify_manifest_version("v17")["matches"] is True
    assert verify_manifest_version("v18")["matches"] is True
    assert verify_manifest_version("v19")["matches"] is True
    assert verify_manifest_version("v20")["matches"] is True
    assert verify_manifest_version("v21")["matches"] is True
    assert verify_manifest_version("v22")["matches"] is True
    assert verify_manifest_version("v23")["matches"] is True
    assert verify_manifest_version("v24")["matches"] is True
    assert verify_manifest_version("v25")["matches"] is True
    assert verify_manifest_version("v26")["matches"] is True
    assert verify_manifest_version("v27")["matches"] is True
    assert verify_manifest_version("v28")["matches"] is True
    assert verify_manifest_version("v29")["matches"] is True
    assert verify_manifest_version("v30")["matches"] is True
    assert verify_manifest_version("v31")["matches"] is True
    assert verify_manifest_version("v32")["matches"] is True
    assert verify_manifest_version("v33")["matches"] is True
    assert verify_manifest_version("v34")["matches"] is True
    assert verify_manifest_version("v35")["matches"] is True


def test_v1_to_v16_manifest_bytes_remain_at_their_accepted_pins() -> None:
    for version, expected in EXPECTED_V1_TO_V16_MANIFEST_SHA256.items():
        path = (
            PROJECT_ROOT
            / "tests"
            / "fixtures"
            / "memory_retrieval_production_freeze"
            / f"{version}.json"
        )
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_v17_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v17.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V17_MANIFEST_SHA256


def test_v18_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v18.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V18_MANIFEST_SHA256


def test_v19_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v19.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V19_MANIFEST_SHA256


def test_v20_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v20.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V20_MANIFEST_SHA256


def test_v21_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v21.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V21_MANIFEST_SHA256


def test_v22_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v22.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V22_MANIFEST_SHA256


def test_v23_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v23.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V23_MANIFEST_SHA256


def test_v24_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v24.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V24_MANIFEST_SHA256


def test_v25_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v25.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V25_MANIFEST_SHA256


def test_v26_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v26.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V26_MANIFEST_SHA256


def test_v27_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v27.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V27_MANIFEST_SHA256


def test_v28_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v28.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V28_MANIFEST_SHA256


def test_v29_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v29.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V29_MANIFEST_SHA256


def test_v30_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v30.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V30_MANIFEST_SHA256


def test_v31_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v31.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V31_MANIFEST_SHA256


def test_v32_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v32.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V32_MANIFEST_SHA256


def test_v33_manifest_bytes_match_the_new_accepted_pin() -> None:
    path = PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v33.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_V33_MANIFEST_SHA256


def test_v3_candidate_declares_only_callback_trace_changes_from_v2() -> None:
    v2 = load_baseline_manifest("v2")
    v3 = build_v3_candidate()
    difference = compare_baselines(v2, v3)

    assert v3["baselineId"] == BASELINE_V3_ID
    assert v3["parentBaselineId"] == BASELINE_V2_ID
    assert set(difference["changedFiles"]) == EXPECTED_V3_CHANGED_FILES
    assert difference["addedFiles"] == []
    assert difference["removedFiles"] == []
    assert set(v3["allowedChangesFromParent"]) == EXPECTED_V3_CHANGED_FILES
    assert v3["addedFiles"] == {}
    assert v3["allowedChangesFromParent"]["minicode/run_lifecycle.py"] == {
        "reasonCode": "execution_trace_observer"
    }
    for path in EXPECTED_V3_CHANGED_FILES - {"minicode/run_lifecycle.py"}:
        assert v3["allowedChangesFromParent"][path] == {
            "reasonCode": "execution_trace_entrypoint"
        }


def test_historical_v3_manifest_is_the_deterministic_pinned_evidence() -> None:
    assert load_baseline_manifest("v3") == build_v3_candidate()


def test_v4_candidate_declares_only_model_event_sink_changes_from_v3() -> None:
    v3 = load_baseline_manifest("v3")
    v4 = build_v4_candidate()
    difference = compare_baselines(v3, v4)

    assert v4["baselineId"] == BASELINE_V4_ID
    assert v4["parentBaselineId"] == BASELINE_V3_ID
    assert set(difference["changedFiles"]) == EXPECTED_V4_CHANGED_FILES
    assert set(difference["addedFiles"]) == EXPECTED_V4_ADDED_FILES
    assert difference["removedFiles"] == []
    assert set(v4["allowedChangesFromParent"]) == EXPECTED_V4_CHANGED_FILES
    assert set(v4["addedFiles"]) == EXPECTED_V4_ADDED_FILES
    assert v4["allowedChangesFromParent"]["minicode/agent_loop.py"] == {
        "reasonCode": "model_event_sink"
    }
    assert v4["allowedChangesFromParent"]["minicode/run_lifecycle.py"] == {
        "reasonCode": "model_event_observer"
    }
    for path in EXPECTED_V4_CHANGED_FILES - {
        "minicode/agent_loop.py",
        "minicode/run_lifecycle.py",
    }:
        assert v4["allowedChangesFromParent"][path] == {
            "reasonCode": "model_event_entrypoint"
        }
    assert v4["addedFiles"]["minicode/run_events.py"] == {
        "reasonCode": "model_event_sink_dependency"
    }


def test_historical_v4_manifest_is_the_deterministic_pinned_evidence() -> None:
    assert load_baseline_manifest("v4") == build_v4_candidate()


def test_v5_candidate_declares_only_runtime_event_changes_from_v4() -> None:
    v4 = load_baseline_manifest("v4")
    v5 = build_v5_candidate()
    difference = compare_baselines(v4, v5)

    assert v5["baselineId"] == BASELINE_V5_ID
    assert v5["parentBaselineId"] == BASELINE_V4_ID
    assert set(difference["changedFiles"]) == EXPECTED_V5_CHANGED_FILES
    assert difference["addedFiles"] == []
    assert difference["removedFiles"] == []
    assert set(v5["allowedChangesFromParent"]) == EXPECTED_V5_CHANGED_FILES
    assert v5["addedFiles"] == {}
    assert v5["allowedChangesFromParent"]["minicode/agent_loop.py"] == {
        "reasonCode": "runtime_memory_observer"
    }
    assert v5["allowedChangesFromParent"]["minicode/run_events.py"] == {
        "reasonCode": "runtime_event_projection"
    }
    for path in EXPECTED_V5_CHANGED_FILES - {
        "minicode/agent_loop.py",
        "minicode/run_events.py",
    }:
        assert v5["allowedChangesFromParent"][path] == {
            "reasonCode": "skill_event_entrypoint"
        }


def test_active_v5_manifest_is_the_deterministic_current_candidate() -> None:
    assert load_baseline_manifest("v5") == build_v5_candidate()


def test_v6_candidate_declares_only_model_usage_observation_changes_from_v5() -> None:
    v5 = load_baseline_manifest("v5")
    v6 = build_v6_candidate()
    difference = compare_baselines(v5, v6)

    assert v6["baselineId"] == BASELINE_V6_ID
    assert v6["parentBaselineId"] == BASELINE_V5_ID
    assert set(difference["changedFiles"]) == EXPECTED_V6_CHANGED_FILES
    assert difference["addedFiles"] == []
    assert difference["removedFiles"] == []
    assert v6["allowedChangesFromParent"] == {
        "minicode/agent_loop.py": {"reasonCode": "model_usage_observer"},
        "minicode/run_events.py": {"reasonCode": "model_usage_projection"},
    }
    assert v6["addedFiles"] == {}


def test_active_v6_manifest_is_the_deterministic_current_candidate() -> None:
    assert load_baseline_manifest("v6") == build_v6_candidate()


def test_v7_candidate_declares_only_work_chain_disabled_initialization() -> None:
    v6 = load_baseline_manifest("v6")
    v7 = build_v7_candidate()
    difference = compare_baselines(v6, v7)

    assert v7["baselineId"] == BASELINE_V7_ID
    assert v7["parentBaselineId"] == BASELINE_V6_ID
    assert set(difference["changedFiles"]) == EXPECTED_V7_CHANGED_FILES
    assert difference["addedFiles"] == []
    assert difference["removedFiles"] == []
    assert v7["allowedChangesFromParent"] == {
        "minicode/agent_loop.py": {"reasonCode": "work_chain_disabled_initialization"}
    }
    assert v7["addedFiles"] == {}


def test_active_v7_manifest_is_the_deterministic_current_candidate() -> None:
    assert load_baseline_manifest("v7") == build_v7_candidate()


def test_v8_candidate_declares_only_canonical_model_cost_observation() -> None:
    v7 = load_baseline_manifest("v7")
    v8 = build_v8_candidate()
    difference = compare_baselines(v7, v8)

    assert v8["baselineId"] == BASELINE_V8_ID
    assert v8["parentBaselineId"] == BASELINE_V7_ID
    assert set(difference["changedFiles"]) == EXPECTED_V8_CHANGED_FILES
    assert set(difference["addedFiles"]) == EXPECTED_V8_ADDED_FILES
    assert difference["removedFiles"] == []
    assert set(v8["allowedChangesFromParent"]) == EXPECTED_V8_CHANGED_FILES
    assert set(v8["addedFiles"]) == EXPECTED_V8_ADDED_FILES
    for details in (
        *v8["allowedChangesFromParent"].values(),
        *v8["addedFiles"].values(),
    ):
        assert details == {"reasonCode": "canonical_model_cost_observation"}


def test_active_v8_manifest_is_the_deterministic_current_candidate() -> None:
    assert load_baseline_manifest("v8") == build_v8_candidate()


def test_v9_candidate_declares_only_context_working_memory_observation() -> None:
    v8 = load_baseline_manifest("v8")
    v9 = build_v9_candidate()
    difference = compare_baselines(v8, v9)

    assert v9["baselineId"] == BASELINE_V9_ID
    assert v9["parentBaselineId"] == BASELINE_V8_ID
    assert set(difference["changedFiles"]) == EXPECTED_V9_CHANGED_FILES
    assert set(difference["addedFiles"]) == EXPECTED_V9_ADDED_FILES
    assert difference["removedFiles"] == []
    assert set(v9["allowedChangesFromParent"]) == EXPECTED_V9_CHANGED_FILES
    assert set(v9["addedFiles"]) == EXPECTED_V9_ADDED_FILES
    for details in (
        *v9["allowedChangesFromParent"].values(),
        *v9["addedFiles"].values(),
    ):
        assert details == {"reasonCode": "context_working_memory_observation"}


def test_historical_v9_manifest_remains_pinned() -> None:
    assert load_baseline_manifest("v9")["baselineId"] == BASELINE_V9_ID


def test_v10_candidate_declares_only_mcp_runtime_observation() -> None:
    v9 = load_baseline_manifest("v9")
    v10 = build_v10_candidate()
    difference = compare_baselines(v9, v10)

    assert v10["schemaVersion"] == 1
    assert v10["baselineId"] == BASELINE_V10_ID
    assert v10["parentBaselineId"] == BASELINE_V9_ID
    assert v10["reason"] == "Batch 5C-1A run-scoped MCP runtime observation"
    assert set(v10["allowedChangesFromParent"]) == EXPECTED_V10_CHANGED_FILES
    assert set(v10["addedFiles"]) == EXPECTED_V10_ADDED_FILES
    assert EXPECTED_V10_ADDED_FILES == frozenset(
        {
            "minicode/mcp.py",
            "minicode/mcp_event_contract.py",
            "minicode/mcp_observation.py",
            "minicode/tooling.py",
        }
    )
    assert len(v10["files"]) == 19
    assert difference == {
        "changedFiles": sorted(EXPECTED_V10_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V10_ADDED_FILES),
        "removedFiles": [],
    }
    assert all(
        details == {"reasonCode": "mcp_runtime_observation"}
        for details in (
            *v10["allowedChangesFromParent"].values(),
            *v10["addedFiles"].values(),
        )
    )


def test_active_v10_manifest_is_the_deterministic_current_candidate() -> None:
    assert load_baseline_manifest("v10") == build_v10_candidate()


def test_v11_candidate_declares_only_process_local_current_state_wiring() -> None:
    v10 = load_baseline_manifest("v10")
    v11 = build_v11_candidate()

    assert v11["schemaVersion"] == 1
    assert v11["baselineId"] == BASELINE_V11_ID
    assert v11["parentBaselineId"] == BASELINE_V10_ID
    assert v11["reason"] == "Batch 5C-2A process-local MCP current state"
    assert EXPECTED_V11_CHANGED_FILES == frozenset(
        {"minicode/headless.py", "minicode/mcp.py", "minicode/tooling.py"}
    )
    assert EXPECTED_V11_ADDED_FILES == frozenset(
        {
            "minicode/gateway.py",
            "minicode/mcp_current_state.py",
            "minicode/tools/__init__.py",
            "minicode/tools/task.py",
        }
    )
    assert compare_baselines(v10, v11) == {
        "changedFiles": sorted(EXPECTED_V11_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V11_ADDED_FILES),
        "removedFiles": [],
    }
    assert len(v11["files"]) == 23
    assert all(
        details == {"reasonCode": "mcp_current_state_observation"}
        for details in (
            *v11["allowedChangesFromParent"].values(),
            *v11["addedFiles"].values(),
        )
    )


def test_v12_candidate_declares_only_gateway_current_projection_wiring() -> None:
    v11 = load_baseline_manifest("v11")
    v12 = build_v12_candidate()

    assert v12["schemaVersion"] == 1
    assert v12["baselineId"] == BASELINE_V12_ID
    assert v12["parentBaselineId"] == BASELINE_V11_ID
    assert v12["reason"] == "Batch 5C-2B MCP current-state Dashboard projection"
    assert EXPECTED_V12_CHANGED_FILES == frozenset({"minicode/gateway.py"})
    assert v12["addedFiles"] == {}
    assert v12["allowedChangesFromParent"] == {
        "minicode/gateway.py": {"reasonCode": "mcp_current_state_projection"}
    }
    assert compare_baselines(v11, v12) == {
        "changedFiles": ["minicode/gateway.py"],
        "addedFiles": [],
        "removedFiles": [],
    }
    assert len(v12["files"]) == 23


def test_v13_candidate_declares_only_workspace_current_state_isolation() -> None:
    v12 = load_baseline_manifest("v12")
    v13 = build_v13_candidate()
    assert v13["baselineId"] == BASELINE_V13_ID
    assert v13["parentBaselineId"] == BASELINE_V12_ID
    assert v13["reason"] == "Batch 5C-2B.1 MCP current-state workspace isolation"
    assert v13["addedFiles"] == {}
    assert set(v13["allowedChangesFromParent"]) == EXPECTED_V13_CHANGED_FILES
    assert all(
        item == {"reasonCode": "mcp_current_state_workspace_isolation"}
        for item in v13["allowedChangesFromParent"].values()
    )
    assert compare_baselines(v12, v13) == {
        "changedFiles": ["minicode/gateway.py", "minicode/mcp_current_state.py"],
        "addedFiles": [],
        "removedFiles": [],
    }


def test_v14_candidate_declares_only_dashboard_chat_entrypoints() -> None:
    v13 = load_baseline_manifest("v13")
    v14 = load_baseline_manifest("v14")

    assert v14["baselineId"] == BASELINE_V14_ID
    assert v14["parentBaselineId"] == BASELINE_V13_ID
    assert v14["reason"] == (
        "Batch 6B-1 Dashboard Chat and Session-backed synchronous turn"
    )
    assert set(v14["allowedChangesFromParent"]) == EXPECTED_V14_CHANGED_FILES
    assert set(v14["addedFiles"]) == EXPECTED_V14_ADDED_FILES
    assert all(
        item == {"reasonCode": "dashboard_chat_session_turn"}
        for item in (
            *v14["allowedChangesFromParent"].values(),
            *v14["addedFiles"].values(),
        )
    )
    assert compare_baselines(v13, v14) == {
        "changedFiles": sorted(EXPECTED_V14_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V14_ADDED_FILES),
        "removedFiles": [],
    }


def test_v15_candidate_declares_only_durable_turn_identity_boundaries() -> None:
    v14 = load_baseline_manifest("v14")
    v15 = load_baseline_manifest("v15")

    assert v15["baselineId"] == BASELINE_V15_ID
    assert v15["parentBaselineId"] == BASELINE_V14_ID
    assert v15["reason"] == ("Batch 6B-2A durable turn identity and restart recovery")
    assert set(v15["allowedChangesFromParent"]) == EXPECTED_V15_CHANGED_FILES
    assert set(v15["addedFiles"]) == EXPECTED_V15_ADDED_FILES
    assert all(
        item == {"reasonCode": "dashboard_chat_durable_turn_identity"}
        for item in (
            *v15["allowedChangesFromParent"].values(),
            *v15["addedFiles"].values(),
        )
    )
    assert compare_baselines(v14, v15) == {
        "changedFiles": sorted(EXPECTED_V15_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V15_ADDED_FILES),
        "removedFiles": [],
    }


def test_v16_candidate_declares_only_cooperative_cancellation_boundaries() -> None:
    v15 = load_baseline_manifest("v15")
    v16 = build_v16_candidate()

    assert v16["baselineId"] == BASELINE_V16_ID
    assert v16["parentBaselineId"] == BASELINE_V15_ID
    assert v16["reason"] == (
        "Batch 6B-2B cooperative cancellation and commit-race safety"
    )
    assert set(v16["allowedChangesFromParent"]) == EXPECTED_V16_CHANGED_FILES
    assert set(v16["addedFiles"]) == EXPECTED_V16_ADDED_FILES
    assert all(
        item == {"reasonCode": "dashboard_chat_cooperative_cancellation"}
        for item in (
            *v16["allowedChangesFromParent"].values(),
            *v16["addedFiles"].values(),
        )
    )
    assert compare_baselines(v15, v16) == {
        "changedFiles": sorted(EXPECTED_V16_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V16_ADDED_FILES),
        "removedFiles": [],
    }


def test_v17_candidate_declares_only_cancellation_boundary_hardening() -> None:
    v16 = load_baseline_manifest("v16")
    v17 = build_v17_candidate()

    assert v17["baselineId"] == BASELINE_V17_ID
    assert v17["parentBaselineId"] == BASELINE_V16_ID
    assert v17["reason"] == "Batch 6B-2B.1 cancellation boundary hardening"
    assert set(v17["allowedChangesFromParent"]) == EXPECTED_V17_CHANGED_FILES
    assert v17["addedFiles"] == {}
    assert all(
        item == {"reasonCode": "dashboard_chat_cancellation_boundary_hardening"}
        for item in v17["allowedChangesFromParent"].values()
    )
    assert compare_baselines(v16, v17) == {
        "changedFiles": sorted(EXPECTED_V17_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }


def test_v18_candidate_declares_only_dashboard_live_refresh_foundation() -> None:
    v17 = load_baseline_manifest("v17")
    v18 = build_v18_candidate()

    assert v18["baselineId"] == BASELINE_V18_ID
    assert v18["parentBaselineId"] == BASELINE_V17_ID
    assert v18["reason"] == "Batch 7A Dashboard live refresh foundation"
    assert set(v18["allowedChangesFromParent"]) == EXPECTED_V18_CHANGED_FILES
    assert set(v18["addedFiles"]) == EXPECTED_V18_ADDED_FILES
    assert all(
        item == {"reasonCode": "dashboard_live_refresh_foundation"}
        for item in (
            *v18["allowedChangesFromParent"].values(),
            *v18["addedFiles"].values(),
        )
    )
    assert compare_baselines(v17, v18) == {
        "changedFiles": sorted(EXPECTED_V18_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V18_ADDED_FILES),
        "removedFiles": [],
    }


def test_v19_candidate_declares_only_dashboard_sse_event_transport() -> None:
    v18 = load_baseline_manifest("v18")
    v19 = build_v19_candidate()

    assert v19["baselineId"] == BASELINE_V19_ID
    assert v19["parentBaselineId"] == BASELINE_V18_ID
    assert v19["reason"] == "Batch 7A.1 versioned SSE event transport"
    assert set(v19["allowedChangesFromParent"]) == EXPECTED_V19_CHANGED_FILES
    assert set(v19["addedFiles"]) == EXPECTED_V19_ADDED_FILES
    assert all(
        item == {"reasonCode": "dashboard_sse_event_transport"}
        for item in (
            *v19["allowedChangesFromParent"].values(),
            *v19["addedFiles"].values(),
        )
    )
    assert compare_baselines(v18, v19) == {
        "changedFiles": sorted(EXPECTED_V19_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V19_ADDED_FILES),
        "removedFiles": [],
    }


def test_v20_candidate_declares_only_dashboard_sse_store_switchover() -> None:
    v19 = load_baseline_manifest("v19")
    v20 = build_v20_candidate()

    assert v20["baselineId"] == BASELINE_V20_ID
    assert v20["parentBaselineId"] == BASELINE_V19_ID
    assert v20["reason"] == "Batch 7B SSE-driven Dashboard store invalidation"
    assert set(v20["allowedChangesFromParent"]) == EXPECTED_V20_CHANGED_FILES
    assert v20["addedFiles"] == {}
    assert all(
        item == {"reasonCode": "dashboard_sse_store_switchover"}
        for item in v20["allowedChangesFromParent"].values()
    )
    assert compare_baselines(v19, v20) == {
        "changedFiles": sorted(EXPECTED_V20_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }


def test_v21_candidate_declares_only_connection_scoped_chat_streaming() -> None:
    v20 = load_baseline_manifest("v20")
    v21 = build_v21_candidate()

    assert v21["baselineId"] == BASELINE_V21_ID
    assert v21["parentBaselineId"] == BASELINE_V20_ID
    assert v21["reason"] == ("Batch 7C connection-scoped Assistant and Tool streaming")
    assert set(v21["allowedChangesFromParent"]) == EXPECTED_V21_CHANGED_FILES
    assert set(v21["addedFiles"]) == EXPECTED_V21_ADDED_FILES
    assert all(
        item == {"reasonCode": "dashboard_connection_scoped_chat_stream"}
        for item in (
            *v21["allowedChangesFromParent"].values(),
            *v21["addedFiles"].values(),
        )
    )
    assert compare_baselines(v20, v21) == {
        "changedFiles": sorted(EXPECTED_V21_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V21_ADDED_FILES),
        "removedFiles": [],
    }


def test_v22_candidate_declares_only_gateway_permission_authority() -> None:
    v21 = load_baseline_manifest("v21")
    v22 = build_v22_candidate()

    assert v22["baselineId"] == BASELINE_V22_ID
    assert v22["parentBaselineId"] == BASELINE_V21_ID
    assert v22["reason"] == (
        "Batch 8A-1 Gateway permission approval authority and safe HTTP contract"
    )
    assert set(v22["allowedChangesFromParent"]) == EXPECTED_V22_CHANGED_FILES
    assert set(v22["addedFiles"]) == EXPECTED_V22_ADDED_FILES
    assert all(
        item == {"reasonCode": "gateway_permission_approval_authority"}
        for item in (
            *v22["allowedChangesFromParent"].values(),
            *v22["addedFiles"].values(),
        )
    )
    assert compare_baselines(v21, v22) == {
        "changedFiles": sorted(EXPECTED_V22_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V22_ADDED_FILES),
        "removedFiles": [],
    }


def test_v23_candidate_declares_only_permission_command_review_hardening() -> None:
    v22 = load_baseline_manifest("v22")
    v23 = build_v23_candidate()

    assert v23["baselineId"] == BASELINE_V23_ID
    assert v23["parentBaselineId"] == BASELINE_V22_ID
    assert v23["reason"] == (
        "Batch 8A-1.1 permission command review projection hardening"
    )
    assert set(v23["allowedChangesFromParent"]) == EXPECTED_V23_CHANGED_FILES
    assert set(v23["addedFiles"]) == EXPECTED_V23_ADDED_FILES
    assert v23["allowedChangesFromParent"] == {
        "minicode/permission_approval.py": {
            "reasonCode": "gateway_permission_command_review_hardening"
        }
    }
    assert compare_baselines(v22, v23) == {
        "changedFiles": sorted(EXPECTED_V23_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V23_ADDED_FILES),
        "removedFiles": [],
    }


def test_v24_candidate_declares_only_dashboard_permission_ui_changes() -> None:
    v23 = load_baseline_manifest("v23")
    v24 = build_v24_candidate()

    assert v24["baselineId"] == BASELINE_V24_ID
    assert v24["parentBaselineId"] == BASELINE_V23_ID
    assert v24["reason"] == (
        "Batch 8A-2 Dashboard permission approval UI and realtime invalidation"
    )
    assert set(v24["allowedChangesFromParent"]) == EXPECTED_V24_CHANGED_FILES
    assert set(v24["addedFiles"]) == EXPECTED_V24_ADDED_FILES
    assert all(
        item == {"reasonCode": "dashboard_permission_approval_ui"}
        for item in v24["allowedChangesFromParent"].values()
    )
    assert compare_baselines(v23, v24) == {
        "changedFiles": sorted(EXPECTED_V24_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V24_ADDED_FILES),
        "removedFiles": [],
    }


def test_v25_candidate_declares_only_permission_ui_fail_closed_hardening() -> None:
    v24 = load_baseline_manifest("v24")
    v25 = build_v25_candidate()

    assert v25["baselineId"] == BASELINE_V25_ID
    assert v25["parentBaselineId"] == BASELINE_V24_ID
    assert v25["reason"] == "Batch 8A-2.1 permission UI fail-closed state hardening"
    assert set(v25["allowedChangesFromParent"]) == EXPECTED_V25_CHANGED_FILES
    assert set(v25["addedFiles"]) == EXPECTED_V25_ADDED_FILES
    assert v25["allowedChangesFromParent"] == {
        "minicode/web/static/assets/app.js": {
            "reasonCode": "dashboard_permission_ui_fail_closed_hardening"
        }
    }
    assert compare_baselines(v24, v25) == {
        "changedFiles": sorted(EXPECTED_V25_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }


def test_v26_candidate_declares_only_persistent_memory_approval_contract() -> None:
    v25 = load_baseline_manifest("v25")
    v26 = build_v26_candidate()

    assert v26["baselineId"] == BASELINE_V26_ID
    assert v26["parentBaselineId"] == BASELINE_V25_ID
    assert v26["reason"] == (
        "Batch 8C-1 persistent Memory approval authority and HTTP contract"
    )
    assert set(v26["allowedChangesFromParent"]) == EXPECTED_V26_CHANGED_FILES
    assert set(v26["addedFiles"]) == EXPECTED_V26_ADDED_FILES
    assert all(
        item == {"reasonCode": "persistent_memory_approval_authority"}
        for item in (
            *v26["allowedChangesFromParent"].values(),
            *v26["addedFiles"].values(),
        )
    )
    assert compare_baselines(v25, v26) == {
        "changedFiles": sorted(EXPECTED_V26_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V26_ADDED_FILES),
        "removedFiles": [],
    }


def test_v27_candidate_declares_only_memory_approval_read_hardening() -> None:
    v26 = load_baseline_manifest("v26")
    v27 = build_v27_candidate()

    assert v27["baselineId"] == BASELINE_V27_ID
    assert v27["parentBaselineId"] == BASELINE_V26_ID
    assert v27["reason"] == (
        "Batch 8C-1.1 Memory Approval read-only snapshot hardening"
    )
    assert set(v27["allowedChangesFromParent"]) == EXPECTED_V27_CHANGED_FILES
    assert set(v27["addedFiles"]) == EXPECTED_V27_ADDED_FILES
    assert v27["allowedChangesFromParent"] == {
        "minicode/memory_approval.py": {
            "reasonCode": "memory_approval_read_only_hardening"
        }
    }
    assert compare_baselines(v26, v27) == {
        "changedFiles": sorted(EXPECTED_V27_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V27_ADDED_FILES),
        "removedFiles": [],
    }


def test_v28_candidate_declares_only_workspace_diff_review_normalization() -> None:
    v27 = load_baseline_manifest("v27")
    v28 = build_v28_candidate()

    assert v28["baselineId"] == BASELINE_V28_ID
    assert v28["parentBaselineId"] == BASELINE_V27_ID
    assert v28["reason"] == ("Batch 8A-2.2 workspace-local Diff review normalization")
    assert set(v28["allowedChangesFromParent"]) == EXPECTED_V28_CHANGED_FILES
    assert set(v28["addedFiles"]) == EXPECTED_V28_ADDED_FILES
    assert v28["allowedChangesFromParent"] == {
        "minicode/file_review.py": {
            "reasonCode": "workspace_local_diff_review_normalization"
        },
        "minicode/permission_approval.py": {
            "reasonCode": "workspace_local_diff_review_normalization"
        },
    }
    assert compare_baselines(v27, v28) == {
        "changedFiles": sorted(EXPECTED_V28_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V28_ADDED_FILES),
        "removedFiles": [],
    }


def test_v29_candidate_declares_only_invisible_control_hardening() -> None:
    v28 = load_baseline_manifest("v28")
    v29 = build_v29_candidate()

    assert v29["baselineId"] == BASELINE_V29_ID
    assert v29["parentBaselineId"] == BASELINE_V28_ID
    assert v29["reason"] == ("Batch 8A-2.2.1 invisible control Diff fidelity hardening")
    assert set(v29["allowedChangesFromParent"]) == EXPECTED_V29_CHANGED_FILES
    assert set(v29["addedFiles"]) == EXPECTED_V29_ADDED_FILES
    assert v29["allowedChangesFromParent"] == {
        "minicode/file_review.py": {
            "reasonCode": "invisible_control_diff_fidelity_hardening"
        },
        "minicode/permission_approval.py": {
            "reasonCode": "invisible_control_diff_fidelity_hardening"
        },
    }
    assert compare_baselines(v28, v29) == {
        "changedFiles": sorted(EXPECTED_V29_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V29_ADDED_FILES),
        "removedFiles": [],
    }


def test_v30_candidate_declares_only_memory_approval_store_ui() -> None:
    v29 = load_baseline_manifest("v29")
    v30 = build_v30_candidate()

    assert v30["baselineId"] == BASELINE_V30_ID
    assert v30["parentBaselineId"] == BASELINE_V29_ID
    assert v30["reason"] == (
        "Batch 8C-2 persistent Memory approval Dashboard store and UI"
    )
    assert set(v30["allowedChangesFromParent"]) == EXPECTED_V30_CHANGED_FILES
    assert set(v30["addedFiles"]) == EXPECTED_V30_ADDED_FILES
    assert v30["allowedChangesFromParent"] == {
        "minicode/web/static/assets/app.js": {"reasonCode": "memory_approval_store_ui"},
        "minicode/web/static/assets/styles.css": {
            "reasonCode": "memory_approval_store_ui"
        },
    }
    assert compare_baselines(v29, v30) == {
        "changedFiles": sorted(EXPECTED_V30_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V30_ADDED_FILES),
        "removedFiles": [],
    }


def test_v31_candidate_declares_only_dashboard_data_deletion_authority() -> None:
    v30 = load_baseline_manifest("v30")
    v31 = build_v31_candidate()

    assert v31["baselineId"] == BASELINE_V31_ID
    assert v31["parentBaselineId"] == BASELINE_V30_ID
    assert set(v31["allowedChangesFromParent"]) == EXPECTED_V31_CHANGED_FILES
    assert set(v31["addedFiles"]) == EXPECTED_V31_ADDED_FILES
    assert compare_baselines(v30, v31) == {
        "changedFiles": sorted(EXPECTED_V31_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V31_ADDED_FILES),
        "removedFiles": [],
    }


def test_v32_candidate_declares_only_dashboard_data_deletion_ui() -> None:
    v31 = load_baseline_manifest("v31")
    v32 = build_v32_candidate()

    assert v32["baselineId"] == BASELINE_V32_ID
    assert v32["parentBaselineId"] == BASELINE_V31_ID
    assert set(v32["allowedChangesFromParent"]) == EXPECTED_V32_CHANGED_FILES
    assert set(v32["addedFiles"]) == EXPECTED_V32_ADDED_FILES
    assert v32["allowedChangesFromParent"] == {
        "minicode/web/static/assets/app.js": {
            "reasonCode": "dashboard_data_deletion_ui"
        },
        "minicode/web/static/assets/styles.css": {
            "reasonCode": "dashboard_data_deletion_ui"
        },
    }
    assert compare_baselines(v31, v32) == {
        "changedFiles": sorted(EXPECTED_V32_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V32_ADDED_FILES),
        "removedFiles": [],
    }


def test_v33_candidate_declares_only_read_only_persistence_health() -> None:
    v32 = load_baseline_manifest("v32")
    v33 = load_baseline_manifest("v33")

    assert v33["baselineId"] == BASELINE_V33_ID
    assert v33["parentBaselineId"] == BASELINE_V32_ID
    assert set(v33["allowedChangesFromParent"]) == EXPECTED_V33_CHANGED_FILES
    assert set(v33["addedFiles"]) == EXPECTED_V33_ADDED_FILES
    assert all(
        details == {"reasonCode": "persistence_inventory_read_only_health"}
        for details in (
            *v33["allowedChangesFromParent"].values(),
            *v33["addedFiles"].values(),
        )
    )
    assert compare_baselines(v32, v33) == {
        "changedFiles": sorted(EXPECTED_V33_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V33_ADDED_FILES),
        "removedFiles": [],
    }


def test_v8_v9_and_v10_run_journal_deltas_are_exactly_the_closed_allowlist() -> None:
    v7 = load_baseline_manifest("v7")
    v8 = load_baseline_manifest("v8")
    v9 = load_baseline_manifest("v9")
    v10 = load_baseline_manifest("v10")
    active = load_baseline_manifest("v39")
    path = PROJECT_ROOT / "minicode/run_journal.py"
    source = path.read_text(encoding="utf-8")

    assert source.count('        "model.costed",\n') == 1
    assert source.count('        "working_memory.observed",\n') == 1
    assert source.count('        "mcp.runtime.observed",\n') == 1
    assert source.count('        "permission.requested",\n') == 0
    assert (
        hashlib.sha256(source.encode("utf-8")).hexdigest()
        == active["files"]["minicode/run_journal.py"]
    )
    assert compare_baselines(v7, v8) == {
        "changedFiles": sorted(EXPECTED_V8_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V8_ADDED_FILES),
        "removedFiles": [],
    }
    assert compare_baselines(v8, v9) == {
        "changedFiles": sorted(EXPECTED_V9_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V9_ADDED_FILES),
        "removedFiles": [],
    }
    assert compare_baselines(v9, v10) == {
        "changedFiles": sorted(EXPECTED_V10_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V10_ADDED_FILES),
        "removedFiles": [],
    }


def test_v8_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("1", "777")):
        cwd = tmp_path / f"cwd-{index}"
        home = tmp_path / f"home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v8"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v9_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("3", "999")):
        cwd = tmp_path / f"v9-cwd-{index}"
        home = tmp_path / f"v9-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v9"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v11_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("11", "2026")):
        cwd = tmp_path / f"v11-cwd-{index}"
        home = tmp_path / f"v11-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v11"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v12_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("12", "2027")):
        cwd = tmp_path / f"v12-cwd-{index}"
        home = tmp_path / f"v12-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v12"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v13_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("13", "2028")):
        cwd = tmp_path / f"v13-cwd-{index}"
        home = tmp_path / f"v13-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v13"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v14_validation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("14", "2029")):
        cwd = tmp_path / f"v14-cwd-{index}"
        home = tmp_path / f"v14-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--write-v14"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v16_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("15", "2030")):
        cwd = tmp_path / f"v16-cwd-{index}"
        home = tmp_path / f"v16-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v16"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v17_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("17", "2031")):
        cwd = tmp_path / f"v17-cwd-{index}"
        home = tmp_path / f"v17-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v17"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V17_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v18_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("18", "2032")):
        cwd = tmp_path / f"v18-cwd-{index}"
        home = tmp_path / f"v18-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v18"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V18_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v19_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("19", "2033")):
        cwd = tmp_path / f"v19-cwd-{index}"
        home = tmp_path / f"v19-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v19"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V19_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v20_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("20", "2034")):
        cwd = tmp_path / f"v20-cwd-{index}"
        home = tmp_path / f"v20-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v20"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V20_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v21_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("21", "2107")):
        cwd = tmp_path / f"v21-cwd-{index}"
        home = tmp_path / f"v21-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v21"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V21_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v22_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("22", "2207")):
        cwd = tmp_path / f"v22-cwd-{index}"
        home = tmp_path / f"v22-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v22"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V22_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v23_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("23", "2307")):
        cwd = tmp_path / f"v23-cwd-{index}"
        home = tmp_path / f"v23-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v23"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V23_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v24_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("24", "2408")):
        cwd = tmp_path / f"v24-cwd-{index}"
        home = tmp_path / f"v24-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v24"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V24_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v25_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("25", "2501")):
        cwd = tmp_path / f"v25-cwd-{index}"
        home = tmp_path / f"v25-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v25"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V25_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v26_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("26", "2601")):
        cwd = tmp_path / f"v26-cwd-{index}"
        home = tmp_path / f"v26-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v26"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V26_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v27_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("27", "2701")):
        cwd = tmp_path / f"v27-cwd-{index}"
        home = tmp_path / f"v27-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v27"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V27_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v28_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("28", "2801")):
        cwd = tmp_path / f"v28-cwd-{index}"
        home = tmp_path / f"v28-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v28"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V28_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v29_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("29", "2901")):
        cwd = tmp_path / f"v29-cwd-{index}"
        home = tmp_path / f"v29-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v29"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V29_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v30_candidate_generation_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("30", "3001")):
        cwd = tmp_path / f"v30-cwd-{index}"
        home = tmp_path / f"v30-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v30"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V30_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v33_pinned_manifest_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    manifest_path = (
        PROJECT_ROOT / "tests/fixtures/memory_retrieval_production_freeze/v33.json"
    )
    for index, seed in enumerate(("33", "3301")):
        cwd = tmp_path / f"v33-cwd-{index}"
        home = tmp_path / f"v33-home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; import sys; "
                    "sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes())"
                ),
                str(manifest_path),
            ],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == EXPECTED_V33_MANIFEST_SHA256
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_manifest_schema_rejects_wildcards_and_contains_no_machine_or_secret_data() -> (
    None
):
    manifests = [
        load_baseline_manifest(version)
        for version in (
            "v1",
            "v2",
            "v3",
            "v4",
            "v5",
            "v6",
            "v7",
            "v8",
            "v9",
            "v10",
            "v11",
            "v12",
            "v13",
            "v14",
            "v15",
            "v16",
            "v17",
            "v18",
            "v19",
            "v20",
            "v21",
            "v22",
            "v23",
            "v24",
            "v25",
            "v26",
            "v27",
            "v28",
            "v29",
            "v30",
            "v31",
            "v32",
            "v33",
            "v34",
            "v35",
        )
    ]
    encoded = json.dumps(manifests, ensure_ascii=False, sort_keys=True)

    assert not re.search(r"/Users/|\bHOME\b|\bsk-[A-Za-z0-9_-]{20,}\b", encoded)
    assert "Bearer " not in encoded
    assert "Authorization" not in encoded
    assert "Cookie" not in encoded
    assert "generatedAt" not in encoded
    invalid = copy.deepcopy(manifests[8])
    invalid["allowedChangesFromParent"]["minicode/*.py"] = {
        "reasonCode": "lifecycle_observer_entrypoint"
    }
    with pytest.raises(BaselineCertificationError, match="invalid source path"):
        validate_manifest(invalid, version="v9")


def test_explicit_writer_uses_only_the_fixed_v5_target(tmp_path: Path) -> None:
    v4 = load_baseline_manifest("v4")
    paths = set(v4["files"]) | {
        "tests/fixtures/memory_retrieval_production_freeze/v1.json",
        "tests/fixtures/memory_retrieval_production_freeze/v2.json",
        "tests/fixtures/memory_retrieval_production_freeze/v3.json",
        "tests/fixtures/memory_retrieval_production_freeze/v4.json",
        "tests/fixtures/memory_retrieval_production_freeze/v5.json",
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    target = write_v5_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v5.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v5_candidate(
        project_root=tmp_path
    )


def test_explicit_writer_uses_only_the_fixed_v6_target(tmp_path: Path) -> None:
    v6 = load_baseline_manifest("v6")
    paths = set(v6["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 7)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v6.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v6_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert json.loads(target.read_text(encoding="utf-8")) == v6
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v7_target(tmp_path: Path) -> None:
    v7 = load_baseline_manifest("v7")
    paths = set(v7["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 8)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    target = write_v7_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v7.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v7_candidate(
        project_root=tmp_path
    )


def test_explicit_writer_uses_only_the_fixed_v8_target(tmp_path: Path) -> None:
    v7 = load_baseline_manifest("v7")
    paths = (
        set(v7["files"])
        | EXPECTED_V8_ADDED_FILES
        | {
            f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            for version in range(1, 9)
        }
    )
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    target = write_v8_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v8.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v8_candidate(
        project_root=tmp_path
    )


def test_explicit_writer_uses_only_the_fixed_v9_target(tmp_path: Path) -> None:
    v9 = load_baseline_manifest("v9")
    paths = set(v9["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 10)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v9.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v9_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v9.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == v9
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v11_target(tmp_path: Path) -> None:
    v11 = load_baseline_manifest("v11")
    paths = set(v11["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 12)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v11.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v11_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert json.loads(target.read_text(encoding="utf-8")) == v11
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v12_target(tmp_path: Path) -> None:
    v12 = load_baseline_manifest("v12")
    paths = {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 13)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v12.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v12_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert json.loads(target.read_text(encoding="utf-8")) == v12
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v13_target(tmp_path: Path) -> None:
    v13 = load_baseline_manifest("v13")
    paths = set(v13["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 14)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v13.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v13_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert json.loads(target.read_text(encoding="utf-8")) == v13
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v14_target(tmp_path: Path) -> None:
    v14 = load_baseline_manifest("v14")
    paths = set(v14["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 15)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v14.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v14_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert json.loads(target.read_text(encoding="utf-8")) == v14
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v15_target(tmp_path: Path) -> None:
    v15 = load_baseline_manifest("v15")
    paths = set(v15["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 16)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v15.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v15_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert json.loads(target.read_text(encoding="utf-8")) == v15
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v16_target(tmp_path: Path) -> None:
    v16 = load_baseline_manifest("v16")
    paths = {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 17)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v16.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v16_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert json.loads(target.read_text(encoding="utf-8")) == v16
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v17_target(tmp_path: Path) -> None:
    v17 = load_baseline_manifest("v17")
    paths = set(v17["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 18)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v17.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v17_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert json.loads(target.read_text(encoding="utf-8")) == v17
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v18_target(tmp_path: Path) -> None:
    v18 = load_baseline_manifest("v18")
    paths = set(v18["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 19)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v18.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v18_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert json.loads(target.read_text(encoding="utf-8")) == v18
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v19_target(tmp_path: Path) -> None:
    v19 = load_baseline_manifest("v19")
    paths = set(v19["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 21)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v19.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v19_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert json.loads(target.read_text(encoding="utf-8")) == build_v19_candidate(
        project_root=tmp_path
    )
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v20_target(tmp_path: Path) -> None:
    v19 = load_baseline_manifest("v19")
    paths = set(v19["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 21)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v20.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v20_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v20.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v20_candidate(
        project_root=tmp_path
    )
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v22_target(tmp_path: Path) -> None:
    v22 = load_baseline_manifest("v22")
    paths = set(v22["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 23)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v22.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v22_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert json.loads(target.read_text(encoding="utf-8")) == build_v22_candidate(
        project_root=tmp_path
    )
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v23_target(tmp_path: Path) -> None:
    v23 = load_baseline_manifest("v23")
    paths = set(v23["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 24)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v23.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v23_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v24_target(tmp_path: Path) -> None:
    v24 = load_baseline_manifest("v24")
    paths = set(v24["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 25)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v24.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v24_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v25_target(tmp_path: Path) -> None:
    v25 = load_baseline_manifest("v25")
    paths = set(v25["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 26)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v25.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v25_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_explicit_writer_uses_only_the_fixed_v26_target(tmp_path: Path) -> None:
    v26 = load_baseline_manifest("v26")
    paths = set(v26["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 28)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)

    manifest_path = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v26.json"
    )
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    target = write_v26_manifest(project_root=tmp_path)

    assert target == manifest_path
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before
    assert json.loads(target.read_text(encoding="utf-8")) == build_v26_candidate(
        project_root=tmp_path
    )


def test_explicit_writer_uses_only_the_fixed_v27_target(tmp_path: Path) -> None:
    v26 = load_baseline_manifest("v26")
    paths = set(v26["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 28)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    v26_path = tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v26.json"
    before_v26 = (v26_path.read_bytes(), v26_path.stat().st_mtime_ns)
    v27_path = tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v27.json"
    before_v27 = (v27_path.read_bytes(), v27_path.stat().st_mtime_ns)

    target = write_v27_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v27.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v27_candidate(
        project_root=tmp_path
    )
    assert (v26_path.read_bytes(), v26_path.stat().st_mtime_ns) == before_v26
    assert (v27_path.read_bytes(), v27_path.stat().st_mtime_ns) == before_v27


def test_explicit_writer_uses_only_the_fixed_v28_target(tmp_path: Path) -> None:
    v28 = load_baseline_manifest("v28")
    paths = set(v28["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 30)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    v28_path = tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v28.json"
    before_v28 = (v28_path.read_bytes(), v28_path.stat().st_mtime_ns)
    v29_path = tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v29.json"
    before_v29 = (v29_path.read_bytes(), v29_path.stat().st_mtime_ns)

    target = write_v28_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v28.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == v28
    assert (v28_path.read_bytes(), v28_path.stat().st_mtime_ns) == before_v28
    assert (v29_path.read_bytes(), v29_path.stat().st_mtime_ns) == before_v29


def test_explicit_writer_uses_only_the_fixed_v29_target(tmp_path: Path) -> None:
    v28 = load_baseline_manifest("v28")
    paths = set(v28["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 30)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    v28_path = tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v28.json"
    before_v28 = (v28_path.read_bytes(), v28_path.stat().st_mtime_ns)
    v29_path = tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v29.json"
    before_v29 = (v29_path.read_bytes(), v29_path.stat().st_mtime_ns)

    target = write_v29_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v29.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v29_candidate(
        project_root=tmp_path
    )
    assert (v28_path.read_bytes(), v28_path.stat().st_mtime_ns) == before_v28
    assert (v29_path.read_bytes(), v29_path.stat().st_mtime_ns) == before_v29


def test_explicit_writer_uses_only_the_fixed_v30_target(tmp_path: Path) -> None:
    v30 = load_baseline_manifest("v30")
    paths = set(v30["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 31)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    historical = {
        version: (
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            ).read_bytes(),
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            )
            .stat()
            .st_mtime_ns,
        )
        for version in range(1, 31)
    }

    target = write_v30_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v30.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v30_candidate(
        project_root=tmp_path
    )
    assert historical == {
        version: (
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            ).read_bytes(),
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            )
            .stat()
            .st_mtime_ns,
        )
        for version in range(1, 31)
    }


def test_explicit_writer_uses_only_the_fixed_v31_target(tmp_path: Path) -> None:
    v31 = load_baseline_manifest("v31")
    paths = set(v31["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 32)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    historical = {
        version: (
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            ).read_bytes(),
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            )
            .stat()
            .st_mtime_ns,
        )
        for version in range(1, 32)
    }

    target = write_v31_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v31.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v31_candidate(
        project_root=tmp_path
    )
    assert historical == {
        version: (
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            ).read_bytes(),
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            )
            .stat()
            .st_mtime_ns,
        )
        for version in range(1, 32)
    }


def test_explicit_writer_uses_only_the_fixed_v32_target(tmp_path: Path) -> None:
    v32 = load_baseline_manifest("v32")
    paths = set(v32["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 33)
    }
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    historical = {
        version: (
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            ).read_bytes(),
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            )
            .stat()
            .st_mtime_ns,
        )
        for version in range(1, 33)
    }

    target = write_v32_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v32.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v32_candidate(
        project_root=tmp_path
    )
    assert historical == {
        version: (
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            ).read_bytes(),
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            )
            .stat()
            .st_mtime_ns,
        )
        for version in range(1, 33)
    }


def test_explicit_writer_uses_only_the_fixed_v33_target(tmp_path: Path) -> None:
    v33 = load_baseline_manifest("v33")
    paths = (
        set(v33["files"])
        | {
            f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            for version in range(1, 34)
        }
    )
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    historical = {
        version: (
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            ).read_bytes(),
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            )
            .stat()
            .st_mtime_ns,
        )
        for version in range(1, 34)
    }

    target = write_v33_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v33.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == v33
    assert historical == {
        version: (
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            ).read_bytes(),
            (
                tmp_path
                / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
            )
            .stat()
            .st_mtime_ns,
        )
        for version in range(1, 34)
    }


def test_candidate_cli_failure_uses_a_generic_path_free_error(tmp_path: Path) -> None:
    v7 = load_baseline_manifest("v7")
    paths = (
        set(v7["files"])
        | EXPECTED_V8_ADDED_FILES
        | {
            "tests/fixtures/memory_retrieval_production_freeze/v1.json",
            "tests/fixtures/memory_retrieval_production_freeze/v2.json",
            "tests/fixtures/memory_retrieval_production_freeze/v3.json",
            "tests/fixtures/memory_retrieval_production_freeze/v4.json",
            "tests/fixtures/memory_retrieval_production_freeze/v5.json",
            "tests/fixtures/memory_retrieval_production_freeze/v6.json",
            "tests/fixtures/memory_retrieval_production_freeze/v7.json",
            "scripts/memory_retrieval_production_baseline.py",
            "scripts/generate_memory_retrieval_production_baseline.py",
        }
    )
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    tampered = tmp_path / "minicode/memory.py"
    tampered.write_bytes(tampered.read_bytes() + b"\n# controlled tamper\n")

    completed = subprocess.run(
        [
            sys.executable,
            str(tmp_path / "scripts/generate_memory_retrieval_production_baseline.py"),
            "--print-v8",
        ],
        cwd=tmp_path,
        capture_output=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == b""
    assert json.loads(completed.stdout) == {
        "matches": False,
        "error": {"code": "baseline_verification_failed"},
    }
    assert str(tmp_path).encode() not in completed.stdout
