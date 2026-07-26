"""Versioned Memory Retrieval production-source baseline contracts through v39."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_ROOT = Path("tests/fixtures/memory_retrieval_production_freeze")
BASELINE_V1_ID = "memory-retrieval-production-v1"
BASELINE_V2_ID = "memory-retrieval-production-v2"
BASELINE_V3_ID = "memory-retrieval-production-v3"
BASELINE_V4_ID = "memory-retrieval-production-v4"
BASELINE_V5_ID = "memory-retrieval-production-v5"
BASELINE_V6_ID = "memory-retrieval-production-v6"
BASELINE_V7_ID = "memory-retrieval-production-v7"
BASELINE_V8_ID = "memory-retrieval-production-v8"
BASELINE_V9_ID = "memory-retrieval-production-v9"
BASELINE_V10_ID = "memory-retrieval-production-v10"
BASELINE_V11_ID = "memory-retrieval-production-v11"
BASELINE_V12_ID = "memory-retrieval-production-v12"
BASELINE_V13_ID = "memory-retrieval-production-v13"
BASELINE_V14_ID = "memory-retrieval-production-v14"
BASELINE_V15_ID = "memory-retrieval-production-v15"
BASELINE_V16_ID = "memory-retrieval-production-v16"
BASELINE_V17_ID = "memory-retrieval-production-v17"
BASELINE_V18_ID = "memory-retrieval-production-v18"
BASELINE_V19_ID = "memory-retrieval-production-v19"
BASELINE_V20_ID = "memory-retrieval-production-v20"
BASELINE_V21_ID = "memory-retrieval-production-v21"
BASELINE_V22_ID = "memory-retrieval-production-v22"
BASELINE_V23_ID = "memory-retrieval-production-v23"
BASELINE_V24_ID = "memory-retrieval-production-v24"
BASELINE_V25_ID = "memory-retrieval-production-v25"
BASELINE_V26_ID = "memory-retrieval-production-v26"
BASELINE_V27_ID = "memory-retrieval-production-v27"
BASELINE_V28_ID = "memory-retrieval-production-v28"
BASELINE_V29_ID = "memory-retrieval-production-v29"
BASELINE_V30_ID = "memory-retrieval-production-v30"
BASELINE_V31_ID = "memory-retrieval-production-v31"
BASELINE_V32_ID = "memory-retrieval-production-v32"
BASELINE_V33_ID = "memory-retrieval-production-v33"
BASELINE_V34_ID = "memory-retrieval-production-v34"
BASELINE_V35_ID = "memory-retrieval-production-v35"
BASELINE_V36_ID = "memory-retrieval-production-v36"
BASELINE_V37_ID = "memory-retrieval-production-v37"
BASELINE_V38_ID = "memory-retrieval-production-v38"
BASELINE_V39_ID = "memory-retrieval-production-v39"
PINNED_MANIFEST_SHA256 = {
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
    "v17": "2ac1d7185488dd1008407e4711fc3777213dcc1cd405e104f44bf6ca20206857",
    "v18": "515d3cacd96365bc09bfb608df59ff1bfcc4b0c10cff1d1e4e114cb8ef6ecee5",
    "v19": "9c48c5c0f02f48c49a31411292b1d65b1e52de4667c2048477343ff64eaa82c6",
    "v20": "4104965fd30bdfeb06910701be6b53d0a623607f3965b15ed8f9d80809baca05",
    "v21": "5a6422b0ae18649166e3e8d28c990a9736f457093f105db661f7ff4b40d8a8ff",
    "v22": "a47b1e5f203371e9ced01fed01e6df37947a2a0e891c1bee6c2ed43a51e59906",
    "v23": "c6cab0e867db309f9ddfbaf3034e269f4f65ce7b1c66e155997c0697b3388aa8",
    "v24": "f6022dec899fbf083db090385dd4358560673817e25764e469d97548e827307f",
    "v25": "c431a30e03e12aab5085f49eab22a86aa57c99190fb93fb7fcb0c207c4a22aef",
    "v26": "b44abf36befb98723b26036530296f8675a0d92ae59884956767b352445ed936",
    "v27": "18ad99488f7a73e71bbe30011d9c86a8de6ab077b5d1be8790718c6ffac14013",
    "v28": "75c71d1d740b35f530965d7f797f4bbe3ceafb019129be3ee4d73d9256b453e5",
    "v29": "e43777832841629549d180e039d40ac54209c5f15a3581e9bdf09b308592d4d1",
    "v30": "55654b2b979812440514686b44c5bf09b5a0527a59709d37907ffb7ffd9c5edd",
    "v31": "d0ea9a10ccd45d6f8e7807f92acfc38afce801f22e8be0967897653aed82fbae",
    "v32": "9680f6f4bb61d3489a98fd63cff01d99f6a5af2c98891befbfb6c513fc023fb1",
    "v33": "a5a6c84205d68c6c30f85724f1091d06593cf203dc8390514731d1b65e995313",
    "v34": "3136e096a97192de5078882523106f5179cb20a3e9885c050fd187038f815cbb",
    "v35": "bc2f16ee8f19dc7d59b878e35324486acd0cd110f16602ed722d3f4163572fc4",
    "v36": "7d576aed1594c58e96d3125c28e2556ffab7bb60ccdd43c97b462201456a678a",
    "v37": "27dda6944d88016ceabcd08960b3b2ef230df7460590d1165b3195ed23adb67b",
    "v38": "49f3319b06289ef23ab8c2f40bc3da0deaf443cb365f654cd2d1683a42b727f3",
    "v39": "9bcf038d20aa7c044f4db613626b484e2fa89819929be20b46390bca00a99d6e",
}
ALLOWED_REASON_CODES = frozenset(
    {
        "lifecycle_observer_entrypoint",
        "lifecycle_observer_dependency",
        "execution_trace_observer",
        "execution_trace_entrypoint",
        "model_event_sink",
        "model_event_observer",
        "model_event_entrypoint",
        "model_event_sink_dependency",
        "runtime_memory_observer",
        "runtime_event_projection",
        "skill_event_entrypoint",
        "model_usage_observer",
        "model_usage_projection",
        "work_chain_disabled_initialization",
        "canonical_model_cost_observation",
        "context_working_memory_observation",
        "mcp_runtime_observation",
        "mcp_current_state_observation",
        "mcp_current_state_projection",
        "mcp_current_state_workspace_isolation",
        "dashboard_chat_session_turn",
        "dashboard_chat_durable_turn_identity",
        "dashboard_chat_cooperative_cancellation",
        "dashboard_chat_cancellation_boundary_hardening",
        "dashboard_live_refresh_foundation",
        "dashboard_sse_event_transport",
        "dashboard_sse_store_switchover",
        "dashboard_connection_scoped_chat_stream",
        "gateway_permission_approval_authority",
        "gateway_permission_command_review_hardening",
        "dashboard_permission_approval_ui",
        "dashboard_permission_ui_fail_closed_hardening",
        "persistent_memory_approval_authority",
        "memory_approval_read_only_hardening",
        "workspace_local_diff_review_normalization",
        "invisible_control_diff_fidelity_hardening",
        "memory_approval_store_ui",
        "dashboard_data_deletion_authority",
        "dashboard_data_deletion_ui",
        "persistence_inventory_read_only_health",
        "dashboard_waku_visual_shell",
        "dashboard_agent_observatory_core_pages",
        "http_request_network_safety",
        "bounded_dns_resolver_capacity",
        "web_fetch_safe_transport_boundary",
        "web_search_provider_chain",
    }
)
EXPECTED_CHANGED_FILES = frozenset(
    {
        "minicode/headless.py",
        "minicode/main.py",
        "minicode/tui/input_handler.py",
    }
)
EXPECTED_ADDED_FILES = frozenset(
    {"minicode/run_lifecycle.py", "minicode/run_journal.py"}
)
EXPECTED_V3_CHANGED_FILES = frozenset(
    {
        "minicode/run_lifecycle.py",
        "minicode/headless.py",
        "minicode/main.py",
        "minicode/tui/input_handler.py",
    }
)
EXPECTED_V4_CHANGED_FILES = frozenset(
    {
        "minicode/agent_loop.py",
        "minicode/run_lifecycle.py",
        "minicode/headless.py",
        "minicode/main.py",
        "minicode/tui/input_handler.py",
    }
)
EXPECTED_V4_ADDED_FILES = frozenset({"minicode/run_events.py"})
EXPECTED_V5_CHANGED_FILES = frozenset(
    {
        "minicode/agent_loop.py",
        "minicode/run_events.py",
        "minicode/headless.py",
        "minicode/main.py",
        "minicode/tui/input_handler.py",
    }
)
EXPECTED_V6_CHANGED_FILES = frozenset(
    {"minicode/agent_loop.py", "minicode/run_events.py"}
)
EXPECTED_V7_CHANGED_FILES = frozenset({"minicode/agent_loop.py"})
EXPECTED_V8_CHANGED_FILES = frozenset(
    {"minicode/agent_loop.py", "minicode/run_journal.py"}
)
EXPECTED_V8_ADDED_FILES = frozenset({"minicode/pricing.py"})
EXPECTED_V9_CHANGED_FILES = frozenset(
    {"minicode/agent_loop.py", "minicode/run_events.py", "minicode/run_journal.py"}
)
EXPECTED_V9_ADDED_FILES = frozenset({"minicode/working_memory.py"})
EXPECTED_V10_CHANGED_FILES = frozenset(
    {"minicode/agent_loop.py", "minicode/run_journal.py"}
)
EXPECTED_V10_ADDED_FILES = frozenset(
    {
        "minicode/mcp.py",
        "minicode/mcp_event_contract.py",
        "minicode/mcp_observation.py",
        "minicode/tooling.py",
    }
)
EXPECTED_V11_CHANGED_FILES = frozenset(
    {
        "minicode/headless.py",
        "minicode/mcp.py",
        "minicode/tooling.py",
    }
)
EXPECTED_V11_ADDED_FILES = frozenset(
    {
        "minicode/gateway.py",
        "minicode/mcp_current_state.py",
        "minicode/tools/__init__.py",
        "minicode/tools/task.py",
    }
)
EXPECTED_V12_CHANGED_FILES = frozenset({"minicode/gateway.py"})
EXPECTED_V13_CHANGED_FILES = frozenset(
    {"minicode/gateway.py", "minicode/mcp_current_state.py"}
)
EXPECTED_V14_CHANGED_FILES = frozenset(
    {
        "minicode/gateway.py",
        "minicode/headless.py",
        "minicode/run_lifecycle.py",
    }
)
EXPECTED_V14_ADDED_FILES = frozenset(
    {
        "minicode/agent_runtime.py",
        "minicode/conversation.py",
        "minicode/web/chat_http.py",
    }
)
EXPECTED_V15_CHANGED_FILES = frozenset(
    {
        "minicode/conversation.py",
        "minicode/web/chat_http.py",
    }
)
EXPECTED_V15_ADDED_FILES = frozenset(
    {
        "minicode/conversation_turn_store.py",
        "minicode/session.py",
        "minicode/web/http.py",
        "minicode/web/static/assets/app.js",
    }
)
EXPECTED_V16_CHANGED_FILES = frozenset(
    {
        "minicode/agent_loop.py",
        "minicode/agent_runtime.py",
        "minicode/conversation.py",
        "minicode/conversation_turn_store.py",
        "minicode/gateway.py",
        "minicode/run_lifecycle.py",
        "minicode/web/chat_http.py",
        "minicode/web/static/assets/app.js",
    }
)
EXPECTED_V16_ADDED_FILES = frozenset(
    {
        "minicode/turn_cancellation.py",
        "minicode/web/static/assets/styles.css",
        "minicode/web/static/index.html",
    }
)
EXPECTED_V17_CHANGED_FILES = frozenset(
    {
        "minicode/conversation.py",
        "minicode/conversation_turn_store.py",
        "minicode/web/static/assets/app.js",
    }
)
EXPECTED_V18_CHANGED_FILES = frozenset(
    {
        "minicode/gateway.py",
        "minicode/web/http.py",
        "minicode/web/static/assets/app.js",
        "minicode/web/static/assets/styles.css",
        "minicode/web/static/index.html",
    }
)
EXPECTED_V18_ADDED_FILES = frozenset(
    {
        "minicode/web/change_feed.py",
        "minicode/web/read_model.py",
    }
)
EXPECTED_V19_CHANGED_FILES = frozenset(
    {
        "minicode/gateway.py",
        "minicode/web/http.py",
    }
)
EXPECTED_V19_ADDED_FILES = frozenset({"minicode/web/event_stream.py"})
EXPECTED_V20_CHANGED_FILES = frozenset(
    {
        "minicode/web/static/assets/app.js",
        "minicode/web/static/assets/styles.css",
        "minicode/web/static/index.html",
    }
)
EXPECTED_V21_CHANGED_FILES = frozenset(
    {
        "minicode/agent_runtime.py",
        "minicode/conversation.py",
        "minicode/web/chat_http.py",
        "minicode/web/static/assets/app.js",
        "minicode/web/static/assets/styles.css",
        "minicode/web/static/index.html",
    }
)
EXPECTED_V21_ADDED_FILES = frozenset(
    {
        "minicode/conversation_presentation.py",
        "minicode/web/chat_stream.py",
    }
)
EXPECTED_V22_CHANGED_FILES = frozenset(
    {
        "minicode/agent_loop.py",
        "minicode/agent_runtime.py",
        "minicode/conversation.py",
        "minicode/gateway.py",
        "minicode/run_journal.py",
        "minicode/web/http.py",
        "minicode/web/read_model.py",
    }
)
EXPECTED_V22_ADDED_FILES = frozenset(
    {
        "minicode/file_review.py",
        "minicode/permission_approval.py",
        "minicode/permission_event_contract.py",
        "minicode/permissions.py",
        "minicode/tools/run_command.py",
        "minicode/web/permission_http.py",
        "minicode/workspace.py",
    }
)
EXPECTED_V23_CHANGED_FILES = frozenset({"minicode/permission_approval.py"})
EXPECTED_V23_ADDED_FILES: frozenset[str] = frozenset()
EXPECTED_V24_CHANGED_FILES = frozenset(
    {
        "minicode/gateway.py",
        "minicode/web/change_feed.py",
        "minicode/web/event_stream.py",
        "minicode/web/http.py",
        "minicode/web/static/index.html",
        "minicode/web/static/assets/app.js",
        "minicode/web/static/assets/styles.css",
    }
)
EXPECTED_V24_ADDED_FILES: frozenset[str] = frozenset()
EXPECTED_V25_CHANGED_FILES = frozenset({"minicode/web/static/assets/app.js"})
EXPECTED_V25_ADDED_FILES: frozenset[str] = frozenset()
EXPECTED_V26_CHANGED_FILES = frozenset(
    {
        "minicode/gateway.py",
        "minicode/memory.py",
        "minicode/memory_pipeline.py",
        "minicode/web/http.py",
    }
)
EXPECTED_V26_ADDED_FILES = frozenset(
    {
        "minicode/agent_reflection.py",
        "minicode/memory_approval.py",
        "minicode/memory_curator_agent.py",
        "minicode/memory_store.py",
        "minicode/web/memory_approval_http.py",
    }
)
EXPECTED_V27_CHANGED_FILES = frozenset({"minicode/memory_approval.py"})
EXPECTED_V27_ADDED_FILES: frozenset[str] = frozenset()
EXPECTED_V28_CHANGED_FILES = frozenset(
    {"minicode/file_review.py", "minicode/permission_approval.py"}
)
EXPECTED_V28_ADDED_FILES: frozenset[str] = frozenset()
EXPECTED_V29_CHANGED_FILES = frozenset(
    {"minicode/file_review.py", "minicode/permission_approval.py"}
)
EXPECTED_V29_ADDED_FILES: frozenset[str] = frozenset()
EXPECTED_V30_CHANGED_FILES = frozenset(
    {"minicode/web/static/assets/app.js", "minicode/web/static/assets/styles.css"}
)
EXPECTED_V30_ADDED_FILES: frozenset[str] = frozenset()
EXPECTED_V31_CHANGED_FILES = frozenset(
    {
        "minicode/conversation_turn_store.py",
        "minicode/gateway.py",
        "minicode/memory.py",
        "minicode/run_journal.py",
        "minicode/session.py",
        "minicode/web/change_feed.py",
        "minicode/web/http.py",
    }
)
EXPECTED_V31_ADDED_FILES = frozenset(
    {
        "minicode/conversation_deletion.py",
        "minicode/deletion_store.py",
        "minicode/project_memory_deletion.py",
        "minicode/web/data_management_http.py",
    }
)
EXPECTED_V32_CHANGED_FILES = frozenset(
    {"minicode/web/static/assets/app.js", "minicode/web/static/assets/styles.css"}
)
EXPECTED_V32_ADDED_FILES: frozenset[str] = frozenset()
EXPECTED_V33_CHANGED_FILES = frozenset(
    {
        "minicode/gateway.py",
        "minicode/web/http.py",
        "minicode/web/static/assets/app.js",
        "minicode/web/static/assets/styles.css",
    }
)
EXPECTED_V33_ADDED_FILES = frozenset(
    {
        "minicode/storage_health.py",
        "minicode/web/storage_health_http.py",
    }
)
EXPECTED_V34_CHANGED_FILES = frozenset(
    {
        "minicode/web/static/index.html",
        "minicode/web/static/assets/styles.css",
        "minicode/web/static/assets/app.js",
    }
)
EXPECTED_V34_ADDED_FILES: frozenset[str] = frozenset()
EXPECTED_V35_CHANGED_FILES = frozenset(
    {
        "minicode/web/static/index.html",
        "minicode/web/static/assets/styles.css",
        "minicode/web/static/assets/app.js",
    }
)
EXPECTED_V35_ADDED_FILES: frozenset[str] = frozenset()
EXPECTED_V36_CHANGED_FILES = frozenset(
    {
        "minicode/permission_approval.py",
        "minicode/permission_event_contract.py",
        "minicode/permissions.py",
        "minicode/tooling.py",
        "minicode/web/static/assets/app.js",
    }
)
EXPECTED_V36_ADDED_FILES = frozenset(
    {
        "minicode/tools/http_utils.py",
        "minicode/tools/network_safety.py",
    }
)
EXPECTED_V37_CHANGED_FILES = frozenset(
    {"minicode/tools/network_safety.py"}
)
EXPECTED_V37_ADDED_FILES = frozenset(
    {"minicode/tools/bounded_resolver.py"}
)
EXPECTED_V38_CHANGED_FILES = frozenset(
    {
        "minicode/tools/http_utils.py",
    }
)
EXPECTED_V38_ADDED_FILES = frozenset(
    {"minicode/tools/web_fetch.py"}
)
EXPECTED_V39_CHANGED_FILES = frozenset(
    {
        "minicode/tools/http_utils.py",
    }
)
EXPECTED_V39_ADDED_FILES = frozenset(
    {
        "minicode/tools/search_providers.py",
        "minicode/tools/web_search.py",
    }
)

EXPECTED_V1_FILES = {
    "minicode/agent_loop.py": "a9980e6df7e9f3bb9858ea3b1907e6e6eb02e5fe7a90d533bf8096a741981d5e",
    "minicode/context_compactor.py": "f05bd72a46be720bfa9b50e42e4824825b5e532c1b8885ae6db1ab7a316b4bec",
    "minicode/headless.py": "de48f0f28eb4772fe6267695418eee6a30dc674f1cb95bd39823d8f73ef099c8",
    "minicode/main.py": "04e73b50e60aeea48cc2f35cccb4dc660400a06178d50a7feec4856b686f229a",
    "minicode/memory.py": "2706a3e684c84fc830a7c553b907f45a06899c6a0d091a5c366c1dfbd3741108",
    "minicode/memory_candidate_consolidation.py": "a2f44bd09a8594819f740f001c819458962fb00181206cf16a5f529b3169c6e4",
    "minicode/memory_injector.py": "059d2812f0fa92e2d4db2ee3463b5843c6c84194b83ed11da1de0ebd921721ee",
    "minicode/memory_pipeline.py": "a71062f86268010245ce1e924d65be8314bcc07d2afe142e5797726f957c35a1",
    "minicode/memory_retrieval.py": "33b27c4e5ea32321d6ba776918da374b91ea04b7358649fc8e0167b56c576376",
    "minicode/tui/input_handler.py": "b075996ec3b661a488e688678c46e408a600546ce792eb9b9c2bf0da22f2c3e8",
}


class BaselineCertificationError(ValueError):
    """The source tree cannot be certified by the declared lineage."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_files(project_root: Path, paths: set[str] | frozenset[str]) -> dict[str, str]:
    root = Path(project_root).resolve()
    return {
        relative: sha256_file(root / relative)
        if (root / relative).is_file()
        else "missing"
        for relative in sorted(paths)
    }


