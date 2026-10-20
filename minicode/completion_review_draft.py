"""Exact rejected native submissions, retained as reference rather than authority.

Only trusted host persistence/checkpoints may supply these records. Hashes bind
bytes; they do not turn a model-supplied document into authenticated evidence.
No function here executes a submission or certifies requirement coverage.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import PurePosixPath, PureWindowsPath
import re


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RECORD_NAME = re.compile(r"([0-9]{5,})-tool-evidence\.json\Z")
_DRAFT_FIELDS = frozenset({"draftVersion", "status", "reviewId", "reviewIndex",
    "candidateDigest", "objectiveDigest", "started", "finished"})
_RECORD_FIELDS = frozenset({"role", "reviewId", "toolName", "phase", "input",
    "output", "isError", "invocationId"})


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate draft record JSON key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("nonfinite draft record JSON value")


def _record(item):
    if not isinstance(item, dict) or set(item) != {"ref", "sha256", "rawJson"}:
        raise ValueError("draft must retain exact native record bytes and reference")
    ref, raw = item["ref"], item["rawJson"]
    if not isinstance(ref, str) or "\x00" in ref:
        raise ValueError("invalid draft evidence reference")
    if "\\" not in ref:
        path = PurePosixPath(ref)
    elif os.name != "nt":
        raise ValueError("invalid draft evidence reference")
    else:
        path = PureWindowsPath(ref)
    match = _RECORD_NAME.fullmatch(path.name)
    if not path.is_absolute() or ".." in path.parts or str(path) != ref or match is None:
        raise ValueError("invalid draft evidence reference")
    if not isinstance(raw, str) or item["sha256"] != _sha(raw.encode("utf-8")):
        raise ValueError("draft evidence bytes changed")
    document = json.loads(raw, object_pairs_hook=_object, parse_constant=_invalid_constant)
    if not isinstance(document, dict) or set(document) not in (
            _RECORD_FIELDS, _RECORD_FIELDS | {"candidateDigest", "objectiveDigest"}):
        raise ValueError("invalid native submission record fields")
    return document, path, int(match.group(1))


def validate_submission_draft(draft: dict, *, objective_digest: str,
                              candidate_digest: str | None = None) -> dict:
    """Validate a trusted saved draft and return its unaccepted prompt view.

    A caller may validate historical state without a current candidate. A draft
    may be offered as current reference only with candidate_digest supplied and
    matching. The returned input is decoded from the original raw record, never
    repaired, normalized or replayed as a decision.
    """
    if (not isinstance(draft, dict) or set(draft) != _DRAFT_FIELDS
            or type(draft["draftVersion"]) is not int or draft["draftVersion"] != 1
            or draft["status"] != "unaccepted"
            or type(draft["reviewIndex"]) is not int or draft["reviewIndex"] < 0):
        raise ValueError("invalid unaccepted submission draft")
    for name in ("candidateDigest", "objectiveDigest"):
        if not isinstance(draft[name], str) or not _DIGEST.fullmatch(draft[name]):
            raise ValueError("invalid submission draft binding")
    if draft["objectiveDigest"] != objective_digest:
        raise ValueError("submission draft objective changed")
    if candidate_digest is not None and draft["candidateDigest"] != candidate_digest:
        raise ValueError("submission draft candidate changed")
    review_id = "completion_review_" + hashlib.sha256(
        f"{draft['candidateDigest']}:{draft['reviewIndex']}".encode()).hexdigest()[:24]
    if draft["reviewId"] != review_id:
        raise ValueError("submission draft review identity changed")
    started, start_path, start_index = _record(draft["started"])
    finished, finish_path, finish_index = _record(draft["finished"])
    if start_path.parent != finish_path.parent or start_index >= finish_index:
        raise ValueError("submission draft record order or directory changed")
    for item in (started, finished):
        if (item["role"] != "completion_reviewer" or item["toolName"] != "completion_review_submit"
                or item["reviewId"] != review_id):
            raise ValueError("submission draft records belong to another tool or review")
        if "candidateDigest" in item and any(item[name] != draft[name]
                for name in ("candidateDigest", "objectiveDigest")):
            raise ValueError("submission draft native record binding changed")
    if (started["phase"] != "started" or not isinstance(started["input"], dict)
            or started["output"] != "" or started["isError"] is not False
            or started["invocationId"] is not None):
        raise ValueError("submission draft lacks its original native started input")
    if (finished["phase"] != "finished" or finished["input"] is not None
            or finished["isError"] is not True or not isinstance(finished["output"], str)
            or not finished["output"] or finished["invocationId"] != str(start_path)):
        raise ValueError("submission draft lacks a paired native rejection")
    return {"status": "unaccepted", "reviewId": review_id,
        "candidateDigest": draft["candidateDigest"], "objectiveDigest": objective_digest,
        "input": started["input"], "output": finished["output"],
        "startedRef": str(start_path), "finishedRef": str(finish_path),
        "startedSha256": draft["started"]["sha256"], "finishedSha256": draft["finished"]["sha256"]}


def create_submission_draft(started_raw: bytes, finished_raw: bytes, *, started_ref: str,
                            finished_ref: str, candidate_digest: str,
                            objective_digest: str, review_index: int) -> dict:
    """Capture two already host-authenticated original persistence outputs."""
    if not isinstance(started_raw, bytes) or not isinstance(finished_raw, bytes):
        raise ValueError("submission draft requires the original UTF-8 bytes")
    review_id = "completion_review_" + hashlib.sha256(
        f"{candidate_digest}:{review_index}".encode()).hexdigest()[:24]
    draft = {"draftVersion": 1, "status": "unaccepted", "reviewId": review_id,
        "reviewIndex": review_index, "candidateDigest": candidate_digest,
        "objectiveDigest": objective_digest,
        "started": {"ref": started_ref, "sha256": _sha(started_raw), "rawJson": started_raw.decode("utf-8")},
        "finished": {"ref": finished_ref, "sha256": _sha(finished_raw), "rawJson": finished_raw.decode("utf-8")}}
    validate_submission_draft(draft, objective_digest=objective_digest, candidate_digest=candidate_digest)
    return draft


__all__ = ["create_submission_draft", "validate_submission_draft"]
