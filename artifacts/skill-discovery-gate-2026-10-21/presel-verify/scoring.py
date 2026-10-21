"""Offline, label-aware scoring. Never import this module in the execution worker.

Selection/handoff, workflow order, and final task correctness are separate
outcomes. An absent measurement is never silently promoted to a success.
"""
from __future__ import annotations

import hashlib
from itertools import product
import json
from pathlib import Path
import posixpath
import re
from typing import Any, Mapping


SCHEMA = "skill-routing-three-arm-score/v1"
OBSERVATION_SCOPE = (
    "Successful SKILL.md file reads and exact complete frozen body matches in "
    "recorded tool outputs and model inputs; successful load_skill results. Fragmented, encoded, "
    "or unrecorded exposure is outside this measurement. Directory metadata "
    "alone is not body exposure."
)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_digest(value: Any) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _relative(value: Any, workspace: str) -> str:
    path = posixpath.normpath(str(value))
    root = posixpath.normpath(workspace)
    return path[len(root) + 1:] if path.startswith(root + "/") else path


def _skill_path(value: Any) -> bool:
    parts = posixpath.normpath(str(value)).split("/")
    return len(parts) >= 3 and parts[-1].lower() == "skill.md" and "skills" in parts[:-2]


def validate_expected(expected: Mapping[str, Any]) -> None:
    if type(expected.get("negative")) is not bool:
        raise ValueError("negative must be an explicit boolean")
    groups = expected.get("required_skill_groups", [])
    if not isinstance(groups, list) or any(
        not isinstance(group, list) or not group
        or any(not isinstance(name, str) or not name for name in group)
        for group in groups
    ):
        raise ValueError("required_skill_groups must contain nonempty name groups")
    if bool(groups) == expected["negative"]:
        raise ValueError("positives require skills; negatives cannot require skills")
    if expected["negative"] and expected.get("body_load_forbidden") is not True:
        raise ValueError("negative body-exposure metric needs body_load_forbidden=true")
    for field in ("allowed_extra_skills", "forbidden_skills"):
        if not isinstance(expected.get(field, []), list):
            raise ValueError(f"{field} must be a list")
    allowed = {name for group in groups for name in group} | set(expected.get("allowed_extra_skills", []))
    if allowed & set(expected.get("forbidden_skills", [])):
        raise ValueError("allowed and forbidden skills overlap")
    previous = set()
    for stage in expected.get("stages", []):
        name = stage.get("id")
        if not isinstance(name, str) or not name or name in previous:
            raise ValueError("stage IDs must be unique nonempty strings")
        if not set(stage.get("depends_on", [])) <= previous:
            raise ValueError("stage dependencies must reference earlier stage IDs")
        if not stage.get("skills") or not set(stage["skills"]) <= allowed:
            raise ValueError("stage skills must be nonempty and allowed")
        if not isinstance(stage.get("evidence_paths", []), list):
            raise ValueError("evidence_paths must be a list")
        previous.add(name)
    task = expected.get("task", {})
    if task.get("kind") != "final_json" or not task.get("checks"):
        raise ValueError("task requires final_json and nonempty checks")
    for check in task["checks"]:
        pointer = check.get("pointer")
        if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
            raise ValueError("invalid JSON pointer")
        if re.search(r"~(?![01])", pointer) or "equals" not in check:
            raise ValueError("invalid pointer escape or missing expected value")


