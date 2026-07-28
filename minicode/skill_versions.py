"""Project-scoped immutable Skill version lineage and locked gate projection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from minicode.intent_parser import ActionType, IntentType


_SCHEMA_VERSION = 1
_LEDGER_VERSION = 1
_MAX_VERSIONS = 1_000
_MAX_STORAGE_BYTES = 2 * 1024 * 1024
_VERSION_ID_RE = re.compile(r"^skillv_[0-9a-f]{32}$")
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,255}$")
_SKILL_DIRECTORY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
)
_SKILL_SOURCES = frozenset(
    {"project", "user", "compat_project", "compat_user"}
)
_INTENT_TYPES = frozenset(item.value for item in IntentType)
_ACTION_TYPES = frozenset(item.value for item in ActionType)
_SHADOW_STATUSES = frozenset(
    {
        "positive_signal",
        "negative_signal",
        "inconclusive",
        "insufficient_evidence",
    }
)
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}

SkillIdentity = tuple[str, str, str, str]
SkillKey = tuple[str, str, str]
GateEvidence = tuple[
    bool,
    str,
    int,
    int | None,
    int | None,
    str | None,
    str | None,
    int,
    int | None,
    int | None,
    str | None,
    str | None,
]


class SkillVersionLedgerError(RuntimeError):
    """The immutable Skill version history could not be trusted."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() == timedelta(0)
        and _iso_time(parsed) == value
    )


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _skill_identity(value: object) -> SkillIdentity:
    name = _field(value, "qualified_name") or _field(value, "qualifiedName")
    source = _field(value, "source")
    directory = _field(value, "directory")
    digest = _field(value, "content_digest") or _field(
        value, "contentDigest"
    )
    if (
        not isinstance(name, str)
        or _SKILL_NAME_RE.fullmatch(name) is None
        or source not in _SKILL_SOURCES
        or not isinstance(directory, str)
        or (
            directory
            and _SKILL_DIRECTORY_RE.fullmatch(directory) is None
        )
        or not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
    ):
        raise SkillVersionLedgerError("invalid Skill catalog identity")
    return name, source, directory, digest


def _skill_key(identity: SkillIdentity) -> SkillKey:
    return identity[0], identity[1], identity[2]


def _skill_dict(identity: SkillIdentity) -> dict[str, str]:
    return {
        "qualifiedName": identity[0],
        "source": identity[1],
        "directory": identity[2],
        "contentDigest": identity[3],
    }