def _manifest_path(version: str, project_root: Path) -> Path:
    if version not in {
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
        "v36",
        "v37",
        "v38",
        "v39",
    }:
        raise ValueError("unsupported production baseline version")
    return Path(project_root) / BASELINE_ROOT / f"{version}.json"


def _validate_relative_path(value: str) -> None:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or any(character in value for character in "*?[]{}")
    ):
        raise BaselineCertificationError("baseline contains an invalid source path")


def validate_manifest(manifest: dict[str, Any], *, version: str) -> None:
    """Validate the closed manifest schema and the version-specific contract."""
    required = {
        "schemaVersion",
        "baselineId",
        "parentBaselineId",
        "reason",
        "files",
        "allowedChangesFromParent",
        "addedFiles",
    }
    if set(manifest) != required or manifest.get("schemaVersion") != 1:
        raise BaselineCertificationError("baseline manifest schema is invalid")
    files = manifest.get("files")
    allowed = manifest.get("allowedChangesFromParent")
    added = manifest.get("addedFiles")
    if (
        not isinstance(files, dict)
        or not isinstance(allowed, dict)
        or not isinstance(added, dict)
    ):
        raise BaselineCertificationError("baseline manifest mappings are invalid")
    for path, digest in files.items():
        if not isinstance(path, str):
            raise BaselineCertificationError("baseline source path must be text")
        _validate_relative_path(path)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BaselineCertificationError("baseline source digest is invalid")
    for path, details in {**allowed, **added}.items():
        _validate_relative_path(path)
        if (
            not isinstance(details, dict)
            or set(details) != {"reasonCode"}
            or details.get("reasonCode") not in ALLOWED_REASON_CODES
        ):
            raise BaselineCertificationError("baseline reason code is invalid")
    if version == "v1":
        if (
            manifest["baselineId"] != BASELINE_V1_ID
            or manifest["parentBaselineId"] is not None
            or files != EXPECTED_V1_FILES
            or allowed
            or added
        ):
            raise BaselineCertificationError("historical v1 contract changed")
    elif version == "v2":
        if (
            manifest["baselineId"] != BASELINE_V2_ID
            or manifest["parentBaselineId"] != BASELINE_V1_ID
            or set(allowed) != EXPECTED_CHANGED_FILES
            or set(added) != EXPECTED_ADDED_FILES
            or set(files) != set(EXPECTED_V1_FILES) | EXPECTED_ADDED_FILES
        ):
            raise BaselineCertificationError("active v2 contract is invalid")
    elif version == "v3":
        expected_files = set(EXPECTED_V1_FILES) | EXPECTED_ADDED_FILES
        expected_reasons = {
            path: (
                "execution_trace_observer"
                if path == "minicode/run_lifecycle.py"
                else "execution_trace_entrypoint"
            )
            for path in EXPECTED_V3_CHANGED_FILES
        }
        if (
            manifest["baselineId"] != BASELINE_V3_ID
            or manifest["parentBaselineId"] != BASELINE_V2_ID
            or set(allowed) != EXPECTED_V3_CHANGED_FILES
            or added
            or set(files) != expected_files
            or any(
                allowed[path].get("reasonCode") != reason
                for path, reason in expected_reasons.items()
            )
        ):
            raise BaselineCertificationError("active v3 contract is invalid")
    elif version == "v4":
        expected_files = (
            set(EXPECTED_V1_FILES) | EXPECTED_ADDED_FILES | EXPECTED_V4_ADDED_FILES
        )
        expected_reasons = {
            "minicode/agent_loop.py": "model_event_sink",
            "minicode/run_lifecycle.py": "model_event_observer",
            "minicode/headless.py": "model_event_entrypoint",
            "minicode/main.py": "model_event_entrypoint",
            "minicode/tui/input_handler.py": "model_event_entrypoint",
        }
        if (
            manifest["baselineId"] != BASELINE_V4_ID
            or manifest["parentBaselineId"] != BASELINE_V3_ID
            or set(allowed) != EXPECTED_V4_CHANGED_FILES
            or set(added) != EXPECTED_V4_ADDED_FILES
            or set(files) != expected_files
            or any(
                allowed[path].get("reasonCode") != reason
                for path, reason in expected_reasons.items()
            )
            or added.get("minicode/run_events.py", {}).get("reasonCode")
            != "model_event_sink_dependency"
        ):
            raise BaselineCertificationError("active v4 contract is invalid")
    elif version == "v5":
        expected_files = (
            set(EXPECTED_V1_FILES) | EXPECTED_ADDED_FILES | EXPECTED_V4_ADDED_FILES
        )
        expected_reasons = {
            "minicode/agent_loop.py": "runtime_memory_observer",
            "minicode/run_events.py": "runtime_event_projection",
            "minicode/headless.py": "skill_event_entrypoint",
            "minicode/main.py": "skill_event_entrypoint",
            "minicode/tui/input_handler.py": "skill_event_entrypoint",
        }
        if (
            manifest["baselineId"] != BASELINE_V5_ID
            or manifest["parentBaselineId"] != BASELINE_V4_ID
            or set(allowed) != EXPECTED_V5_CHANGED_FILES
            or added
            or set(files) != expected_files
            or any(
                allowed[path].get("reasonCode") != reason
                for path, reason in expected_reasons.items()
            )
        ):
            raise BaselineCertificationError("active v5 contract is invalid")
    elif version == "v6":
        expected_files = (
            set(EXPECTED_V1_FILES) | EXPECTED_ADDED_FILES | EXPECTED_V4_ADDED_FILES
        )
        expected_reasons = {
            "minicode/agent_loop.py": "model_usage_observer",
            "minicode/run_events.py": "model_usage_projection",
        }
        if (
            manifest["baselineId"] != BASELINE_V6_ID
            or manifest["parentBaselineId"] != BASELINE_V5_ID
            or set(allowed) != EXPECTED_V6_CHANGED_FILES
            or added
            or set(files) != expected_files
            or any(
                allowed[path].get("reasonCode") != reason
                for path, reason in expected_reasons.items()
            )
        ):
            raise BaselineCertificationError("active v6 contract is invalid")
    elif version == "v7":
        expected_files = (
            set(EXPECTED_V1_FILES) | EXPECTED_ADDED_FILES | EXPECTED_V4_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V7_ID
            or manifest["parentBaselineId"] != BASELINE_V6_ID
            or set(allowed) != EXPECTED_V7_CHANGED_FILES
            or added
            or set(files) != expected_files
            or allowed["minicode/agent_loop.py"].get("reasonCode")
            != "work_chain_disabled_initialization"
        ):
            raise BaselineCertificationError("active v7 contract is invalid")
    elif version == "v8":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V8_ID
            or manifest["parentBaselineId"] != BASELINE_V7_ID
            or set(allowed) != EXPECTED_V8_CHANGED_FILES
            or set(added) != EXPECTED_V8_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "canonical_model_cost_observation"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v8 contract is invalid")
    elif version == "v9":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V9_ID
            or manifest["parentBaselineId"] != BASELINE_V8_ID
            or set(allowed) != EXPECTED_V9_CHANGED_FILES
            or set(added) != EXPECTED_V9_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "context_working_memory_observation"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v9 contract is invalid")
    elif version == "v10":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V10_ID
            or manifest["parentBaselineId"] != BASELINE_V9_ID
            or set(allowed) != EXPECTED_V10_CHANGED_FILES
            or set(added) != EXPECTED_V10_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "mcp_runtime_observation"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v10 contract is invalid")
    elif version == "v11":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V11_ID
            or manifest["parentBaselineId"] != BASELINE_V10_ID
            or set(allowed) != EXPECTED_V11_CHANGED_FILES
            or set(added) != EXPECTED_V11_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "mcp_current_state_observation"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v11 contract is invalid")
    elif version == "v12":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V12_ID
            or manifest["parentBaselineId"] != BASELINE_V11_ID
            or set(allowed) != EXPECTED_V12_CHANGED_FILES
            or added
            or set(files) != expected_files
            or allowed.get("minicode/gateway.py", {}).get("reasonCode")
            != "mcp_current_state_projection"
        ):
            raise BaselineCertificationError("active v12 contract is invalid")
    elif version == "v13":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V13_ID
            or manifest["parentBaselineId"] != BASELINE_V12_ID
            or set(allowed) != EXPECTED_V13_CHANGED_FILES
            or added
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "mcp_current_state_workspace_isolation"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v13 contract is invalid")
    elif version == "v14":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V14_ID
            or manifest["parentBaselineId"] != BASELINE_V13_ID
            or set(allowed) != EXPECTED_V14_CHANGED_FILES
            or set(added) != EXPECTED_V14_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "dashboard_chat_session_turn"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v14 contract is invalid")
    elif version == "v15":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V15_ID
            or manifest["parentBaselineId"] != BASELINE_V14_ID
            or set(allowed) != EXPECTED_V15_CHANGED_FILES
            or set(added) != EXPECTED_V15_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "dashboard_chat_durable_turn_identity"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v15 contract is invalid")
    elif version == "v16":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V16_ID
            or manifest["parentBaselineId"] != BASELINE_V15_ID
            or set(allowed) != EXPECTED_V16_CHANGED_FILES
            or set(added) != EXPECTED_V16_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "dashboard_chat_cooperative_cancellation"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v16 contract is invalid")
    elif version == "v17":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V17_ID
            or manifest["parentBaselineId"] != BASELINE_V16_ID
            or set(allowed) != EXPECTED_V17_CHANGED_FILES
            or added
            or set(files) != expected_files
            or any(
                details.get("reasonCode")
                != "dashboard_chat_cancellation_boundary_hardening"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v17 contract is invalid")
    elif version == "v18":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V18_ID
            or manifest["parentBaselineId"] != BASELINE_V17_ID
            or set(allowed) != EXPECTED_V18_CHANGED_FILES
            or set(added) != EXPECTED_V18_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "dashboard_live_refresh_foundation"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v18 contract is invalid")
    elif version == "v19":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V19_ID
            or manifest["parentBaselineId"] != BASELINE_V18_ID
            or set(allowed) != EXPECTED_V19_CHANGED_FILES
            or set(added) != EXPECTED_V19_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "dashboard_sse_event_transport"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v19 contract is invalid")
    elif version == "v20":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V20_ID
            or manifest["parentBaselineId"] != BASELINE_V19_ID
            or set(allowed) != EXPECTED_V20_CHANGED_FILES
            or added
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "dashboard_sse_store_switchover"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v20 contract is invalid")
    elif version == "v21":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V21_ID
            or manifest["parentBaselineId"] != BASELINE_V20_ID
            or set(allowed) != EXPECTED_V21_CHANGED_FILES
            or set(added) != EXPECTED_V21_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "dashboard_connection_scoped_chat_stream"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v21 contract is invalid")
    elif version == "v22":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V22_ID
            or manifest["parentBaselineId"] != BASELINE_V21_ID
            or set(allowed) != EXPECTED_V22_CHANGED_FILES
            or set(added) != EXPECTED_V22_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "gateway_permission_approval_authority"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v22 contract is invalid")
    elif version == "v23":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V23_ID
            or manifest["parentBaselineId"] != BASELINE_V22_ID
            or set(allowed) != EXPECTED_V23_CHANGED_FILES
            or set(added) != EXPECTED_V23_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode")
                != "gateway_permission_command_review_hardening"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v23 contract is invalid")
    elif version == "v24":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V24_ID
            or manifest["parentBaselineId"] != BASELINE_V23_ID
            or set(allowed) != EXPECTED_V24_CHANGED_FILES
            or set(added) != EXPECTED_V24_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "dashboard_permission_approval_ui"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v24 contract is invalid")
    elif version == "v25":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V25_ID
            or manifest["parentBaselineId"] != BASELINE_V24_ID
            or set(allowed) != EXPECTED_V25_CHANGED_FILES
            or set(added) != EXPECTED_V25_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode")
                != "dashboard_permission_ui_fail_closed_hardening"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v25 contract is invalid")
    elif version == "v26":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V26_ID
            or manifest["parentBaselineId"] != BASELINE_V25_ID
            or set(allowed) != EXPECTED_V26_CHANGED_FILES
            or set(added) != EXPECTED_V26_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "persistent_memory_approval_authority"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v26 contract is invalid")
    elif version == "v27":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V27_ID
            or manifest["parentBaselineId"] != BASELINE_V26_ID
            or set(allowed) != EXPECTED_V27_CHANGED_FILES
            or set(added) != EXPECTED_V27_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "memory_approval_read_only_hardening"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v27 contract is invalid")
    elif version == "v28":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V28_ID
            or manifest["parentBaselineId"] != BASELINE_V27_ID
            or set(allowed) != EXPECTED_V28_CHANGED_FILES
            or set(added) != EXPECTED_V28_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "workspace_local_diff_review_normalization"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v28 contract is invalid")
    elif version == "v29":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V29_ID
            or manifest["parentBaselineId"] != BASELINE_V28_ID
            or set(allowed) != EXPECTED_V29_CHANGED_FILES
            or set(added) != EXPECTED_V29_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "invisible_control_diff_fidelity_hardening"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v29 contract is invalid")
    elif version == "v30":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V30_ID
            or manifest["parentBaselineId"] != BASELINE_V29_ID
            or set(allowed) != EXPECTED_V30_CHANGED_FILES
            or set(added) != EXPECTED_V30_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "memory_approval_store_ui"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v30 contract is invalid")
    elif version == "v31":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
            | EXPECTED_V31_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V31_ID
            or manifest["parentBaselineId"] != BASELINE_V30_ID
            or set(allowed) != EXPECTED_V31_CHANGED_FILES
            or set(added) != EXPECTED_V31_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "dashboard_data_deletion_authority"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v31 contract is invalid")
    elif version == "v32":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
            | EXPECTED_V31_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V32_ID
            or manifest["parentBaselineId"] != BASELINE_V31_ID
            or set(allowed) != EXPECTED_V32_CHANGED_FILES
            or set(added) != EXPECTED_V32_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "dashboard_data_deletion_ui"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v32 contract is invalid")
    elif version == "v33":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
            | EXPECTED_V31_ADDED_FILES
            | EXPECTED_V33_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V33_ID
            or manifest["parentBaselineId"] != BASELINE_V32_ID
            or set(allowed) != EXPECTED_V33_CHANGED_FILES
            or set(added) != EXPECTED_V33_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "persistence_inventory_read_only_health"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v33 contract is invalid")
    elif version == "v34":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
            | EXPECTED_V31_ADDED_FILES
            | EXPECTED_V33_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V34_ID
            or manifest["parentBaselineId"] != BASELINE_V33_ID
            or set(allowed) != EXPECTED_V34_CHANGED_FILES
            or set(added) != EXPECTED_V34_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "dashboard_waku_visual_shell"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v34 contract is invalid")
    elif version == "v35":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
            | EXPECTED_V31_ADDED_FILES
            | EXPECTED_V33_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V35_ID
            or manifest["parentBaselineId"] != BASELINE_V34_ID
            or set(allowed) != EXPECTED_V35_CHANGED_FILES
            or set(added) != EXPECTED_V35_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode")
                != "dashboard_agent_observatory_core_pages"
                for details in allowed.values()
            )
        ):
            raise BaselineCertificationError("active v35 contract is invalid")
    elif version == "v36":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
            | EXPECTED_V31_ADDED_FILES
            | EXPECTED_V33_ADDED_FILES
            | EXPECTED_V36_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V36_ID
            or manifest["parentBaselineId"] != BASELINE_V35_ID
            or set(allowed) != EXPECTED_V36_CHANGED_FILES
            or set(added) != EXPECTED_V36_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "http_request_network_safety"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v36 contract is invalid")
    elif version == "v37":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
            | EXPECTED_V31_ADDED_FILES
            | EXPECTED_V33_ADDED_FILES
            | EXPECTED_V36_ADDED_FILES
            | EXPECTED_V37_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V37_ID
            or manifest["parentBaselineId"] != BASELINE_V36_ID
            or set(allowed) != EXPECTED_V37_CHANGED_FILES
            or set(added) != EXPECTED_V37_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "bounded_dns_resolver_capacity"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v37 contract is invalid")
    elif version == "v38":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
            | EXPECTED_V31_ADDED_FILES
            | EXPECTED_V33_ADDED_FILES
            | EXPECTED_V36_ADDED_FILES
            | EXPECTED_V37_ADDED_FILES
            | EXPECTED_V38_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V38_ID
            or manifest["parentBaselineId"] != BASELINE_V37_ID
            or set(allowed) != EXPECTED_V38_CHANGED_FILES
            or set(added) != EXPECTED_V38_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode")
                != "web_fetch_safe_transport_boundary"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v38 contract is invalid")
    elif version == "v39":
        expected_files = (
            set(EXPECTED_V1_FILES)
            | EXPECTED_ADDED_FILES
            | EXPECTED_V4_ADDED_FILES
            | EXPECTED_V8_ADDED_FILES
            | EXPECTED_V9_ADDED_FILES
            | EXPECTED_V10_ADDED_FILES
            | EXPECTED_V11_ADDED_FILES
            | EXPECTED_V14_ADDED_FILES
            | EXPECTED_V15_ADDED_FILES
            | EXPECTED_V16_ADDED_FILES
            | EXPECTED_V18_ADDED_FILES
            | EXPECTED_V19_ADDED_FILES
            | EXPECTED_V21_ADDED_FILES
            | EXPECTED_V22_ADDED_FILES
            | EXPECTED_V26_ADDED_FILES
            | EXPECTED_V31_ADDED_FILES
            | EXPECTED_V33_ADDED_FILES
            | EXPECTED_V36_ADDED_FILES
            | EXPECTED_V37_ADDED_FILES
            | EXPECTED_V38_ADDED_FILES
            | EXPECTED_V39_ADDED_FILES
        )
        if (
            manifest["baselineId"] != BASELINE_V39_ID
            or manifest["parentBaselineId"] != BASELINE_V38_ID
            or set(allowed) != EXPECTED_V39_CHANGED_FILES
            or set(added) != EXPECTED_V39_ADDED_FILES
            or set(files) != expected_files
            or any(
                details.get("reasonCode") != "web_search_provider_chain"
                for details in (*allowed.values(), *added.values())
            )
        ):
            raise BaselineCertificationError("active v39 contract is invalid")
    else:
        raise ValueError("unsupported production baseline version")


