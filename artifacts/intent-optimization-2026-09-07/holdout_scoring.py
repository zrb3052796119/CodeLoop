"""Frozen author interpretation of the new40 action-alternative contract.

This module never imports a parser or calls a model. It delegates all original
checks to the sealed old50 scorer, changing only explicitly listed alternative
action labels. The new40 baseline and candidate must use this identical module.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


WORK = Path(__file__).resolve().parent
ROOT = WORK.parents[1]
CONTRACT_VERSION = "new40-author-action-alternatives-v1"
HOLDOUT_SHA256 = "a99d48fa2c459fb04653855d9c7d3721100ae84c212b7537cf9c7c6cc7af3d3e"
AUTHORSHIP_SHA256 = "cb67239489d4c65eb2dd49bc84defdb0386e6d0628e40ca58367b94bd580d665"
ORIGINAL_SCORER_SHA256 = "d7239de029e47782e4fc2d220964c6640e65b88d09c9777f3469ac4221a680f0"

# Each tuple is one required task expressed through interchangeable labels.
# Different tuples for a case are conjunctive. All unlisted action labels keep
# the original scorer's unconditional requirement. No group is inferred from
# model output, label order, or the number of accepted primary intents.
ACTION_ALTERNATIVE_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "N01": (("explain", "question"),),
    "N02": (("explain", "question", "review"),),
    "N03": (("explain", "question"),),
    "N05": (("explain", "question"),),
    "N06": (("explain", "question", "review"),),
    "N10": (("explain", "question"),),
    "N12": (("code", "debug"),),
    "N13": (("code", "refactor"),),
    "N17": (("refactor", "code"),),
    "N20": (("system", "code"),),
    "N22": (("explain", "question"),),
    "N28": (("explain", "question"),),
    # search remains independently mandatory through required_affirmed.
    "N29": (("search", "question"),),
    "N32": (("explain", "question"),),
    # Search is an independent required task; only explanation labels vary.
    "N34": (("explain", "question"),),
    # Both implementation restructuring AND its explanation remain required.
    "N36": (("refactor", "code"), ("explain", "question")),
}


class _Label(Protocol):
    value: str


class _Operation(Protocol):
    intent_type: _Label
    action_type: _Label


class _ParsedIntentLike(Protocol):
    intent_type: _Label
    affirmed_operations: Sequence[_Operation]
    negated_operations: Sequence[_Operation]
    entities: Mapping[str, Sequence[str]]

    def is_read_only(self) -> bool: ...


def _verified_bytes(path: Path, expected_hash: str) -> bytes:
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise RuntimeError(f"frozen scoring dependency changed: {path.name}")
    return content


_CASES = {
    case["id"]: case
    for case in json.loads(_verified_bytes(WORK / "holdout.json", HOLDOUT_SHA256))
}
_verified_bytes(WORK / "holdout-authorship.md", AUTHORSHIP_SHA256)
_ORIGINAL_PATH = ROOT / "artifacts/intent-evaluation-2026-09-07/run_evaluation.py"
_verified_bytes(_ORIGINAL_PATH, ORIGINAL_SCORER_SHA256)
_spec = importlib.util.spec_from_file_location("_sealed_original_intent_scorer", _ORIGINAL_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError("original scorer cannot be loaded")
_module = importlib.util.module_from_spec(_spec)
_original_sys_path = list(sys.path)
try:
    # The original module adjusts sys.path at import time. Restore it so this
    # scorer cannot affect source-root isolation in the evaluation runner.
    _spec.loader.exec_module(_module)
finally:
    sys.path[:] = _original_sys_path
_original_score = _module.score

for _case_id, _groups in ACTION_ALTERNATIVE_GROUPS.items():
    _labels = [label for group in _groups for label in group]
    if len(_labels) != len(set(_labels)):
        raise RuntimeError("action alternatives overlap")
    if not set(_labels) <= set(_CASES[_case_id]["expected_actions"]):
        raise RuntimeError("action alternative has no original action label")


def score(
    case: Mapping[str, Any], intent: _ParsedIntentLike
) -> tuple[bool, list[str]]:
    """Score a ParsedIntent using the unchanged labels and author task groups."""
    case_id = case.get("id")
    if case_id not in _CASES or dict(case) != _CASES[case_id]:
        raise ValueError("case does not exactly match the frozen new40 dataset")
    groups = ACTION_ALTERNATIVE_GROUPS.get(case_id, ())
    if not groups:
        return _original_score(case, intent)

    present = {op.intent_type.value for op in intent.affirmed_operations}
    alternatives = {label for group in groups for label in group}
    effective_case = dict(case)
    effective_case["expected_actions"] = {
        label: actions
        for label, actions in case["expected_actions"].items()
        if label not in alternatives or label in present
    }
    primary_ok, issues = _original_score(effective_case, intent)
    for group in groups:
        if not any(
            op.intent_type.value in group
            and op.action_type.value in case["expected_actions"][op.intent_type.value]
            for op in intent.affirmed_operations
        ):
            issues.append(f"missing action alternative group: {' | '.join(group)}")
    return primary_ok, issues


@dataclass(frozen=True)
class _RecordedLabel:
    value: str


@dataclass(frozen=True)
class _RecordedOperation:
    intent_type: _RecordedLabel
    action_type: _RecordedLabel
    polarity: str


@dataclass(frozen=True)
class _RecordedIntent:
    intent_type: _RecordedLabel
    affirmed_operations: tuple[_RecordedOperation, ...]
    negated_operations: tuple[_RecordedOperation, ...]
    entities: Mapping[str, Sequence[str]]
    read_only: bool

    def is_read_only(self) -> bool:
        return self.read_only


def score_recorded(
    case: Mapping[str, Any],
    actual: Mapping[str, Any] | None,
    *,
    read_only: bool | None = None,
) -> tuple[bool, list[str]]:
    """Rescore saved to_detailed_dict data without a parser or model call.

    Supply the separately recorded read_only boolean when available. With only
    actual, reconstruct the unchanged is_read_only projection from its saved
    operations and scalar action; no intent inference or relabeling occurs.
    A recorded parser failure remains a failed result.
    """
    if actual is None:
        return False, ["recorded parser result unavailable"]
    if not isinstance(actual, Mapping):
        raise TypeError("recorded actual must be an object or null")
    if read_only is not None and not isinstance(read_only, bool):
        raise TypeError("recorded read_only must be boolean")
    for field in ("intent_type", "action_type"):
        if not isinstance(actual.get(field), str):
            raise ValueError(f"recorded actual is missing {field}")
    raw_operations = actual.get("operations")
    if not isinstance(raw_operations, list):
        raise ValueError("recorded actual is missing operations")
    operations = []
    for operation in raw_operations:
        if not isinstance(operation, Mapping) or not all(
            isinstance(operation.get(field), str)
            for field in ("intent_type", "action_type", "polarity")
        ):
            raise ValueError("recorded operation is incomplete")
        if operation["polarity"] not in {"affirmed", "negated"}:
            raise ValueError("recorded operation polarity is invalid")
        operations.append(_RecordedOperation(
            _RecordedLabel(operation["intent_type"]),
            _RecordedLabel(operation["action_type"]),
            operation["polarity"],
        ))
    entities = actual.get("entities")
    if not isinstance(entities, Mapping) or not all(
        isinstance(key, str)
        and isinstance(values, list)
        and all(isinstance(value, str) for value in values)
        for key, values in entities.items()
    ):
        raise ValueError("recorded actual entities are incomplete")
    affirmed = tuple(op for op in operations if op.polarity == "affirmed")
    negated = tuple(op for op in operations if op.polarity == "negated")
    if read_only is None:
        readonly_actions = {"read", "analyze", "compare"}
        if affirmed:
            read_only = all(op.action_type.value in readonly_actions for op in affirmed)
        elif operations:
            read_only = True
        else:
            read_only = actual["action_type"] in readonly_actions
    return score(case, _RecordedIntent(
        _RecordedLabel(actual["intent_type"]), affirmed, negated, entities, read_only
    ))


__all__ = [
    "ACTION_ALTERNATIVE_GROUPS", "CONTRACT_VERSION", "HOLDOUT_SHA256",
    "ORIGINAL_SCORER_SHA256", "score", "score_recorded",
]
