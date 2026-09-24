"""Offline final review: non-executing and unparsed verification modes.

Run from the repository root:
  .venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/five-issue-fix/t12-final-review-probe.py
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


arguments = load("argument_fixture", "tests/test_reflection_objective_arguments.py")
episodes = load("episode_fixture", "tests/test_recovery_lesson.py")

from minicode.agent_reflection import ReflectionEngine
from minicode.reflection_evidence import call_objective


def summarize(result):
    return {
        "verifications": [[item.call_id, item.result] for item in result.task_evidence.verification],
        "claims": [[item.claim_type, item.epistemic_status] for item in result.structured_claims],
        "persistable": result.value_decision.accepted,
    }


def version_cases():
    result = {}
    for flag in ["-V", "--version"]:
        command = ["pytest", "tests/test_auth.py"]
        result[flag] = summarize(arguments._reflect(command, [*command, flag], "pytest 9.1.1"))
        result[flag]["executes_work"] = call_objective(f"pytest tests/test_auth.py {flag}").executes_work
    return result


def unparsed_shell_help():
    trace = episodes._fix_verified_trace()
    trace.insert(5, {
        "event_id": "e12", "type": "recovery",
        "action": "Configured pytest fixtures", "related_error_call_ids": ["c1"],
    })
    trace[6]["input"] = {"command": "bash", "args": ["-lc", "pytest tests/test_auth.py --help $FLAGS"]}
    trace[7]["output_summary"] = "usage: pytest [options] [file_or_dir]\n--fixtures show available fixtures"
    result = summarize(ReflectionEngine().reflect(episodes.TASK, trace))
    objective = call_objective("bash -lc 'pytest tests/test_auth.py --help $FLAGS'")
    result["ambiguous_selection"] = objective.ambiguous_selection
    result["executes_work"] = objective.executes_work
    return result


if __name__ == "__main__":
    print(json.dumps({"pytest_version": version_cases(), "unparsed_shell_help": unparsed_shell_help()}, indent=2))
