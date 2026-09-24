"""Action-group extension of the unchanged original scorer, frozen before runs."""
from __future__ import annotations

from pathlib import Path
from score_loader import load_original_score

ROOT = Path(__file__).resolve().parents[2]
_original_score = load_original_score()
HOLDOUT_SHA256 = "72b912439196384499b50a8d135650f5a5a95e14f9b73b6d5775854452840622"
AUTHORSHIP_SHA256 = "58486b3337dcbca51e05307cd12dc93252fa9a9e969a9c0dd21b0e463d0fe6f4"

INTENTS = frozenset("code debug refactor explain search review test document configure question chat memory system unknown".split())
ACTIONS = frozenset("create read update delete execute analyze compare merge split move rename unknown".split())
CONTRACT = "phase2-action-groups-v1"


def validate_case(case):
    assert isinstance(case.get("id"), str)
    assert isinstance(case.get("text"), str) and case["text"].strip()
    assert isinstance(case.get("expected"), list) and case["expected"]
    assert set(case["expected"]) <= INTENTS
    for key in ("required_affirmed", "forbidden_affirmed", "required_negated", "expected_order"):
        assert set(case.get(key, [])) <= INTENTS
    for intent, actions in case.get("expected_actions", {}).items():
        assert intent in INTENTS and actions and set(actions) <= ACTIONS
    assert set(case.get("forbidden_actions", [])) <= ACTIONS
    if "expected_read_only" in case:
        assert type(case["expected_read_only"]) is bool
    groups = case.get("required_operation_groups", [])
    assert isinstance(groups, list)
    for group in groups:
        assert isinstance(group, list) and group
        for choice in group:
            assert set(choice) == {"intent", "actions"}
            assert choice["intent"] in INTENTS
            assert isinstance(choice["actions"], list) and choice["actions"]
            assert set(choice["actions"]) <= ACTIONS


def score(case, intent):
    """Groups are required together; alternatives within a group are disjunctive."""
    validate_case(case)
    primary, issues = _original_score(case, intent)
    for index, group in enumerate(case.get("required_operation_groups", []), 1):
        if not any(
            operation.intent_type.value == choice["intent"]
            and operation.action_type.value in choice["actions"]
            for choice in group
            for operation in intent.affirmed_operations
        ):
            issues.append(f"missing required operation group {index}: {group}")
    return primary, issues