def _version_id(identity: SkillIdentity) -> str:
    encoded = json.dumps(
        _skill_dict(identity),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "skillv_" + hashlib.sha256(encoded).hexdigest()[:32]


def _normalize_catalog(values: Iterable[object]) -> tuple[SkillIdentity, ...]:
    identities: list[SkillIdentity] = []
    seen_versions: set[SkillIdentity] = set()
    seen_keys: dict[SkillKey, str] = {}
    try:
        iterator = iter(values)
    except TypeError as error:
        raise SkillVersionLedgerError("invalid Skill catalog") from error
    for value in iterator:
        identity = _skill_identity(value)
        key = _skill_key(identity)
        prior_digest = seen_keys.get(key)
        if prior_digest is not None and prior_digest != identity[3]:
            raise SkillVersionLedgerError("ambiguous Skill catalog version")
        seen_keys[key] = identity[3]
        if identity not in seen_versions:
            identities.append(identity)
            seen_versions.add(identity)
        if len(identities) > _MAX_VERSIONS:
            raise SkillVersionLedgerError("Skill catalog exceeds version limit")
    return tuple(identities)


def _process_lock(path: Path) -> threading.RLock:
    key = os.path.abspath(os.fspath(path))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _storage_lock(path: Path) -> Iterator[None]:
    process_lock = _process_lock(path)
    with process_lock:
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            raise SkillVersionLedgerError(
                "Skill version lock is unavailable"
            ) from error
        assert descriptor is not None
        try:
            try:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except ImportError:  # pragma: no cover - non-POSIX fallback
                pass
            yield
        finally:
            try:
                try:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except ImportError:  # pragma: no cover - non-POSIX fallback
                    pass
            finally:
                os.close(descriptor)


def _normalize_evidence(
    evidence: Mapping[str, object],
) -> dict[SkillIdentity, tuple[GateEvidence, ...]]:
    if (
        not isinstance(evidence, Mapping)
        or evidence.get("ledgerVersion") != 1
        or evidence.get("mode") != "shadow"
        or evidence.get("promotionEligible") is not False
        or not isinstance(evidence.get("evaluations"), list)
        or len(evidence["evaluations"]) > 100
    ):
        raise SkillVersionLedgerError("invalid Skill evidence snapshot")
    grouped: dict[SkillIdentity, list[GateEvidence]] = {}
    seen: set[tuple[SkillIdentity, str, str]] = set()
    for value in evidence["evaluations"]:
        if not isinstance(value, Mapping):
            raise SkillVersionLedgerError("invalid Skill evidence evaluation")
        skill = value.get("skill")
        profile = value.get("profile")
        treatment = value.get("treatment")
        control = value.get("control")
        sample_gate = value.get("sampleGatePassed")
        shadow_status = value.get("shadowStatus")
        if (
            not isinstance(skill, Mapping)
            or not isinstance(profile, Mapping)
            or not isinstance(treatment, Mapping)
            or not isinstance(control, Mapping)
            or not isinstance(sample_gate, bool)
            or shadow_status not in _SHADOW_STATUSES
            or value.get("promotionEligible") is not False
        ):
            raise SkillVersionLedgerError("invalid Skill evidence evaluation")
        intent_type = profile.get("intentType")
        action_type = profile.get("actionType")
        if (
            intent_type not in _INTENT_TYPES
            or action_type not in _ACTION_TYPES
            or (
                sample_gate
                and shadow_status == "insufficient_evidence"
            )
            or (
                not sample_gate
                and shadow_status != "insufficient_evidence"
            )
        ):
            raise SkillVersionLedgerError("invalid Skill evidence profile")
        identity = _skill_identity(skill)
        dedupe_key = (identity, intent_type, action_type)
        if dedupe_key in seen:
            raise SkillVersionLedgerError("duplicate Skill evidence profile")
        seen.add(dedupe_key)
        treatment_facts = _cohort_gate_facts(treatment)
        control_facts = _cohort_gate_facts(control)
        if sample_gate != (
            treatment_facts[0] >= 5 and control_facts[0] >= 5
        ):
            raise SkillVersionLedgerError(
                "inconsistent Skill evidence sample gate"
            )
        grouped.setdefault(identity, []).append(
            (
                sample_gate,
                str(shadow_status),
                *treatment_facts,
                *control_facts,
            )
        )
    return {key: tuple(values) for key, values in grouped.items()}


def _cohort_gate_facts(
    cohort: Mapping[str, object],
) -> tuple[int, int | None, int | None, str | None, str | None]:
    runs = cohort.get("runs")
    if (
        isinstance(runs, bool)
        or not isinstance(runs, int)
        or not 0 <= runs <= 200
    ):
        raise SkillVersionLedgerError("invalid Skill evidence cohort")
    return (
        runs,
        _covered_total(cohort.get("cost"), runs, monetary=True),
        _covered_total(cohort.get("latency"), runs, monetary=False),
        _verification_gate_fact(cohort.get("verification"), runs),
        _user_gate_fact(cohort.get("userSignal"), runs),
    )


def _verification_gate_fact(metric: object, runs: int) -> str | None:
    if not isinstance(metric, Mapping) or set(metric) != {
        "observedRuns",
        "passedRuns",
        "failedRuns",
        "coverageComplete",
    }:
        raise SkillVersionLedgerError("invalid verification evidence")
    observed = metric.get("observedRuns")
    passed = metric.get("passedRuns")
    failed = metric.get("failedRuns")
    complete = metric.get("coverageComplete")
    if (
        any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= runs
            for value in (observed, passed, failed)
        )
        or not isinstance(complete, bool)
        or observed != passed + failed
        or complete != (runs > 0 and observed == runs)
    ):
        raise SkillVersionLedgerError("inconsistent verification evidence")
    if failed:
        return "failed"
    if complete and passed == runs:
        return "passed"
    return None


