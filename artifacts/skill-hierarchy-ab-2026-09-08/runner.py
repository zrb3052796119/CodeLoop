"""Paired live runner for the hierarchical skill-discovery A/B experiment.

Arms (black-box, env-var configured; identical otherwise):
- A:  MINI_CODE_SKILL_SELECTION_MODE=model + MINI_CODE_SKILL_FLAT_CATALOG_CHARS=1000
      (budget floor; forces the hierarchical directory-card projection)
- B:  A + MINI_CODE_SKILL_INTENT_HINTS=1
- B2: B + MINI_CODE_SKILL_INTENT_PREFETCH=1

Each run is a fresh subprocess (``runner.py child``) against the pinned code
snapshot under ``snapshot/minicode/`` and a generated catalog workspace, with a
sandboxed HOME (symlink farm of the real ~/.mini-code minus its skills dir, so
user-scope skills cannot contaminate the synthetic catalog and synthetic loads
cannot pollute the real skill-version ledger).

Unlike the 2026-09-08 model-vs-routed probe, the child allows multi-step tool
use: browse_skill_directory / search_skills / load_skill (all read-only) are
dispatched for up to ``max_steps`` model calls (default 8), stopping after the
first batch containing a successful load. Any other tool request is recorded
as blocked (never executed); the model gets a synthetic error tool_result and
the run continues, mirroring production's defer-and-continue semantics, so a
mixed batch (e.g. browse + list_files) no longer ends the run early.

Subcommands:
    snapshot   -- pin minicode/ into snapshot/ (once per experiment, --refresh to redo)
    build      -- generate catalogs via catalog_gen.py
    validate   -- validate all catalogs against tasks.json
    run        -- run one scale/variant slice (arms x tasks)
    run-all    -- the full experiment matrix
    child      -- internal: execute one probe run (not for direct use)

Live runs use the configured glm-5.3-flash via ~/.mini-code/.env (values never
read or printed by this harness). ``--fake SCENARIO`` replaces the model with
a scripted adapter for offline smoke tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

WORK = Path(__file__).resolve().parent
ROOT = WORK.parents[1]
SNAPSHOT = WORK / "snapshot"
CATALOGS = WORK / "catalogs"
TASKS_JSON = WORK / "tasks.json"

SKILL_TOOLS = ("browse_skill_directory", "search_skills", "load_skill")
BASE_ARM_ENV = {
    "MINI_CODE_SKILL_SELECTION_MODE": "model",
    # config.py clamps this to >= 1000; 1000 is the smallest legal value and
    # is far below any multi-skill flat rendering, forcing hierarchical mode.
    "MINI_CODE_SKILL_FLAT_CATALOG_CHARS": "1000",
}
ARMS = {
    "A": {},
    "B": {"MINI_CODE_SKILL_INTENT_HINTS": "1"},
    "B2": {"MINI_CODE_SKILL_INTENT_HINTS": "1", "MINI_CODE_SKILL_INTENT_PREFETCH": "1"},
}
SCALES = ("S100", "S1000")
VARIANTS = ("base", "wrong-placement", "stale-summary")

FAKE_SCENARIOS = ("happy", "search-first", "negative", "nonload", "wander", "explicit", "oracle", "mixed")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_sha(value: object) -> str:
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _load_tasks() -> list[dict]:
    return json.loads(TASKS_JSON.read_text(encoding="utf-8"))["tasks"]


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

def cmd_snapshot(refresh: bool = False) -> None:
    target = SNAPSHOT / "minicode"
    if target.exists():
        if not refresh:
            print(f"snapshot already pinned at {target}; use --refresh to redo")
            return
        shutil.rmtree(target)
    shutil.copytree(
        ROOT / "minicode",
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    hashes = {}
    for path in sorted(target.rglob("*.py")):
        hashes[str(path.relative_to(SNAPSHOT))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    _write_json(SNAPSHOT / "source-hashes.json", {
        "pinned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": str(ROOT / "minicode"),
        "files": hashes,
    })
    print(f"pinned {len(hashes)} files into {target}")


# ---------------------------------------------------------------------------
# build / validate (delegate to catalog_gen)
# ---------------------------------------------------------------------------

def cmd_build(scale: str | None, variant: str | None, build_all: bool) -> None:
    sys.path.insert(0, str(WORK))
    import catalog_gen

    combos = (
        [(s, v) for s in SCALES for v in VARIANTS]
        if build_all
        else [(scale, variant)]
    )
    for combo_scale, combo_variant in combos:
        if not combo_scale or not combo_variant:
            raise SystemExit("build requires --scale and --variant, or --all")
        out = CATALOGS / f"{combo_scale}-{combo_variant}"
        if (out / ".mini-code" / "skills").exists():
            print(f"exists, skipping: {out}")
            continue
        tasks = _load_tasks()
        manifest = catalog_gen.build_catalog(combo_scale, combo_variant, tasks)
        catalog_gen.write_catalog(manifest, out)
        errors = catalog_gen.validate_manifest(manifest, tasks)
        if errors:
            raise SystemExit(f"validation failed for {out}: {errors}")
        print(f"built {out} ({manifest['stats']['total_skills']} skills)")


def cmd_validate() -> None:
    sys.path.insert(0, str(WORK))
    import catalog_gen

    tasks = _load_tasks()
    failed = False
    for manifest_path in sorted(CATALOGS.glob("*/catalog-manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        errors = catalog_gen.validate_manifest(manifest, tasks)
        label = f"{manifest['scale']}/{manifest['variant']}"
        if errors:
            failed = True
            for error in errors:
                print(f"ERROR {label}: {error}")
        else:
            stats = manifest["stats"]
            print(
                f"OK {label}: {stats['total_skills']} skills, "
                f"{stats['directories']} dirs, {stats['decoys']} decoys"
            )
    if failed:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# sandbox HOME
# ---------------------------------------------------------------------------

def sandbox_home() -> Path:
    """A HOME that shares credentials/config but isolates all mutable state.

    Only ``.env`` and ``settings.json`` are hardlinked (same inode, not
    copies, satisfying env_file.py's no-symlink/0600 checks and never
    duplicating secret bytes). ``skills`` is a real empty directory so the
    synthetic catalog is the only skill source, and every other state file
    (skill_versions ledger, sessions, history) simply does not exist here —
    nothing a probe run writes can reach the real ~/.mini-code.
    """
    real = Path.home() / ".mini-code"
    sandbox = WORK / "home"
    mini = sandbox / ".mini-code"
    skills = mini / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    os.chmod(mini, 0o700)
    for name in (".env", "settings.json"):
        source = real / name
        link = mini / name
        if link.exists():
            continue
        if source.is_file():
            try:
                os.link(source, link)
            except OSError:
                # Cross-device or unsupported filesystem: skip rather than
                # copy secret bytes; live mode will fail loudly on missing
                # credentials while fake-mode smoke tests still work.
                pass
    return sandbox


# ---------------------------------------------------------------------------
# run orchestration
# ---------------------------------------------------------------------------

def run_cell(
    *,
    task: dict,
    scale: str,
    variant: str,
    arm: str,
    out_root: Path,
    fake: str | None,
    max_steps: int,
    call_timeout: float,
    run_timeout: float,
) -> dict:
    workspace = (CATALOGS / f"{scale}-{variant}").resolve()
    if not (workspace / "catalog-manifest.json").exists():
        raise SystemExit(f"catalog missing: {workspace} (run build first)")
    run_dir = (out_root / f"{scale}-{variant}" / f"{task['id']}-{arm}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    fake_target = ""
    if fake:
        manifest = json.loads(
            (workspace / "catalog-manifest.json").read_text(encoding="utf-8")
        )
        target = (manifest.get("task_targets") or {}).get(task["id"]) or {}
        fake_target = target.get("qualified_name", "")
    config = {
        "task_id": task["id"],
        "prompt": task["prompt"],
        "scale": scale,
        "variant": variant,
        "arm": arm,
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "snapshot": str(SNAPSHOT.resolve()),
        "fake_scenario": fake,
        "fake_target": fake_target,
        "max_steps": max_steps,
        "call_timeout_seconds": call_timeout,
        "run_timeout_seconds": run_timeout,
    }
    config_path = run_dir / "child-config.json"
    _write_json(config_path, config)

    env = dict(os.environ)
    env.update(BASE_ARM_ENV)
    env.update(ARMS[arm])
    env["HOME"] = str(sandbox_home())
    env["PYTHONPATH"] = str(SNAPSHOT.resolve())
    env["MINI_CODE_LOG_LEVEL"] = "ERROR"
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, str(WORK / "runner.py"), "child",
             "--config", str(config_path)],
            cwd=str(workspace),
            env=env,
            capture_output=True,
            text=True,
            timeout=run_timeout + 60,
        )
    except subprocess.TimeoutExpired:
        result = {
            "task_id": task["id"], "scale": scale, "variant": variant,
            "arm": arm, "stop_reason": "run_timeout", "timeout": True,
            "steps": 0, "model_call_count": 0, "tool_calls": [],
            "load_calls": [], "model_calls": [], "input_tokens": 0,
            "output_tokens": 0,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
        }
        _write_json(run_dir / "run.json", result)
        return result
    (run_dir / "child-stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (run_dir / "child-stderr.txt").write_text(proc.stderr, encoding="utf-8")
    result_path = run_dir / "run.json"
    if proc.returncode != 0 or not result_path.exists():
        result = {
            "task_id": task["id"], "scale": scale, "variant": variant,
            "arm": arm, "stop_reason": "child_crashed", "timeout": False,
            "child_returncode": proc.returncode,
            "child_stderr_tail": proc.stderr[-2000:],
            "steps": 0, "model_call_count": 0, "tool_calls": [],
            "load_calls": [], "model_calls": [], "input_tokens": 0,
            "output_tokens": 0,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
        }
        _write_json(result_path, result)
        return result
    return json.loads(result_path.read_text(encoding="utf-8"))


def cmd_run(
    *,
    scales: list[str],
    variants: list[str],
    arms: list[str],
    task_ids: list[str] | None,
    fake: str | None,
    out: Path,
    max_steps: int,
    call_timeout: float,
    run_timeout: float,
) -> None:
    tasks = _load_tasks()
    if task_ids:
        wanted = set(task_ids)
        tasks = [t for t in tasks if t["id"] in wanted]
        if not tasks:
            raise SystemExit("no matching tasks")
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.jsonl"
    summary = {"runs": 0, "timeouts": 0, "crashes": 0}
    for scale in scales:
        for variant in variants:
            for task in tasks:
                for arm in arms:
                    result = run_cell(
                        task=task, scale=scale, variant=variant, arm=arm,
                        out_root=out, fake=fake, max_steps=max_steps,
                        call_timeout=call_timeout, run_timeout=run_timeout,
                    )
                    with results_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({
                            "task_id": result["task_id"], "scale": scale,
                            "variant": variant, "arm": arm,
                            "stop_reason": result["stop_reason"],
                            "timeout": result.get("timeout", False),
                            "steps": result.get("steps", 0),
                            "loaded": [
                                l.get("qualified_name")
                                for l in result.get("load_calls", [])
                                if l.get("ok")
                            ],
                            "input_tokens": result.get("input_tokens", 0),
                            "output_tokens": result.get("output_tokens", 0),
                            "elapsed_ms": result.get("elapsed_ms", 0),
                        }, ensure_ascii=False) + "\n")
                    summary["runs"] += 1
                    summary["timeouts"] += int(bool(result.get("timeout")))
                    summary["crashes"] += int(
                        result["stop_reason"] == "child_crashed"
                    )
                    print(json.dumps({
                        "task": task["id"], "scale": scale, "variant": variant,
                        "arm": arm, "stop": result["stop_reason"],
                        "steps": result.get("steps", 0),
                        "loaded": [
                            l.get("qualified_name")
                            for l in result.get("load_calls", []) if l.get("ok")
                        ],
                    }, ensure_ascii=False), flush=True)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


# ---------------------------------------------------------------------------
# child: one probe run
# ---------------------------------------------------------------------------

class ScriptedSkillModel:
    """Deterministic offline stand-in for the live adapter (smoke tests).

    Behaviors are driven only by the conversation content (system prompt
    directory cards, tool results), so the full plumbing — config, routing,
    prompt build, registry, tool dispatch, logging — is genuinely exercised.
    """

    def __init__(self, scenario: str, explicit_name: str = "") -> None:
        self.scenario = scenario
        self.explicit_name = explicit_name
        self.counter = 0

    def _step(self, messages: list[dict]) -> "object":
        from minicode.types import AgentStep, ModelUsage

        self.counter += 1
        system = next(
            (m["content"] for m in messages if m["role"] == "system"), ""
        )
        last_tool = next(
            (m for m in reversed(messages) if m["role"] == "tool_result"), None
        )
        usage = ModelUsage(
            input_tokens=max(1, sum(len(str(m.get("content", ""))) for m in messages) // 4),
            output_tokens=8,
            source="estimated",
        )

        def calls(name: str, args: dict) -> AgentStep:
            return AgentStep(
                type="tool_calls",
                calls=[{
                    "id": f"fake-{self.counter}",
                    "toolName": name,
                    "input": args,
                }],
                usage=usage,
            )

        def final() -> AgentStep:
            return AgentStep(type="assistant", content="<final>Done.", usage=usage)

        import re

        cards = re.findall(
            r"^- ([a-z0-9._-]+): .* \(\d+ skills, ref [0-9a-f]+\)$",
            system,
            flags=re.MULTILINE,
        )
        seen = []
        if last_tool is not None:
            seen = re.findall(r"^- ([a-z0-9._/-]+)[: ]", str(last_tool["content"]), flags=re.MULTILINE)
            last_name = last_tool.get("toolName", "")
        else:
            last_name = ""

        if self.scenario == "negative":
            return final()
        if self.scenario == "mixed":
            # Step 1 batches a skill-discovery call with a non-skill call;
            # under defer-and-continue the run must reach step 2 and load.
            if not last_tool:
                return AgentStep(
                    type="tool_calls",
                    calls=[
                        {
                            "id": f"fake-{self.counter}-a",
                            "toolName": "browse_skill_directory",
                            "input": {"directory_names": [cards[0]] if cards else [""]},
                        },
                        {
                            "id": f"fake-{self.counter}-b",
                            "toolName": "list_files",
                            "input": {"path": "."},
                        },
                    ],
                    usage=usage,
                )
            browse_result = next(
                (m for m in reversed(messages)
                 if m["role"] == "tool_result"
                 and m.get("toolName") == "browse_skill_directory"),
                None,
            )
            if browse_result is not None:
                names = re.findall(
                    r"^- ([a-z0-9._/-]+)[: ]",
                    str(browse_result["content"]),
                    flags=re.MULTILINE,
                )
                if names:
                    return calls("load_skill", {"name": names[0]})
            return final()
        if self.scenario == "nonload":
            if not last_tool:
                return calls("list_files", {"path": "."})
            return final()
        if self.scenario == "explicit":
            if not last_tool:
                return calls("load_skill", {"name": self.explicit_name})
            return final()
        if self.scenario == "oracle":
            # Smoke-test the fully-correct path: browse the target's directory,
            # then load the target by its variant-adjusted qualified name.
            directory = self.explicit_name.rpartition("/")[0]
            if not last_tool:
                return calls("browse_skill_directory", {"directory_names": [directory]})
            if last_name == "browse_skill_directory":
                return calls("load_skill", {"name": self.explicit_name})
            return final()

        if last_name == "load_skill":
            return final()
        if self.scenario == "happy":
            if not last_tool:
                return calls("browse_skill_directory",
                             {"directory_names": [cards[0]] if cards else [""]})
            if last_name == "browse_skill_directory" and seen:
                return calls("load_skill", {"name": seen[0]})
            return final()
        if self.scenario == "search-first":
            if not last_tool:
                return calls("search_skills", {"query": "retry backoff"})
            if last_name == "search_skills" and seen:
                return calls("load_skill", {"name": seen[0]})
            return final()
        if self.scenario == "wander":
            if not last_tool:
                pick = cards[1] if len(cards) > 1 else (cards[0] if cards else "")
                return calls("browse_skill_directory", {"directory_names": [pick]})
            if last_name == "browse_skill_directory" and self.counter == 2:
                return calls("browse_skill_directory",
                             {"directory_names": [cards[0]] if cards else [""]})
            if last_name == "browse_skill_directory" and seen:
                return calls("load_skill", {"name": seen[0]})
            return final()
        raise ValueError(f"unknown fake scenario {self.scenario!r}")

    def next(self, messages, on_stream_chunk=None, **_kwargs):
        return self._step(messages)


def _parse_seen_entries(output: str) -> tuple[list[str], list[str]]:
    """Extract skill qualified names and directories from a tool result."""
    import re

    qnames: list[str] = []
    dirs: list[str] = []
    for line in output.splitlines():
        header = re.match(r"^## (.+?) \(\d+ skills\)$", line)
        if header:
            dirs.append("" if header.group(1) == "(root)" else header.group(1))
            continue
        entry = re.match(r"^- ([A-Za-z0-9._/-]+)(?: \[([^\]]+)\])?: ", line)
        if entry:
            qnames.append(entry.group(1))
            if entry.group(2) is not None:
                dirs.append("" if entry.group(2) == "root" else entry.group(2))
    return qnames, dirs


def child_main(config_path: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    snapshot = Path(config["snapshot"])
    sys.path.insert(0, str(snapshot))
    import minicode

    resolved_pkg = Path(minicode.__file__).resolve()
    if not str(resolved_pkg).startswith(str(snapshot.resolve())):
        raise RuntimeError(f"minicode must resolve from the snapshot, got {resolved_pkg}")

    from minicode.capability_registry import build_tool_capability_snapshot
    from minicode.config import load_runtime_config
    from minicode.permissions import PermissionManager
    from minicode.prompt import build_system_prompt
    from minicode.skill_router import (
        build_skill_router,
        skill_load_policy_for_routing,
    )
    from minicode.skill_routing_turn import (
        SkillRoutingTurnAdapters,
        SkillRoutingTurnRequest,
        route_skill_turn,
    )
    from minicode.skills import resolve_skill_catalog_binding
    from minicode.tooling import ToolContext
    from minicode.tools import create_default_tool_registry
    from minicode.turn_cancellation import TurnCancellationToken

    workspace = Path(config["workspace"]).resolve()
    run_dir = Path(config["run_dir"]).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    max_steps = int(config.get("max_steps", 8))
    call_timeout = float(config.get("call_timeout_seconds", 60.0))
    run_deadline = time.monotonic() + float(config.get("run_timeout_seconds", 480.0))
    fake_scenario = config.get("fake_scenario")

    runtime = load_runtime_config(str(workspace))
    selection = runtime.get("skillSelection", {})
    tools = create_default_tool_registry(
        str(workspace), runtime=runtime,
        include_mcp=False, include_user_interaction=False,
    )
    result: dict = {
        "task_id": config["task_id"],
        "scale": config["scale"],
        "variant": config["variant"],
        "arm": config["arm"],
        "workspace": str(workspace),
        "fake": bool(fake_scenario),
        "fake_scenario": fake_scenario,
        "env": {k: os.environ.get(k) for k in
                sorted(set(BASE_ARM_ENV) | set(ARMS["B2"]))},
        "model": runtime.get("model"),
        "skill_selection": selection,
        "max_steps": max_steps,
        "timeout": False,
        "tool_calls": [],
        "load_calls": [],
        "model_calls": [],
        "blocked_nonload_calls": [],
        "stop_reason": "step_budget_exhausted",
        "limitations": [
            "Only browse_skill_directory/search_skills/load_skill are dispatched; "
            "non-skill tool requests are recorded as blocked (never executed) and "
            "answered with a synthetic error tool_result; the run continues so "
            "the model can finish skill discovery on a later step.",
            "The loop stops after the first batch containing a successful load; "
            "second-stage loads of multi-stage tasks are out of window.",
            "A batch with zero skill-tool calls stops the run as "
            "no_skill_discovery (model abandoned discovery).",
            "Negative-task false-trigger means any successful load in-window.",
            "Timeout runs are flagged and must be excluded from correctness rates.",
        ],
    }
    started = time.perf_counter()
    try:
        if selection.get("mode") != "model":
            raise RuntimeError(
                f"skillSelection.mode resolved to {selection.get('mode')!r}, "
                "expected 'model' — check arm env configuration"
            )
        catalog = tools.get_skills()
        result["catalog_size"] = len(catalog)
        result["catalog_sha256"] = _json_sha(catalog)
        events: list[dict] = []

        class _Events:
            def emit(self, event_type, *, step=None, payload=None):
                events.append({"type": event_type, "step": step})

        decision = route_skill_turn(
            SkillRoutingTurnRequest(
                prompt=config["prompt"],
                skills=catalog,
                registry=build_tool_capability_snapshot(tools),
            ),
            SkillRoutingTurnAdapters(router=build_skill_router(str(workspace))),
            controls={
                "event_sink": _Events(),
                "deadline_monotonic": run_deadline,
                "skill_selection_mode": str(selection.get("mode", "routed")),
                "skill_flat_catalog_chars": int(
                    selection.get("flatCatalogCharBudget", 8000) or 8000
                ),
                "skill_page_chars": int(
                    selection.get("skillPageCharBudget", 4000) or 4000
                ),
                # Arm B wiring, mirroring agent_runtime.py: config.py reads
                # MINI_CODE_SKILL_INTENT_HINTS / _PREFETCH into skillSelection;
                # forward them so route_skill_turn attaches intent_hints.
                "skill_intent_hints": bool(selection.get("intentHints", False)),
                "skill_intent_prefetch": bool(
                    selection.get("intentPrefetch", False)
                ),
            },
        )
        result["prompt_mode"] = decision.prompt_mode
        result["routing_outcome"] = decision.outcome
        result["routing_outcome_reason"] = decision.outcome_reason
        permissions = PermissionManager(str(workspace), prompt=None)
        system_prompt = build_system_prompt(
            str(workspace),
            permissions.get_summary(),
            {
                "skills": list(decision.prompt_skills),
                "skill_routing": decision.routing.to_dict(),
                "mcpServers": [],
                "memory_context": "",
                "user_interaction_available": False,
            },
        )
        (run_dir / "system-prompt.txt").write_text(system_prompt, encoding="utf-8")
        result["system_prompt_sha256"] = _sha(system_prompt)
        result["system_prompt_chars"] = len(system_prompt)
        policy = skill_load_policy_for_routing(decision.routing)
        result["policy"] = {
            "reason_code": getattr(policy, "reason_code", None),
            "catalog_scope": bool(getattr(policy, "allow_catalog_scope", False)),
            "denied": sorted(getattr(policy, "denied_qualified_names", []) or []),
        }

        if fake_scenario:
            model = ScriptedSkillModel(
                fake_scenario,
                explicit_name=config.get("fake_target")
                or os.environ.get("FAKE_LOAD_NAME", ""),
            )
            adapter_name = "ScriptedSkillModel"
        else:
            from minicode.model_registry import create_model_adapter

            local_runtime = dict(runtime)
            local_runtime.update(
                modelTimeoutSeconds=call_timeout,
                modelMaxRetries=0,
                maxOutputTokens=2048,
            )
            model = create_model_adapter(
                runtime["model"], tools, runtime=local_runtime
            )
            adapter_name = type(model).__name__
            if adapter_name != "OpenAIModelAdapter":
                raise TypeError(
                    f"live runs require the OpenAI-compatible adapter, got {adapter_name}"
                )
        result["adapter"] = adapter_name

        from minicode.model_call_control import call_model_next
        from minicode.run_events import project_model_usage

        token = TurnCancellationToken("turn_" + hashlib.sha256(
            f"{config['task_id']}:{config['arm']}".encode()
        ).hexdigest()[:32])
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": config["prompt"]},
        ]
        loaded_ok_this_batch = False
        for step_index in range(1, max_steps + 1):
            if time.monotonic() > run_deadline:
                result["stop_reason"] = "run_timeout"
                result["timeout"] = True
                break
            call_record: dict = {
                "index": step_index, "completed": False,
                "input_messages_sha256": _json_sha(messages),
            }
            result["model_calls"].append(call_record)
            call_started = time.perf_counter()
            try:
                if fake_scenario:
                    step = model.next(messages)
                else:
                    step = call_model_next(
                        model,
                        messages,
                        optional_kwargs={
                            "max_retries_override": 0,
                            "max_output_tokens_override": 2048,
                        },
                        cancellation_token=token,
                        deadline_monotonic=min(
                            time.monotonic() + call_timeout, run_deadline
                        ),
                    )
            except Exception as error:  # noqa: BLE001 - recorded, not raised
                call_record["error_type"] = type(error).__name__
                call_record["elapsed_ms"] = (time.perf_counter() - call_started) * 1000
                name = type(error).__name__.lower()
                if "timeout" in name or "deadline" in name or "cancel" in name:
                    result["timeout"] = True
                result["stop_reason"] = "model_error"
                break
            call_record.update(
                completed=True,
                elapsed_ms=(time.perf_counter() - call_started) * 1000,
                type=step.type,
                content_chars=len(step.content or ""),
                tool_call_names=[c["toolName"] for c in step.calls],
                usage=project_model_usage(step.usage),
            )
            if not step.calls:
                result["stop_reason"] = "assistant_response"
                break
            loaded_ok_this_batch = False
            batch_skill_calls = 0
            for position, call in enumerate(step.calls):
                name = call["toolName"]
                if name not in SKILL_TOOLS:
                    # Non-skill tools are never executed (nothing mutates the
                    # workspace). Mirror production's defer-and-continue
                    # semantics: return a synthetic error tool_result and let
                    # the loop continue, so a mixed batch (e.g. browse +
                    # list_files) gets a second turn to finish discovery
                    # instead of ending the run mid-navigation.
                    result["blocked_nonload_calls"].append(
                        {"toolName": name, "input": call.get("input"),
                         "batch_index": position}
                    )
                    messages.extend([
                        {
                            "role": "assistant_tool_call",
                            "toolUseId": call["id"],
                            "toolName": name,
                            "input": call.get("input"),
                        },
                        {
                            "role": "tool_result",
                            "toolUseId": call["id"],
                            "toolName": name,
                            "content": (
                                "error[blocked_in_skill_discovery_window]: "
                                "not executed; finish skill discovery "
                                "(browse/search/load) first"
                            ),
                            "isError": True,
                        },
                    ])
                    continue
                batch_skill_calls += 1
                context = ToolContext(
                    cwd=str(workspace),
                    _runtime=runtime,
                    _step=step_index,
                    _skill_load_policy=policy,
                    _cancellation_token=token,
                )
                exec_started = time.perf_counter()
                try:
                    outcome = tools.execute(name, call.get("input") or {}, context)
                except Exception as error:  # noqa: BLE001 - validator errors etc.
                    outcome = None
                    tool_record = {
                        "step": step_index, "tool_call_id": call["id"],
                        "name": name, "args": call.get("input"),
                        "ok": False, "error_type": type(error).__name__,
                        "elapsed_ms": (time.perf_counter() - exec_started) * 1000,
                    }
                else:
                    seen_qnames, seen_dirs = _parse_seen_entries(outcome.output)
                    tool_record = {
                        "step": step_index, "tool_call_id": call["id"],
                        "name": name, "args": call.get("input"),
                        "ok": outcome.ok,
                        "output_sha256": _sha(outcome.output),
                        "output_chars": len(outcome.output),
                        "seen_qualified_names": seen_qnames,
                        "seen_directories": sorted(set(seen_dirs)),
                        "elapsed_ms": (time.perf_counter() - exec_started) * 1000,
                    }
                result["tool_calls"].append(tool_record)
                output_text = "" if outcome is None else outcome.output
                ok = bool(tool_record["ok"])
                if name == "load_skill":
                    requested = (
                        (call.get("input") or {}).get("name", "")
                        if isinstance(call.get("input"), dict) else ""
                    )
                    binding = resolve_skill_catalog_binding(requested, catalog)
                    body = (
                        output_text.split("\n\n", 1)[1]
                        if ok and "\n\n" in output_text else None
                    )
                    expected = (
                        (binding.get("content_digest") if isinstance(binding, dict)
                         else getattr(binding, "content_digest", None))
                        if binding is not None else None
                    )
                    result["load_calls"].append({
                        "step": step_index,
                        "tool_call_id": call["id"],
                        "requested_name": requested,
                        "qualified_name": (
                            (binding.get("qualified_name") if isinstance(binding, dict)
                             else getattr(binding, "qualified_name", None))
                            if binding is not None else None
                        ),
                        "ok": ok,
                        "body_chars": len(body) if body is not None else 0,
                        "body_integrity_ok": bool(
                            ok and body is not None
                            and _sha(body) == expected
                        ),
                        # Handoff confirmation needs a later completed call;
                        # with stop-after-first-load this stays False and the
                        # body-delivery fact is the tool_result in messages.
                        "confirmed_handoff": False,
                    })
                    loaded_ok_this_batch = loaded_ok_this_batch or ok
                messages.extend([
                    {
                        "role": "assistant_tool_call",
                        "toolUseId": call["id"],
                        "toolName": name,
                        "input": call.get("input"),
                    },
                    {
                        "role": "tool_result",
                        "toolUseId": call["id"],
                        "toolName": name,
                        "content": output_text,
                        "isError": not ok,
                    },
                ])
            if batch_skill_calls == 0:
                # The model abandoned skill discovery entirely (only non-skill
                # calls). Stop — continuing would just spin on blocked calls.
                result["stop_reason"] = "no_skill_discovery"
                break
            if loaded_ok_this_batch:
                result["stop_reason"] = "skill_loaded"
                break
        result["steps"] = len(result["model_calls"])
        result["model_call_count"] = sum(
            1 for c in result["model_calls"] if c.get("completed")
        )
        result["input_tokens"] = sum(
            (c.get("usage") or {}).get("inputTokens") or 0
            for c in result["model_calls"]
        )
        result["output_tokens"] = sum(
            (c.get("usage") or {}).get("outputTokens") or 0
            for c in result["model_calls"]
        )
        result["routing_events"] = events
        _write_json(run_dir / "messages-at-stop.json", messages)
    except Exception as error:  # noqa: BLE001 - one bad cell must not kill a sweep
        result["stop_reason"] = "child_error"
        result["error_type"] = type(error).__name__
        result["error_message_sha256"] = _sha(str(error))
    finally:
        try:
            tools.dispose()
        except Exception:  # noqa: BLE001
            pass
    result["elapsed_ms"] = (time.perf_counter() - started) * 1000
    _write_json(run_dir / "run.json", result)
    print(json.dumps({
        "task": result["task_id"], "arm": result["arm"],
        "prompt_mode": result.get("prompt_mode"),
        "stop": result["stop_reason"], "steps": result.get("steps", 0),
    }, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("snapshot")
    p.add_argument("--refresh", action="store_true")

    p = sub.add_parser("build")
    p.add_argument("--scale", choices=SCALES)
    p.add_argument("--variant", choices=VARIANTS)
    p.add_argument("--all", action="store_true")

    sub.add_parser("validate")

    def run_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--scales", nargs="*", default=list(SCALES), choices=SCALES)
        p.add_argument("--variants", nargs="*", default=list(VARIANTS), choices=VARIANTS)
        p.add_argument("--arms", nargs="*", default=list(ARMS), choices=list(ARMS))
        p.add_argument("--tasks", nargs="*", default=None)
        p.add_argument("--fake", choices=FAKE_SCENARIOS, default=None,
                       help="offline smoke: scripted model instead of glm-5.3-flash")
        p.add_argument("--out", type=Path, default=WORK / "live")
        p.add_argument("--max-steps", type=int, default=8)
        p.add_argument("--call-timeout", type=float, default=60.0)
        p.add_argument("--run-timeout", type=float, default=480.0)

    run_args(sub.add_parser("run"))
    run_args(sub.add_parser("run-all"))

    p = sub.add_parser("child")
    p.add_argument("--config", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "snapshot":
        cmd_snapshot(refresh=args.refresh)
    elif args.command == "build":
        cmd_build(args.scale, args.variant, args.all)
    elif args.command == "validate":
        cmd_validate()
    elif args.command in {"run", "run-all"}:
        cmd_run(
            scales=args.scales, variants=args.variants, arms=args.arms,
            task_ids=args.tasks, fake=args.fake, out=args.out,
            max_steps=args.max_steps, call_timeout=args.call_timeout,
            run_timeout=args.run_timeout,
        )
    elif args.command == "child":
        return child_main(args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