def load_baseline_manifest(
    version: str,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Load one fixed-version manifest from a project root."""
    path = _manifest_path(version, project_root)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(manifest, version=version)
    return manifest


def compare_baselines(
    parent: dict[str, Any],
    child: dict[str, Any],
) -> dict[str, list[str]]:
    parent_files = parent["files"]
    child_files = child["files"]
    common = set(parent_files) & set(child_files)
    return {
        "changedFiles": sorted(
            path for path in common if parent_files[path] != child_files[path]
        ),
        "addedFiles": sorted(set(child_files) - set(parent_files)),
        "removedFiles": sorted(set(parent_files) - set(child_files)),
    }


def build_v2_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return the immutable v2 evidence after pin and lineage validation."""
    root = Path(project_root).resolve()
    if (
        sha256_file(_manifest_path("v1", root)) != PINNED_MANIFEST_SHA256["v1"]
        or sha256_file(_manifest_path("v2", root)) != PINNED_MANIFEST_SHA256["v2"]
    ):
        raise BaselineCertificationError("historical baseline integrity changed")
    v1 = load_baseline_manifest("v1", project_root=root)
    v2 = load_baseline_manifest("v2", project_root=root)
    if compare_baselines(v1, v2) != {
        "changedFiles": sorted(EXPECTED_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_ADDED_FILES),
        "removedFiles": [],
    }:
        raise BaselineCertificationError("historical v1 to v2 lineage changed")
    return v2


def build_v3_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v3 evidence after all historical checks."""
    root = Path(project_root).resolve()
    if (
        sha256_file(_manifest_path("v1", root)) != PINNED_MANIFEST_SHA256["v1"]
        or sha256_file(_manifest_path("v2", root)) != PINNED_MANIFEST_SHA256["v2"]
        or sha256_file(_manifest_path("v3", root)) != PINNED_MANIFEST_SHA256["v3"]
    ):
        raise BaselineCertificationError("historical baseline integrity changed")
    v1 = load_baseline_manifest("v1", project_root=root)
    v2 = load_baseline_manifest("v2", project_root=root)
    v3 = load_baseline_manifest("v3", project_root=root)
    expected_historical_lineage = {
        "changedFiles": sorted(EXPECTED_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_ADDED_FILES),
        "removedFiles": [],
    }
    if compare_baselines(v1, v2) != expected_historical_lineage:
        raise BaselineCertificationError("historical v1 to v2 lineage changed")
    if compare_baselines(v2, v3) != {
        "changedFiles": sorted(EXPECTED_V3_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }:
        raise BaselineCertificationError("historical v2 to v3 lineage changed")
    return v3


def build_v4_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v4 evidence after all historical checks."""
    root = Path(project_root).resolve()
    for version in ("v1", "v2", "v3", "v4"):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v1 = load_baseline_manifest("v1", project_root=root)
    v2 = load_baseline_manifest("v2", project_root=root)
    v3 = load_baseline_manifest("v3", project_root=root)
    v4 = load_baseline_manifest("v4", project_root=root)
    if compare_baselines(v1, v2) != {
        "changedFiles": sorted(EXPECTED_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_ADDED_FILES),
        "removedFiles": [],
    }:
        raise BaselineCertificationError("historical v1 to v2 lineage changed")
    if compare_baselines(v2, v3) != {
        "changedFiles": sorted(EXPECTED_V3_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }:
        raise BaselineCertificationError("historical v2 to v3 lineage changed")
    if compare_baselines(v3, v4) != {
        "changedFiles": sorted(EXPECTED_V4_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V4_ADDED_FILES),
        "removedFiles": [],
    }:
        raise BaselineCertificationError("historical v3 to v4 lineage changed")
    return v4


def build_v5_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v5 evidence after all historical checks."""
    root = Path(project_root).resolve()
    for version in ("v1", "v2", "v3", "v4", "v5"):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v1 = load_baseline_manifest("v1", project_root=root)
    v2 = load_baseline_manifest("v2", project_root=root)
    v3 = load_baseline_manifest("v3", project_root=root)
    v4 = load_baseline_manifest("v4", project_root=root)
    v5 = load_baseline_manifest("v5", project_root=root)
    if compare_baselines(v1, v2) != {
        "changedFiles": sorted(EXPECTED_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_ADDED_FILES),
        "removedFiles": [],
    }:
        raise BaselineCertificationError("historical v1 to v2 lineage changed")
    if compare_baselines(v2, v3) != {
        "changedFiles": sorted(EXPECTED_V3_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }:
        raise BaselineCertificationError("historical v2 to v3 lineage changed")
    if compare_baselines(v3, v4) != {
        "changedFiles": sorted(EXPECTED_V4_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V4_ADDED_FILES),
        "removedFiles": [],
    }:
        raise BaselineCertificationError("historical v3 to v4 lineage changed")
    if compare_baselines(v4, v5) != {
        "changedFiles": sorted(EXPECTED_V5_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }:
        raise BaselineCertificationError("historical v4 to v5 lineage changed")
    return v5


def build_v6_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v6 evidence after all historical checks."""
    root = Path(project_root).resolve()
    for version in ("v1", "v2", "v3", "v4", "v5", "v6"):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v1 = load_baseline_manifest("v1", project_root=root)
    v2 = load_baseline_manifest("v2", project_root=root)
    v3 = load_baseline_manifest("v3", project_root=root)
    v4 = load_baseline_manifest("v4", project_root=root)
    v5 = load_baseline_manifest("v5", project_root=root)
    v6 = load_baseline_manifest("v6", project_root=root)
    lineage_contracts = (
        (
            v1,
            v2,
            EXPECTED_CHANGED_FILES,
            EXPECTED_ADDED_FILES,
        ),
        (v2, v3, EXPECTED_V3_CHANGED_FILES, frozenset()),
        (v3, v4, EXPECTED_V4_CHANGED_FILES, EXPECTED_V4_ADDED_FILES),
        (v4, v5, EXPECTED_V5_CHANGED_FILES, frozenset()),
        (v5, v6, EXPECTED_V6_CHANGED_FILES, frozenset()),
    )
    for parent, child, changed_files, added_files in lineage_contracts:
        if compare_baselines(parent, child) != {
            "changedFiles": sorted(changed_files),
            "addedFiles": sorted(added_files),
            "removedFiles": [],
        }:
            raise BaselineCertificationError("historical baseline lineage changed")
    return v6


def build_v7_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build v7 only for the Work Chain disabled initialization hotfix."""
    root = Path(project_root).resolve()
    historical_target = _manifest_path("v7", root)
    if (
        historical_target.is_file()
        and sha256_file(historical_target) == PINNED_MANIFEST_SHA256["v7"]
    ):
        return load_baseline_manifest("v7", project_root=root)
    for version in ("v1", "v2", "v3", "v4", "v5", "v6"):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    manifests = {
        version: load_baseline_manifest(version, project_root=root)
        for version in ("v1", "v2", "v3", "v4", "v5", "v6")
    }
    lineage_contracts = (
        (
            manifests["v1"],
            manifests["v2"],
            EXPECTED_CHANGED_FILES,
            EXPECTED_ADDED_FILES,
        ),
        (
            manifests["v2"],
            manifests["v3"],
            EXPECTED_V3_CHANGED_FILES,
            frozenset(),
        ),
        (
            manifests["v3"],
            manifests["v4"],
            EXPECTED_V4_CHANGED_FILES,
            EXPECTED_V4_ADDED_FILES,
        ),
        (
            manifests["v4"],
            manifests["v5"],
            EXPECTED_V5_CHANGED_FILES,
            frozenset(),
        ),
        (
            manifests["v5"],
            manifests["v6"],
            EXPECTED_V6_CHANGED_FILES,
            frozenset(),
        ),
    )
    for parent, child, changed_files, added_files in lineage_contracts:
        if compare_baselines(parent, child) != {
            "changedFiles": sorted(changed_files),
            "addedFiles": sorted(added_files),
            "removedFiles": [],
        }:
            raise BaselineCertificationError("historical baseline lineage changed")

    v6 = manifests["v6"]
    current_files = _hash_files(root, set(v6["files"]))
    changed = {
        path
        for path, expected in v6["files"].items()
        if current_files[path] != expected
    }
    if changed != EXPECTED_V7_CHANGED_FILES:
        paths = ", ".join(sorted(changed)) or "none"
        raise BaselineCertificationError(f"unexpected v6 source differences: {paths}")
    missing = sorted(
        path for path, digest in current_files.items() if digest == "missing"
    )
    if missing:
        raise BaselineCertificationError(
            f"required v7 source files missing: {', '.join(missing)}"
        )
    return {
        "addedFiles": {},
        "allowedChangesFromParent": {
            "minicode/agent_loop.py": {
                "reasonCode": "work_chain_disabled_initialization"
            },
        },
        "baselineId": BASELINE_V7_ID,
        "files": dict(sorted(current_files.items())),
        "parentBaselineId": BASELINE_V6_ID,
        "reason": "Batch 4A.1 enable_work_chain=False execution flag hotfix",
        "schemaVersion": 1,
    }


def build_v8_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build v8 only for canonical model-cost observation."""
    root = Path(project_root).resolve()
    historical_target = _manifest_path("v8", root)
    if (
        historical_target.is_file()
        and sha256_file(historical_target) == PINNED_MANIFEST_SHA256["v8"]
    ):
        return load_baseline_manifest("v8", project_root=root)
    for version in ("v1", "v2", "v3", "v4", "v5", "v6", "v7"):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v7 = load_baseline_manifest("v7", project_root=root)
    if build_v7_candidate(project_root=root) != v7:
        raise BaselineCertificationError("historical v7 candidate changed")

    current_files = _hash_files(
        root,
        set(v7["files"]) | EXPECTED_V8_ADDED_FILES,
    )
    changed = {
        path
        for path, expected in v7["files"].items()
        if current_files[path] != expected
    }
    if changed != EXPECTED_V8_CHANGED_FILES:
        paths = ", ".join(sorted(changed)) or "none"
        raise BaselineCertificationError(f"unexpected v7 source differences: {paths}")
    missing = sorted(
        path for path, digest in current_files.items() if digest == "missing"
    )
    if missing:
        raise BaselineCertificationError(
            f"required v8 source files missing: {', '.join(missing)}"
        )
    reason = {"reasonCode": "canonical_model_cost_observation"}
    return {
        "addedFiles": {path: dict(reason) for path in sorted(EXPECTED_V8_ADDED_FILES)},
        "allowedChangesFromParent": {
            path: dict(reason) for path in sorted(EXPECTED_V8_CHANGED_FILES)
        },
        "baselineId": BASELINE_V8_ID,
        "files": dict(sorted(current_files.items())),
        "parentBaselineId": BASELINE_V7_ID,
        "reason": "Batch 4B-1 canonical model cost observation",
        "schemaVersion": 1,
    }


def build_v9_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build v9 only for Context and WorkingMemory observation."""
    root = Path(project_root).resolve()
    historical_target = _manifest_path("v9", root)
    if (
        historical_target.is_file()
        and sha256_file(historical_target) == PINNED_MANIFEST_SHA256["v9"]
    ):
        return load_baseline_manifest("v9", project_root=root)
    for version in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v8 = load_baseline_manifest("v8", project_root=root)
    if build_v8_candidate(project_root=root) != v8:
        raise BaselineCertificationError("historical v8 candidate changed")

    current_files = _hash_files(
        root,
        set(v8["files"]) | EXPECTED_V9_ADDED_FILES,
    )
    changed = {
        path
        for path, expected in v8["files"].items()
        if current_files[path] != expected
    }
    if changed != EXPECTED_V9_CHANGED_FILES:
        paths = ", ".join(sorted(changed)) or "none"
        raise BaselineCertificationError(f"unexpected v8 source differences: {paths}")
    missing = sorted(
        path for path, digest in current_files.items() if digest == "missing"
    )
    if missing:
        raise BaselineCertificationError(
            f"required v9 source files missing: {', '.join(missing)}"
        )
    reason = {"reasonCode": "context_working_memory_observation"}
    return {
        "addedFiles": {path: dict(reason) for path in sorted(EXPECTED_V9_ADDED_FILES)},
        "allowedChangesFromParent": {
            path: dict(reason) for path in sorted(EXPECTED_V9_CHANGED_FILES)
        },
        "baselineId": BASELINE_V9_ID,
        "files": dict(sorted(current_files.items())),
        "parentBaselineId": BASELINE_V8_ID,
        "reason": "Batch 5B-1 Context and WorkingMemory observation events",
        "schemaVersion": 1,
    }


def build_v10_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build v10 only for run-scoped MCP runtime observation."""
    root = Path(project_root).resolve()
    historical_target = _manifest_path("v10", root)
    if (
        historical_target.is_file()
        and sha256_file(historical_target) == PINNED_MANIFEST_SHA256["v10"]
    ):
        return load_baseline_manifest("v10", project_root=root)
    for version in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v9 = load_baseline_manifest("v9", project_root=root)

    current_files = _hash_files(root, set(v9["files"]) | EXPECTED_V10_ADDED_FILES)
    changed = {
        path
        for path, expected in v9["files"].items()
        if current_files[path] != expected
    }
    if changed != EXPECTED_V10_CHANGED_FILES:
        paths = ", ".join(sorted(changed)) or "none"
        raise BaselineCertificationError(f"unexpected v9 source differences: {paths}")
    missing = sorted(
        path for path, digest in current_files.items() if digest == "missing"
    )
    if missing:
        raise BaselineCertificationError(
            f"required v10 source files missing: {', '.join(missing)}"
        )
    reason = {"reasonCode": "mcp_runtime_observation"}
    return {
        "addedFiles": {path: dict(reason) for path in sorted(EXPECTED_V10_ADDED_FILES)},
        "allowedChangesFromParent": {
            path: dict(reason) for path in sorted(EXPECTED_V10_CHANGED_FILES)
        },
        "baselineId": BASELINE_V10_ID,
        "files": dict(sorted(current_files.items())),
        "parentBaselineId": BASELINE_V9_ID,
        "reason": "Batch 5C-1A run-scoped MCP runtime observation",
        "schemaVersion": 1,
    }


def build_v11_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build v11 only for process-local MCP current-state observation."""
    root = Path(project_root).resolve()
    historical_target = _manifest_path("v11", root)
    if (
        historical_target.is_file()
        and sha256_file(historical_target) == PINNED_MANIFEST_SHA256["v11"]
    ):
        return load_baseline_manifest("v11", project_root=root)
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
    ):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v10 = load_baseline_manifest("v10", project_root=root)
    current_files = _hash_files(
        root,
        set(v10["files"]) | EXPECTED_V11_ADDED_FILES,
    )
    changed = {
        path
        for path, expected in v10["files"].items()
        if current_files[path] != expected
    }
    if changed != EXPECTED_V11_CHANGED_FILES:
        paths = ", ".join(sorted(changed)) or "none"
        raise BaselineCertificationError(f"unexpected v10 source differences: {paths}")
    missing = sorted(
        path for path, digest in current_files.items() if digest == "missing"
    )
    if missing:
        raise BaselineCertificationError(
            f"required v11 source files missing: {', '.join(missing)}"
        )
    reason = {"reasonCode": "mcp_current_state_observation"}
    return {
        "addedFiles": {path: dict(reason) for path in sorted(EXPECTED_V11_ADDED_FILES)},
        "allowedChangesFromParent": {
            path: dict(reason) for path in sorted(EXPECTED_V11_CHANGED_FILES)
        },
        "baselineId": BASELINE_V11_ID,
        "files": dict(sorted(current_files.items())),
        "parentBaselineId": BASELINE_V10_ID,
        "reason": "Batch 5C-2A process-local MCP current state",
        "schemaVersion": 1,
    }


def build_v12_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build v12 only for Gateway current-state Dashboard projection wiring."""
    root = Path(project_root).resolve()
    historical_target = _manifest_path("v12", root)
    if (
        historical_target.is_file()
        and sha256_file(historical_target) == PINNED_MANIFEST_SHA256["v12"]
    ):
        return load_baseline_manifest("v12", project_root=root)
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
    ):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v11 = load_baseline_manifest("v11", project_root=root)
    current_files = _hash_files(root, set(v11["files"]))
    changed = {
        path
        for path, expected in v11["files"].items()
        if current_files[path] != expected
    }
    if changed != EXPECTED_V12_CHANGED_FILES:
        paths = ", ".join(sorted(changed)) or "none"
        raise BaselineCertificationError(f"unexpected v11 source differences: {paths}")
    missing = sorted(
        path for path, digest in current_files.items() if digest == "missing"
    )
    if missing:
        raise BaselineCertificationError(
            f"required v12 source files missing: {', '.join(missing)}"
        )
    return {
        "addedFiles": {},
        "allowedChangesFromParent": {
            "minicode/gateway.py": {"reasonCode": "mcp_current_state_projection"}
        },
        "baselineId": BASELINE_V12_ID,
        "files": dict(sorted(current_files.items())),
        "parentBaselineId": BASELINE_V11_ID,
        "reason": "Batch 5C-2B MCP current-state Dashboard projection",
        "schemaVersion": 1,
    }


def build_v13_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return pinned v13, or build it only when its target does not exist."""
    root = Path(project_root).resolve()
    historical_target = _manifest_path("v13", root)
    if historical_target.exists():
        if sha256_file(historical_target) != PINNED_MANIFEST_SHA256["v13"]:
            raise BaselineCertificationError("historical v13 baseline changed")
        return load_baseline_manifest("v13", project_root=root)
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
    ):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v12 = load_baseline_manifest("v12", project_root=root)
    current_files = _hash_files(root, set(v12["files"]))
    changed = {
        path
        for path, expected in v12["files"].items()
        if current_files[path] != expected
    }
    if changed != EXPECTED_V13_CHANGED_FILES:
        paths = ", ".join(sorted(changed)) or "none"
        raise BaselineCertificationError(f"unexpected v12 source differences: {paths}")
    missing = sorted(
        path for path, digest in current_files.items() if digest == "missing"
    )
    if missing:
        raise BaselineCertificationError(
            f"required v13 source files missing: {', '.join(missing)}"
        )
    reason = {"reasonCode": "mcp_current_state_workspace_isolation"}
    return {
        "addedFiles": {},
        "allowedChangesFromParent": {
            path: dict(reason) for path in sorted(EXPECTED_V13_CHANGED_FILES)
        },
        "baselineId": BASELINE_V13_ID,
        "files": dict(sorted(current_files.items())),
        "parentBaselineId": BASELINE_V12_ID,
        "reason": "Batch 5C-2B.1 MCP current-state workspace isolation",
        "schemaVersion": 1,
    }


def build_v14_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build v14 only for the synchronous Dashboard Chat entrypoint."""
    root = Path(project_root).resolve()
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
    ):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v13 = load_baseline_manifest("v13", project_root=root)
    parent_files = set(v13["files"])
    if parent_files & EXPECTED_V14_ADDED_FILES:
        raise BaselineCertificationError("v14 additions already exist in parent")
    current_files = _hash_files(
        root,
        parent_files | EXPECTED_V14_ADDED_FILES,
    )
    changed = {
        path
        for path, expected in v13["files"].items()
        if current_files[path] != expected
    }
    if changed != EXPECTED_V14_CHANGED_FILES:
        paths = ", ".join(sorted(changed)) or "none"
        raise BaselineCertificationError(f"unexpected v13 source differences: {paths}")
    missing = sorted(
        path for path, digest in current_files.items() if digest == "missing"
    )
    if missing:
        raise BaselineCertificationError(
            f"required v14 source files missing: {', '.join(missing)}"
        )
    reason = {"reasonCode": "dashboard_chat_session_turn"}
    return {
        "addedFiles": {path: dict(reason) for path in sorted(EXPECTED_V14_ADDED_FILES)},
        "allowedChangesFromParent": {
            path: dict(reason) for path in sorted(EXPECTED_V14_CHANGED_FILES)
        },
        "baselineId": BASELINE_V14_ID,
        "files": dict(sorted(current_files.items())),
        "parentBaselineId": BASELINE_V13_ID,
        "reason": "Batch 6B-1 Dashboard Chat and Session-backed synchronous turn",
        "schemaVersion": 1,
    }


