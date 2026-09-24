"""Paired routed-vs-model skill-selection evaluation on the frozen 20-task set.

Adapted from .repair-work/skill-evaluation-2026-09-07/evaluator.py.  The only
intentional variable between the two arms is ``controls["skill_selection_mode"]``
("routed" vs "model"); task set (frozen cases.json, sha pinned), catalog,
prompts, model, probe limits (4 calls, 30 s/call, 0 retries, 2048 output
tokens) and scoring functions are identical, and the probe itself
(model_probe.py, byte-identical to the 2026-09-07 baseline probe) stops at the
first non-load tool request in both arms.  Task actions never execute.

The 2026-09-07 source freeze cannot be re-asserted: production code changed
intentionally (the skillSelection feature under test).  Current source and
catalog hashes are recorded for provenance instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent
# Pinned code snapshot: production minicode/ was being edited concurrently
# during this evaluation (mid-run NameError from a half-applied edit), so the
# package is evaluated from a byte-stable copy under snapshot/.  Data roots
# (skills, prompts, permissions, config) still come from ROOT.
SNAPSHOT = WORK / "snapshot"
assert (SNAPSHOT / "minicode" / "skill_routing_turn.py").exists()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WORK))
sys.path.insert(0, str(SNAPSHOT))

from minicode.capability_registry import build_tool_capability_snapshot
from minicode.config import load_runtime_config
from minicode.embeddings import create_openai_compatible_embedding_client
from minicode.intent_parser import IntentContext
from minicode.model_registry import build_provider_config
from minicode.permissions import PermissionManager
from minicode.prompt import build_system_prompt
from minicode.skill_router import build_skill_router, skill_load_policy_for_routing
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)
from minicode.skill_semantics import EmbeddingSemanticMatcher
from minicode.skills import discover_skills
from minicode.tooling import ToolContext
from minicode.tools import create_default_tool_registry
from model_probe import run_case

import minicode as _minicode_pkg

assert str(Path(_minicode_pkg.__file__).resolve()).startswith(str(SNAPSHOT.resolve())), (
    "minicode must resolve from the pinned snapshot, got " + str(_minicode_pkg.__file__)
)

CASE_SHA = "c5930a29a4b8289600df4f48eab3caeb8c4a0f15e0c76d9c0b6ed4e153fcb2cc"
EMBED_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
MODEL_BASE = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
MODES = ("routed", "model")
NEGATIVE_IDS = {"S18", "S19", "S20"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def source_hashes():
    paths = sorted(SNAPSHOT.joinpath("minicode").rglob("*.py"))
    return {"snapshot/" + str(p.relative_to(SNAPSHOT)): digest(p) for p in paths}


def coverage(case, names):
    names = set(names)
    return all(names.intersection(group) for group in case["required_skill_groups"])


def strict(case, names):
    return coverage(case, names) and set(names).issubset(case["allowed_skills"])


class Events:
    def __init__(self):
        self.items = []

    def emit(self, event_type, *, step=None, payload=None):
        self.items.append({"type": event_type, "step": step, "payload": dict(payload or {})})


def prompt_checks_for(decision, projected, catalog, prompt):
    checks = {"body_not_automatically_injected": all(Path(s["path"]).read_text() not in prompt for s in catalog)}
    if decision.prompt_mode == "model":
        checks["model_catalog_header_present"] = "Available skills:" in prompt
        checks["model_catalog_names_descriptions_present"] = all(
            f'- {s["qualified_name"]}: {s["description"]}' in prompt for s in projected
        )
        checks["model_usage_guide_present"] = "SKILL USAGE GUIDE:" in prompt
    elif decision.prompt_mode == "routed":
        checks["selected_name_description_present"] = all(f'- {s["qualified_name"]}: {s["description"]}' in prompt for s in projected)
        checks["routed_header_present"] = "Routed skills for intent:" in prompt
        checks["directory_projection_present"] = all(f'- {s.name}: {s.description}' in prompt for s in decision.routing.selected_directories)
    elif decision.prompt_mode == "inventory_names":
        checks["inventory_header_present"] = "names only):" in prompt
        checks["inventory_names_present"] = all(f'- {s["qualified_name"]}\n' in prompt for s in projected)
        checks["no_description_projection"] = all(f'- {s["qualified_name"]}: {s["description"]}' not in prompt for s in projected)
    elif decision.prompt_mode == "suppressed":
        checks["suppression_present"] = "Skill routing is suppressed" in prompt and not projected
    return checks


def evaluate(out_name, only_cases=None):
    assert digest(WORK / "cases.json") == CASE_SHA, "frozen task set changed"
    frozen_catalog = json.loads((WORK / "catalog-frozen-2026-09-07.json").read_text())
    cases = json.loads((WORK / "cases.json").read_text())
    assert len(cases) == 20
    if only_cases:
        cases = [c for c in cases if c["id"] in set(only_cases)]
        assert cases, "no matching cases"
    out = WORK / out_name
    out.mkdir(exist_ok=False)
    runtime = load_runtime_config(ROOT)
    assert runtime["model"] == "glm-5.3-flash"
    assert not runtime["intentSemanticClassifier"]["enabled"]
    assert not runtime["skillCandidateReranker"]["enabled"]
    provider = build_provider_config(runtime["model"], runtime)
    assert provider.base_url.rstrip("/") == MODEL_BASE
    assert not (ROOT / "CLAUDE.md").exists()
    assert not (Path.home() / ".claude/CLAUDE.md").exists()
    tools = create_default_tool_registry(str(ROOT), runtime=runtime, include_mcp=False)
    catalog = tools.get_skills()
    assert len(catalog) == 17
    # Catalog drift vs the 2026-09-07 freeze (disclosed, not asserted away).
    frozen_by_name = {s["qualified_name"]: s for s in frozen_catalog}
    drift = {}
    for entry in catalog:
        old = frozen_by_name.get(entry["qualified_name"])
        if old is None:
            drift[entry["qualified_name"]] = "added-since-freeze"
            continue
        changed = sorted(k for k in set(entry) | set(old) if entry.get(k) != old.get(k))
        if changed:
            drift[entry["qualified_name"]] = changed
    capabilities = build_tool_capability_snapshot(tools)
    permissions = PermissionManager(str(ROOT))
    live_client = create_openai_compatible_embedding_client(ROOT)
    assert live_client and live_client.endpoint == EMBED_URL
    assert live_client.model == "text-embedding-v3"
    old_cache = ROOT / ".mini-code/skill-embeddings.json"
    cache_path = out / "skill-embeddings.json"
    if old_cache.exists():
        shutil.copyfile(old_cache, cache_path)
    routed_router = build_skill_router(ROOT)
    routed_router._embedding = EmbeddingSemanticMatcher(live_client, cache_path, cache_anchor=out)
    plain_router = build_skill_router(ROOT)  # model arm never invokes it
    write(out / "settings.json", {
        "model": runtime["model"], "tool_profile": runtime["toolProfile"],
        "skill_selection_modes": list(MODES),
        "intent_semantic": False, "candidate_reranker": False,
        "embedding_routed_arm": True, "embedding_model": live_client.model,
        "embedding_cache_seed_sha256": digest(old_cache) if old_cache.exists() else None,
        "model_base": MODEL_BASE, "embedding_url": EMBED_URL,
        "configured_mcp_count": len(runtime.get("mcpServers", {})), "mcp_started": False,
        "memory_context": "", "tool_names": tools.list_all(),
        "catalog_drift_vs_2026_09_07": drift,
        "probe": "model_probe.py byte-identical to the 2026-09-07 baseline probe (sha256 c2630d0b); "
                 "stops at the first non-load tool request in both arms",
        "scope": "Paired fresh core-tool turns; only skill_selection_mode varies. No MCP, memories, or task execution.",
    })
    write(out / "source-hashes.json", source_hashes())
    skill_digests_at_start = {s["qualified_name"]: s["content_digest"] for s in catalog}
    rows = []
    try:
        for case in cases:
            ctx = case.get("context", {})
            context = IntentContext.from_messages(ctx.get("recent_turns", []), active_task=ctx.get("active_task", ""))
            for mode in MODES:
                events = Events()
                router = routed_router if mode == "routed" else plain_router
                started = time.perf_counter()
                decision = route_skill_turn(
                    SkillRoutingTurnRequest(prompt=case["text"], skills=catalog, registry=capabilities, context=context),
                    SkillRoutingTurnAdapters(router=router),
                    controls={
                        "event_sink": events,
                        "deadline_monotonic": time.monotonic() + 40,
                        "skill_selection_mode": mode,
                    },
                )
                routing = decision.routing
                selected = [s.qualified_name for s in (routing.selected_skills or routing.selected)]
                projected = list(decision.prompt_skills)
                prompt = build_system_prompt(str(ROOT), permissions.get_summary(), {
                    "skills": projected, "skill_routing": routing.to_dict(), "mcpServers": [],
                    "memory_context": "", "user_interaction_available": True,
                })
                (out / f"{case['id']}-{mode}-system-prompt.txt").write_text(prompt)
                checks = prompt_checks_for(decision, projected, catalog, prompt)
                policy = skill_load_policy_for_routing(routing)
                denied_names = set(policy.denied_qualified_names) if policy else set()
                catalog_scope = bool(getattr(policy, "allow_catalog_scope", False)) if policy else False
                if policy is None:
                    allowed_names = []
                elif catalog_scope:
                    # Catalog-scoped policy permits the whole turn catalog
                    # snapshot minus explicit denials (allowed_bindings stays
                    # empty by construction).
                    allowed_names = sorted(
                        s["qualified_name"] for s in catalog if s["qualified_name"] not in denied_names
                    )
                else:
                    allowed_names = sorted(
                        b.qualified_name for b in policy.allowed_bindings
                        if b.qualified_name not in denied_names
                    )
                visible_names = sorted(
                    s["qualified_name"] if isinstance(s, dict) else s.qualified_name for s in projected
                )
                # Deterministic admission: every required group has a member that
                # is both visible in the prompt and permitted by the load policy.
                admission = all(
                    any(member in allowed_names and member in visible_names for member in group)
                    for group in case["required_skill_groups"]
                )
                # Independent loader contract: actually load the first member of
                # each required group under this arm's policy; verify digest.
                local_loads = []
                for group in case["required_skill_groups"]:
                    name = group[0]
                    result = tools.execute(
                        "load_skill", {"name": name},
                        ToolContext(cwd=str(ROOT), _runtime=runtime, _skill_load_policy=policy),
                    )
                    entry = next(s for s in catalog if s["qualified_name"] == name)
                    body = result.output.split("\n\n", 1)[-1]
                    local_loads.append({
                        "name": name, "ok": result.ok,
                        "body_integrity": result.ok and hashlib.sha256(body.encode()).hexdigest() == entry["content_digest"],
                        "denial_without_body": not result.ok and Path(entry["path"]).read_text() not in result.output,
                    })
                probe = run_case(case, decision, prompt, tools, runtime, ROOT, out / f"{case['id']}-{mode}")
                loaded = probe["loaded_qualified_names"]
                handed = probe["body_handoff_qualified_names"]
                observable = bool(probe["model_calls"]) and all(call["completed"] for call in probe["model_calls"])
                timeouts = sum(
                    1 for call in probe["model_calls"]
                    if not call["completed"] and "timeout" in str(call.get("error_type", "")).lower()
                )
                errors = sum(1 for call in probe["model_calls"] if not call["completed"]) - timeouts
                input_tokens = sum(
                    (call.get("usage") or {}).get("inputTokens") or 0 for call in probe["model_calls"]
                )
                output_tokens = sum(
                    (call.get("usage") or {}).get("outputTokens") or 0 for call in probe["model_calls"]
                )
                negative = case["id"] in NEGATIVE_IDS
                row = {
                    "id": case["id"], "mode": mode,
                    "outcome": decision.outcome, "reason": decision.outcome_reason,
                    "prompt_mode": decision.prompt_mode,
                    "selected": selected,
                    "policy_reason": policy.reason_code if policy else None,
                    "policy_catalog_scope": catalog_scope,
                    "policy_denied_names": sorted(denied_names),
                    "policy_allowed_count": len(allowed_names),
                    "visible_count": len(visible_names),
                    "required_skill_admission": admission,
                    "local_loader_contract_checks": local_loads,
                    "retrieval_strict": (
                        strict(case, selected) and decision.outcome in case["expected_outcomes"]
                        if mode == "routed" else None
                    ),
                    "prompt_checks": checks, "prompt_integrity": all(checks.values()),
                    "routing_seconds": time.perf_counter() - started,
                    "model_probe": {k: probe.get(k) for k in (
                        "stop_reason", "loaded_qualified_names", "body_handoff_qualified_names",
                    )},
                    "model_calls": len(probe["model_calls"]),
                    "model_timeouts": timeouts, "model_errors": errors,
                    "model_elapsed_ms": probe["elapsed_ms"],
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "attempted_loads": [
                        {"name": load["requested_name"], "ok": load["ok"],
                         "qualified_name": load.get("qualified_name"),
                         "body_integrity_ok": load["body_integrity_ok"]}
                        for load in probe["load_calls"]
                    ],
                    "blocked_nonload_calls": [c.get("toolName") for c in probe["blocked_nonload_calls"]],
                    "model_observation_available": observable,
                    "model_load_strict": observable and strict(case, loaded),
                    "body_handoff_strict": observable and strict(case, handed) and set(loaded).issubset(case["allowed_skills"]),
                    "false_trigger": negative and bool(loaded),
                    "correct_abstain": negative and observable and not loaded,
                }
                rows.append(row)
                write(out / "results.json", rows)
                print(json.dumps({
                    "id": row["id"], "mode": mode, "prompt_mode": row["prompt_mode"],
                    "selected": selected, "loaded": loaded, "stop": probe["stop_reason"],
                    "load_strict": row["model_load_strict"], "handoff": row["body_handoff_strict"],
                }, ensure_ascii=False), flush=True)
    finally:
        tools.dispose()
        after = {s.qualified_name: s.content_digest for s in discover_skills(ROOT)}
        write(out / "catalog-stability.json", {
            "unchanged_during_run": after == skill_digests_at_start,
            "at_start": skill_digests_at_start, "at_end": after,
        })
    positives = [r for r in rows if r["id"] not in NEGATIVE_IDS]
    negatives = [r for r in rows if r["id"] in NEGATIVE_IDS]
    summary = {}
    for mode in MODES:
        arm = [r for r in rows if r["mode"] == mode]
        pos = [r for r in arm if r["id"] not in NEGATIVE_IDS]
        neg = [r for r in arm if r["id"] in NEGATIVE_IDS]
        summary[mode] = {
            "tasks": len(arm),
            "retrieval_strict": sum(bool(r["retrieval_strict"]) for r in arm),
            "required_skill_admission": sum(bool(r["required_skill_admission"]) for r in pos),
            "local_required_load_ok": sum(
                bool(r["local_loader_contract_checks"]) and all(c["ok"] and c["body_integrity"] for c in r["local_loader_contract_checks"])
                for r in pos
            ),
            "model_observation_available": sum(bool(r["model_observation_available"]) for r in arm),
            "model_load_strict_positives": sum(bool(r["model_load_strict"]) for r in pos),
            "body_handoff_strict_positives": sum(bool(r["body_handoff_strict"]) for r in pos),
            "negative_false_trigger": sum(bool(r["false_trigger"]) for r in neg),
            "negative_correct_abstain": sum(bool(r["correct_abstain"]) for r in neg),
            "model_calls": sum(r["model_calls"] for r in arm),
            "model_timeouts": sum(r["model_timeouts"] for r in arm),
            "model_errors": sum(r["model_errors"] for r in arm),
            "input_tokens": sum(r["input_tokens"] for r in arm),
            "output_tokens": sum(r["output_tokens"] for r in arm),
            "elapsed_ms": sum(r["model_elapsed_ms"] for r in arm),
            "prompt_integrity": sum(bool(r["prompt_integrity"]) for r in arm),
        }
    summary["positives_per_arm"] = len(positives) // len(MODES) if rows else 0
    summary["negatives_per_arm"] = len(negatives) // len(MODES) if rows else 0
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="live-paired")
    p.add_argument("--only", nargs="*", default=None, help="case ids for smoke runs")
    args = p.parse_args()
    evaluate(args.out, only_cases=args.only)
