"""Offline confidence calibration for the semantic intent fallback.

The runtime surface of this module is deliberately small: load a reviewed,
versioned artifact and map one raw confidence to a calibrated confidence.  The
fitting path accepts only numeric confidence/correctness observations; request
text, model output, and identifiers are not part of the schema.  Calibration is
never updated online.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from numbers import Real
from pathlib import Path
from typing import Any

CALIBRATION_SCHEMA_VERSION = 1
OBSERVATION_SCHEMA_VERSION = 1
INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION = "intent-semantic-classifier-v3"
_MAX_ARTIFACT_BYTES = 1_000_000
_MAX_OBSERVATION_BYTES = 8_000_000
_MAX_BINS = 256
_BOUNDARY_TOLERANCE = 1e-12
_CALIBRATION_VALUE_TOLERANCE = 1e-12
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SPLIT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z")
_MODEL_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}\Z")
_ARTIFACT_KEYS = frozenset(
    {
        "schemaVersion",
        "calibrationVersion",
        "classifierContractVersion",
        "modelKey",
        "fitMethod",
        "productionFit",
        "prior",
        "bins",
    }
)
_BIN_KEYS = frozenset(
    {
        "lowerBound",
        "upperBound",
        "calibratedConfidence",
        "sampleCount",
        "correctCount",
    }
)
_OBSERVATION_KEYS = frozenset({"rawConfidence", "correct", "split"})
_FIT_METHODS = frozenset({"bootstrap_conservative", "beta_binning_pav"})
_BOOTSTRAP_VERSION = "bootstrap-conservative-v1"
_BOOTSTRAP_BIN_SPEC = (
    (0.0, 0.60, 0.45, 0, 0),
    (0.60, 0.75, 0.58, 0, 0),
    (0.75, 0.90, 0.72, 0, 0),
    (0.90, 1.0, 0.82, 0, 0),
)


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    *,
    label: str,
    optional: frozenset[str] = frozenset(),
) -> None:
    keys = frozenset(value)
    required = expected - optional
    if not required.issubset(keys) or not keys.issubset(expected):
        raise ValueError(
            f"{label} must contain exactly the documented content-free fields"
        )


def _probability(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be finite")
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return normalized


def _positive_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and greater than 0")
    return normalized


def _nonnegative_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _validate_version(value: object) -> str:
    if not isinstance(value, str) or _VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "calibrationVersion must be 1-128 safe alphanumeric/version characters"
        )
    return value


def _validate_classifier_contract_version(value: object) -> str:
    if not isinstance(value, str) or _VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "classifierContractVersion must be 1-128 safe version characters"
        )
    return value


def _validate_model_key(value: object) -> str:
    if value == "*":
        return "*"
    if not isinstance(value, str) or _MODEL_KEY_PATTERN.fullmatch(value) is None:
        raise ValueError("modelKey must be a safe canonical model identifier")
    return value


def _validate_split(value: object, *, allow_none: bool = True) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or _SPLIT_PATTERN.fullmatch(value) is None:
        raise ValueError("split must be a short alphanumeric label")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON document contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"JSON document contains non-finite number {value}")


def _read_json(path: Path, *, max_bytes: int) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"could not read JSON document: {path}") from exc
    if len(raw) > max_bytes:
        raise ValueError(f"JSON document exceeds the {max_bytes}-byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("JSON document must be UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json,
        )
    except json.JSONDecodeError as exc:
        raise ValueError("JSON document is invalid") from exc


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _bin_index_from_upper_bounds(
    raw_confidence: float,
    upper_bounds: Sequence[float],
) -> int:
    """Apply the canonical ``[lower, upper)`` bin boundary comparison."""

    if not upper_bounds:
        raise ValueError("at least one bin boundary is required")
    final_index = len(upper_bounds) - 1
    for index, upper_bound in enumerate(upper_bounds):
        if raw_confidence < upper_bound or index == final_index:
            return index
    raise AssertionError("final bin boundary was not selected")


@dataclass(slots=True)
class _PAVBlock:
    start: int
    end: int
    weighted_sum: float
    weight: float

    @property
    def mean(self) -> float:
        return self.weighted_sum / self.weight


def _pool_adjacent_violators(
    values: Sequence[float], weights: Sequence[float]
) -> tuple[float, ...]:
    blocks: list[_PAVBlock] = []
    for index, (value, weight) in enumerate(zip(values, weights, strict=True)):
        blocks.append(_PAVBlock(index, index, value * weight, weight))
        while len(blocks) >= 2 and blocks[-2].mean > blocks[-1].mean:
            right = blocks.pop()
            left = blocks.pop()
            blocks.append(
                _PAVBlock(
                    start=left.start,
                    end=right.end,
                    weighted_sum=left.weighted_sum + right.weighted_sum,
                    weight=left.weight + right.weight,
                )
            )
    result = [0.0] * len(values)
    for block in blocks:
        for index in range(block.start, block.end + 1):
            result[index] = block.mean
    return tuple(result)


def _expected_beta_calibration(
    sample_counts: Sequence[int],
    correct_counts: Sequence[int],
    *,
    alpha: float,
    beta: float,
) -> tuple[float, ...]:
    posterior_weights = [count + alpha + beta for count in sample_counts]
    posterior_means = [
        (correct + alpha) / weight
        for correct, weight in zip(
            correct_counts,
            posterior_weights,
            strict=True,
        )
    ]
    return _pool_adjacent_violators(posterior_means, posterior_weights)


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    """One contiguous raw-confidence interval and its calibrated value.

    Intervals are ``[lower_bound, upper_bound)`` except that the final interval
    includes 1.0.
    """

    lower_bound: float
    upper_bound: float
    calibrated_confidence: float
    sample_count: int = 0
    correct_count: int = 0

    def __post_init__(self) -> None:
        lower = _probability(self.lower_bound, field_name="lowerBound")
        upper = _probability(self.upper_bound, field_name="upperBound")
        calibrated = _probability(
            self.calibrated_confidence,
            field_name="calibratedConfidence",
        )
        samples = _nonnegative_integer(self.sample_count, field_name="sampleCount")
        correct = _nonnegative_integer(self.correct_count, field_name="correctCount")
        if lower >= upper:
            raise ValueError("each calibration bin must have positive width")
        if correct > samples:
            raise ValueError("correctCount cannot exceed sampleCount")
        object.__setattr__(self, "lower_bound", lower)
        object.__setattr__(self, "upper_bound", upper)
        object.__setattr__(self, "calibrated_confidence", calibrated)
        object.__setattr__(self, "sample_count", samples)
        object.__setattr__(self, "correct_count", correct)

    @classmethod
    def from_mapping(cls, value: object) -> CalibrationBin:
        if not isinstance(value, Mapping):
            raise TypeError("each calibration bin must be an object")
        _require_exact_keys(value, _BIN_KEYS, label="calibration bin")
        return cls(
            lower_bound=value["lowerBound"],
            upper_bound=value["upperBound"],
            calibrated_confidence=value["calibratedConfidence"],
            sample_count=value["sampleCount"],
            correct_count=value["correctCount"],
        )

    def to_dict(self) -> dict[str, int | float]:
        return {
            "lowerBound": self.lower_bound,
            "upperBound": self.upper_bound,
            "calibratedConfidence": self.calibrated_confidence,
            "sampleCount": self.sample_count,
            "correctCount": self.correct_count,
        }


@dataclass(frozen=True, slots=True)
class IntentConfidenceCalibration:
    """Validated immutable confidence-calibration artifact."""

    version: str
    classifier_contract_version: str
    model_key: str
    fit_method: str
    production_fit: bool
    bins: tuple[CalibrationBin, ...]
    prior_alpha: float | None = None
    prior_beta: float | None = None
    schema_version: int = CALIBRATION_SCHEMA_VERSION
    _builtin: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != CALIBRATION_SCHEMA_VERSION
        ):
            raise ValueError(f"schemaVersion must be {CALIBRATION_SCHEMA_VERSION}")
        version = _validate_version(self.version)
        classifier_contract_version = _validate_classifier_contract_version(
            self.classifier_contract_version
        )
        model_key = _validate_model_key(self.model_key)
        if self.fit_method not in _FIT_METHODS:
            raise ValueError("fitMethod is unsupported")
        if not isinstance(self.production_fit, bool):
            raise TypeError("productionFit must be a boolean")
        if self.production_fit:
            raise ValueError(
                "schemaVersion 1 cannot verify productionFit provenance; it must be false"
            )
        if not isinstance(self.bins, tuple):
            raise TypeError("bins must be an immutable tuple")
        if not self.bins or len(self.bins) > _MAX_BINS:
            raise ValueError(f"bins must contain between 1 and {_MAX_BINS} entries")
        if not all(isinstance(item, CalibrationBin) for item in self.bins):
            raise TypeError("bins must contain CalibrationBin objects")
        if not isinstance(self._builtin, bool):
            raise TypeError("_builtin must be boolean")

        if self.fit_method == "bootstrap_conservative":
            if self.prior_alpha is not None or self.prior_beta is not None:
                raise ValueError(
                    "bootstrap calibration must not declare a fitted prior"
                )
        else:
            alpha = _positive_number(self.prior_alpha, field_name="prior.alpha")
            beta = _positive_number(self.prior_beta, field_name="prior.beta")
            object.__setattr__(self, "prior_alpha", alpha)
            object.__setattr__(self, "prior_beta", beta)

        first = self.bins[0]
        last = self.bins[-1]
        if not math.isclose(first.lower_bound, 0.0, abs_tol=_BOUNDARY_TOLERANCE):
            raise ValueError("calibration bins must start at 0")
        if not math.isclose(last.upper_bound, 1.0, abs_tol=_BOUNDARY_TOLERANCE):
            raise ValueError("calibration bins must end at 1")
        previous = first
        for current in self.bins[1:]:
            if not math.isclose(
                previous.upper_bound,
                current.lower_bound,
                rel_tol=0.0,
                abs_tol=_BOUNDARY_TOLERANCE,
            ):
                raise ValueError("calibration bins must be contiguous without gaps")
            if previous.calibrated_confidence > current.calibrated_confidence:
                raise ValueError("calibrated confidence must be monotonic")
            previous = current

        if self.fit_method == "bootstrap_conservative":
            signature = tuple(
                (
                    item.lower_bound,
                    item.upper_bound,
                    item.calibrated_confidence,
                    item.sample_count,
                    item.correct_count,
                )
                for item in self.bins
            )
            if (
                version != _BOOTSTRAP_VERSION
                or classifier_contract_version
                != INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION
                or model_key != "*"
                or signature != _BOOTSTRAP_BIN_SPEC
            ):
                raise ValueError(
                    "bootstrap calibration must exactly match the built-in "
                    "conservative specification"
                )
        else:
            sample_counts = tuple(item.sample_count for item in self.bins)
            correct_counts = tuple(item.correct_count for item in self.bins)
            if sum(sample_counts) == 0:
                raise ValueError(
                    "beta calibration must contain at least one labeled sample"
                )
            assert self.prior_alpha is not None
            assert self.prior_beta is not None
            expected = _expected_beta_calibration(
                sample_counts,
                correct_counts,
                alpha=self.prior_alpha,
                beta=self.prior_beta,
            )
            if any(
                not math.isclose(
                    item.calibrated_confidence,
                    expected_confidence,
                    rel_tol=0.0,
                    abs_tol=_CALIBRATION_VALUE_TOLERANCE,
                )
                for item, expected_confidence in zip(
                    self.bins,
                    expected,
                    strict=True,
                )
            ):
                raise ValueError(
                    "calibratedConfidence must equal the Beta posterior plus "
                    "weighted PAV result derived from the artifact counts"
                )
        if self._builtin and self.fit_method != "bootstrap_conservative":
            raise ValueError("only the conservative bootstrap may be built in")
        if model_key == "*" and not self._builtin:
            raise ValueError("wildcard modelKey is reserved for the built-in bootstrap")
        object.__setattr__(self, "version", version)
        object.__setattr__(
            self,
            "classifier_contract_version",
            classifier_contract_version,
        )
        object.__setattr__(self, "model_key", model_key)

    @classmethod
    def from_mapping(cls, value: object) -> IntentConfidenceCalibration:
        if not isinstance(value, Mapping):
            raise TypeError("calibration artifact must be an object")
        _require_exact_keys(value, _ARTIFACT_KEYS, label="calibration artifact")
        schema_version = value["schemaVersion"]
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != CALIBRATION_SCHEMA_VERSION
        ):
            raise ValueError(f"schemaVersion must be {CALIBRATION_SCHEMA_VERSION}")
        raw_bins = value["bins"]
        if isinstance(raw_bins, (str, bytes)) or not isinstance(raw_bins, Sequence):
            raise TypeError("bins must be an array")
        prior = value["prior"]
        prior_alpha: float | None = None
        prior_beta: float | None = None
        if prior is not None:
            if not isinstance(prior, Mapping):
                raise TypeError("prior must be null or an object")
            _require_exact_keys(
                prior,
                frozenset({"alpha", "beta"}),
                label="calibration prior",
            )
            prior_alpha = prior["alpha"]
            prior_beta = prior["beta"]
        return cls(
            schema_version=schema_version,
            version=value["calibrationVersion"],
            classifier_contract_version=value["classifierContractVersion"],
            model_key=value["modelKey"],
            fit_method=value["fitMethod"],
            production_fit=value["productionFit"],
            prior_alpha=prior_alpha,
            prior_beta=prior_beta,
            bins=tuple(CalibrationBin.from_mapping(item) for item in raw_bins),
        )

    @classmethod
    def load(
        cls,
        path: str | Path | None,
        *,
        expected_classifier_contract_version: str = (
            INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION
        ),
        expected_model_key: str | None = None,
        allow_unreviewed_calibration: bool = False,
    ) -> IntentConfidenceCalibration:
        """Load and validate a calibration for one classifier/model runtime.

        A missing path selects the in-process conservative bootstrap. External
        schema-v1 artifacts are necessarily unreviewed and require the explicit
        ``allow_unreviewed_calibration`` experiment boundary.
        """

        if not isinstance(allow_unreviewed_calibration, bool):
            raise TypeError("allow_unreviewed_calibration must be boolean")
        if path is None:
            calibration = BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION
        else:
            payload = _read_json(Path(path), max_bytes=_MAX_ARTIFACT_BYTES)
            calibration = cls.from_mapping(payload)
        calibration.validate_for_runtime(
            classifier_contract_version=expected_classifier_contract_version,
            model_key=expected_model_key,
            allow_unreviewed_calibration=allow_unreviewed_calibration,
        )
        return calibration

    def validate_for_runtime(
        self,
        *,
        classifier_contract_version: str,
        model_key: str | None,
        allow_unreviewed_calibration: bool = False,
    ) -> None:
        """Fail closed when an artifact is not valid for this runtime."""

        expected_contract = _validate_classifier_contract_version(
            classifier_contract_version
        )
        if not isinstance(allow_unreviewed_calibration, bool):
            raise TypeError("allow_unreviewed_calibration must be boolean")
        if self.classifier_contract_version != expected_contract:
            raise ValueError("calibration classifier contract does not match runtime")
        if self._builtin:
            if self is not BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION:
                raise ValueError("unrecognized built-in calibration instance")
            return
        if model_key is None:
            raise ValueError(
                "runtime model key is unavailable for external calibration"
            )
        expected_model_key = _validate_model_key(model_key)
        if expected_model_key == "*" or self.model_key != expected_model_key:
            raise ValueError("calibration model key does not match runtime")
        if not self.production_fit and not allow_unreviewed_calibration:
            raise ValueError("external calibration is unreviewed and not allowed")

    def calibrate(self, raw_confidence: float) -> float:
        raw = _probability(raw_confidence, field_name="raw confidence")
        index = _bin_index_from_upper_bounds(
            raw,
            tuple(item.upper_bound for item in self.bins),
        )
        return self.bins[index].calibrated_confidence

    def to_dict(self) -> dict[str, Any]:
        prior: dict[str, float] | None = None
        if self.prior_alpha is not None and self.prior_beta is not None:
            prior = {"alpha": self.prior_alpha, "beta": self.prior_beta}
        return {
            "schemaVersion": self.schema_version,
            "calibrationVersion": self.version,
            "classifierContractVersion": self.classifier_contract_version,
            "modelKey": self.model_key,
            "fitMethod": self.fit_method,
            "productionFit": self.production_fit,
            "prior": prior,
            "bins": [item.to_dict() for item in self.bins],
        }

    def write(self, path: str | Path) -> None:
        _write_json_atomic(Path(path), self.to_dict())


BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION = IntentConfidenceCalibration(
    version=_BOOTSTRAP_VERSION,
    classifier_contract_version=INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
    model_key="*",
    fit_method="bootstrap_conservative",
    production_fit=False,
    bins=(
        CalibrationBin(0.0, 0.60, 0.45),
        CalibrationBin(0.60, 0.75, 0.58),
        CalibrationBin(0.75, 0.90, 0.72),
        CalibrationBin(0.90, 1.0, 0.82),
    ),
    _builtin=True,
)


@dataclass(frozen=True, slots=True)
class CalibrationObservation:
    """A content-free correctness label for one whole classifier response.

    For compound responses, ``raw_confidence`` is the minimum confidence over
    all proposed operations and ``correct`` is true only when the complete,
    ordered operation set is correct. Partial correctness must be labeled
    false so calibration matches the runtime's all-or-nothing acceptance rule.
    """

    raw_confidence: float
    correct: bool
    split: str | None = None

    def __post_init__(self) -> None:
        raw = _probability(self.raw_confidence, field_name="rawConfidence")
        if not isinstance(self.correct, bool):
            raise TypeError("correct must be a boolean")
        split = _validate_split(self.split)
        object.__setattr__(self, "raw_confidence", raw)
        object.__setattr__(self, "split", split)

    @classmethod
    def from_mapping(cls, value: object) -> CalibrationObservation:
        if not isinstance(value, Mapping):
            raise TypeError("each observation must be an object")
        _require_exact_keys(
            value,
            _OBSERVATION_KEYS,
            optional=frozenset({"split"}),
            label="observation",
        )
        return cls(
            raw_confidence=value["rawConfidence"],
            correct=value["correct"],
            split=value.get("split"),
        )

    def to_dict(self) -> dict[str, float | bool | str]:
        result: dict[str, float | bool | str] = {
            "rawConfidence": self.raw_confidence,
            "correct": self.correct,
        }
        if self.split is not None:
            result["split"] = self.split
        return result


def load_calibration_observations(
    path: str | Path,
) -> tuple[CalibrationObservation, ...]:
    """Load a strict content-free observation list from JSON."""

    payload = _read_json(Path(path), max_bytes=_MAX_OBSERVATION_BYTES)
    raw_observations: object
    if isinstance(payload, list):
        raw_observations = payload
    elif isinstance(payload, Mapping):
        _require_exact_keys(
            payload,
            frozenset({"schemaVersion", "observations"}),
            label="observation document",
        )
        schema_version = payload["schemaVersion"]
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != OBSERVATION_SCHEMA_VERSION
        ):
            raise ValueError(
                f"observation schemaVersion must be {OBSERVATION_SCHEMA_VERSION}"
            )
        raw_observations = payload["observations"]
    else:
        raise TypeError("observation document must be an array or versioned object")
    if isinstance(raw_observations, (str, bytes)) or not isinstance(
        raw_observations, Sequence
    ):
        raise TypeError("observations must be an array")
    return tuple(CalibrationObservation.from_mapping(item) for item in raw_observations)


def _selected_observations(
    observations: Sequence[CalibrationObservation],
    *,
    split: str | None,
) -> tuple[CalibrationObservation, ...]:
    selected_split = _validate_split(split)
    normalized: list[CalibrationObservation] = []
    for item in observations:
        if not isinstance(item, CalibrationObservation):
            raise TypeError("observations must contain CalibrationObservation objects")
        if selected_split is None or item.split == selected_split:
            normalized.append(item)
    if not normalized:
        suffix = "" if selected_split is None else f" for split {selected_split!r}"
        raise ValueError(f"at least one calibration observation is required{suffix}")
    return tuple(normalized)


def fit_confidence_calibration(
    observations: Sequence[CalibrationObservation],
    *,
    version: str,
    model_key: str,
    classifier_contract_version: str = (INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION),
    bin_count: int = 10,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
    split: str | None = None,
) -> IntentConfidenceCalibration:
    """Fit fixed-width Beta-smoothed bins and make them monotonic with PAV.

    The returned schema-v1 artifact is intentionally marked
    ``productionFit: false``. This numeric fitting function cannot establish
    dataset provenance or review status; a future provenance-bearing schema is
    required before an artifact can be production-fit.
    """

    validated_version = _validate_version(version)
    validated_contract_version = _validate_classifier_contract_version(
        classifier_contract_version
    )
    validated_model_key = _validate_model_key(model_key)
    if validated_model_key == "*":
        raise ValueError("external fitted calibration cannot use wildcard modelKey")
    if isinstance(bin_count, bool) or not isinstance(bin_count, int):
        raise TypeError("bin_count must be an integer")
    if not 2 <= bin_count <= _MAX_BINS:
        raise ValueError(f"bin_count must be between 2 and {_MAX_BINS}")
    alpha = _positive_number(prior_alpha, field_name="prior_alpha")
    beta = _positive_number(prior_beta, field_name="prior_beta")
    selected = _selected_observations(observations, split=split)

    sample_counts = [0] * bin_count
    correct_counts = [0] * bin_count
    upper_bounds = tuple((index + 1) / bin_count for index in range(bin_count))
    for observation in selected:
        index = _bin_index_from_upper_bounds(
            observation.raw_confidence,
            upper_bounds,
        )
        sample_counts[index] += 1
        correct_counts[index] += int(observation.correct)

    calibrated_values = _expected_beta_calibration(
        sample_counts,
        correct_counts,
        alpha=alpha,
        beta=beta,
    )
    bins = tuple(
        CalibrationBin(
            lower_bound=index / bin_count,
            upper_bound=(index + 1) / bin_count,
            calibrated_confidence=calibrated_values[index],
            sample_count=sample_counts[index],
            correct_count=correct_counts[index],
        )
        for index in range(bin_count)
    )
    return IntentConfidenceCalibration(
        version=validated_version,
        classifier_contract_version=validated_contract_version,
        model_key=validated_model_key,
        fit_method="beta_binning_pav",
        production_fit=False,
        prior_alpha=alpha,
        prior_beta=beta,
        bins=bins,
    )


@dataclass(frozen=True, slots=True)
class CalibrationEvaluation:
    """Content-free aggregate calibration and selective-routing metrics."""

    sample_count: int
    correct_count: int
    accepted_count: int
    accepted_correct_count: int
    raw_brier_score: float
    brier_score: float
    brier_improvement: float
    raw_expected_calibration_error: float
    expected_calibration_error: float
    coverage: float
    selective_accuracy: float | None
    selective_accuracy_lower_95: float | None
    acceptance_threshold: float

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "sampleCount": self.sample_count,
            "correctCount": self.correct_count,
            "acceptedCount": self.accepted_count,
            "acceptedCorrectCount": self.accepted_correct_count,
            "rawBrierScore": self.raw_brier_score,
            "brierScore": self.brier_score,
            "brierImprovement": self.brier_improvement,
            "rawExpectedCalibrationError": self.raw_expected_calibration_error,
            "expectedCalibrationError": self.expected_calibration_error,
            "coverage": self.coverage,
            "selectiveAccuracy": self.selective_accuracy,
            "selectiveAccuracyLower95": self.selective_accuracy_lower_95,
            "acceptanceThreshold": self.acceptance_threshold,
        }


def _wilson_lower_bound(successes: int, total: int) -> float | None:
    """Return the two-sided 95% Wilson interval's lower endpoint."""

    if total <= 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    z_squared = z * z
    denominator = 1.0 + z_squared / total
    center = proportion + z_squared / (2.0 * total)
    margin = z * math.sqrt(
        (proportion * (1.0 - proportion) + z_squared / (4.0 * total)) / total
    )
    return max(0.0, (center - margin) / denominator)


