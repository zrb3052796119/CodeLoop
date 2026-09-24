"""Privacy-safe historical evaluation for semantic intent classification.

The public label and result documents in this module are deliberately
content-free.  Request text is reconstructed from an exact local Session
locator, held only in memory, and sent only to an explicitly configured
loopback OpenAI-compatible endpoint.  The evaluator never treats historical
task-level feedback as an intent label.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from pathlib import Path
from typing import Any, Protocol

from minicode.intent_parser import (
    SEMANTIC_INTENT_ALLOWED_ACTIONS,
    ActionType,
    IntentContext,
    IntentType,
    SemanticIntentRequest,
)
from minicode.intent_semantic_calibration import (
    INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
)

LABEL_MANIFEST_SCHEMA_VERSION = 1
SOURCE_MANIFEST_SCHEMA_VERSION = 1
EVALUATION_SCHEMA_VERSION = 1
CALIBRATION_OBSERVATION_SCHEMA_VERSION = 1

_MAX_LABEL_MANIFEST_BYTES = 8_000_000
_MAX_SOURCE_MANIFEST_BYTES = 8_000_000
_MAX_CASES = 100_000
_MAX_OPERATIONS = 4
_MAX_SESSION_MESSAGES = 100_000
_HMAC_ID_RE = re.compile(r"h1_[0-9a-f]{64}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,127}\Z")
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_MODEL_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}\Z")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_DISALLOWED_ACTIONS = frozenset(
    {
        ActionType.DELETE,
        ActionType.EXECUTE,
        ActionType.MERGE,
        ActionType.SPLIT,
        ActionType.MOVE,
        ActionType.RENAME,
    }
)


class LabelDisposition(str, Enum):
    """Whether a case may contribute to classifier metrics."""

    ELIGIBLE = "eligible"
    AMBIGUOUS = "ambiguous"
    EXCLUDED = "excluded"


class PredictionStatus(str, Enum):
    """Content-free outcome of one replay prediction."""

    ACCEPTED = "accepted"
    LOW_CONFIDENCE = "low_confidence"
    ABSTAINED = "abstained"
    INVALID_RESPONSE = "invalid_response"
    PROVIDER_ERROR = "provider_error"
    MODEL_ERROR = "model_error"
    NOT_EVALUATED = "not_evaluated"


class SourceKind(str, Enum):
    GATEWAY_TURN = "gateway_turn"
    TUI_SESSION_TURN = "tui_session_turn"


class ReconstructionKind(str, Enum):
    FINGERPRINT_COMMIT_MARKER = "fingerprint_commit_marker"
    EXACT_UNIQUE_MESSAGE = "exact_unique_message"


class AdjudicationStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    RESOLVED = "resolved"
    PENDING = "pending"


def _require_mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], *, label: str
) -> None:
    if frozenset(value) != expected:
        raise ValueError(f"{label} must contain exactly the documented fields")


def _safe_token(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a safe opaque token")
    return value


def _hmac_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _HMAC_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be an h1 HMAC identifier")
    return value


def _probability(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")
    return parsed


def _bounded_positive_int(
    value: object, *, field_name: str, minimum: int, maximum: int
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field_name} is outside its allowed range")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON document contains a duplicate key")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"JSON document contains non-finite number {value}")


def _read_json(path: Path, *, maximum_bytes: int) -> object:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("could not read evaluation JSON document") from exc
    if len(raw) > maximum_bytes:
        raise ValueError("evaluation JSON document exceeds its byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("evaluation JSON document must be UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json,
        )
    except json.JSONDecodeError as exc:
        raise ValueError("evaluation JSON document is invalid") from exc


def _write_json_atomic(path: Path, payload: object) -> None:
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def stable_hmac_id(key: bytes, *, namespace: str, source_identifier: str) -> str:
    """Return a stable, domain-separated identifier without exposing its source."""

    if not isinstance(key, bytes) or len(key) < 16:
        raise ValueError("HMAC key must contain at least 16 bytes")
    normalized_namespace = _safe_token(namespace, field_name="namespace")
    if not isinstance(source_identifier, str) or not source_identifier:
        raise ValueError("source_identifier must be a non-empty string")
    digest = hmac.new(
        key,
        f"intent-production-eval-v1\0{normalized_namespace}\0{source_identifier}".encode(
            "utf-8"
        ),
        hashlib.sha256,
    ).hexdigest()
    return f"h1_{digest}"


def stable_session_group_id(key: bytes, session_id: str) -> str:
    """Pseudonymize one production Session for grouped evaluation."""

    if not isinstance(session_id, str) or _SESSION_ID_RE.fullmatch(session_id) is None:
        raise ValueError("session_id is invalid")
    return stable_hmac_id(
        key,
        namespace="session",
        source_identifier=session_id,
    )


def stable_case_id(
    key: bytes,
    *,
    session_id: str,
    user_message_index: int,
    request_sha256: str,
) -> str:
    """Pseudonymize an exact Session user turn."""

    if not isinstance(session_id, str) or _SESSION_ID_RE.fullmatch(session_id) is None:
        raise ValueError("session_id is invalid")
    index = _bounded_positive_int(
        user_message_index,
        field_name="user_message_index",
        minimum=0,
        maximum=_MAX_SESSION_MESSAGES - 1,
    )
    if (
        not isinstance(request_sha256, str)
        or _SHA256_RE.fullmatch(request_sha256) is None
    ):
        raise ValueError("request_sha256 must be a lowercase SHA-256 digest")
    return stable_hmac_id(
        key,
        namespace="case",
        source_identifier=f"{session_id}\0{index}\0{request_sha256}",
    )


@dataclass(frozen=True, slots=True)
class IntentActionLabel:
    intent_type: IntentType
    action_type: ActionType

    def __post_init__(self) -> None:
        if not isinstance(self.intent_type, IntentType) or not isinstance(
            self.action_type, ActionType
        ):
            raise TypeError("intent/action labels must use the canonical enums")
        if (
            self.intent_type in {IntentType.UNKNOWN, IntentType.SYSTEM}
            or self.action_type in _DISALLOWED_ACTIONS
            or self.action_type
            not in SEMANTIC_INTENT_ALLOWED_ACTIONS.get(self.intent_type, frozenset())
        ):
            raise ValueError("intent/action pair is not allowed for semantic routing")

    @classmethod
    def from_mapping(cls, value: object) -> IntentActionLabel:
        mapping = _require_mapping(value, label="operation label")
        _require_exact_keys(
            mapping,
            frozenset({"intent", "action"}),
            label="operation label",
        )
        try:
            return cls(
                intent_type=IntentType(mapping["intent"]),
                action_type=ActionType(mapping["action"]),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("operation label is not a supported pair") from exc

    def to_dict(self) -> dict[str, str]:
        return {
            "intent": self.intent_type.value,
            "action": self.action_type.value,
        }


@dataclass(frozen=True, slots=True)
class LabelProvenance:
    source_kind: SourceKind
    reconstruction: ReconstructionKind
    request_fingerprint_verified: bool

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, SourceKind):
            raise TypeError("source_kind must be a SourceKind")
        if not isinstance(self.reconstruction, ReconstructionKind):
            raise TypeError("reconstruction must be a ReconstructionKind")
        if not isinstance(self.request_fingerprint_verified, bool):
            raise TypeError("request_fingerprint_verified must be boolean")

    @classmethod
    def from_mapping(cls, value: object) -> LabelProvenance:
        mapping = _require_mapping(value, label="provenance")
        _require_exact_keys(
            mapping,
            frozenset({"sourceKind", "reconstruction", "requestFingerprintVerified"}),
            label="provenance",
        )
        try:
            return cls(
                source_kind=SourceKind(mapping["sourceKind"]),
                reconstruction=ReconstructionKind(mapping["reconstruction"]),
                request_fingerprint_verified=mapping["requestFingerprintVerified"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("provenance contains an unsupported value") from exc

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "sourceKind": self.source_kind.value,
            "reconstruction": self.reconstruction.value,
            "requestFingerprintVerified": self.request_fingerprint_verified,
        }


@dataclass(frozen=True, slots=True)
class LabelAdjudication:
    status: AdjudicationStatus
    adjudicator: str | None
    reason_code: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AdjudicationStatus):
            raise TypeError("status must be an AdjudicationStatus")
        if self.adjudicator is not None:
            _safe_token(self.adjudicator, field_name="adjudicator")
        if self.reason_code is not None:
            _safe_token(self.reason_code, field_name="reasonCode")
        if self.status is AdjudicationStatus.RESOLVED:
            if self.adjudicator is None or self.reason_code is None:
                raise ValueError(
                    "resolved adjudication requires adjudicator and reasonCode"
                )
        elif self.adjudicator is not None or self.reason_code is not None:
            raise ValueError("non-resolved adjudication cannot name an adjudicator")

    @classmethod
    def from_mapping(cls, value: object) -> LabelAdjudication:
        mapping = _require_mapping(value, label="adjudication")
        _require_exact_keys(
            mapping,
            frozenset({"status", "adjudicator", "reasonCode"}),
            label="adjudication",
        )
        try:
            return cls(
                status=AdjudicationStatus(mapping["status"]),
                adjudicator=mapping["adjudicator"],
                reason_code=mapping["reasonCode"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("adjudication contains an unsupported value") from exc

    def to_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status.value,
            "adjudicator": self.adjudicator,
            "reasonCode": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class LabelAnnotation:
    annotators: tuple[str, ...]
    blind_to_prediction: bool
    adjudication: LabelAdjudication

    def __post_init__(self) -> None:
        if not isinstance(self.annotators, tuple) or not 1 <= len(self.annotators) <= 8:
            raise ValueError("annotators must contain between one and eight IDs")
        if len(set(self.annotators)) != len(self.annotators):
            raise ValueError("annotator IDs must be unique")
        for annotator in self.annotators:
            _safe_token(annotator, field_name="annotator")
        if not isinstance(self.blind_to_prediction, bool):
            raise TypeError("blind_to_prediction must be boolean")
        if not isinstance(self.adjudication, LabelAdjudication):
            raise TypeError("adjudication must be a LabelAdjudication")

    @classmethod
    def from_mapping(cls, value: object) -> LabelAnnotation:
        mapping = _require_mapping(value, label="annotation")
        _require_exact_keys(
            mapping,
            frozenset({"annotators", "blindToPrediction", "adjudication"}),
            label="annotation",
        )
        raw_annotators = mapping["annotators"]
        if not isinstance(raw_annotators, list):
            raise TypeError("annotators must be an array")
        if any(not isinstance(item, str) for item in raw_annotators):
            raise TypeError("annotator IDs must be strings")
        return cls(
            annotators=tuple(raw_annotators),
            blind_to_prediction=mapping["blindToPrediction"],
            adjudication=LabelAdjudication.from_mapping(mapping["adjudication"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "annotators": list(self.annotators),
            "blindToPrediction": self.blind_to_prediction,
            "adjudication": self.adjudication.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProductionIntentLabel:
    case_id: str
    session_group_id: str
    disposition: LabelDisposition
    expected_operations: tuple[IntentActionLabel, ...]
    provenance: LabelProvenance
    annotation: LabelAnnotation

    def __post_init__(self) -> None:
        _hmac_id(self.case_id, field_name="caseId")
        _hmac_id(self.session_group_id, field_name="sessionGroupId")
        if not isinstance(self.disposition, LabelDisposition):
            raise TypeError("disposition must be a LabelDisposition")
        if (
            not isinstance(self.expected_operations, tuple)
            or len(self.expected_operations) > _MAX_OPERATIONS
        ):
            raise ValueError("expectedOperations must contain at most four labels")
        if any(
            not isinstance(item, IntentActionLabel) for item in self.expected_operations
        ):
            raise TypeError("expectedOperations contains an invalid label")
        if not isinstance(self.provenance, LabelProvenance):
            raise TypeError("provenance must be a LabelProvenance")
        if not isinstance(self.annotation, LabelAnnotation):
            raise TypeError("annotation must be a LabelAnnotation")
        if self.disposition is LabelDisposition.ELIGIBLE:
            if not self.expected_operations:
                raise ValueError(
                    "eligible labels require at least one expected operation"
                )
            if not self.provenance.request_fingerprint_verified:
                raise ValueError(
                    "eligible labels require a verified request fingerprint"
                )
            if not self.annotation.blind_to_prediction:
                raise ValueError("eligible labels must be blind to model prediction")
            if self.annotation.adjudication.status is AdjudicationStatus.PENDING:
                raise ValueError("eligible labels cannot have pending adjudication")
        elif self.expected_operations:
            raise ValueError(
                "ambiguous/excluded labels cannot encode expected operations"
            )

    @classmethod
    def from_mapping(cls, value: object) -> ProductionIntentLabel:
        mapping = _require_mapping(value, label="label case")
        _require_exact_keys(
            mapping,
            frozenset(
                {
                    "caseId",
                    "sessionGroupId",
                    "disposition",
                    "expectedOperations",
                    "provenance",
                    "annotation",
                }
            ),
            label="label case",
        )
        raw_operations = mapping["expectedOperations"]
        if not isinstance(raw_operations, list):
            raise TypeError("expectedOperations must be an array")
        try:
            disposition = LabelDisposition(mapping["disposition"])
        except (TypeError, ValueError) as exc:
            raise ValueError("label case has an unsupported disposition") from exc
        return cls(
            case_id=mapping["caseId"],
            session_group_id=mapping["sessionGroupId"],
            disposition=disposition,
            expected_operations=tuple(
                IntentActionLabel.from_mapping(item) for item in raw_operations
            ),
            provenance=LabelProvenance.from_mapping(mapping["provenance"]),
            annotation=LabelAnnotation.from_mapping(mapping["annotation"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "sessionGroupId": self.session_group_id,
            "disposition": self.disposition.value,
            "expectedOperations": [item.to_dict() for item in self.expected_operations],
            "provenance": self.provenance.to_dict(),
            "annotation": self.annotation.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProductionIntentLabelManifest:
    dataset_id: str
    classifier_contract_version: str
    cases: tuple[ProductionIntentLabel, ...]

    def __post_init__(self) -> None:
        _safe_token(self.dataset_id, field_name="datasetId")
        _safe_token(
            self.classifier_contract_version,
            field_name="classifierContractVersion",
        )
        if self.classifier_contract_version != (
            INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION
        ):
            raise ValueError(
                "label manifest targets an unsupported classifier contract"
            )
        if not isinstance(self.cases, tuple) or not 1 <= len(self.cases) <= _MAX_CASES:
            raise ValueError("label manifest must contain between one and 100000 cases")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("label manifest caseId values must be unique")

    @classmethod
    def from_mapping(cls, value: object) -> ProductionIntentLabelManifest:
        mapping = _require_mapping(value, label="label manifest")
        _require_exact_keys(
            mapping,
            frozenset(
                {"schemaVersion", "datasetId", "classifierContractVersion", "cases"}
            ),
            label="label manifest",
        )
        if (
            isinstance(mapping["schemaVersion"], bool)
            or not isinstance(mapping["schemaVersion"], int)
            or mapping["schemaVersion"] != LABEL_MANIFEST_SCHEMA_VERSION
        ):
            raise ValueError("unsupported label manifest schemaVersion")
        raw_cases = mapping["cases"]
        if not isinstance(raw_cases, list):
            raise TypeError("label manifest cases must be an array")
        return cls(
            dataset_id=mapping["datasetId"],
            classifier_contract_version=mapping["classifierContractVersion"],
            cases=tuple(ProductionIntentLabel.from_mapping(item) for item in raw_cases),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": LABEL_MANIFEST_SCHEMA_VERSION,
            "datasetId": self.dataset_id,
            "classifierContractVersion": self.classifier_contract_version,
            "cases": [case.to_dict() for case in self.cases],
        }


def load_label_manifest(path: str | Path) -> ProductionIntentLabelManifest:
    return ProductionIntentLabelManifest.from_mapping(
        _read_json(Path(path), maximum_bytes=_MAX_LABEL_MANIFEST_BYTES)
    )


def write_label_manifest(
    path: str | Path, manifest: ProductionIntentLabelManifest
) -> None:
    if not isinstance(manifest, ProductionIntentLabelManifest):
        raise TypeError("manifest must be a ProductionIntentLabelManifest")
    _write_json_atomic(Path(path), manifest.to_dict())


@dataclass(frozen=True, slots=True)
class SessionTurnLocator:
    session_id: str
    user_message_index: int
    request_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.session_id, str)
            or _SESSION_ID_RE.fullmatch(self.session_id) is None
        ):
            raise ValueError("sessionId is invalid")
        _bounded_positive_int(
            self.user_message_index,
            field_name="userMessageIndex",
            minimum=0,
            maximum=_MAX_SESSION_MESSAGES - 1,
        )
        if (
            not isinstance(self.request_sha256, str)
            or _SHA256_RE.fullmatch(self.request_sha256) is None
        ):
            raise ValueError("requestSha256 must be a lowercase SHA-256 digest")

    @classmethod
    def from_mapping(cls, value: object) -> SessionTurnLocator:
        mapping = _require_mapping(value, label="source locator")
        _require_exact_keys(
            mapping,
            frozenset({"kind", "sessionId", "userMessageIndex", "requestSha256"}),
            label="source locator",
        )
        if mapping["kind"] != "session_turn":
            raise ValueError("source locator kind must be session_turn")
        return cls(
            session_id=mapping["sessionId"],
            user_message_index=mapping["userMessageIndex"],
            request_sha256=mapping["requestSha256"],
        )

    def to_dict(self) -> dict[str, str | int]:
        return {
            "kind": "session_turn",
            "sessionId": self.session_id,
            "userMessageIndex": self.user_message_index,
            "requestSha256": self.request_sha256,
        }


@dataclass(frozen=True, slots=True)
class ProductionIntentSource:
    case_id: str
    session_group_id: str
    locator: SessionTurnLocator

    def __post_init__(self) -> None:
        _hmac_id(self.case_id, field_name="caseId")
        _hmac_id(self.session_group_id, field_name="sessionGroupId")
        if not isinstance(self.locator, SessionTurnLocator):
            raise TypeError("locator must be a SessionTurnLocator")

    @classmethod
    def from_mapping(cls, value: object) -> ProductionIntentSource:
        mapping = _require_mapping(value, label="source case")
        _require_exact_keys(
            mapping,
            frozenset({"caseId", "sessionGroupId", "locator"}),
            label="source case",
        )
        return cls(
            case_id=mapping["caseId"],
            session_group_id=mapping["sessionGroupId"],
            locator=SessionTurnLocator.from_mapping(mapping["locator"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "sessionGroupId": self.session_group_id,
            "locator": self.locator.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProductionIntentSourceManifest:
    dataset_id: str
    cases: tuple[ProductionIntentSource, ...]

    def __post_init__(self) -> None:
        _safe_token(self.dataset_id, field_name="datasetId")
        if not isinstance(self.cases, tuple) or not 1 <= len(self.cases) <= _MAX_CASES:
            raise ValueError(
                "source manifest must contain between one and 100000 cases"
            )
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("source manifest caseId values must be unique")

    @classmethod
    def from_mapping(cls, value: object) -> ProductionIntentSourceManifest:
        mapping = _require_mapping(value, label="source manifest")
        _require_exact_keys(
            mapping,
            frozenset({"schemaVersion", "datasetId", "cases"}),
            label="source manifest",
        )
        if (
            isinstance(mapping["schemaVersion"], bool)
            or not isinstance(mapping["schemaVersion"], int)
            or mapping["schemaVersion"] != SOURCE_MANIFEST_SCHEMA_VERSION
        ):
            raise ValueError("unsupported source manifest schemaVersion")
        raw_cases = mapping["cases"]
        if not isinstance(raw_cases, list):
            raise TypeError("source manifest cases must be an array")
        return cls(
            dataset_id=mapping["datasetId"],
            cases=tuple(
                ProductionIntentSource.from_mapping(item) for item in raw_cases
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": SOURCE_MANIFEST_SCHEMA_VERSION,
            "datasetId": self.dataset_id,
            "cases": [case.to_dict() for case in self.cases],
        }


def load_source_manifest(path: str | Path) -> ProductionIntentSourceManifest:
    return ProductionIntentSourceManifest.from_mapping(
        _read_json(Path(path), maximum_bytes=_MAX_SOURCE_MANIFEST_BYTES)
    )


def write_source_manifest(
    path: str | Path, manifest: ProductionIntentSourceManifest
) -> None:
    """Write the private locator manifest; it still contains no request text."""

    if not isinstance(manifest, ProductionIntentSourceManifest):
        raise TypeError("manifest must be a ProductionIntentSourceManifest")
    _write_json_atomic(Path(path), manifest.to_dict())


@dataclass(frozen=True, slots=True, repr=False)
class ReplayRequest:
    """Transient request material.  This type intentionally has no serializer."""

    text: str
    context: IntentContext

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("reconstructed request text must be non-empty")
        if not isinstance(self.context, IntentContext):
            raise TypeError("context must be an IntentContext")

    def as_semantic_request(self) -> SemanticIntentRequest:
        return SemanticIntentRequest(
            text=self.text,
            context=self.context,
            deterministic_operations=(),
        )


SessionLoader = Callable[[str], object | None]


def reconstruct_session_turn(
    locator: SessionTurnLocator,
    *,
    session_loader: SessionLoader | None = None,
) -> ReplayRequest:
    """Reconstruct and fingerprint-check one exact local production turn."""

    if not isinstance(locator, SessionTurnLocator):
        raise TypeError("locator must be a SessionTurnLocator")
    if session_loader is None:
        from minicode.session import load_session

        session_loader = load_session
    session = session_loader(locator.session_id)
    messages = getattr(session, "messages", None)
    if session is None or not isinstance(messages, list):
        raise ValueError("source Session is unavailable")
    if len(messages) > _MAX_SESSION_MESSAGES:
        raise ValueError("source Session contains too many messages")
    if locator.user_message_index >= len(messages):
        raise ValueError("source user message index is out of range")
    message = messages[locator.user_message_index]
    if not isinstance(message, Mapping) or message.get("role") != "user":
        raise ValueError("source locator does not identify a user message")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise ValueError("source user message has no textual content")
    actual_fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual_fingerprint, locator.request_sha256):
        raise ValueError("source request fingerprint mismatch")
    return ReplayRequest(
        text=content,
        context=IntentContext.from_messages(messages[: locator.user_message_index]),
    )


def discover_session_turn_source(
    session_id: str,
    user_message_index: int,
    *,
    hmac_key: bytes,
    session_loader: SessionLoader | None = None,
) -> ProductionIntentSource:
    """Discover one exact source locator without returning or writing its text."""

    provisional = SessionTurnLocator(
        session_id=session_id,
        user_message_index=user_message_index,
        request_sha256="0" * 64,
    )
    if session_loader is None:
        from minicode.session import load_session

        session_loader = load_session
    session = session_loader(session_id)
    messages = getattr(session, "messages", None)
    if session is None or not isinstance(messages, list):
        raise ValueError("source Session is unavailable")
    if provisional.user_message_index >= len(messages):
        raise ValueError("source user message index is out of range")
    message = messages[provisional.user_message_index]
    if not isinstance(message, Mapping) or message.get("role") != "user":
        raise ValueError("source locator does not identify a user message")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise ValueError("source user message has no textual content")
    request_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    locator = SessionTurnLocator(
        session_id=session_id,
        user_message_index=user_message_index,
        request_sha256=request_sha256,
    )
    return ProductionIntentSource(
        case_id=stable_case_id(
            hmac_key,
            session_id=session_id,
            user_message_index=user_message_index,
            request_sha256=request_sha256,
        ),
        session_group_id=stable_session_group_id(hmac_key, session_id),
        locator=locator,
    )


@dataclass(frozen=True, slots=True)
class ReplayPrediction:
    status: PredictionStatus
    operations: tuple[IntentActionLabel, ...] = ()
    minimum_raw_confidence: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, PredictionStatus):
            raise TypeError("status must be a PredictionStatus")
        if (
            not isinstance(self.operations, tuple)
            or len(self.operations) > _MAX_OPERATIONS
        ):
            raise ValueError("prediction operations must contain at most four items")
        if any(not isinstance(item, IntentActionLabel) for item in self.operations):
            raise TypeError("prediction operations contain an invalid item")
        confidence = self.minimum_raw_confidence
        if confidence is not None:
            object.__setattr__(
                self,
                "minimum_raw_confidence",
                _probability(confidence, field_name="minimum_raw_confidence"),
            )
        if self.status in {
            PredictionStatus.ACCEPTED,
            PredictionStatus.LOW_CONFIDENCE,
        }:
            if not self.operations or confidence is None:
                raise ValueError("confidence-bearing status requires operations")
        elif self.operations or confidence is not None:
            raise ValueError(
                "non-predictive status cannot contain operations/confidence"
            )


def decode_semantic_classifier_content(
    content: object,
    *,
    acceptance_threshold: float,
    max_output_bytes: int,
) -> ReplayPrediction:
    """Strictly decode the runtime classifier's schema-v1 assistant content."""

    threshold = _probability(acceptance_threshold, field_name="acceptance_threshold")
    maximum = _bounded_positive_int(
        max_output_bytes,
        field_name="max_output_bytes",
        minimum=256,
        maximum=16_384,
    )
    if not isinstance(content, str) or not content:
        return ReplayPrediction(PredictionStatus.INVALID_RESPONSE)
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError:
        return ReplayPrediction(PredictionStatus.INVALID_RESPONSE)
    if len(encoded) > maximum:
        return ReplayPrediction(PredictionStatus.INVALID_RESPONSE)
    try:
        document = json.loads(
            content,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json,
        )
        mapping = _require_mapping(document, label="semantic response")
        _require_exact_keys(
            mapping,
            frozenset({"schemaVersion", "operations"}),
            label="semantic response",
        )
        if (
            isinstance(mapping["schemaVersion"], bool)
            or not isinstance(mapping["schemaVersion"], int)
            or mapping["schemaVersion"] != 1
        ):
            raise ValueError("unsupported semantic response schemaVersion")
        raw_operations = mapping["operations"]
        if (
            not isinstance(raw_operations, list)
            or len(raw_operations) > _MAX_OPERATIONS
        ):
            raise ValueError("semantic response operations are invalid")
        if not raw_operations:
            return ReplayPrediction(PredictionStatus.ABSTAINED)
        operations: list[IntentActionLabel] = []
        confidences: list[float] = []
        for raw_operation in raw_operations:
            operation = _require_mapping(raw_operation, label="semantic operation")
            _require_exact_keys(
                operation,
                frozenset({"intent", "action", "confidence"}),
                label="semantic operation",
            )
            operations.append(
                IntentActionLabel(
                    intent_type=IntentType(operation["intent"]),
                    action_type=ActionType(operation["action"]),
                )
            )
            confidences.append(
                _probability(operation["confidence"], field_name="confidence")
            )
    except (TypeError, ValueError, json.JSONDecodeError):
        return ReplayPrediction(PredictionStatus.INVALID_RESPONSE)
    minimum = min(confidences)
    status = (
        PredictionStatus.ACCEPTED
        if minimum >= threshold
        else PredictionStatus.LOW_CONFIDENCE
    )
    return ReplayPrediction(status, tuple(operations), minimum)


