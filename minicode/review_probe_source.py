"""Pure identity validation for complete, host-owned review probe source data."""

from __future__ import annotations

import hashlib
import json


REVIEW_PROBE_SOURCE_PREFIX = "artifact_review_probe_source_"
# Existing Assignment/coverage metadata bounds; full source code is data.
MAX_REVIEW_PROBE_CRITERIA = 100
MAX_REVIEW_PROBE_CRITERION_ID_CHARS = 80
MAX_REVIEW_PROBE_SCENARIO_CHARS = 1200
_FIELDS = {"probeVersion", "resultType", "probeId", "criterionIds", "scenario", "code", "sourceDigest"}


def review_probe_source_artifact_id(probe_id: str) -> str:
    return REVIEW_PROBE_SOURCE_PREFIX + hashlib.sha256(probe_id.encode("utf-8")).hexdigest()[:32]


def validate_review_probe_source(value: object) -> bool:
    """Validate bytes and immutable IDs, without granting execution or approval.

    Capacity belongs to the shared artifact store, not to this source format.
    This helper has no store/contract imports so durable audit can use it too.
    """
    if (not isinstance(value, dict) or set(value) != _FIELDS
            or type(value.get("probeVersion")) is not int or value["probeVersion"] != 1
            or value.get("resultType") != "ReviewProbeSource.v1"):
        return False
    ids, scenario, code = value.get("criterionIds"), value.get("scenario"), value.get("code")
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_REVIEW_PROBE_CRITERIA
            or any(not isinstance(cid, str) or not cid.strip() or "\x00" in cid
                   or len(cid) > MAX_REVIEW_PROBE_CRITERION_ID_CHARS for cid in ids)
            or len(set(ids)) != len(ids) or ids != sorted(ids)
            or not isinstance(scenario, str) or not scenario.strip() or "\x00" in scenario
            or len(scenario) > MAX_REVIEW_PROBE_SCENARIO_CHARS
            or not isinstance(code, str) or not code.strip() or "\x00" in code):
        return False
    try:
        source_digest = "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()
        identity = json.dumps({"criterionIds": ids, "scenario": scenario, "sourceDigest": source_digest},
                              ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        probe_id = "review_probe_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    except (UnicodeError, TypeError, ValueError):
        return False
    return value.get("sourceDigest") == source_digest and value.get("probeId") == probe_id


__all__ = ["REVIEW_PROBE_SOURCE_PREFIX", "review_probe_source_artifact_id", "validate_review_probe_source"]