def _pointer(document: Any, pointer: str) -> Any:
    value = document
    if not pointer:
        return value
    for raw in pointer[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", part):
                raise KeyError(part)
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise KeyError(part)
    return value


def _exact_json(left: Any, right: Any) -> bool:
    # Python considers True == 1. JSON answers must retain the declared types.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_exact_json(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_exact_json(a, b) for a, b in zip(left, right))
    return left == right


def _parse_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("non-JSON numeric constant")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def task_score(run: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    checks = []
    parse_error = None
    try:
        document = _parse_json(str(run.get("final", "")))
    except (ValueError, TypeError) as error:
        document = None
        parse_error = type(error).__name__
    for check in task["checks"]:
        try:
            actual = _pointer(document, check["pointer"])
            passed = parse_error is None and _exact_json(actual, check["equals"])
        except (KeyError, IndexError, TypeError):
            passed = False
        checks.append({"pointer": check["pointer"], "passed": passed})
    source_ok = None
    if isinstance(run.get("source_changed_paths"), list):
        source_ok = not bool(run["source_changed_paths"])
    source_required = bool(task.get("no_source_changes"))
    passed = bool(run.get("completed") and parse_error is None and all(x["passed"] for x in checks)
                  and (not source_required or source_ok is True))
    return {"passed": passed, "parse_error": parse_error, "checks": checks,
            "source_unchanged": source_ok, "source_observation_required": source_required,
            "label": "frozen JSON artifact checks, not general semantic correctness"}


def _requests(run: Mapping[str, Any], run_dir: Path | None):
    snapshots = {}
    errors = []
    if run_dir is None:
        return snapshots, ["request_artifacts_not_supplied"]
    base = run_dir.resolve()
    for call in run.get("model_calls", []):
        try:
            path = (base / call["input_file"]).resolve()
            if not path.is_relative_to(base):
                raise ValueError("outside run directory")
            content = json.loads(path.read_text(encoding="utf-8"))
            expected_hash = call.get("input_sha256", call.get("input_messages_sha256"))
            if not isinstance(content, list) or not expected_hash or _json_digest(content) != expected_hash:
                raise ValueError("request hash mismatch")
            if call["index"] in snapshots:
                raise ValueError("duplicate call index")
            snapshots[call["index"]] = content
        except (OSError, ValueError, KeyError, TypeError):
            errors.append(f"invalid_request_artifact:{call.get('index')}")
    if not run.get("model_calls"):
        errors.append("no_model_requests")
    return snapshots, errors


def _exposures(run: Mapping[str, Any], bodies: Mapping[str, str]) -> list[dict[str, Any]]:
    exposed = []
    for ordinal, event in enumerate(run.get("executions", []), 1):
        output = str(event.get("output", ""))
        arguments = event.get("input") or {}
        path = arguments.get("path", "") if isinstance(arguments, dict) else ""
        skill_read = event.get("name") == "read_file" and event.get("ok") and _skill_path(path)
        header = re.match(r"(?i)^(?:FILE|PATH):[ \t]*(.+?)(?:\r?\n|$)", output)
        if event.get("name") == "read_file" and event.get("ok") and header:
            skill_read = skill_read or _skill_path(header[1])
        matches = sorted(name for name, body in bodies.items() if body and body in output)
        # Check failed load results too: permission denial cannot justify a body leak.
        if skill_read or matches:
            exposed.append({"ordinal": event.get("ordinal", ordinal), "tool": event.get("name"),
                            "model_index": event.get("model_index"), "skill_file_read": bool(skill_read),
                            "body_matches": matches, "output_sha256": digest(output)})
    return exposed


def _wire_requests(run: Mapping[str, Any], run_dir: Path | None):
    """Independently verify the serialized HTTP payload, beyond adapter inputs."""
    completed_payloads = {}
    all_payloads = []
    errors = []
    if run.get("fake") is not False:
        return completed_payloads, all_payloads, errors
    if run_dir is None:
        return completed_payloads, all_payloads, ["wire_artifacts_not_supplied"]
    root = run_dir.resolve()
    chat_requests = [r for r in run.get("provider_requests", []) if r.get("kind") == "chat"]
    if not chat_requests:
        errors.append("no_chat_wire_requests")
    for request in chat_requests:
        try:
            path = (root / request["request_file"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("outside run directory")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or _json_digest(payload) != request.get("request_payload_sha256"):
                raise ValueError("wire payload hash mismatch")
            messages = payload.get("messages")
            if not isinstance(messages, list):
                raise ValueError("wire messages missing")
            all_payloads.append({"request_index": request.get("index"), "messages": messages})
            status = request.get("status")
            if (type(status) is int and 200 <= status < 300 and request.get("response_received")
                    and not request.get("error_type")):
                completed_payloads.setdefault(request.get("model_index"), []).append(messages)
        except (OSError, ValueError, KeyError, TypeError):
            errors.append(f"invalid_wire_artifact:{request.get('index')}")
    return completed_payloads, all_payloads, errors


def score(run: Mapping[str, Any], expected: Mapping[str, Any], *,
          run_dir: Path | str | None = None, bodies: Mapping[str, str] | None = None,
          corpus_complete: bool = False) -> dict[str, Any]:
    """Score one retained terminal run; all assigned runs stay in denominators."""
    validate_expected(expected)
    bodies = dict(bodies or {})
    directory = Path(run_dir) if run_dir is not None else None
    snapshots, evidence_errors = _requests(run, directory)
    wire_payloads, all_wire, wire_errors = _wire_requests(run, directory)
    evidence_errors.extend(wire_errors)
    calls = {c.get("index"): c for c in run.get("model_calls", [])}
    successful = [x for x in run.get("loads", []) if x.get("ok")]
    names = {str(x.get("qualified_name")) for x in successful}
    valid_loads = []
    load_observations = []
    for load in successful:
        body, name = load.get("body"), str(load.get("qualified_name"))
        valid = bool(isinstance(body, str) and body and load.get("body_integrity_ok")
                     and type(load.get("model_index")) is int
                     and digest(body) == load.get("body_sha256")
                     and name in bodies and bodies[name] == body)
        handoffs = []
        if valid:
            for index in load.get("confirmed_handoff_requests", []):
                call = calls.get(index, {})
                wire_confirmed = (run.get("fake") is not False or any(
                    any(body in str(message.get("content", "")) for message in messages)
                    for messages in wire_payloads.get(index, [])))
                if (index > load.get("model_index", -1) and call.get("completed") and wire_confirmed
                        and load["body_sha256"] in call.get("body_digests_present", [])
                        and any(body in str(message.get("content", "")) for message in snapshots.get(index, []))):
                    handoffs.append(index)
            valid_loads.append({**load, "verified_handoff_requests": sorted(set(handoffs))})
        load_observations.append({"name": name, "model_index": load.get("model_index"),
                                  "body_verified": valid, "verified_handoffs": sorted(set(handoffs))})
    groups = expected["required_skill_groups"]
    allowed = {name for group in groups for name in group} | set(expected.get("allowed_extra_skills", []))
    wrong = names - allowed
    forbidden = names & set(expected.get("forbidden_skills", []))
    load_hits = [any(x["qualified_name"] in group for x in valid_loads) for group in groups]
    handoff_hits = [any(x["qualified_name"] in group and x["verified_handoff_requests"] for x in valid_loads)
                    for group in groups]
    strict_load = bool(groups) and all(load_hits) and not wrong and not forbidden
    retrieval = bool(strict_load and all(handoff_hits))
    exposures = _exposures(run, bodies)
    ordinary = [x for x in exposures if x["tool"] != "load_skill"]

    stage_rows, candidate_sets = [], []
    for stage in expected.get("stages", []):
        candidates = []
        for load in valid_loads:
            if load["qualified_name"] not in stage["skills"] or not load["verified_handoff_requests"]:
                continue
            reads_by_path = {}
            for event in run.get("executions", []):
                args = event.get("input") or {}
                path = _relative(args.get("path", ""), str(run.get("workspace", ""))) if isinstance(args, dict) else ""
                index = event.get("model_index", -1)
                if (event.get("name") == "read_file" and event.get("ok")
                        and index > load["model_index"]
                        and any(handoff <= index for handoff in load["verified_handoff_requests"])):
                    reads_by_path.setdefault(path, []).append(index)
            paths = stage.get("evidence_paths", [])
            if all(path in reads_by_path for path in paths):
                read_index = max((min(reads_by_path[path]) for path in paths), default=min(load["verified_handoff_requests"]))
                candidates.append({"skill": load["qualified_name"], "load_index": load["model_index"], "read_index": read_index})
        stage_rows.append({"id": stage["id"], "depends_on": stage.get("depends_on", []),
                           "fresh_evidence": bool(candidates), "candidates": candidates})
        candidate_sets.append(candidates)
    valid_chain = None
    if candidate_sets and all(candidate_sets):
        positions = {s["id"]: i for i, s in enumerate(stage_rows)}
        for combination in product(*candidate_sets):
            if all(combination[i]["load_index"] > combination[positions[dependency]]["read_index"]
                   for i, stage in enumerate(stage_rows) for dependency in stage["depends_on"]):
                valid_chain = list(combination)
                break
    negative = expected["negative"]
    input_exposures = []
    if negative:
        for index, messages in snapshots.items():
            matches = sorted(name for name, body in bodies.items() if body and any(
                body in str(message.get("content", "")) for message in messages))
            if matches:
                input_exposures.append({"model_index": index, "body_matches": matches, "surface": "adapter"})
        for request in all_wire:
            matches = sorted(name for name, body in bodies.items() if body and any(
                body in str(message.get("content", "")) for message in request["messages"]))
            if matches:
                input_exposures.append({"request_index": request["request_index"], "body_matches": matches, "surface": "wire"})
    false_trigger = bool(successful or exposures or input_exposures) if negative else None
    expected_count = run.get("actual_catalog_count", len(bodies))
    observed = bool(corpus_complete and bodies and len(bodies) == expected_count and not evidence_errors)
    negative_clean = bool(run.get("completed") and observed and not false_trigger) if negative else None
    live = run.get("fake") is False
    primary = bool(live and observed and (negative_clean if negative else retrieval))
    task = task_score(run, expected["task"])
    return {"schema": SCHEMA, "attempted": 1, "negative": negative, "live": live,
            "primary_success": primary, "observation_complete": observed,
            "completed": bool(run.get("completed")), "stop_reason": run.get("stop_reason"),
            "timeout": bool(run.get("timeout")), "infrastructure_error": bool(run.get("infrastructure_error")),
            "strict_load": strict_load, "required_group_load_hits": load_hits,
            "required_group_handoff_hits": handoff_hits, "retrieval_and_handoff": retrieval,
            "successful_loader_names": sorted(names), "wrong_extra_loads": sorted(wrong),
            "forbidden_loads": sorted(forbidden), "loads": load_observations,
            "stages": stage_rows, "strict_chain": bool(retrieval and valid_chain), "valid_chain": valid_chain,
            "negative_false_trigger": false_trigger, "negative_clean": negative_clean,
            "body_exposures": exposures, "ordinary_body_exposures": ordinary,
            "input_body_exposures": input_exposures,
            "body_corpus_complete": bool(corpus_complete), "evidence_errors": evidence_errors,
            "observation_scope": OBSERVATION_SCOPE, "task": task, "task_pass": task["passed"]}