def validate_loopback_openai_base_url(base_url: object) -> str:
    """Accept only an explicit HTTP(S) loopback endpoint with no credentials."""

    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base_url must be explicit")
    normalized = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").casefold() not in _LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("semantic replay endpoint must be explicit loopback HTTP(S)")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("semantic replay endpoint has an invalid port") from exc
    return normalized


def _chat_completions_url(base_url: str) -> str:
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


@dataclass(frozen=True, slots=True)
class LocalOpenAIReplayConfig:
    base_url: str
    model: str
    timeout_seconds: float = 5.0
    acceptance_threshold: float = 0.72
    max_input_chars: int = 4000
    max_output_bytes: int = 4096
    max_output_tokens: int = 256
    max_envelope_bytes: int = 128 * 1024
    api_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "base_url", validate_loopback_openai_base_url(self.base_url)
        )
        if (
            not isinstance(self.model, str)
            or _MODEL_KEY_RE.fullmatch(self.model) is None
        ):
            raise ValueError("model must be an explicit safe model identifier")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, Real)
            or not math.isfinite(float(self.timeout_seconds))
            or not 0.05 <= float(self.timeout_seconds) <= 30.0
        ):
            raise ValueError("timeout_seconds must be between 0.05 and 30")
        threshold = _probability(
            self.acceptance_threshold, field_name="acceptance_threshold"
        )
        if not 0.5 <= threshold <= 0.85:
            raise ValueError("acceptance_threshold must be between 0.5 and 0.85")
        _bounded_positive_int(
            self.max_input_chars,
            field_name="max_input_chars",
            minimum=256,
            maximum=20_000,
        )
        _bounded_positive_int(
            self.max_output_bytes,
            field_name="max_output_bytes",
            minimum=256,
            maximum=16_384,
        )
        _bounded_positive_int(
            self.max_output_tokens,
            field_name="max_output_tokens",
            minimum=32,
            maximum=1024,
        )
        _bounded_positive_int(
            self.max_envelope_bytes,
            field_name="max_envelope_bytes",
            minimum=1024,
            maximum=1_000_000,
        )
        if not isinstance(self.api_key, str) or len(self.api_key) > 4096:
            raise ValueError("api_key must be a bounded string")