def _user_gate_fact(metric: object, runs: int) -> str | None:
    if not isinstance(metric, Mapping) or set(metric) != {
        "observedRuns",
        "acceptedRuns",
        "correctedRuns",
        "rejectedRuns",
        "coverageComplete",
    }:
        raise SkillVersionLedgerError("invalid user signal evidence")
    observed = metric.get("observedRuns")
    accepted = metric.get("acceptedRuns")
    corrected = metric.get("correctedRuns")
    rejected = metric.get("rejectedRuns")
    complete = metric.get("coverageComplete")
    if (
        any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= runs
            for value in (observed, accepted, corrected, rejected)
        )
        or not isinstance(complete, bool)
        or observed != accepted + corrected + rejected
        or complete != (runs > 0 and observed == runs)
    ):
        raise SkillVersionLedgerError("inconsistent user signal evidence")
    if corrected or rejected:
        return "negative"
    if complete and accepted == runs:
        return "accepted"
    return None


def _covered_total(
    metric: object,
    runs: int,
    *,
    monetary: bool,
) -> int | None:
    if not isinstance(metric, Mapping):
        raise SkillVersionLedgerError("invalid Skill evidence metric")
    observed = metric.get("observedRuns")
    complete = metric.get("coverageComplete")
    total_name = "totalNanoUsd" if monetary else "totalDurationMs"
    raw_total = metric.get(total_name)
    if (
        isinstance(observed, bool)
        or not isinstance(observed, int)
        or not 0 <= observed <= runs
        or not isinstance(complete, bool)
    ):
        raise SkillVersionLedgerError("invalid Skill evidence metric")
    if monetary:
        if raw_total is None:
            total = None
        elif (
            isinstance(raw_total, str)
            and raw_total.isascii()
            and raw_total.isdecimal()
            and str(int(raw_total)) == raw_total
        ):
            total = int(raw_total)
        else:
            raise SkillVersionLedgerError("invalid Skill evidence cost")
    else:
        if raw_total is None:
            total = None
        elif (
            isinstance(raw_total, int)
            and not isinstance(raw_total, bool)
            and raw_total >= 0
        ):
            total = raw_total
        else:
            raise SkillVersionLedgerError("invalid Skill evidence latency")
    if (
        (observed == 0) != (total is None)
        or complete != (runs > 0 and observed == runs)
        or (total is not None and total > 10**24)
    ):
        raise SkillVersionLedgerError("inconsistent Skill evidence metric")
    return total if complete else None


def _evaluate_version(evaluations: tuple[GateEvidence, ...]) -> dict[str, object]:
    sampled = [item for item in evaluations if item[0]]
    statuses = {item[1] for item in sampled}
    if not sampled:
        outcome = (
            "unavailable",
            "sample_gated_outcome_evidence_unavailable",
        )
    elif statuses == {"positive_signal"}:
        outcome = (
            "pass",
            "positive_signal_without_negative_profile",
        )
    else:
        outcome = (
            "fail",
            "negative_or_inconclusive_outcome_profile",
        )
    cost = _economics_gate(sampled, cost=True)
    latency = _economics_gate(sampled, cost=False)
    verification = _verification_gate(sampled)
    user = _user_signal_gate(sampled)
    gates = [
        {"name": "outcome", "status": outcome[0], "reason": outcome[1]},
        {
            "name": "verification",
            "status": verification[0],
            "reason": verification[1],
        },
        {
            "name": "user",
            "status": user[0],
            "reason": user[1],
        },
        {"name": "cost", "status": cost[0], "reason": cost[1]},
        {
            "name": "latency",
            "status": latency[0],
            "reason": latency[1],
        },
    ]
    all_passed = all(gate["status"] == "pass" for gate in gates)
    return {
        "gatePolicyVersion": 2,
        "evidenceProfiles": len(evaluations),
        "gates": gates,
        "allRequiredGatesPassed": all_passed,
        "promotionCandidate": all_passed,
        "promotionLocked": True,
    }


def _verification_gate(
    sampled: list[GateEvidence],
) -> tuple[str, str]:
    statuses = [item[5] for item in sampled]
    if "failed" in statuses:
        return "fail", "verification_failed"
    if sampled and all(status == "passed" for status in statuses):
        return "pass", "all_treatment_runs_verified"
    return "unavailable", "verification_coverage_incomplete"


def _user_signal_gate(
    sampled: list[GateEvidence],
) -> tuple[str, str]:
    statuses = [item[6] for item in sampled]
    if "negative" in statuses:
        return "fail", "user_correction_or_rejection_observed"
    if sampled and all(status == "accepted" for status in statuses):
        return "pass", "all_treatment_runs_explicitly_accepted"
    return "unavailable", "user_signal_coverage_incomplete"