def build_v15_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build v15 only for durable Dashboard turn identity and recovery."""
    root = Path(project_root).resolve()
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
    ):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v14 = load_baseline_manifest("v14", project_root=root)
    parent_files = set(v14["files"])
    if parent_files & EXPECTED_V15_ADDED_FILES:
        raise BaselineCertificationError("v15 additions already exist in parent")
    current_files = _hash_files(
        root,
        parent_files | EXPECTED_V15_ADDED_FILES,
    )
    changed = {
        path
        for path, expected in v14["files"].items()
        if current_files[path] != expected
    }
    if changed != EXPECTED_V15_CHANGED_FILES:
        paths = ", ".join(sorted(changed)) or "none"
        raise BaselineCertificationError(f"unexpected v14 source differences: {paths}")
    missing = sorted(
        path for path, digest in current_files.items() if digest == "missing"
    )
    if missing:
        raise BaselineCertificationError(
            f"required v15 source files missing: {', '.join(missing)}"
        )
    reason = {"reasonCode": "dashboard_chat_durable_turn_identity"}
    return {
        "addedFiles": {path: dict(reason) for path in sorted(EXPECTED_V15_ADDED_FILES)},
        "allowedChangesFromParent": {
            path: dict(reason) for path in sorted(EXPECTED_V15_CHANGED_FILES)
        },
        "baselineId": BASELINE_V15_ID,
        "files": dict(sorted(current_files.items())),
        "parentBaselineId": BASELINE_V14_ID,
        "reason": "Batch 6B-2A durable turn identity and restart recovery",
        "schemaVersion": 1,
    }


def build_v16_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v16 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v16", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v16 baseline is not valid")
    return load_baseline_manifest("v16", project_root=root)


def build_v17_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v17 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v17", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v17 baseline is not valid")
    return load_baseline_manifest("v17", project_root=root)


def build_v18_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v18 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v18", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v18 baseline is not valid")
    return load_baseline_manifest("v18", project_root=root)


def build_v19_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v19 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v19", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v19 baseline is not valid")
    return load_baseline_manifest("v19", project_root=root)


def build_v20_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v20 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v20", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v20 baseline is not valid")
    return load_baseline_manifest("v20", project_root=root)


def build_v21_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v21 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v21", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v21 baseline is not valid")
    return load_baseline_manifest("v21", project_root=root)


def build_v22_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v22 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v22", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v22 baseline is not valid")
    return load_baseline_manifest("v22", project_root=root)


def build_v23_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v23 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v23", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v23 baseline is not valid")
    return load_baseline_manifest("v23", project_root=root)


def build_v24_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v24 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v24", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v24 baseline is not valid")
    return load_baseline_manifest("v24", project_root=root)


def build_v25_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v25 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v25", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v25 baseline is not valid")
    return load_baseline_manifest("v25", project_root=root)


def build_v26_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v26 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v26", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v26 baseline is not valid")
    return load_baseline_manifest("v26", project_root=root)


def build_v27_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v27 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v27", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v27 baseline is not valid")
    return load_baseline_manifest("v27", project_root=root)


def build_v28_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v28 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v28", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v28 baseline is not valid")
    return load_baseline_manifest("v28", project_root=root)


def build_v29_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v29 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v29", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v29 baseline is not valid")
    return load_baseline_manifest("v29", project_root=root)


def build_v30_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v30 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v30", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v30 baseline is not valid")
    return load_baseline_manifest("v30", project_root=root)


def build_v31_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v31 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v31", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v31 baseline is not valid")
    return load_baseline_manifest("v31", project_root=root)


def build_v32_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return immutable v32 evidence after validating its accepted pin."""
    root = Path(project_root).resolve()
    if not verify_manifest_version("v32", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v32 baseline is not valid")
    return load_baseline_manifest("v32", project_root=root)


def build_v33_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build v33 for the bounded, read-only persistence inventory."""
    root = Path(project_root).resolve()
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
    ):
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v32 = load_baseline_manifest("v32", project_root=root)
    protected_paths = set(v32["files"]) | set(EXPECTED_V33_ADDED_FILES)
    current_files = _hash_files(root, protected_paths)
    changed = {
        path
        for path, expected in v32["files"].items()
        if current_files[path] != expected
    }
    added = set(current_files) - set(v32["files"])
    if changed != EXPECTED_V33_CHANGED_FILES or added != EXPECTED_V33_ADDED_FILES:
        raise BaselineCertificationError(
            "unexpected v32 source differences: "
            f"changed={', '.join(sorted(changed)) or 'none'}; "
            f"added={', '.join(sorted(added)) or 'none'}"
        )
    missing = sorted(
        path for path, digest in current_files.items() if digest == "missing"
    )
    if missing:
        raise BaselineCertificationError(
            f"required v33 source files missing: {', '.join(missing)}"
        )
    reason = {"reasonCode": "persistence_inventory_read_only_health"}
    return {
        "addedFiles": {path: dict(reason) for path in sorted(EXPECTED_V33_ADDED_FILES)},
        "allowedChangesFromParent": {
            path: dict(reason) for path in sorted(EXPECTED_V33_CHANGED_FILES)
        },
        "baselineId": BASELINE_V33_ID,
        "files": dict(sorted(current_files.items())),
        "parentBaselineId": BASELINE_V32_ID,
        "reason": "Batch 9A-1 bounded read-only persistence inventory and Data Health",
        "schemaVersion": 1,
    }


def build_v34_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return the immutable pinned v34 visual-shell baseline."""
    root = Path(project_root).resolve()
    for number in range(1, 35):
        version = f"v{number}"
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    return load_baseline_manifest("v34", project_root=root)


def build_v35_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return the immutable pinned v35 Agent Observatory baseline."""
    root = Path(project_root).resolve()
    for number in range(1, 36):
        version = f"v{number}"
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    return load_baseline_manifest("v35", project_root=root)


def build_v36_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return the immutable pinned v36 HTTP Request safety baseline."""
    root = Path(project_root).resolve()
    for number in range(1, 37):
        version = f"v{number}"
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    return load_baseline_manifest("v36", project_root=root)


def build_v37_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return the immutable pinned v37 bounded DNS resolver baseline."""
    root = Path(project_root).resolve()
    for number in range(1, 38):
        version = f"v{number}"
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    return load_baseline_manifest("v37", project_root=root)


def build_v38_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return the immutable pinned v38 web_fetch safety baseline."""
    root = Path(project_root).resolve()
    for number in range(1, 39):
        version = f"v{number}"
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    return load_baseline_manifest("v38", project_root=root)


def build_v39_candidate(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build v39 for the built-in web search provider chain."""
    root = Path(project_root).resolve()
    for number in range(1, 39):
        version = f"v{number}"
        if (
            sha256_file(_manifest_path(version, root))
            != PINNED_MANIFEST_SHA256[version]
        ):
            raise BaselineCertificationError("historical baseline integrity changed")
    v38 = load_baseline_manifest("v38", project_root=root)
    protected_paths = set(v38["files"]) | set(EXPECTED_V39_ADDED_FILES)
    current_files = _hash_files(root, protected_paths)
    changed = {
        path
        for path, expected in v38["files"].items()
        if current_files[path] != expected
    }
    added = set(current_files) - set(v38["files"])
    if changed != EXPECTED_V39_CHANGED_FILES or added != EXPECTED_V39_ADDED_FILES:
        raise BaselineCertificationError(
            "unexpected v38 source differences: "
            f"changed={', '.join(sorted(changed)) or 'none'}; "
            f"added={', '.join(sorted(added)) or 'none'}"
        )
    missing = sorted(
        path for path, digest in current_files.items() if digest == "missing"
    )
    if missing:
        raise BaselineCertificationError(
            f"required v39 source files missing: {', '.join(missing)}"
        )
    reason = {"reasonCode": "web_search_provider_chain"}
    return {
        "addedFiles": {
            path: dict(reason) for path in sorted(EXPECTED_V39_ADDED_FILES)
        },
        "allowedChangesFromParent": {
            path: dict(reason) for path in sorted(EXPECTED_V39_CHANGED_FILES)
        },
        "baselineId": BASELINE_V39_ID,
        "files": dict(sorted(current_files.items())),
        "parentBaselineId": BASELINE_V38_ID,
        "reason": (
            "Reliability 1B-1C built-in web search provider chain and truthful "
            "failure taxonomy"
        ),
        "schemaVersion": 1,
    }


def _verify_expected_files(
    project_root: Path,
    expected: dict[str, str],
) -> dict[str, Any]:
    actual = _hash_files(project_root, set(expected))
    mismatches = {
        path: {"expected": expected[path], "actual": actual[path]}
        for path in sorted(expected)
        if expected[path] != actual[path]
    }
    return {
        "fileCount": len(expected),
        "matches": not mismatches,
        "mismatches": mismatches,
    }


def verify_manifest_version(
    version: str, *, project_root: Path = PROJECT_ROOT
) -> dict[str, Any]:
    """Verify one immutable manifest and its declared parent lineage."""
    if version not in {
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
        "v36",
        "v37",
        "v38",
        "v39",
    }:
        raise ValueError("unsupported production baseline version")
    root = Path(project_root).resolve()
    manifest = load_baseline_manifest(version, project_root=root)
    expected_pin = PINNED_MANIFEST_SHA256.get(version)
    integrity = (
        isinstance(expected_pin, str)
        and sha256_file(_manifest_path(version, root)) == expected_pin
    )
    lineage_matches = True
    if version == "v2":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v1", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v39":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v38", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V39_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V39_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v38":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v37", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V38_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V38_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v37":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v36", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V37_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V37_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v36":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v35", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V36_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V36_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v35":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v34", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V35_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V35_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v9":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v8", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V9_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V9_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v10":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v9", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V10_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V10_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v11":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v10", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V11_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V11_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v12":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v11", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V12_CHANGED_FILES),
            "addedFiles": [],
            "removedFiles": [],
        }
    elif version == "v13":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v12", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V13_CHANGED_FILES),
            "addedFiles": [],
            "removedFiles": [],
        }
    elif version == "v14":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v13", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V14_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V14_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v15":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v14", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V15_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V15_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v16":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v15", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V16_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V16_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v17":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v16", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V17_CHANGED_FILES),
            "addedFiles": [],
            "removedFiles": [],
        }
    elif version == "v18":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v17", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V18_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V18_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v19":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v18", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V19_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V19_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v20":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v19", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V20_CHANGED_FILES),
            "addedFiles": [],
            "removedFiles": [],
        }
    elif version == "v21":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v20", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V21_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V21_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v22":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v21", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V22_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V22_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v23":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v22", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V23_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V23_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v24":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v23", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V24_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V24_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v25":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v24", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V25_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V25_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v26":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v25", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V26_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V26_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v27":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v26", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V27_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V27_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v28":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v27", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V28_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V28_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v29":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v28", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V29_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V29_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v30":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v29", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V30_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V30_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v31":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v30", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V31_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V31_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v32":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v31", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V32_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V32_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v33":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v32", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V33_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V33_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v34":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v33", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V34_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V34_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v3":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v2", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V3_CHANGED_FILES),
            "addedFiles": [],
            "removedFiles": [],
        }
    elif version == "v4":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v3", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V4_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V4_ADDED_FILES),
            "removedFiles": [],
        }
    elif version == "v5":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v4", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V5_CHANGED_FILES),
            "addedFiles": [],
            "removedFiles": [],
        }
    elif version == "v6":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v5", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V6_CHANGED_FILES),
            "addedFiles": [],
            "removedFiles": [],
        }
    elif version == "v7":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v6", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V7_CHANGED_FILES),
            "addedFiles": [],
            "removedFiles": [],
        }
    elif version == "v8":
        lineage_matches = compare_baselines(
            load_baseline_manifest("v7", project_root=root), manifest
        ) == {
            "changedFiles": sorted(EXPECTED_V8_CHANGED_FILES),
            "addedFiles": sorted(EXPECTED_V8_ADDED_FILES),
            "removedFiles": [],
        }
    return {
        "version": version,
        "baselineId": manifest["baselineId"],
        "manifestIntegrity": integrity,
        "lineageMatches": lineage_matches,
        "matches": integrity and lineage_matches,
    }


def verify_active_baseline(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Verify pinned v1-v39 evidence, all lineage, and active source files."""
    root = Path(project_root).resolve()
    manifest_integrity = {
        version: sha256_file(_manifest_path(version, root))
        == PINNED_MANIFEST_SHA256.get(version)
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
            "v36",
            "v37",
            "v38",
            "v39",
        )
    }
    v1 = load_baseline_manifest("v1", project_root=root)
    v2 = load_baseline_manifest("v2", project_root=root)
    v3 = load_baseline_manifest("v3", project_root=root)
    v4 = load_baseline_manifest("v4", project_root=root)
    v5 = load_baseline_manifest("v5", project_root=root)
    v6 = load_baseline_manifest("v6", project_root=root)
    v7 = load_baseline_manifest("v7", project_root=root)
    v8 = load_baseline_manifest("v8", project_root=root)
    v9 = load_baseline_manifest("v9", project_root=root)
    v10 = load_baseline_manifest("v10", project_root=root)
    v11 = load_baseline_manifest("v11", project_root=root)
    v12 = load_baseline_manifest("v12", project_root=root)
    v13 = load_baseline_manifest("v13", project_root=root)
    v14 = load_baseline_manifest("v14", project_root=root)
    v15 = load_baseline_manifest("v15", project_root=root)
    v16 = load_baseline_manifest("v16", project_root=root)
    v17 = load_baseline_manifest("v17", project_root=root)
    v18 = load_baseline_manifest("v18", project_root=root)
    v19 = load_baseline_manifest("v19", project_root=root)
    v20 = load_baseline_manifest("v20", project_root=root)
    v21 = load_baseline_manifest("v21", project_root=root)
    v22 = load_baseline_manifest("v22", project_root=root)
    v23 = load_baseline_manifest("v23", project_root=root)
    v24 = load_baseline_manifest("v24", project_root=root)
    v25 = load_baseline_manifest("v25", project_root=root)
    v26 = load_baseline_manifest("v26", project_root=root)
    v27 = load_baseline_manifest("v27", project_root=root)
    v28 = load_baseline_manifest("v28", project_root=root)
    v29 = load_baseline_manifest("v29", project_root=root)
    v30 = load_baseline_manifest("v30", project_root=root)
    v31 = load_baseline_manifest("v31", project_root=root)
    v32 = load_baseline_manifest("v32", project_root=root)
    v33 = load_baseline_manifest("v33", project_root=root)
    v34 = load_baseline_manifest("v34", project_root=root)
    v35 = load_baseline_manifest("v35", project_root=root)
    v36 = load_baseline_manifest("v36", project_root=root)
    v37 = load_baseline_manifest("v37", project_root=root)
    v38 = load_baseline_manifest("v38", project_root=root)
    v39 = load_baseline_manifest("v39", project_root=root)
    historical_lineage = compare_baselines(v1, v2)
    expected_historical_lineage = {
        "changedFiles": sorted(EXPECTED_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_ADDED_FILES),
        "removedFiles": [],
    }
    prior_lineage = compare_baselines(v2, v3)
    expected_prior_lineage = {
        "changedFiles": sorted(EXPECTED_V3_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }
    model_lineage = compare_baselines(v3, v4)
    expected_model_lineage = {
        "changedFiles": sorted(EXPECTED_V4_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V4_ADDED_FILES),
        "removedFiles": [],
    }
    runtime_lineage = compare_baselines(v4, v5)
    expected_runtime_lineage = {
        "changedFiles": sorted(EXPECTED_V5_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }
    usage_lineage = compare_baselines(v5, v6)
    expected_usage_lineage = {
        "changedFiles": sorted(EXPECTED_V6_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }
    work_chain_lineage = compare_baselines(v6, v7)
    expected_work_chain_lineage = {
        "changedFiles": sorted(EXPECTED_V7_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }
    model_cost_lineage = compare_baselines(v7, v8)
    expected_model_cost_lineage = {
        "changedFiles": sorted(EXPECTED_V8_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V8_ADDED_FILES),
        "removedFiles": [],
    }
    context_lineage = compare_baselines(v8, v9)
    expected_context_lineage = {
        "changedFiles": sorted(EXPECTED_V9_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V9_ADDED_FILES),
        "removedFiles": [],
    }
    mcp_runtime_lineage = compare_baselines(v9, v10)
    expected_mcp_runtime_lineage = {
        "changedFiles": sorted(EXPECTED_V10_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V10_ADDED_FILES),
        "removedFiles": [],
    }
    mcp_current_state_lineage = compare_baselines(v10, v11)
    expected_mcp_current_state_lineage = {
        "changedFiles": sorted(EXPECTED_V11_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V11_ADDED_FILES),
        "removedFiles": [],
    }
    mcp_current_projection_lineage = compare_baselines(v11, v12)
    expected_mcp_current_projection_lineage = {
        "changedFiles": sorted(EXPECTED_V12_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }
    mcp_current_workspace_lineage = compare_baselines(v12, v13)
    expected_mcp_current_workspace_lineage = {
        "changedFiles": sorted(EXPECTED_V13_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }
    chat_lineage = compare_baselines(v13, v14)
    expected_chat_lineage = {
        "changedFiles": sorted(EXPECTED_V14_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V14_ADDED_FILES),
        "removedFiles": [],
    }
    turn_identity_lineage = compare_baselines(v14, v15)
    expected_turn_identity_lineage = {
        "changedFiles": sorted(EXPECTED_V15_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V15_ADDED_FILES),
        "removedFiles": [],
    }
    cancellation_lineage = compare_baselines(v15, v16)
    expected_cancellation_lineage = {
        "changedFiles": sorted(EXPECTED_V16_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V16_ADDED_FILES),
        "removedFiles": [],
    }
    boundary_lineage = compare_baselines(v16, v17)
    expected_boundary_lineage = {
        "changedFiles": sorted(EXPECTED_V17_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }
    live_refresh_lineage = compare_baselines(v17, v18)
    expected_live_refresh_lineage = {
        "changedFiles": sorted(EXPECTED_V18_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V18_ADDED_FILES),
        "removedFiles": [],
    }
    event_stream_lineage = compare_baselines(v18, v19)
    expected_event_stream_lineage = {
        "changedFiles": sorted(EXPECTED_V19_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V19_ADDED_FILES),
        "removedFiles": [],
    }
    store_switchover_lineage = compare_baselines(v19, v20)
    expected_store_switchover_lineage = {
        "changedFiles": sorted(EXPECTED_V20_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }
    chat_stream_lineage = compare_baselines(v20, v21)
    expected_chat_stream_lineage = {
        "changedFiles": sorted(EXPECTED_V21_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V21_ADDED_FILES),
        "removedFiles": [],
    }
    permission_authority_lineage = compare_baselines(v21, v22)
    expected_permission_authority_lineage = {
        "changedFiles": sorted(EXPECTED_V22_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V22_ADDED_FILES),
        "removedFiles": [],
    }
    permission_command_review_lineage = compare_baselines(v22, v23)
    expected_permission_command_review_lineage = {
        "changedFiles": sorted(EXPECTED_V23_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V23_ADDED_FILES),
        "removedFiles": [],
    }
    permission_ui_lineage = compare_baselines(v23, v24)
    expected_permission_ui_lineage = {
        "changedFiles": sorted(EXPECTED_V24_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V24_ADDED_FILES),
        "removedFiles": [],
    }
    permission_ui_hardening_lineage = compare_baselines(v24, v25)
    expected_permission_ui_hardening_lineage = {
        "changedFiles": sorted(EXPECTED_V25_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V25_ADDED_FILES),
        "removedFiles": [],
    }
    memory_approval_lineage = compare_baselines(v25, v26)
    expected_memory_approval_lineage = {
        "changedFiles": sorted(EXPECTED_V26_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V26_ADDED_FILES),
        "removedFiles": [],
    }
    memory_approval_read_lineage = compare_baselines(v26, v27)
    expected_memory_approval_read_lineage = {
        "changedFiles": sorted(EXPECTED_V27_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V27_ADDED_FILES),
        "removedFiles": [],
    }
    diff_review_lineage = compare_baselines(v27, v28)
    expected_diff_review_lineage = {
        "changedFiles": sorted(EXPECTED_V28_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V28_ADDED_FILES),
        "removedFiles": [],
    }
    diff_fidelity_lineage = compare_baselines(v28, v29)
    expected_diff_fidelity_lineage = {
        "changedFiles": sorted(EXPECTED_V29_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V29_ADDED_FILES),
        "removedFiles": [],
    }
    memory_approval_ui_lineage = compare_baselines(v29, v30)
    expected_memory_approval_ui_lineage = {
        "changedFiles": sorted(EXPECTED_V30_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V30_ADDED_FILES),
        "removedFiles": [],
    }
    deletion_authority_lineage = compare_baselines(v30, v31)
    expected_deletion_authority_lineage = {
        "changedFiles": sorted(EXPECTED_V31_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V31_ADDED_FILES),
        "removedFiles": [],
    }
    deletion_ui_lineage = compare_baselines(v31, v32)
    expected_deletion_ui_lineage = {
        "changedFiles": sorted(EXPECTED_V32_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V32_ADDED_FILES),
        "removedFiles": [],
    }
    persistence_health_lineage = compare_baselines(v32, v33)
    expected_persistence_health_lineage = {
        "changedFiles": sorted(EXPECTED_V33_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V33_ADDED_FILES),
        "removedFiles": [],
    }
    visual_shell_lineage = compare_baselines(v33, v34)
    expected_visual_shell_lineage = {
        "changedFiles": sorted(EXPECTED_V34_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V34_ADDED_FILES),
        "removedFiles": [],
    }
    agent_observatory_lineage = compare_baselines(v34, v35)
    expected_agent_observatory_lineage = {
        "changedFiles": sorted(EXPECTED_V35_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V35_ADDED_FILES),
        "removedFiles": [],
    }
    http_request_safety_lineage = compare_baselines(v35, v36)
    expected_http_request_safety_lineage = {
        "changedFiles": sorted(EXPECTED_V36_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V36_ADDED_FILES),
        "removedFiles": [],
    }
    bounded_dns_resolver_lineage = compare_baselines(v36, v37)
    expected_bounded_dns_resolver_lineage = {
        "changedFiles": sorted(EXPECTED_V37_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V37_ADDED_FILES),
        "removedFiles": [],
    }
    web_fetch_safety_lineage = compare_baselines(v37, v38)
    expected_web_fetch_safety_lineage = {
        "changedFiles": sorted(EXPECTED_V38_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V38_ADDED_FILES),
        "removedFiles": [],
    }
    lineage = compare_baselines(v38, v39)
    expected_lineage = {
        "changedFiles": sorted(EXPECTED_V39_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V39_ADDED_FILES),
        "removedFiles": [],
    }
    current = _verify_expected_files(root, v39["files"])
    try:
        candidate_matches = build_v39_candidate(project_root=root) == v39
    except (BaselineCertificationError, FileNotFoundError):
        candidate_matches = False
    matches = (
        all(manifest_integrity.values())
        and lineage == expected_lineage
        and web_fetch_safety_lineage == expected_web_fetch_safety_lineage
        and bounded_dns_resolver_lineage
        == expected_bounded_dns_resolver_lineage
        and http_request_safety_lineage
        == expected_http_request_safety_lineage
        and agent_observatory_lineage == expected_agent_observatory_lineage
        and visual_shell_lineage == expected_visual_shell_lineage
        and persistence_health_lineage == expected_persistence_health_lineage
        and deletion_ui_lineage == expected_deletion_ui_lineage
        and deletion_authority_lineage == expected_deletion_authority_lineage
        and memory_approval_ui_lineage == expected_memory_approval_ui_lineage
        and diff_fidelity_lineage == expected_diff_fidelity_lineage
        and diff_review_lineage == expected_diff_review_lineage
        and memory_approval_read_lineage == expected_memory_approval_read_lineage
        and memory_approval_lineage == expected_memory_approval_lineage
        and permission_ui_hardening_lineage == expected_permission_ui_hardening_lineage
        and permission_ui_lineage == expected_permission_ui_lineage
        and permission_command_review_lineage
        == expected_permission_command_review_lineage
        and permission_authority_lineage == expected_permission_authority_lineage
        and chat_stream_lineage == expected_chat_stream_lineage
        and store_switchover_lineage == expected_store_switchover_lineage
        and event_stream_lineage == expected_event_stream_lineage
        and live_refresh_lineage == expected_live_refresh_lineage
        and boundary_lineage == expected_boundary_lineage
        and cancellation_lineage == expected_cancellation_lineage
        and turn_identity_lineage == expected_turn_identity_lineage
        and chat_lineage == expected_chat_lineage
        and mcp_current_workspace_lineage == expected_mcp_current_workspace_lineage
        and mcp_current_projection_lineage == expected_mcp_current_projection_lineage
        and mcp_current_state_lineage == expected_mcp_current_state_lineage
        and mcp_runtime_lineage == expected_mcp_runtime_lineage
        and context_lineage == expected_context_lineage
        and model_cost_lineage == expected_model_cost_lineage
        and work_chain_lineage == expected_work_chain_lineage
        and usage_lineage == expected_usage_lineage
        and runtime_lineage == expected_runtime_lineage
        and model_lineage == expected_model_lineage
        and prior_lineage == expected_prior_lineage
        and historical_lineage == expected_historical_lineage
        and current["matches"]
        and candidate_matches
    )
    return {
        "activeBaselineId": BASELINE_V39_ID,
        "matches": matches,
        "manifestIntegrity": manifest_integrity,
        "lineage": lineage,
        "webSearchProviderLineage": lineage,
        "webFetchSafetyLineage": web_fetch_safety_lineage,
        "boundedDnsResolverLineage": bounded_dns_resolver_lineage,
        "httpRequestSafetyLineage": http_request_safety_lineage,
        "agentObservatoryLineage": agent_observatory_lineage,
        "visualShellLineage": visual_shell_lineage,
        "persistenceHealthLineage": persistence_health_lineage,
        "deletionUiLineage": deletion_ui_lineage,
        "deletionAuthorityLineage": deletion_authority_lineage,
        "memoryApprovalUiLineage": memory_approval_ui_lineage,
        "diffFidelityLineage": diff_fidelity_lineage,
        "diffReviewLineage": diff_review_lineage,
        "memoryApprovalReadLineage": memory_approval_read_lineage,
        "memoryApprovalLineage": memory_approval_lineage,
        "permissionUiHardeningLineage": permission_ui_hardening_lineage,
        "permissionUiLineage": permission_ui_lineage,
        "permissionCommandReviewLineage": permission_command_review_lineage,
        "permissionAuthorityLineage": permission_authority_lineage,
        "chatStreamLineage": chat_stream_lineage,
        "storeSwitchoverLineage": store_switchover_lineage,
        "eventStreamLineage": event_stream_lineage,
        "liveRefreshLineage": live_refresh_lineage,
        "boundaryLineage": boundary_lineage,
        "cancellationLineage": cancellation_lineage,
        "turnIdentityLineage": turn_identity_lineage,
        "chatLineage": chat_lineage,
        "mcpCurrentWorkspaceLineage": mcp_current_workspace_lineage,
        "mcpCurrentProjectionLineage": mcp_current_projection_lineage,
        "mcpCurrentStateLineage": mcp_current_state_lineage,
        "mcpRuntimeLineage": mcp_runtime_lineage,
        "contextLineage": context_lineage,
        "modelCostLineage": model_cost_lineage,
        "workChainLineage": work_chain_lineage,
        "usageLineage": usage_lineage,
        "runtimeLineage": runtime_lineage,
        "modelLineage": model_lineage,
        "historicalLineage": historical_lineage,
        "priorLineage": prior_lineage,
        "candidateMatches": candidate_matches,
        "currentFiles": current,
    }


def canonical_manifest_text(manifest: dict[str, Any]) -> str:
    """Serialize a manifest deterministically without machine metadata."""
    return (
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def write_v2_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v2 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v2", root)
    if not verify_manifest_version("v2", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v2 baseline is not valid")
    return target


def write_v3_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v3 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v3", root)
    if not verify_manifest_version("v3", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v3 baseline is not valid")
    return target


def write_v4_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v4 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v4", root)
    if not verify_manifest_version("v4", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v4 baseline is not valid")
    return target


def write_v5_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v5 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v5", root)
    if not verify_manifest_version("v5", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v5 baseline is not valid")
    return target


def write_v6_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v6 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v6", root)
    if not verify_manifest_version("v6", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v6 baseline is not valid")
    return target


def write_v7_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Write only the fixed v7 target after strict historical validation."""
    root = Path(project_root).resolve()
    target = _manifest_path("v7", root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        canonical_manifest_text(build_v7_candidate(project_root=root)),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def write_v8_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Write only the fixed v8 target after strict historical validation."""
    root = Path(project_root).resolve()
    target = _manifest_path("v8", root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        canonical_manifest_text(build_v8_candidate(project_root=root)),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def write_v9_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v9 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v9", root)
    if not verify_manifest_version("v9", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v9 baseline is not valid")
    return target


def write_v10_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v10 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v10", root)
    if not verify_manifest_version("v10", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v10 baseline is not valid")
    return target


def write_v11_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v11 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v11", root)
    if not verify_manifest_version("v11", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v11 baseline is not valid")
    return target


def write_v12_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v12 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v12", root)
    if not verify_manifest_version("v12", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v12 baseline is not valid")
    return target


def write_v13_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v13 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v13", root)
    if not verify_manifest_version("v13", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v13 baseline is not valid")
    return target


def write_v14_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v14 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v14", root)
    if not verify_manifest_version("v14", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v14 baseline is not valid")
    return target


def write_v15_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v15 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v15", root)
    if not verify_manifest_version("v15", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v15 baseline is not valid")
    return target


def write_v16_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v16 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v16", root)
    if not verify_manifest_version("v16", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v16 baseline is not valid")
    return target


def write_v17_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v17 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v17", root)
    if not verify_manifest_version("v17", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v17 baseline is not valid")
    return target


def write_v18_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v18 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v18", root)
    if not verify_manifest_version("v18", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v18 baseline is not valid")
    return target


def write_v19_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v19 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v19", root)
    if not verify_manifest_version("v19", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v19 baseline is not valid")
    return target


def write_v20_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v20 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v20", root)
    if not verify_manifest_version("v20", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v20 baseline is not valid")
    return target


def write_v21_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v21 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v21", root)
    if not verify_manifest_version("v21", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v21 baseline is not valid")
    return target


def write_v22_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v22 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v22", root)
    if not verify_manifest_version("v22", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v22 baseline is not valid")
    return target


def write_v23_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v23 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v23", root)
    if not verify_manifest_version("v23", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v23 baseline is not valid")
    return target


def write_v24_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v24 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v24", root)
    if not verify_manifest_version("v24", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v24 baseline is not valid")
    return target


def write_v25_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v25 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v25", root)
    if not verify_manifest_version("v25", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v25 baseline is not valid")
    return target


def write_v26_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v26 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v26", root)
    if not verify_manifest_version("v26", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v26 baseline is not valid")
    return target


def write_v27_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v27 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v27", root)
    if not verify_manifest_version("v27", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v27 baseline is not valid")
    return target


def write_v28_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v28 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v28", root)
    if not verify_manifest_version("v28", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v28 baseline is not valid")
    return target


def write_v29_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v29 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v29", root)
    if not verify_manifest_version("v29", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v29 baseline is not valid")
    return target


def write_v30_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v30 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v30", root)
    if not verify_manifest_version("v30", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v30 baseline is not valid")
    return target


def write_v31_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v31 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v31", root)
    if not verify_manifest_version("v31", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v31 baseline is not valid")
    return target


def write_v32_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v32 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v32", root)
    if not verify_manifest_version("v32", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v32 baseline is not valid")
    return target


def write_v33_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v33 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v33", root)
    if not verify_manifest_version("v33", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v33 baseline is not valid")
    return target


def write_v34_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v34 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v34", root)
    if not verify_manifest_version("v34", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v34 baseline is not valid")
    return target


def write_v35_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v35 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v35", root)
    if not verify_manifest_version("v35", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v35 baseline is not valid")
    return target


def write_v36_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v36 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v36", root)
    if not verify_manifest_version("v36", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v36 baseline is not valid")
    return target


def write_v37_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v37 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v37", root)
    if not verify_manifest_version("v37", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v37 baseline is not valid")
    return target


def write_v38_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the immutable pinned v38 target without rewriting history."""
    root = Path(project_root).resolve()
    target = _manifest_path("v38", root)
    if not verify_manifest_version("v38", project_root=root)["matches"]:
        raise BaselineCertificationError("historical v38 baseline is not valid")
    return target


def write_v39_manifest(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Write only the fixed v39 target after strict historical validation."""
    root = Path(project_root).resolve()
    target = _manifest_path("v39", root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        canonical_manifest_text(build_v39_candidate(project_root=root)),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the versioned Memory Retrieval production baseline."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--print-v2",
        action="store_true",
        help="Print the deterministic candidate without writing it.",
    )
    action.add_argument(
        "--write-v2",
        action="store_true",
        help="Write only the fixed v2 manifest target.",
    )
    action.add_argument(
        "--print-v3",
        action="store_true",
        help="Print the deterministic v3 candidate without writing it.",
    )
    action.add_argument(
        "--write-v3",
        action="store_true",
        help="Write only the fixed v3 manifest target.",
    )
    action.add_argument(
        "--print-v4",
        action="store_true",
        help="Print the deterministic v4 candidate without writing it.",
    )
    action.add_argument(
        "--write-v4",
        action="store_true",
        help="Validate only the fixed immutable v4 manifest target.",
    )
    action.add_argument(
        "--print-v5",
        action="store_true",
        help="Print the deterministic v5 candidate without writing it.",
    )
    action.add_argument(
        "--write-v5",
        action="store_true",
        help="Write only the fixed v5 manifest target.",
    )
    action.add_argument(
        "--print-v6",
        action="store_true",
        help="Print the deterministic v6 candidate without writing it.",
    )
    action.add_argument(
        "--write-v6",
        action="store_true",
        help="Validate only the fixed immutable v6 manifest target.",
    )
    action.add_argument(
        "--print-v7",
        action="store_true",
        help="Print the deterministic v7 candidate without writing it.",
    )
    action.add_argument(
        "--write-v7",
        action="store_true",
        help="Write only the fixed v7 manifest target.",
    )
    action.add_argument(
        "--print-v8",
        action="store_true",
        help="Print the deterministic v8 candidate without writing it.",
    )
    action.add_argument(
        "--write-v8",
        action="store_true",
        help="Write only the fixed v8 manifest target.",
    )
    action.add_argument(
        "--print-v9",
        action="store_true",
        help="Print the deterministic v9 candidate without writing it.",
    )
    action.add_argument(
        "--write-v9",
        action="store_true",
        help="Write only the fixed v9 manifest target.",
    )
    action.add_argument(
        "--print-v10",
        action="store_true",
        help="Print the deterministic v10 candidate without writing it.",
    )
    action.add_argument(
        "--write-v10",
        action="store_true",
        help="Write only the fixed v10 manifest target.",
    )
    action.add_argument(
        "--print-v11",
        action="store_true",
        help="Print the deterministic v11 candidate without writing it.",
    )
    action.add_argument(
        "--write-v11",
        action="store_true",
        help="Write only the fixed v11 manifest target.",
    )
    action.add_argument(
        "--print-v12",
        action="store_true",
        help="Print the deterministic v12 candidate without writing it.",
    )
    action.add_argument(
        "--write-v12",
        action="store_true",
        help="Validate only the fixed immutable v12 manifest target.",
    )
    action.add_argument(
        "--print-v13",
        action="store_true",
        help="Print the deterministic v13 candidate without writing it.",
    )
    action.add_argument(
        "--write-v13",
        action="store_true",
        help="Validate only the fixed immutable v13 manifest target.",
    )
    action.add_argument(
        "--print-v14",
        action="store_true",
        help="Print the deterministic v14 candidate without writing it.",
    )
    action.add_argument(
        "--write-v14",
        action="store_true",
        help="Validate only the fixed immutable v14 manifest target.",
    )
    action.add_argument(
        "--print-v15",
        action="store_true",
        help="Print the deterministic v15 candidate without writing it.",
    )
    action.add_argument(
        "--write-v15",
        action="store_true",
        help="Validate only the fixed immutable v15 manifest target.",
    )
    action.add_argument(
        "--print-v16",
        action="store_true",
        help="Print the deterministic v16 candidate without writing it.",
    )
    action.add_argument(
        "--write-v16",
        action="store_true",
        help="Validate only the fixed immutable v16 manifest target.",
    )
    action.add_argument(
        "--print-v17",
        action="store_true",
        help="Print the deterministic v17 candidate without writing it.",
    )
    action.add_argument(
        "--write-v17",
        action="store_true",
        help="Validate only the fixed immutable v17 manifest target.",
    )
    action.add_argument(
        "--print-v18",
        action="store_true",
        help="Print the deterministic v18 candidate without writing it.",
    )
    action.add_argument(
        "--write-v18",
        action="store_true",
        help="Validate only the fixed immutable v18 manifest target.",
    )
    action.add_argument(
        "--print-v19",
        action="store_true",
        help="Print the deterministic v19 candidate without writing it.",
    )
    action.add_argument(
        "--write-v19",
        action="store_true",
        help="Write only the fixed v19 manifest target.",
    )
    action.add_argument(
        "--print-v20",
        action="store_true",
        help="Print the deterministic v20 candidate without writing it.",
    )
    action.add_argument(
        "--write-v20",
        action="store_true",
        help="Write only the fixed v20 manifest target.",
    )
    action.add_argument(
        "--print-v21",
        action="store_true",
        help="Print the deterministic v21 candidate without writing it.",
    )
    action.add_argument(
        "--write-v21",
        action="store_true",
        help="Write only the fixed v21 manifest target.",
    )
    action.add_argument(
        "--print-v22",
        action="store_true",
        help="Print the deterministic v22 candidate without writing it.",
    )
    action.add_argument(
        "--write-v22",
        action="store_true",
        help="Validate only the fixed immutable v22 manifest target.",
    )
    action.add_argument(
        "--print-v23",
        action="store_true",
        help="Print the deterministic v23 candidate without writing it.",
    )
    action.add_argument(
        "--write-v23",
        action="store_true",
        help="Validate only the fixed immutable v23 manifest target.",
    )
    action.add_argument(
        "--print-v24",
        action="store_true",
        help="Print the deterministic v24 candidate without writing it.",
    )
    action.add_argument(
        "--write-v24",
        action="store_true",
        help="Validate only the fixed immutable v24 manifest target.",
    )
    action.add_argument(
        "--print-v25",
        action="store_true",
        help="Print the deterministic v25 candidate without writing it.",
    )
    action.add_argument(
        "--write-v25",
        action="store_true",
        help="Write only the fixed v25 manifest target.",
    )
    action.add_argument(
        "--print-v26",
        action="store_true",
        help="Print the deterministic v26 candidate without writing it.",
    )
    action.add_argument(
        "--write-v26",
        action="store_true",
        help="Validate only the fixed immutable v26 manifest target.",
    )
    action.add_argument(
        "--print-v27",
        action="store_true",
        help="Print the deterministic v27 candidate without writing it.",
    )
    action.add_argument(
        "--write-v27",
        action="store_true",
        help="Write only the fixed v27 manifest target.",
    )
    action.add_argument(
        "--print-v28",
        action="store_true",
        help="Print the deterministic v28 candidate without writing it.",
    )
    action.add_argument(
        "--write-v28",
        action="store_true",
        help="Validate only the fixed immutable v28 manifest target.",
    )
    action.add_argument(
        "--print-v29",
        action="store_true",
        help="Print the deterministic v29 candidate without writing it.",
    )
    action.add_argument(
        "--write-v29",
        action="store_true",
        help="Write only the fixed v29 manifest target.",
    )
    action.add_argument(
        "--print-v30",
        action="store_true",
        help="Print the deterministic v30 candidate without writing it.",
    )
    action.add_argument(
        "--write-v30",
        action="store_true",
        help="Write only the fixed v30 manifest target.",
    )
    action.add_argument(
        "--print-v31",
        action="store_true",
        help="Print the deterministic v31 candidate without writing it.",
    )
    action.add_argument(
        "--write-v31",
        action="store_true",
        help="Write only the fixed v31 manifest target.",
    )
    action.add_argument(
        "--print-v32",
        action="store_true",
        help="Print the deterministic v32 candidate without writing it.",
    )
    action.add_argument(
        "--write-v32",
        action="store_true",
        help="Write only the fixed v32 manifest target.",
    )
    action.add_argument(
        "--print-v33",
        action="store_true",
        help="Print the deterministic v33 candidate without writing it.",
    )
    action.add_argument(
        "--write-v33",
        action="store_true",
        help="Validate only the fixed immutable v33 manifest target.",
    )
    action.add_argument(
        "--print-v34",
        action="store_true",
        help="Print the deterministic v34 candidate without writing it.",
    )
    action.add_argument(
        "--write-v34",
        action="store_true",
        help="Validate only the fixed immutable v34 manifest target.",
    )
    action.add_argument(
        "--print-v35",
        action="store_true",
        help="Print the deterministic v35 candidate without writing it.",
    )
    action.add_argument(
        "--write-v35",
        action="store_true",
        help="Write only the fixed v35 manifest target.",
    )
    action.add_argument(
        "--print-v36",
        action="store_true",
        help="Print the deterministic v36 candidate without writing it.",
    )
    action.add_argument(
        "--write-v36",
        action="store_true",
        help="Write only the fixed v36 manifest target.",
    )
    action.add_argument(
        "--print-v37",
        action="store_true",
        help="Print the deterministic v37 candidate without writing it.",
    )
    action.add_argument(
        "--write-v37",
        action="store_true",
        help="Validate only the fixed immutable v37 manifest target.",
    )
    action.add_argument(
        "--print-v38",
        action="store_true",
        help="Print the deterministic v38 candidate without writing it.",
    )
    action.add_argument(
        "--write-v38",
        action="store_true",
        help="Write only the fixed v38 manifest target.",
    )
    action.add_argument(
        "--print-v39",
        action="store_true",
        help="Print the deterministic v39 candidate without writing it.",
    )
    action.add_argument(
        "--write-v39",
        action="store_true",
        help="Write only the fixed v39 manifest target.",
    )
    args = parser.parse_args(argv)
    try:
        if args.print_v2:
            sys.stdout.write(canonical_manifest_text(build_v2_candidate()))
            return 0
        if args.write_v2:
            write_v2_manifest()
        if args.print_v3:
            sys.stdout.write(canonical_manifest_text(build_v3_candidate()))
            return 0
        if args.write_v3:
            write_v3_manifest()
        if args.print_v4:
            sys.stdout.write(canonical_manifest_text(build_v4_candidate()))
            return 0
        if args.write_v4:
            write_v4_manifest()
        if args.print_v5:
            sys.stdout.write(canonical_manifest_text(build_v5_candidate()))
            return 0
        if args.write_v5:
            write_v5_manifest()
            return 0
        if args.print_v6:
            sys.stdout.write(canonical_manifest_text(build_v6_candidate()))
            return 0
        if args.write_v6:
            write_v6_manifest()
            return 0
        if args.print_v7:
            sys.stdout.write(canonical_manifest_text(build_v7_candidate()))
            return 0
        if args.write_v7:
            write_v7_manifest()
            return 0
        if args.print_v8:
            sys.stdout.write(canonical_manifest_text(build_v8_candidate()))
            return 0
        if args.write_v8:
            write_v8_manifest()
            return 0
        if args.print_v9:
            sys.stdout.write(canonical_manifest_text(build_v9_candidate()))
            return 0
        if args.write_v9:
            write_v9_manifest()
            return 0
        if args.print_v10:
            sys.stdout.write(canonical_manifest_text(build_v10_candidate()))
            return 0
        if args.write_v10:
            write_v10_manifest()
            return 0
        if args.print_v11:
            sys.stdout.write(canonical_manifest_text(build_v11_candidate()))
            return 0
        if args.write_v11:
            write_v11_manifest()
            return 0
        if args.print_v12:
            sys.stdout.write(canonical_manifest_text(build_v12_candidate()))
            return 0
        if args.write_v12:
            write_v12_manifest()
            return 0
        if args.print_v13:
            sys.stdout.write(canonical_manifest_text(build_v13_candidate()))
            return 0
        if args.write_v13:
            write_v13_manifest()
            return 0
        if args.print_v14:
            sys.stdout.write(canonical_manifest_text(build_v14_candidate()))
            return 0
        if args.write_v14:
            write_v14_manifest()
            return 0
        if args.print_v15:
            sys.stdout.write(canonical_manifest_text(build_v15_candidate()))
            return 0
        if args.write_v15:
            write_v15_manifest()
            return 0
        if args.print_v16:
            sys.stdout.write(canonical_manifest_text(build_v16_candidate()))
            return 0
        if args.write_v16:
            write_v16_manifest()
            return 0
        if args.print_v17:
            sys.stdout.write(canonical_manifest_text(build_v17_candidate()))
            return 0
        if args.write_v17:
            write_v17_manifest()
            return 0
        if args.print_v18:
            sys.stdout.write(canonical_manifest_text(build_v18_candidate()))
            return 0
        if args.write_v18:
            write_v18_manifest()
            return 0
        if args.print_v19:
            sys.stdout.write(canonical_manifest_text(build_v19_candidate()))
            return 0
        if args.write_v19:
            write_v19_manifest()
            return 0
        if args.print_v20:
            sys.stdout.write(canonical_manifest_text(build_v20_candidate()))
            return 0
        if args.write_v20:
            write_v20_manifest()
            return 0
        if args.print_v21:
            sys.stdout.write(canonical_manifest_text(build_v21_candidate()))
            return 0
        if args.write_v21:
            write_v21_manifest()
            return 0
        if args.print_v22:
            sys.stdout.write(canonical_manifest_text(build_v22_candidate()))
            return 0
        if args.write_v22:
            write_v22_manifest()
            return 0
        if args.print_v23:
            sys.stdout.write(canonical_manifest_text(build_v23_candidate()))
            return 0
        if args.write_v23:
            write_v23_manifest()
            return 0
        if args.print_v24:
            sys.stdout.write(canonical_manifest_text(build_v24_candidate()))
            return 0
        if args.write_v24:
            write_v24_manifest()
            return 0
        if args.print_v25:
            sys.stdout.write(canonical_manifest_text(build_v25_candidate()))
            return 0
        if args.write_v25:
            write_v25_manifest()
            return 0
        if args.print_v26:
            sys.stdout.write(canonical_manifest_text(build_v26_candidate()))
            return 0
        if args.write_v26:
            write_v26_manifest()
            return 0
        if args.print_v27:
            sys.stdout.write(canonical_manifest_text(build_v27_candidate()))
            return 0
        if args.write_v27:
            write_v27_manifest()
            return 0
        if args.print_v28:
            sys.stdout.write(canonical_manifest_text(build_v28_candidate()))
            return 0
        if args.write_v28:
            write_v28_manifest()
            return 0
        if args.print_v29:
            sys.stdout.write(canonical_manifest_text(build_v29_candidate()))
            return 0
        if args.write_v29:
            write_v29_manifest()
            return 0
        if args.print_v30:
            sys.stdout.write(canonical_manifest_text(build_v30_candidate()))
            return 0
        if args.write_v30:
            write_v30_manifest()
            return 0
        if args.print_v31:
            sys.stdout.write(canonical_manifest_text(build_v31_candidate()))
            return 0
        if args.write_v31:
            write_v31_manifest()
            return 0
        if args.print_v32:
            sys.stdout.write(canonical_manifest_text(build_v32_candidate()))
            return 0
        if args.write_v32:
            write_v32_manifest()
            return 0
        if args.print_v33:
            sys.stdout.write(canonical_manifest_text(build_v33_candidate()))
            return 0
        if args.write_v33:
            write_v33_manifest()
            return 0
        if args.print_v34:
            sys.stdout.write(canonical_manifest_text(build_v34_candidate()))
            return 0
        if args.write_v34:
            write_v34_manifest()
            return 0
        if args.print_v35:
            sys.stdout.write(canonical_manifest_text(build_v35_candidate()))
            return 0
        if args.write_v35:
            write_v35_manifest()
            return 0
        if args.print_v36:
            sys.stdout.write(canonical_manifest_text(build_v36_candidate()))
            return 0
        if args.write_v36:
            write_v36_manifest()
            return 0
        if args.print_v37:
            sys.stdout.write(canonical_manifest_text(build_v37_candidate()))
            return 0
        if args.write_v37:
            write_v37_manifest()
            return 0
        if args.print_v38:
            sys.stdout.write(canonical_manifest_text(build_v38_candidate()))
            return 0
        if args.write_v38:
            write_v38_manifest()
            return 0
        if args.print_v39:
            sys.stdout.write(canonical_manifest_text(build_v39_candidate()))
            return 0
        if args.write_v39:
            write_v39_manifest()
            return 0
        result = verify_active_baseline()
    except (
        BaselineCertificationError,
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        sys.stdout.write(
            '{"error":{"code":"baseline_verification_failed"},"matches":false}\n'
        )
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if result["matches"] else 1


PRODUCTION_RETRIEVAL_HASHES_V1 = dict(EXPECTED_V1_FILES)
PRODUCTION_RETRIEVAL_HASHES_V2 = dict(load_baseline_manifest("v2")["files"])
try:
    PRODUCTION_RETRIEVAL_HASHES_V3 = dict(load_baseline_manifest("v3")["files"])
except FileNotFoundError:
    PRODUCTION_RETRIEVAL_HASHES_V3 = dict(build_v3_candidate()["files"])
try:
    PRODUCTION_RETRIEVAL_HASHES_V4 = dict(load_baseline_manifest("v4")["files"])
except FileNotFoundError:
    PRODUCTION_RETRIEVAL_HASHES_V4 = dict(build_v4_candidate()["files"])
try:
    PRODUCTION_RETRIEVAL_HASHES_V5 = dict(load_baseline_manifest("v5")["files"])
except FileNotFoundError:
    PRODUCTION_RETRIEVAL_HASHES_V5 = dict(build_v5_candidate()["files"])
try:
    PRODUCTION_RETRIEVAL_HASHES_V6 = dict(load_baseline_manifest("v6")["files"])
except FileNotFoundError:
    PRODUCTION_RETRIEVAL_HASHES_V6 = dict(build_v6_candidate()["files"])
try:
    PRODUCTION_RETRIEVAL_HASHES_V7 = dict(load_baseline_manifest("v7")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V7 = dict(build_v7_candidate()["files"])
    except BaselineCertificationError:
        PRODUCTION_RETRIEVAL_HASHES_V7 = dict(PRODUCTION_RETRIEVAL_HASHES_V6)
try:
    PRODUCTION_RETRIEVAL_HASHES_V8 = dict(load_baseline_manifest("v8")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V8 = dict(build_v8_candidate()["files"])
    except BaselineCertificationError:
        PRODUCTION_RETRIEVAL_HASHES_V8 = dict(PRODUCTION_RETRIEVAL_HASHES_V7)
try:
    PRODUCTION_RETRIEVAL_HASHES_V9 = dict(load_baseline_manifest("v9")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V9 = dict(build_v9_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V9 = dict(PRODUCTION_RETRIEVAL_HASHES_V8)
try:
    PRODUCTION_RETRIEVAL_HASHES_V10 = dict(load_baseline_manifest("v10")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V10 = dict(build_v10_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V10 = dict(PRODUCTION_RETRIEVAL_HASHES_V9)
try:
    PRODUCTION_RETRIEVAL_HASHES_V11 = dict(load_baseline_manifest("v11")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V11 = dict(build_v11_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V11 = dict(PRODUCTION_RETRIEVAL_HASHES_V10)
try:
    PRODUCTION_RETRIEVAL_HASHES_V12 = dict(load_baseline_manifest("v12")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V12 = dict(build_v12_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V12 = dict(PRODUCTION_RETRIEVAL_HASHES_V11)
try:
    PRODUCTION_RETRIEVAL_HASHES_V13 = dict(load_baseline_manifest("v13")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V13 = dict(build_v13_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V13 = dict(PRODUCTION_RETRIEVAL_HASHES_V12)
try:
    PRODUCTION_RETRIEVAL_HASHES_V14 = dict(load_baseline_manifest("v14")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V14 = dict(build_v14_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V14 = dict(PRODUCTION_RETRIEVAL_HASHES_V13)
try:
    PRODUCTION_RETRIEVAL_HASHES_V15 = dict(load_baseline_manifest("v15")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V15 = dict(build_v15_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V15 = dict(PRODUCTION_RETRIEVAL_HASHES_V14)
try:
    PRODUCTION_RETRIEVAL_HASHES_V16 = dict(load_baseline_manifest("v16")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V16 = dict(build_v16_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V16 = dict(PRODUCTION_RETRIEVAL_HASHES_V15)
try:
    PRODUCTION_RETRIEVAL_HASHES_V17 = dict(load_baseline_manifest("v17")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V17 = dict(build_v17_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V17 = dict(PRODUCTION_RETRIEVAL_HASHES_V16)
try:
    PRODUCTION_RETRIEVAL_HASHES_V18 = dict(load_baseline_manifest("v18")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V18 = dict(build_v18_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V18 = dict(PRODUCTION_RETRIEVAL_HASHES_V17)
try:
    PRODUCTION_RETRIEVAL_HASHES_V19 = dict(load_baseline_manifest("v19")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V19 = dict(build_v19_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V19 = dict(PRODUCTION_RETRIEVAL_HASHES_V18)
try:
    PRODUCTION_RETRIEVAL_HASHES_V20 = dict(load_baseline_manifest("v20")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V20 = dict(build_v20_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V20 = dict(PRODUCTION_RETRIEVAL_HASHES_V19)
try:
    PRODUCTION_RETRIEVAL_HASHES_V21 = dict(load_baseline_manifest("v21")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V21 = dict(build_v21_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V21 = dict(PRODUCTION_RETRIEVAL_HASHES_V20)
try:
    PRODUCTION_RETRIEVAL_HASHES_V22 = dict(load_baseline_manifest("v22")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V22 = dict(build_v22_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V22 = dict(PRODUCTION_RETRIEVAL_HASHES_V21)
try:
    PRODUCTION_RETRIEVAL_HASHES_V23 = dict(load_baseline_manifest("v23")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V23 = dict(build_v23_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V23 = dict(PRODUCTION_RETRIEVAL_HASHES_V22)
try:
    PRODUCTION_RETRIEVAL_HASHES_V24 = dict(load_baseline_manifest("v24")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V24 = dict(build_v24_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V24 = dict(PRODUCTION_RETRIEVAL_HASHES_V23)
try:
    PRODUCTION_RETRIEVAL_HASHES_V25 = dict(load_baseline_manifest("v25")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V25 = dict(build_v25_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V25 = dict(PRODUCTION_RETRIEVAL_HASHES_V24)
try:
    PRODUCTION_RETRIEVAL_HASHES_V26 = dict(load_baseline_manifest("v26")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V26 = dict(build_v26_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V26 = dict(PRODUCTION_RETRIEVAL_HASHES_V25)
try:
    PRODUCTION_RETRIEVAL_HASHES_V27 = dict(load_baseline_manifest("v27")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V27 = dict(build_v27_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V27 = dict(PRODUCTION_RETRIEVAL_HASHES_V26)
try:
    PRODUCTION_RETRIEVAL_HASHES_V28 = dict(load_baseline_manifest("v28")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V28 = dict(build_v28_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V28 = dict(PRODUCTION_RETRIEVAL_HASHES_V27)
try:
    PRODUCTION_RETRIEVAL_HASHES_V29 = dict(load_baseline_manifest("v29")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V29 = dict(build_v29_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V29 = dict(PRODUCTION_RETRIEVAL_HASHES_V28)
try:
    PRODUCTION_RETRIEVAL_HASHES_V30 = dict(load_baseline_manifest("v30")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V30 = dict(build_v30_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V30 = dict(PRODUCTION_RETRIEVAL_HASHES_V29)
try:
    PRODUCTION_RETRIEVAL_HASHES_V31 = dict(load_baseline_manifest("v31")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V31 = dict(build_v31_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V31 = dict(PRODUCTION_RETRIEVAL_HASHES_V30)
try:
    PRODUCTION_RETRIEVAL_HASHES_V32 = dict(load_baseline_manifest("v32")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V32 = dict(build_v32_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V32 = dict(PRODUCTION_RETRIEVAL_HASHES_V31)
try:
    PRODUCTION_RETRIEVAL_HASHES_V33 = dict(load_baseline_manifest("v33")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V33 = dict(build_v33_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V33 = dict(PRODUCTION_RETRIEVAL_HASHES_V32)
try:
    PRODUCTION_RETRIEVAL_HASHES_V34 = dict(load_baseline_manifest("v34")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V34 = dict(build_v34_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V34 = dict(PRODUCTION_RETRIEVAL_HASHES_V33)
try:
    PRODUCTION_RETRIEVAL_HASHES_V35 = dict(load_baseline_manifest("v35")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V35 = dict(build_v35_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V35 = dict(PRODUCTION_RETRIEVAL_HASHES_V34)
try:
    PRODUCTION_RETRIEVAL_HASHES_V36 = dict(load_baseline_manifest("v36")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V36 = dict(build_v36_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V36 = dict(PRODUCTION_RETRIEVAL_HASHES_V35)
try:
    PRODUCTION_RETRIEVAL_HASHES_V37 = dict(load_baseline_manifest("v37")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V37 = dict(build_v37_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V37 = dict(PRODUCTION_RETRIEVAL_HASHES_V36)
try:
    PRODUCTION_RETRIEVAL_HASHES_V38 = dict(load_baseline_manifest("v38")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V38 = dict(build_v38_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V38 = dict(PRODUCTION_RETRIEVAL_HASHES_V37)
try:
    PRODUCTION_RETRIEVAL_HASHES_V39 = dict(load_baseline_manifest("v39")["files"])
except FileNotFoundError:
    try:
        PRODUCTION_RETRIEVAL_HASHES_V39 = dict(build_v39_candidate()["files"])
    except (BaselineCertificationError, FileNotFoundError):
        PRODUCTION_RETRIEVAL_HASHES_V39 = dict(PRODUCTION_RETRIEVAL_HASHES_V38)
ACTIVE_PRODUCTION_RETRIEVAL_HASHES = PRODUCTION_RETRIEVAL_HASHES_V39


__all__ = [
    "BASELINE_V1_ID",
    "BASELINE_V2_ID",
    "BASELINE_V3_ID",
    "BASELINE_V4_ID",
    "BASELINE_V5_ID",
    "BASELINE_V6_ID",
    "BASELINE_V7_ID",
    "BASELINE_V8_ID",
    "BASELINE_V9_ID",
    "BASELINE_V10_ID",
    "BASELINE_V26_ID",
    "BASELINE_V27_ID",
    "BASELINE_V28_ID",
    "BASELINE_V29_ID",
    "BASELINE_V30_ID",
    "BASELINE_V31_ID",
    "BASELINE_V32_ID",
    "BASELINE_V33_ID",
    "BASELINE_V34_ID",
    "BASELINE_V35_ID",
    "BASELINE_V36_ID",
    "BASELINE_V37_ID",
    "BASELINE_V38_ID",
    "BASELINE_V39_ID",
    "BASELINE_V11_ID",
    "BASELINE_V12_ID",
    "BASELINE_V13_ID",
    "BASELINE_V14_ID",
    "BASELINE_V15_ID",
    "BASELINE_V16_ID",
    "BASELINE_V17_ID",
    "BASELINE_V18_ID",
    "BASELINE_V19_ID",
    "BASELINE_V20_ID",
    "BASELINE_V21_ID",
    "BASELINE_V22_ID",
    "BASELINE_V23_ID",
    "BASELINE_V24_ID",
    "BASELINE_V25_ID",
    "ACTIVE_PRODUCTION_RETRIEVAL_HASHES",
    "BaselineCertificationError",
    "EXPECTED_ADDED_FILES",
    "EXPECTED_CHANGED_FILES",
    "EXPECTED_V39_ADDED_FILES",
    "EXPECTED_V39_CHANGED_FILES",
    "EXPECTED_V1_FILES",
    "EXPECTED_V3_CHANGED_FILES",
    "EXPECTED_V4_ADDED_FILES",
    "EXPECTED_V4_CHANGED_FILES",
    "EXPECTED_V5_CHANGED_FILES",
    "EXPECTED_V6_CHANGED_FILES",
    "EXPECTED_V7_CHANGED_FILES",
    "EXPECTED_V8_ADDED_FILES",
    "EXPECTED_V8_CHANGED_FILES",
    "EXPECTED_V9_ADDED_FILES",
    "EXPECTED_V9_CHANGED_FILES",
    "EXPECTED_V10_ADDED_FILES",
    "EXPECTED_V10_CHANGED_FILES",
    "EXPECTED_V11_ADDED_FILES",
    "EXPECTED_V11_CHANGED_FILES",
    "EXPECTED_V12_CHANGED_FILES",
    "EXPECTED_V13_CHANGED_FILES",
    "EXPECTED_V14_ADDED_FILES",
    "EXPECTED_V14_CHANGED_FILES",
    "EXPECTED_V15_ADDED_FILES",
    "EXPECTED_V15_CHANGED_FILES",
    "EXPECTED_V16_ADDED_FILES",
    "EXPECTED_V16_CHANGED_FILES",
    "EXPECTED_V17_CHANGED_FILES",
    "EXPECTED_V18_ADDED_FILES",
    "EXPECTED_V18_CHANGED_FILES",
    "EXPECTED_V19_ADDED_FILES",
    "EXPECTED_V19_CHANGED_FILES",
    "EXPECTED_V20_CHANGED_FILES",
    "EXPECTED_V21_ADDED_FILES",
    "EXPECTED_V21_CHANGED_FILES",
    "EXPECTED_V22_ADDED_FILES",
    "EXPECTED_V22_CHANGED_FILES",
    "EXPECTED_V23_ADDED_FILES",
    "EXPECTED_V23_CHANGED_FILES",
    "EXPECTED_V24_ADDED_FILES",
    "EXPECTED_V24_CHANGED_FILES",
    "EXPECTED_V25_ADDED_FILES",
    "EXPECTED_V25_CHANGED_FILES",
    "EXPECTED_V26_ADDED_FILES",
    "EXPECTED_V26_CHANGED_FILES",
    "EXPECTED_V27_ADDED_FILES",
    "EXPECTED_V27_CHANGED_FILES",
    "EXPECTED_V28_ADDED_FILES",
    "EXPECTED_V28_CHANGED_FILES",
    "EXPECTED_V29_ADDED_FILES",
    "EXPECTED_V29_CHANGED_FILES",
    "EXPECTED_V30_ADDED_FILES",
    "EXPECTED_V30_CHANGED_FILES",
    "EXPECTED_V31_ADDED_FILES",
    "EXPECTED_V31_CHANGED_FILES",
    "EXPECTED_V32_ADDED_FILES",
    "EXPECTED_V32_CHANGED_FILES",
    "EXPECTED_V33_ADDED_FILES",
    "EXPECTED_V33_CHANGED_FILES",
    "EXPECTED_V34_ADDED_FILES",
    "EXPECTED_V34_CHANGED_FILES",
    "EXPECTED_V35_ADDED_FILES",
    "EXPECTED_V35_CHANGED_FILES",
    "EXPECTED_V36_ADDED_FILES",
    "EXPECTED_V36_CHANGED_FILES",
    "EXPECTED_V37_ADDED_FILES",
    "EXPECTED_V37_CHANGED_FILES",
    "EXPECTED_V38_ADDED_FILES",
    "EXPECTED_V38_CHANGED_FILES",
    "PINNED_MANIFEST_SHA256",
    "PRODUCTION_RETRIEVAL_HASHES_V1",
    "PRODUCTION_RETRIEVAL_HASHES_V2",
    "PRODUCTION_RETRIEVAL_HASHES_V3",
    "PRODUCTION_RETRIEVAL_HASHES_V4",
    "PRODUCTION_RETRIEVAL_HASHES_V5",
    "PRODUCTION_RETRIEVAL_HASHES_V6",
    "PRODUCTION_RETRIEVAL_HASHES_V7",
    "PRODUCTION_RETRIEVAL_HASHES_V8",
    "PRODUCTION_RETRIEVAL_HASHES_V9",
    "PRODUCTION_RETRIEVAL_HASHES_V10",
    "PRODUCTION_RETRIEVAL_HASHES_V11",
    "PRODUCTION_RETRIEVAL_HASHES_V12",
    "PRODUCTION_RETRIEVAL_HASHES_V13",
    "PRODUCTION_RETRIEVAL_HASHES_V14",
    "PRODUCTION_RETRIEVAL_HASHES_V15",
    "PRODUCTION_RETRIEVAL_HASHES_V16",
    "PRODUCTION_RETRIEVAL_HASHES_V17",
    "PRODUCTION_RETRIEVAL_HASHES_V18",
    "PRODUCTION_RETRIEVAL_HASHES_V19",
    "PRODUCTION_RETRIEVAL_HASHES_V20",
    "PRODUCTION_RETRIEVAL_HASHES_V21",
    "PRODUCTION_RETRIEVAL_HASHES_V22",
    "PRODUCTION_RETRIEVAL_HASHES_V23",
    "PRODUCTION_RETRIEVAL_HASHES_V24",
    "PRODUCTION_RETRIEVAL_HASHES_V25",
    "PRODUCTION_RETRIEVAL_HASHES_V26",
    "PRODUCTION_RETRIEVAL_HASHES_V27",
    "PRODUCTION_RETRIEVAL_HASHES_V28",
    "PRODUCTION_RETRIEVAL_HASHES_V29",
    "PRODUCTION_RETRIEVAL_HASHES_V30",
    "PRODUCTION_RETRIEVAL_HASHES_V31",
    "PRODUCTION_RETRIEVAL_HASHES_V32",
    "PRODUCTION_RETRIEVAL_HASHES_V33",
    "PRODUCTION_RETRIEVAL_HASHES_V34",
    "PRODUCTION_RETRIEVAL_HASHES_V35",
    "PRODUCTION_RETRIEVAL_HASHES_V36",
    "PRODUCTION_RETRIEVAL_HASHES_V37",
    "PRODUCTION_RETRIEVAL_HASHES_V38",
    "PRODUCTION_RETRIEVAL_HASHES_V39",
    "build_v2_candidate",
    "build_v3_candidate",
    "build_v4_candidate",
    "build_v5_candidate",
    "build_v6_candidate",
    "build_v7_candidate",
    "build_v8_candidate",
    "build_v9_candidate",
    "build_v10_candidate",
    "build_v11_candidate",
    "build_v12_candidate",
    "build_v13_candidate",
    "build_v14_candidate",
    "build_v15_candidate",
    "build_v16_candidate",
    "build_v17_candidate",
    "build_v18_candidate",
    "build_v19_candidate",
    "build_v20_candidate",
    "build_v21_candidate",
    "build_v22_candidate",
    "build_v23_candidate",
    "build_v24_candidate",
    "build_v25_candidate",
    "build_v26_candidate",
    "build_v27_candidate",
    "build_v28_candidate",
    "build_v29_candidate",
    "build_v30_candidate",
    "build_v31_candidate",
    "build_v32_candidate",
    "build_v33_candidate",
    "build_v34_candidate",
    "build_v35_candidate",
    "build_v36_candidate",
    "build_v37_candidate",
    "build_v38_candidate",
    "build_v39_candidate",
    "canonical_manifest_text",
    "compare_baselines",
    "load_baseline_manifest",
    "sha256_file",
    "validate_manifest",
    "verify_active_baseline",
    "verify_manifest_version",
    "write_v2_manifest",
    "write_v3_manifest",
    "write_v4_manifest",
    "write_v5_manifest",
    "write_v6_manifest",
    "write_v7_manifest",
    "write_v8_manifest",
    "write_v9_manifest",
    "write_v10_manifest",
    "write_v11_manifest",
    "write_v12_manifest",
    "write_v13_manifest",
    "write_v14_manifest",
    "write_v15_manifest",
    "write_v16_manifest",
    "write_v17_manifest",
    "write_v18_manifest",
    "write_v19_manifest",
    "write_v20_manifest",
    "write_v21_manifest",
    "write_v22_manifest",
    "write_v23_manifest",
    "write_v24_manifest",
    "write_v25_manifest",
    "write_v26_manifest",
    "write_v27_manifest",
    "write_v28_manifest",
    "write_v29_manifest",
    "write_v30_manifest",
    "write_v31_manifest",
    "write_v32_manifest",
    "write_v33_manifest",
    "write_v34_manifest",
    "write_v35_manifest",
    "write_v36_manifest",
    "write_v37_manifest",
    "write_v38_manifest",
    "write_v39_manifest",
]


if __name__ == "__main__":
    raise SystemExit(main())