def evaluate_confidence_calibration(
    calibration: IntentConfidenceCalibration,
    observations: Sequence[CalibrationObservation],
    *,
    acceptance_threshold: float,
    split: str | None = None,
) -> CalibrationEvaluation:
    """Evaluate Brier score, ECE, and selective accuracy at one threshold."""

    if not isinstance(calibration, IntentConfidenceCalibration):
        raise TypeError("calibration must be an IntentConfidenceCalibration")
    threshold = _probability(
        acceptance_threshold,
        field_name="acceptance_threshold",
    )
    selected = _selected_observations(observations, split=split)
    raw_squared_error = 0.0
    squared_error = 0.0
    correct_total = 0
    accepted = 0
    accepted_correct = 0
    bin_raw_confidence_sum = [0.0] * len(calibration.bins)
    bin_confidence_sum = [0.0] * len(calibration.bins)
    bin_correct = [0] * len(calibration.bins)
    bin_samples = [0] * len(calibration.bins)

    for observation in selected:
        confidence = calibration.calibrate(observation.raw_confidence)
        label = int(observation.correct)
        correct_total += label
        raw_squared_error += (observation.raw_confidence - label) ** 2
        squared_error += (confidence - label) ** 2
        if confidence >= threshold:
            accepted += 1
            accepted_correct += label
        index = _bin_index_from_upper_bounds(
            observation.raw_confidence,
            tuple(item.upper_bound for item in calibration.bins),
        )
        bin_samples[index] += 1
        bin_correct[index] += label
        bin_raw_confidence_sum[index] += observation.raw_confidence
        bin_confidence_sum[index] += confidence

    total = len(selected)
    raw_ece = 0.0
    ece = 0.0
    for samples, correct, raw_confidence_sum, confidence_sum in zip(
        bin_samples,
        bin_correct,
        bin_raw_confidence_sum,
        bin_confidence_sum,
        strict=True,
    ):
        if samples:
            raw_ece += (samples / total) * abs(
                (correct / samples) - (raw_confidence_sum / samples)
            )
            ece += (samples / total) * abs(
                (correct / samples) - (confidence_sum / samples)
            )
    raw_brier = raw_squared_error / total
    calibrated_brier = squared_error / total
    return CalibrationEvaluation(
        sample_count=total,
        correct_count=correct_total,
        accepted_count=accepted,
        accepted_correct_count=accepted_correct,
        raw_brier_score=raw_brier,
        brier_score=calibrated_brier,
        brier_improvement=raw_brier - calibrated_brier,
        raw_expected_calibration_error=raw_ece,
        expected_calibration_error=ece,
        coverage=accepted / total,
        selective_accuracy=(accepted_correct / accepted) if accepted else None,
        selective_accuracy_lower_95=_wilson_lower_bound(
            accepted_correct,
            accepted,
        ),
        acceptance_threshold=threshold,
    )


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Strict content-free report for one fit/holdout calibration run."""

    calibration_version: str
    classifier_contract_version: str
    model_key: str
    production_fit: bool
    fit_split: str
    evaluation_split: str
    fit_metrics: CalibrationEvaluation
    evaluation_metrics: CalibrationEvaluation
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1 or isinstance(self.schema_version, bool):
            raise ValueError("report schema_version must be 1")
        object.__setattr__(
            self, "calibration_version", _validate_version(self.calibration_version)
        )
        object.__setattr__(
            self,
            "classifier_contract_version",
            _validate_classifier_contract_version(self.classifier_contract_version),
        )
        object.__setattr__(self, "model_key", _validate_model_key(self.model_key))
        if not isinstance(self.production_fit, bool):
            raise TypeError("report production_fit must be boolean")
        fit_split = _validate_split(self.fit_split, allow_none=False)
        evaluation_split = _validate_split(
            self.evaluation_split,
            allow_none=False,
        )
        if fit_split == evaluation_split:
            raise ValueError("fit and evaluation splits must be distinct")
        if not isinstance(self.fit_metrics, CalibrationEvaluation) or not isinstance(
            self.evaluation_metrics,
            CalibrationEvaluation,
        ):
            raise TypeError("report metrics must be CalibrationEvaluation values")
        object.__setattr__(self, "fit_split", fit_split)
        object.__setattr__(self, "evaluation_split", evaluation_split)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "calibrationVersion": self.calibration_version,
            "classifierContractVersion": self.classifier_contract_version,
            "modelKey": self.model_key,
            "productionFit": self.production_fit,
            "fitSplit": self.fit_split,
            "evaluationSplit": self.evaluation_split,
            "evaluationIsHoldout": True,
            "fitMetrics": self.fit_metrics.to_dict(),
            "evaluationMetrics": self.evaluation_metrics.to_dict(),
        }


def write_calibration_report(path: str | Path, report: CalibrationReport) -> None:
    """Atomically write one schema-checked, content-free offline report."""

    if not isinstance(report, CalibrationReport):
        raise TypeError("report must be a CalibrationReport")
    _write_json_atomic(Path(path), report.to_dict())


__all__ = [
    "BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION",
    "CALIBRATION_SCHEMA_VERSION",
    "INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION",
    "OBSERVATION_SCHEMA_VERSION",
    "CalibrationBin",
    "CalibrationEvaluation",
    "CalibrationObservation",
    "CalibrationReport",
    "IntentConfidenceCalibration",
    "evaluate_confidence_calibration",
    "fit_confidence_calibration",
    "load_calibration_observations",
    "write_calibration_report",
]