def _economics_gate(
    sampled: list[GateEvidence],
    *,
    cost: bool,
) -> tuple[str, str]:
    treatment_total_index = 3 if cost else 4
    control_total_index = 8 if cost else 9
    label = "cost" if cost else "latency"
    known = [
        item
        for item in sampled
        if item[treatment_total_index] is not None
        and item[control_total_index] is not None
        and item[2] > 0
        and item[7] > 0
    ]
    regressed = any(
        int(item[treatment_total_index]) * item[7]
        > int(item[control_total_index]) * item[2]
        for item in known
    )
    if regressed:
        return "fail", f"mean_{label}_regressed"
    if sampled and len(known) == len(sampled):
        return "pass", f"mean_{label}_not_regressed"
    return "unavailable", f"{label}_coverage_incomplete"


class SkillVersionLedger:
    """Hide version identity, storage, lineage, and gate policy behind two calls."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self._root = self.workspace / ".mini-code"
        self._path = self._root / "skill_versions.json"
        self._lock_path = self._root / ".skill_versions.lock"
        self._clock = clock

    def observe_catalog(self, skills: Iterable[object]) -> None:
        """Atomically append previously unseen immutable catalog versions."""
        identities = _normalize_catalog(skills)
        if not identities:
            return
        self._ensure_safe_root(create=True)
        with _storage_lock(self._lock_path):
            records = self._read_records()
            existing_ids = {str(item["versionId"]) for item in records}
            latest_by_key: dict[SkillKey, str] = {}
            for item in records:
                identity = self._record_identity(item)
                latest_by_key[_skill_key(identity)] = str(item["versionId"])
            observed_at = _iso_time(self._clock())
            changed = False
            for identity in identities:
                version_id = _version_id(identity)
                if version_id in existing_ids:
                    continue
                if len(records) >= _MAX_VERSIONS:
                    raise SkillVersionLedgerError(
                        "Skill version history reached its limit"
                    )
                parent = latest_by_key.get(_skill_key(identity))
                records.append(
                    {
                        "versionId": version_id,
                        "skill": _skill_dict(identity),
                        "parentVersionId": parent,
                        "status": "observed",
                        "firstObservedAt": observed_at,
                        "createdFromRuns": [],
                    }
                )
                existing_ids.add(version_id)
                latest_by_key[_skill_key(identity)] = version_id
                changed = True
            if changed:
                self._write_records(records)

    def snapshot(
        self,
        catalog: Iterable[object],
        evidence: Mapping[str, object],
    ) -> dict[str, object]:
        """Return JSON-safe lineage and locked evaluation without writing."""
        current_ids = {
            _version_id(identity) for identity in _normalize_catalog(catalog)
        }
        records = self._read_records()
        evidence_by_version = _normalize_evidence(evidence)
        versions = [
            {
                **item,
                "rollbackToVersionId": item["parentVersionId"],
                "catalogCurrent": item["versionId"] in current_ids,
                "evaluation": _evaluate_version(
                    evidence_by_version.get(
                        self._record_identity(item),
                        (),
                    )
                ),
            }
            for item in records
        ]
        return {
            "ledgerVersion": _LEDGER_VERSION,
            "mode": "shadow",
            "promotionLocked": True,
            "versions": versions,
            "evaluation": {
                "gatePolicyVersion": 2,
                "versionCount": len(versions),
                "promotionCandidateCount": sum(
                    bool(item["evaluation"]["promotionCandidate"])
                    for item in versions
                ),
            },
        }

    def _read_records(self) -> list[dict[str, object]]:
        if not self._ensure_safe_root(create=False):
            return []
        try:
            path_stat = os.lstat(self._path)
        except FileNotFoundError:
            return []
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or path_stat.st_size > _MAX_STORAGE_BYTES
        ):
            raise SkillVersionLedgerError("unsafe Skill version storage")
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(self._path, flags)
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_size > _MAX_STORAGE_BYTES
                or opened_stat.st_dev != path_stat.st_dev
                or opened_stat.st_ino != path_stat.st_ino
            ):
                raise SkillVersionLedgerError("unsafe Skill version storage")
            handle = os.fdopen(descriptor, "r", encoding="utf-8")
            descriptor = None
            with handle:
                raw = json.load(handle)
        except SkillVersionLedgerError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise SkillVersionLedgerError(
                "Skill version storage could not be read"
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if (
            not isinstance(raw, dict)
            or set(raw) != {"schemaVersion", "ledgerVersion", "versions"}
            or raw.get("schemaVersion") != _SCHEMA_VERSION
            or raw.get("ledgerVersion") != _LEDGER_VERSION
            or not isinstance(raw.get("versions"), list)
            or len(raw["versions"]) > _MAX_VERSIONS
        ):
            raise SkillVersionLedgerError("invalid Skill version storage")
        records: list[dict[str, object]] = []
        prior_records: dict[str, dict[str, object]] = {}
        latest_by_key: dict[SkillKey, str] = {}
        for value in raw["versions"]:
            record = self._normalize_record(
                value,
                prior_records,
                latest_by_key,
            )
            records.append(record)
            version_id = str(record["versionId"])
            prior_records[version_id] = record
            latest_by_key[
                _skill_key(self._record_identity(record))
            ] = version_id
        return records

    @staticmethod
    def _record_identity(record: Mapping[str, object]) -> SkillIdentity:
        skill = record.get("skill")
        if not isinstance(skill, Mapping):
            raise SkillVersionLedgerError("invalid Skill version record")
        return _skill_identity(skill)

    def _normalize_record(
        self,
        value: object,
        prior_records: Mapping[str, Mapping[str, object]],
        latest_by_key: Mapping[SkillKey, str],
    ) -> dict[str, object]:
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "versionId",
                "skill",
                "parentVersionId",
                "status",
                "firstObservedAt",
                "createdFromRuns",
            }
        ):
            raise SkillVersionLedgerError("invalid Skill version record")
        identity = self._record_identity(value)
        version_id = value.get("versionId")
        parent = value.get("parentVersionId")
        created_from_runs = value.get("createdFromRuns")
        if (
            not isinstance(version_id, str)
            or _VERSION_ID_RE.fullmatch(version_id) is None
            or version_id != _version_id(identity)
            or version_id in prior_records
            or parent != latest_by_key.get(_skill_key(identity))
            or value.get("status") != "observed"
            or not _valid_timestamp(value.get("firstObservedAt"))
            or created_from_runs != []
        ):
            raise SkillVersionLedgerError("invalid Skill version record")
        return {
            "versionId": version_id,
            "skill": _skill_dict(identity),
            "parentVersionId": parent,
            "status": "observed",
            "firstObservedAt": value["firstObservedAt"],
            "createdFromRuns": [],
        }

    def _ensure_safe_root(self, *, create: bool) -> bool:
        try:
            root_stat = os.lstat(self._root)
        except FileNotFoundError:
            if not create:
                return False
            try:
                self._root.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError as error:
                raise SkillVersionLedgerError(
                    "Skill version root is unavailable"
                ) from error
            try:
                root_stat = os.lstat(self._root)
            except OSError as error:
                raise SkillVersionLedgerError(
                    "Skill version root is unavailable"
                ) from error
        except OSError as error:
            raise SkillVersionLedgerError(
                "Skill version root is unavailable"
            ) from error
        if not stat.S_ISDIR(root_stat.st_mode):
            raise SkillVersionLedgerError("unsafe Skill version root")
        return True

    def _write_records(self, records: list[dict[str, object]]) -> None:
        payload = json.dumps(
            {
                "schemaVersion": _SCHEMA_VERSION,
                "ledgerVersion": _LEDGER_VERSION,
                "versions": records,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = (payload + "\n").encode("utf-8")
        if len(encoded) > _MAX_STORAGE_BYTES:
            raise SkillVersionLedgerError(
                "Skill version storage exceeds its limit"
            )
        self._ensure_safe_root(create=False)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".skill_versions.",
            suffix=".tmp",
            dir=self._root,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
            if os.name != "nt":
                self._path.chmod(0o600)
        except OSError as error:
            raise SkillVersionLedgerError(
                "Skill version storage could not be written"
            ) from error
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def observe_skill_catalog_safely(
    workspace: str | Path,
    skills: Iterable[object],
) -> None:
    """Observe catalog versions without changing runtime construction."""
    try:
        SkillVersionLedger(workspace).observe_catalog(skills)
    except Exception:
        return


__all__ = [
    "SkillVersionLedger",
    "SkillVersionLedgerError",
    "observe_skill_catalog_safely",
]