class LocalOpenAIIntentPredictor:
    """One-shot classifier client that cannot leave the loopback interface."""

    def __init__(
        self,
        config: LocalOpenAIReplayConfig,
        *,
        opener_factory: Callable[[], Any] | None = None,
    ) -> None:
        if not isinstance(config, LocalOpenAIReplayConfig):
            raise TypeError("config must be a LocalOpenAIReplayConfig")
        # Config validation, including the loopback guard, happens before an
        # opener can be created or any request material can be reconstructed.
        self._config = config
        factory = opener_factory or (
            lambda: urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                _NoRedirectHandler(),
            )
        )
        self._opener = factory()

    @property
    def model_key(self) -> str:
        return self._config.model

    def bounded_payload(self, request: ReplayRequest) -> str:
        """Return the exact bounded user payload sent by :meth:`predict`."""

        if not isinstance(request, ReplayRequest):
            raise TypeError("request must be a ReplayRequest")
        from minicode.intent_semantic_classifier import (
            _bounded_payload as runtime_bounded_payload,
        )

        return runtime_bounded_payload(
            request.as_semantic_request(),
            self._config.max_input_chars,
        )

    def predict(self, request: ReplayRequest) -> ReplayPrediction:
        if not isinstance(request, ReplayRequest):
            raise TypeError("request must be a ReplayRequest")
        # Reuse the production classifier's prompt and bounded payload so the
        # historical challenger exercises the same semantic contract.
        from minicode.intent_semantic_classifier import (
            _SYSTEM_PROMPT as runtime_system_prompt,
        )

        body = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": runtime_system_prompt},
                {
                    "role": "user",
                    "content": self.bounded_payload(request),
                },
            ],
            "max_tokens": self._config.max_output_tokens,
            "temperature": 0.0,
        }
        headers = {"content-type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        http_request = urllib.request.Request(
            _chat_completions_url(self._config.base_url),
            data=json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            response = self._opener.open(
                http_request,
                timeout=float(self._config.timeout_seconds),
            )
            try:
                encoded = response.read(self._config.max_envelope_bytes + 1)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            if not isinstance(encoded, bytes) or len(encoded) > (
                self._config.max_envelope_bytes
            ):
                return ReplayPrediction(PredictionStatus.INVALID_RESPONSE)
            document = json.loads(
                encoded.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite_json,
            )
            envelope = _require_mapping(document, label="provider response")
            choices = envelope.get("choices")
            if not isinstance(choices, list) or not choices:
                return ReplayPrediction(PredictionStatus.INVALID_RESPONSE)
            first = _require_mapping(choices[0], label="provider choice")
            message = _require_mapping(first.get("message"), label="provider message")
            if message.get("tool_calls"):
                return ReplayPrediction(PredictionStatus.INVALID_RESPONSE)
            content = message.get("content")
        except (
            UnicodeDecodeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ):
            return ReplayPrediction(PredictionStatus.PROVIDER_ERROR)
        return decode_semantic_classifier_content(
            content,
            acceptance_threshold=self._config.acceptance_threshold,
            max_output_bytes=self._config.max_output_bytes,
        )


class IntentReplayPredictor(Protocol):
    @property
    def model_key(self) -> str: ...

    def bounded_payload(self, request: ReplayRequest) -> str:
        """Return exactly the bounded payload that ``predict`` will send."""
        ...

    def predict(self, request: ReplayRequest) -> ReplayPrediction: ...


def deterministic_session_split(
    session_group_id: str,
    *,
    key: bytes,
    evaluation_fraction: float = 0.4,
    fit_split: str = "fit",
    evaluation_split: str = "holdout",
) -> str:
    """Assign all turns in one Session to the same deterministic split."""

    normalized_group = _hmac_id(session_group_id, field_name="sessionGroupId")
    if not isinstance(key, bytes) or len(key) < 16:
        raise ValueError("split key must contain at least 16 bytes")
    fraction = _probability(evaluation_fraction, field_name="evaluation_fraction")
    if fraction in {0.0, 1.0}:
        raise ValueError("evaluation_fraction must be strictly between 0 and 1")
    fit = _safe_token(fit_split, field_name="fit_split")
    evaluation = _safe_token(evaluation_split, field_name="evaluation_split")
    if fit == evaluation:
        raise ValueError("split labels must be distinct")
    digest = hmac.new(
        key,
        f"intent-production-split-v1\0{normalized_group}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(2**64)
    return evaluation if bucket < fraction else fit


def wilson_interval(
    successes: int,
    total: int,
    *,
    z: float = 1.959963984540054,
) -> tuple[float, float] | None:
    """Return a two-sided Wilson score interval for one binomial proportion."""

    if isinstance(successes, bool) or not isinstance(successes, int):
        raise TypeError("successes must be an integer")
    if isinstance(total, bool) or not isinstance(total, int):
        raise TypeError("total must be an integer")
    if total < 0 or successes < 0 or successes > total:
        raise ValueError("successes/total are inconsistent")
    if total == 0:
        return None
    proportion = successes / total
    z_squared = z * z
    denominator = 1.0 + (z_squared / total)
    center = (proportion + (z_squared / (2.0 * total))) / denominator
    half_width = (
        z
        * math.sqrt(
            (proportion * (1.0 - proportion) / total)
            + (z_squared / (4.0 * total * total))
        )
        / denominator
    )
    return (max(0.0, center - half_width), min(1.0, center + half_width))


@dataclass(frozen=True, slots=True)
class EvaluationCaseRecord:
    case_id: str
    session_group_id: str
    disposition: LabelDisposition
    split: str
    expected_operations: tuple[IntentActionLabel, ...]
    predicted_operations: tuple[IntentActionLabel, ...]
    prediction_status: PredictionStatus
    minimum_raw_confidence: float | None
    raw_exact_match: bool | None
    accepted_exact_match: bool | None
    covered: bool | None

    def to_dict(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "sessionGroupId": self.session_group_id,
            "disposition": self.disposition.value,
            "split": self.split,
            "expectedOperations": [
                operation.to_dict() for operation in self.expected_operations
            ],
            "predictedOperations": [
                operation.to_dict() for operation in self.predicted_operations
            ],
            "predictionStatus": self.prediction_status.value,
            "minimumRawConfidence": self.minimum_raw_confidence,
            "rawExactMatch": self.raw_exact_match,
            "acceptedExactMatch": self.accepted_exact_match,
            "covered": self.covered,
        }


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    labeled_case_count: int
    eligible_case_count: int
    accepted_count: int
    correct_count: int
    exact_match_accuracy: float | None
    exact_match_wilson_95: tuple[float, float] | None
    coverage: float | None
    selective_accuracy: float | None
    selective_accuracy_wilson_95: tuple[float, float] | None
    explicit_abstain_count: int
    low_confidence_count: int
    invalid_response_count: int
    provider_error_count: int
    model_error_count: int
    confidence_case_count: int
    brier_score: float | None

    def to_dict(self) -> dict[str, object]:
        def interval(value: tuple[float, float] | None) -> list[float] | None:
            return None if value is None else [value[0], value[1]]

        return {
            "labeledCaseCount": self.labeled_case_count,
            "eligibleCaseCount": self.eligible_case_count,
            "acceptedCount": self.accepted_count,
            "correctCount": self.correct_count,
            "exactMatchAccuracy": self.exact_match_accuracy,
            "exactMatchWilson95": interval(self.exact_match_wilson_95),
            "coverage": self.coverage,
            "selectiveAccuracy": self.selective_accuracy,
            "selectiveAccuracyWilson95": interval(self.selective_accuracy_wilson_95),
            "explicitAbstainCount": self.explicit_abstain_count,
            "lowConfidenceCount": self.low_confidence_count,
            "invalidResponseCount": self.invalid_response_count,
            "providerErrorCount": self.provider_error_count,
            "modelErrorCount": self.model_error_count,
            "confidenceCaseCount": self.confidence_case_count,
            "brierScore": self.brier_score,
        }


def _evaluate_metrics(
    records: Sequence[EvaluationCaseRecord],
) -> EvaluationMetrics:
    eligible = [
        record for record in records if record.disposition is LabelDisposition.ELIGIBLE
    ]
    accepted = [
        record
        for record in eligible
        if record.prediction_status is PredictionStatus.ACCEPTED
    ]
    correct = [record for record in eligible if record.accepted_exact_match]
    selective_correct = [record for record in accepted if record.accepted_exact_match]
    confidence_records = [
        record for record in eligible if record.minimum_raw_confidence is not None
    ]
    brier = None
    if confidence_records:
        brier = sum(
            (float(record.minimum_raw_confidence) - float(bool(record.raw_exact_match)))
            ** 2
            for record in confidence_records
        ) / len(confidence_records)
    eligible_count = len(eligible)
    accepted_count = len(accepted)
    correct_count = len(correct)
    return EvaluationMetrics(
        labeled_case_count=len(records),
        eligible_case_count=eligible_count,
        accepted_count=accepted_count,
        correct_count=correct_count,
        exact_match_accuracy=(
            correct_count / eligible_count if eligible_count else None
        ),
        exact_match_wilson_95=wilson_interval(correct_count, eligible_count),
        coverage=(accepted_count / eligible_count if eligible_count else None),
        selective_accuracy=(
            len(selective_correct) / accepted_count if accepted_count else None
        ),
        selective_accuracy_wilson_95=wilson_interval(
            len(selective_correct), accepted_count
        ),
        explicit_abstain_count=sum(
            record.prediction_status is PredictionStatus.ABSTAINED
            for record in eligible
        ),
        low_confidence_count=sum(
            record.prediction_status is PredictionStatus.LOW_CONFIDENCE
            for record in eligible
        ),
        invalid_response_count=sum(
            record.prediction_status is PredictionStatus.INVALID_RESPONSE
            for record in eligible
        ),
        provider_error_count=sum(
            record.prediction_status is PredictionStatus.PROVIDER_ERROR
            for record in eligible
        ),
        model_error_count=sum(
            record.prediction_status is PredictionStatus.MODEL_ERROR
            for record in eligible
        ),
        confidence_case_count=len(confidence_records),
        brier_score=brier,
    )


@dataclass(frozen=True, slots=True)
class ProductionIntentEvaluationReport:
    dataset_id: str
    classifier_contract_version: str
    model_key: str
    fit_split: str
    evaluation_split: str
    evaluation_fraction: float
    metrics: EvaluationMetrics
    cases: tuple[EvaluationCaseRecord, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": EVALUATION_SCHEMA_VERSION,
            "datasetId": self.dataset_id,
            "classifierContractVersion": self.classifier_contract_version,
            "modelKey": self.model_key,
            "splitPolicy": {
                "algorithm": "hmac-sha256-session-bounded-payload-components-v2",
                "fitSplit": self.fit_split,
                "evaluationSplit": self.evaluation_split,
                "evaluationFraction": self.evaluation_fraction,
            },
            "metrics": self.metrics.to_dict(),
            "cases": [case.to_dict() for case in self.cases],
        }


SourceReconstructor = Callable[[SessionTurnLocator], ReplayRequest]


def _private_split_token(key: bytes, *, namespace: bytes, value: str) -> bytes:
    """Create an in-memory-only grouping token for private split material."""

    return namespace + hmac.new(
        key,
        b"intent-production-split-private-v2\0"
        + namespace
        + b"\0"
        + value.encode("utf-8"),
        hashlib.sha256,
    ).digest()


def _component_split(
    private_tokens: set[bytes],
    *,
    key: bytes,
    evaluation_fraction: float,
    fit_split: str,
    evaluation_split: str,
) -> str:
    """Split one connected Session/payload component without exposing its keys."""

    if not private_tokens:
        raise ValueError("split component must contain private grouping tokens")
    component_material = b"".join(
        len(token).to_bytes(2, "big") + token for token in sorted(private_tokens)
    )
    digest = hmac.new(
        key,
        b"intent-production-split-component-v2\0" + component_material,
        hashlib.sha256,
    ).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(2**64)
    return evaluation_split if bucket < evaluation_fraction else fit_split


def evaluate_production_intents(
    labels: ProductionIntentLabelManifest,
    sources: ProductionIntentSourceManifest,
    *,
    predictor: IntentReplayPredictor,
    split_key: bytes,
    evaluation_fraction: float = 0.4,
    fit_split: str = "fit",
    evaluation_split: str = "holdout",
    reconstruct: SourceReconstructor = reconstruct_session_turn,
) -> ProductionIntentEvaluationReport:
    """Replay eligible labels and retain every abstention/error in metrics."""

    if not isinstance(labels, ProductionIntentLabelManifest):
        raise TypeError("labels must be a ProductionIntentLabelManifest")
    if not isinstance(sources, ProductionIntentSourceManifest):
        raise TypeError("sources must be a ProductionIntentSourceManifest")
    if labels.dataset_id != sources.dataset_id:
        raise ValueError("label/source datasetId mismatch")
    model_key = getattr(predictor, "model_key", None)
    if not isinstance(model_key, str) or _MODEL_KEY_RE.fullmatch(model_key) is None:
        raise ValueError("predictor must expose a safe explicit model_key")
    bounded_payload = getattr(predictor, "bounded_payload", None)
    if not callable(bounded_payload):
        raise ValueError("predictor must expose its exact bounded_payload builder")
    if not isinstance(split_key, bytes) or len(split_key) < 16:
        raise ValueError("split key must contain at least 16 bytes")
    fraction = _probability(
        evaluation_fraction,
        field_name="evaluation_fraction",
    )
    if fraction in {0.0, 1.0}:
        raise ValueError("evaluation_fraction must be strictly between 0 and 1")
    fit = _safe_token(fit_split, field_name="fit_split")
    evaluation = _safe_token(evaluation_split, field_name="evaluation_split")
    if fit == evaluation:
        raise ValueError("split labels must be distinct")
    source_by_id = {source.case_id: source for source in sources.cases}
    label_ids = {label.case_id for label in labels.cases}
    if label_ids != set(source_by_id):
        raise ValueError(
            "label/source manifests must contain exactly the same case IDs"
        )

    case_count = len(labels.cases)
    parents = list(range(case_count))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first_index: int, second_index: int) -> None:
        first_root = find(first_index)
        second_root = find(second_index)
        if first_root != second_root:
            parents[second_root] = first_root

    prepared_requests: dict[str, ReplayRequest] = {}
    case_private_tokens: list[set[bytes]] = []
    session_owner: dict[bytes, int] = {}
    payload_owner: dict[bytes, int] = {}

    # Form connected components before making any model calls.  A component
    # joins every turn from one real Session and also joins Sessions whenever
    # the exact bounded payload presented to the model is identical.  Only
    # keyed HMAC tokens survive beyond the current loop iteration.
    for index, label in enumerate(labels.cases):
        source = source_by_id[label.case_id]
        if source.session_group_id != label.session_group_id:
            raise ValueError("label/source sessionGroupId mismatch")
        session_token = _private_split_token(
            split_key,
            namespace=b"session",
            value=source.locator.session_id,
        )
        private_tokens = {session_token}
        prior_session_index = session_owner.setdefault(session_token, index)
        union(index, prior_session_index)
        if label.disposition is LabelDisposition.ELIGIBLE:
            try:
                replay_request = reconstruct(source.locator)
            except Exception as exc:
                raise ValueError(
                    f"could not reconstruct exact source for case {label.case_id}"
                ) from exc
            try:
                model_payload = bounded_payload(replay_request)
            except Exception as exc:
                raise ValueError(
                    f"could not build bounded payload for case {label.case_id}"
                ) from exc
            if not isinstance(model_payload, str):
                raise TypeError("predictor bounded_payload must return a string")
            payload_token = _private_split_token(
                split_key,
                namespace=b"bounded-payload",
                value=model_payload,
            )
            private_tokens.add(payload_token)
            prior_payload_index = payload_owner.setdefault(payload_token, index)
            union(index, prior_payload_index)
            prepared_requests[label.case_id] = replay_request
        case_private_tokens.append(private_tokens)

    component_private_tokens: dict[int, set[bytes]] = {}
    for index, private_tokens in enumerate(case_private_tokens):
        component_private_tokens.setdefault(find(index), set()).update(private_tokens)
    component_splits = {
        root: _component_split(
            private_tokens,
            key=split_key,
            evaluation_fraction=fraction,
            fit_split=fit,
            evaluation_split=evaluation,
        )
        for root, private_tokens in component_private_tokens.items()
    }

    records: list[EvaluationCaseRecord] = []
    for index, label in enumerate(labels.cases):
        split = component_splits[find(index)]
        if label.disposition is not LabelDisposition.ELIGIBLE:
            records.append(
                EvaluationCaseRecord(
                    case_id=label.case_id,
                    session_group_id=label.session_group_id,
                    disposition=label.disposition,
                    split=split,
                    expected_operations=(),
                    predicted_operations=(),
                    prediction_status=PredictionStatus.NOT_EVALUATED,
                    minimum_raw_confidence=None,
                    raw_exact_match=None,
                    accepted_exact_match=None,
                    covered=None,
                )
            )
            continue
        replay_request = prepared_requests[label.case_id]
        try:
            prediction = predictor.predict(replay_request)
            if not isinstance(prediction, ReplayPrediction):
                raise TypeError("predictor returned an invalid result")
        except Exception:  # noqa: BLE001 - model failure remains in denominator
            prediction = ReplayPrediction(PredictionStatus.MODEL_ERROR)
        raw_exact = bool(
            prediction.operations and prediction.operations == label.expected_operations
        )
        covered = prediction.status is PredictionStatus.ACCEPTED
        accepted_exact = bool(covered and raw_exact)
        records.append(
            EvaluationCaseRecord(
                case_id=label.case_id,
                session_group_id=label.session_group_id,
                disposition=label.disposition,
                split=split,
                expected_operations=label.expected_operations,
                predicted_operations=prediction.operations,
                prediction_status=prediction.status,
                minimum_raw_confidence=prediction.minimum_raw_confidence,
                raw_exact_match=raw_exact,
                accepted_exact_match=accepted_exact,
                covered=covered,
            )
        )
    frozen_records = tuple(records)
    return ProductionIntentEvaluationReport(
        dataset_id=labels.dataset_id,
        classifier_contract_version=labels.classifier_contract_version,
        model_key=model_key,
        fit_split=fit,
        evaluation_split=evaluation,
        evaluation_fraction=fraction,
        metrics=_evaluate_metrics(frozen_records),
        cases=frozen_records,
    )


def calibration_observations(
    report: ProductionIntentEvaluationReport,
) -> tuple[dict[str, float | bool | str], ...]:
    """Export exact calibration fields for confidence-bearing eligible cases."""

    if not isinstance(report, ProductionIntentEvaluationReport):
        raise TypeError("report must be a ProductionIntentEvaluationReport")
    return tuple(
        {
            "rawConfidence": float(record.minimum_raw_confidence),
            "correct": bool(record.raw_exact_match),
            "split": record.split,
        }
        for record in report.cases
        if record.disposition is LabelDisposition.ELIGIBLE
        and record.minimum_raw_confidence is not None
    )


def write_evaluation_report(
    path: str | Path, report: ProductionIntentEvaluationReport
) -> None:
    if not isinstance(report, ProductionIntentEvaluationReport):
        raise TypeError("report must be a ProductionIntentEvaluationReport")
    _write_json_atomic(Path(path), report.to_dict())


def write_calibration_observations(
    path: str | Path, report: ProductionIntentEvaluationReport
) -> None:
    _write_json_atomic(
        Path(path),
        {
            "schemaVersion": CALIBRATION_OBSERVATION_SCHEMA_VERSION,
            "observations": list(calibration_observations(report)),
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path, help="content-free reviewed labels")
    parser.add_argument("sources", type=Path, help="private local Session locators")
    parser.add_argument("report_output", type=Path)
    parser.add_argument("--calibration-output", type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--acceptance-threshold", type=float, default=0.72)
    parser.add_argument("--evaluation-fraction", type=float, default=0.4)
    parser.add_argument("--fit-split", default="fit")
    parser.add_argument("--evaluation-split", default="holdout")
    parser.add_argument(
        "--split-key-env",
        default="MINICODE_INTENT_EVAL_SPLIT_KEY",
        help="environment variable containing at least 16 bytes of split key",
    )
    parser.add_argument(
        "--api-key-env",
        default="",
        help="optional environment variable for a loopback server token",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    split_key_value = os.environ.get(args.split_key_env, "")
    if len(split_key_value.encode("utf-8")) < 16:
        parser.error(f"environment variable {args.split_key_env} must contain 16 bytes")
    api_key = os.environ.get(args.api_key_env, "") if args.api_key_env else ""
    try:
        config = LocalOpenAIReplayConfig(
            base_url=args.base_url,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
            acceptance_threshold=args.acceptance_threshold,
            api_key=api_key,
        )
        predictor = LocalOpenAIIntentPredictor(config)
        report = evaluate_production_intents(
            load_label_manifest(args.labels),
            load_source_manifest(args.sources),
            predictor=predictor,
            split_key=split_key_value.encode("utf-8"),
            evaluation_fraction=args.evaluation_fraction,
            fit_split=args.fit_split,
            evaluation_split=args.evaluation_split,
        )
        write_evaluation_report(args.report_output, report)
        if args.calibration_output is not None:
            write_calibration_observations(args.calibration_output, report)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            report.metrics.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


__all__ = [
    "AdjudicationStatus",
    "EvaluationCaseRecord",
    "EvaluationMetrics",
    "IntentActionLabel",
    "LabelAdjudication",
    "LabelAnnotation",
    "LabelDisposition",
    "LabelProvenance",
    "LocalOpenAIIntentPredictor",
    "LocalOpenAIReplayConfig",
    "PredictionStatus",
    "ProductionIntentEvaluationReport",
    "ProductionIntentLabel",
    "ProductionIntentLabelManifest",
    "ProductionIntentSource",
    "ProductionIntentSourceManifest",
    "ReconstructionKind",
    "ReplayPrediction",
    "ReplayRequest",
    "SessionTurnLocator",
    "SourceKind",
    "calibration_observations",
    "decode_semantic_classifier_content",
    "deterministic_session_split",
    "discover_session_turn_source",
    "evaluate_production_intents",
    "load_label_manifest",
    "load_source_manifest",
    "main",
    "reconstruct_session_turn",
    "stable_case_id",
    "stable_hmac_id",
    "stable_session_group_id",
    "validate_loopback_openai_base_url",
    "wilson_interval",
    "write_calibration_observations",
    "write_evaluation_report",
    "write_label_manifest",
    "write_source_manifest",
]


if __name__ == "__main__":
    raise SystemExit(main())
