from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from minicode.intent_semantic_calibration import (
    BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION,
    INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
    CalibrationObservation,
    CalibrationReport,
    IntentConfidenceCalibration,
    evaluate_confidence_calibration,
    fit_confidence_calibration,
    load_calibration_observations,
    write_calibration_report,
)
from scripts.calibrate_intent_semantics import main as calibration_main

_MODEL_KEY = "openai/gpt-4o-mini"


def _artifact_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "calibrationVersion": "test-v1",
        "classifierContractVersion": INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
        "modelKey": _MODEL_KEY,
        "fitMethod": "beta_binning_pav",
        "productionFit": False,
        "prior": {"alpha": 1.0, "beta": 1.0},
        "bins": [
            {
                "lowerBound": 0.0,
                "upperBound": 0.5,
                "calibratedConfidence": 0.5,
                "sampleCount": 2,
                "correctCount": 1,
            },
            {
                "lowerBound": 0.5,
                "upperBound": 1.0,
                "calibratedConfidence": 0.8,
                "sampleCount": 3,
                "correctCount": 3,
            },
        ],
    }


def test_bootstrap_is_conservative_and_explicitly_not_production_fit() -> None:
    calibration = BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION

    assert calibration.version == "bootstrap-conservative-v1"
    assert calibration.fit_method == "bootstrap_conservative"
    assert calibration.production_fit is False
    assert calibration.calibrate(0.59) == pytest.approx(0.45)
    assert calibration.calibrate(0.60) == pytest.approx(0.58)
    assert calibration.calibrate(0.75) == pytest.approx(0.72)
    assert calibration.calibrate(1.0) == pytest.approx(0.82)
    assert all(
        left.calibrated_confidence <= right.calibrated_confidence
        for left, right in zip(calibration.bins, calibration.bins[1:], strict=False)
    )


def test_artifact_round_trip_and_default_load(tmp_path: Path) -> None:
    calibration = IntentConfidenceCalibration.from_mapping(_artifact_payload())
    path = tmp_path / "calibration.json"
    calibration.write(path)

    loaded = IntentConfidenceCalibration.load(
        path,
        expected_model_key=_MODEL_KEY,
        allow_unreviewed_calibration=True,
    )

    assert loaded == calibration
    assert loaded.calibrate(0.0) == pytest.approx(0.5)
    assert loaded.calibrate(0.5) == pytest.approx(0.8)
    assert (
        IntentConfidenceCalibration.load(None)
        is BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION
    )
    assert json.loads(path.read_text(encoding="utf-8")) == calibration.to_dict()


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda value: value.update(schemaVersion=2), "schemaVersion"),
        (lambda value: value.update(schemaVersion=1.0), "schemaVersion"),
        (lambda value: value.update(unexpected=True), "exactly"),
        (lambda value: value["bins"][0].update(lowerBound=0.1), "start at 0"),
        (lambda value: value["bins"][1].update(lowerBound=0.6), "contiguous"),
        (lambda value: value["bins"][1].update(lowerBound=0.4), "contiguous"),
        (lambda value: value["bins"][1].update(upperBound=0.9), "end at 1"),
        (
            lambda value: value["bins"][1].update(calibratedConfidence=0.3),
            "monotonic",
        ),
        (lambda value: value["bins"][0].update(sampleCount=-1), "sampleCount"),
        (lambda value: value["bins"][0].update(correctCount=3), "correctCount"),
    ],
)
def test_artifact_validation_rejects_unsafe_shapes(mutate, match: str) -> None:
    payload = _artifact_payload()
    mutate(payload)

    with pytest.raises(ValueError, match=match):
        IntentConfidenceCalibration.from_mapping(payload)


@pytest.mark.parametrize(
    "bad_value", [math.nan, math.inf, -math.inf, True, -0.01, 1.01]
)
def test_confidence_validation_rejects_non_finite_boolean_and_out_of_range(
    bad_value: object,
) -> None:
    calibration = IntentConfidenceCalibration.from_mapping(_artifact_payload())

    with pytest.raises((TypeError, ValueError)):
        calibration.calibrate(bad_value)  # type: ignore[arg-type]


def test_load_rejects_duplicate_keys_and_json_non_finite_values(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schemaVersion":1,"schemaVersion":1,"calibrationVersion":"x",'
        '"fitMethod":"bootstrap_conservative","productionFit":false,'
        '"prior":null,"bins":[]}',
        encoding="utf-8",
    )
    non_finite = tmp_path / "non-finite.json"
    payload = _artifact_payload()
    encoded = json.dumps(payload).replace("0.5", "NaN", 1)
    non_finite.write_text(encoded, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        IntentConfidenceCalibration.load(duplicate)
    with pytest.raises(ValueError, match="non-finite"):
        IntentConfidenceCalibration.load(non_finite)


def test_observation_schema_is_content_free_and_strict() -> None:
    observation = CalibrationObservation.from_mapping(
        {"rawConfidence": 0.8, "correct": True, "split": "holdout"}
    )

    assert observation.raw_confidence == pytest.approx(0.8)
    assert observation.correct is True
    assert observation.split == "holdout"

    with pytest.raises(ValueError, match="content-free"):
        CalibrationObservation.from_mapping(
            {
                "rawConfidence": 0.8,
                "correct": True,
                "split": "holdout",
                "prompt": "private request text",
            }
        )
    with pytest.raises(TypeError, match="correct"):
        CalibrationObservation.from_mapping({"rawConfidence": 0.8, "correct": 1})
    with pytest.raises(ValueError, match="split"):
        CalibrationObservation.from_mapping(
            {"rawConfidence": 0.8, "correct": True, "split": "contains spaces"}
        )


def test_fit_uses_beta_shrinkage_and_pav_to_make_bins_monotonic() -> None:
    observations = [
        *[CalibrationObservation(0.1, True, "calibration") for _ in range(4)],
        *[CalibrationObservation(0.6, False, "calibration") for _ in range(4)],
        CalibrationObservation(0.9, True, "holdout"),
    ]

    calibration = fit_confidence_calibration(
        observations,
        version="offline-v1",
        model_key=_MODEL_KEY,
        bin_count=2,
        prior_alpha=1.0,
        prior_beta=1.0,
        split="calibration",
    )

    # Raw posteriors are 5/6 and 1/6, so PAV pools the inversion at 1/2.
    assert [item.calibrated_confidence for item in calibration.bins] == pytest.approx(
        [0.5, 0.5]
    )
    assert [item.sample_count for item in calibration.bins] == [4, 4]
    assert calibration.fit_method == "beta_binning_pav"
    assert calibration.production_fit is False
    assert calibration.prior_alpha == pytest.approx(1.0)
    assert calibration.prior_beta == pytest.approx(1.0)


def test_fit_uses_same_boundary_comparison_as_runtime_calibration() -> None:
    calibration = fit_confidence_calibration(
        [CalibrationObservation(0.58, True)],
        version="boundary-v1",
        model_key=_MODEL_KEY,
        bin_count=100,
    )

    # int(0.58 * 100) is 57 on common IEEE-754 implementations.  Both fitting
    # and runtime lookup define an exact boundary as belonging to the next bin.
    assert calibration.bins[57].sample_count == 0
    assert calibration.bins[58].sample_count == 1
    assert calibration.calibrate(0.58) == pytest.approx(
        calibration.bins[58].calibrated_confidence
    )


def test_schema_v1_rejects_unverifiable_production_fit_claim() -> None:
    payload = _artifact_payload()
    payload["productionFit"] = True

    with pytest.raises(ValueError, match="cannot verify productionFit provenance"):
        IntentConfidenceCalibration.from_mapping(payload)


def test_external_artifact_runtime_binding_is_exact_and_unreviewed_is_opt_in(
    tmp_path: Path,
) -> None:
    calibration = IntentConfidenceCalibration.from_mapping(_artifact_payload())
    path = tmp_path / "external.json"
    calibration.write(path)

    with pytest.raises(ValueError, match="unreviewed"):
        IntentConfidenceCalibration.load(path, expected_model_key=_MODEL_KEY)
    with pytest.raises(ValueError, match="model key"):
        IntentConfidenceCalibration.load(
            path,
            expected_model_key="anthropic/claude-test",
            allow_unreviewed_calibration=True,
        )
    with pytest.raises(ValueError, match="classifier contract"):
        IntentConfidenceCalibration.load(
            path,
            expected_classifier_contract_version="intent-semantic-classifier-v1",
            expected_model_key=_MODEL_KEY,
            allow_unreviewed_calibration=True,
        )

    assert (
        IntentConfidenceCalibration.load(
            path,
            expected_model_key=_MODEL_KEY,
            allow_unreviewed_calibration=True,
        )
        == calibration
    )


def test_wildcard_model_key_is_reserved_for_builtin_instance() -> None:
    payload = BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION.to_dict()

    with pytest.raises(ValueError, match="wildcard modelKey"):
        IntentConfidenceCalibration.from_mapping(payload)

    assert (
        IntentConfidenceCalibration.load(
            None,
            expected_model_key="any/runtime-model",
        )
        is BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION
    )
    with pytest.raises(ValueError, match="classifier contract"):
        IntentConfidenceCalibration.load(
            None,
            expected_classifier_contract_version="intent-semantic-classifier-v1",
            expected_model_key="any/runtime-model",
        )

    with pytest.raises(ValueError, match="wildcard"):
        fit_confidence_calibration(
            [CalibrationObservation(0.8, True)],
            version="external-wildcard-v1",
            model_key="*",
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(calibrationVersion="renamed-bootstrap"),
        lambda value: value.update(
            classifierContractVersion="intent-semantic-classifier-v1"
        ),
        lambda value: value.update(modelKey="openai/gpt-4o-mini"),
        lambda value: value["bins"][0].update(calibratedConfidence=0.46),
        lambda value: value["bins"][0].update(sampleCount=1),
        lambda value: value["bins"][0].update(correctCount=1, sampleCount=1),
    ],
)
def test_bootstrap_artifact_must_exactly_match_builtin_specification(mutate) -> None:
    payload = BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION.to_dict()
    mutate(payload)

    with pytest.raises(ValueError, match="exactly match"):
        IntentConfidenceCalibration.from_mapping(payload)


def test_beta_artifact_requires_labeled_samples() -> None:
    payload = _artifact_payload()
    for item in payload["bins"]:
        item.update(
            calibratedConfidence=0.5,
            sampleCount=0,
            correctCount=0,
        )

    with pytest.raises(ValueError, match="at least one labeled sample"):
        IntentConfidenceCalibration.from_mapping(payload)


def test_beta_artifact_rejects_mapping_not_recomputed_from_counts() -> None:
    payload = _artifact_payload()
    payload["bins"][0]["calibratedConfidence"] = 0.49

    with pytest.raises(ValueError, match="Beta posterior"):
        IntentConfidenceCalibration.from_mapping(payload)


def test_beta_artifact_accepts_recomputed_weighted_pav_mapping() -> None:
    payload = _artifact_payload()
    payload["bins"][0].update(
        calibratedConfidence=0.5,
        sampleCount=4,
        correctCount=4,
    )
    payload["bins"][1].update(
        calibratedConfidence=0.5,
        sampleCount=4,
        correctCount=0,
    )

    calibration = IntentConfidenceCalibration.from_mapping(payload)

    assert [item.calibrated_confidence for item in calibration.bins] == [0.5, 0.5]


def test_evaluate_reports_brier_ece_coverage_and_selective_accuracy() -> None:
    observations = [
        CalibrationObservation(0.1, True),
        CalibrationObservation(0.2, False),
        CalibrationObservation(0.9, True),
        CalibrationObservation(0.95, False),
    ]

    metrics = evaluate_confidence_calibration(
        BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION,
        observations,
        acceptance_threshold=0.72,
    )

    expected_brier = (
        (0.45 - 1.0) ** 2 + (0.45 - 0.0) ** 2 + (0.82 - 1.0) ** 2 + (0.82 - 0.0) ** 2
    ) / 4
    expected_ece = 0.5 * abs(0.5 - 0.45) + 0.5 * abs(0.5 - 0.82)
    assert metrics.sample_count == 4
    assert metrics.correct_count == 2
    assert metrics.accepted_count == 2
    assert metrics.accepted_correct_count == 1
    expected_raw_brier = (0.9**2 + 0.2**2 + 0.1**2 + 0.95**2) / 4
    assert metrics.raw_brier_score == pytest.approx(expected_raw_brier)
    assert metrics.brier_score == pytest.approx(expected_brier)
    assert metrics.brier_improvement == pytest.approx(
        expected_raw_brier - expected_brier
    )
    assert metrics.expected_calibration_error == pytest.approx(expected_ece)
    assert metrics.coverage == pytest.approx(0.5)
    assert metrics.selective_accuracy == pytest.approx(0.5)
    assert metrics.selective_accuracy_lower_95 == pytest.approx(0.0945312057)


def test_evaluate_empty_selection_reports_null_selective_accuracy() -> None:
    metrics = evaluate_confidence_calibration(
        BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION,
        [CalibrationObservation(0.1, True)],
        acceptance_threshold=0.9,
    )

    assert metrics.coverage == 0.0
    assert metrics.selective_accuracy is None
    assert metrics.selective_accuracy_lower_95 is None
    assert metrics.to_dict()["selectiveAccuracy"] is None


def test_report_writer_rejects_untyped_content_fields(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="CalibrationReport"):
        write_calibration_report(  # type: ignore[arg-type]
            tmp_path / "unsafe.json",
            {"prompt": "private request text"},
        )


def test_report_requires_distinct_fit_and_evaluation_splits() -> None:
    metrics = evaluate_confidence_calibration(
        BOOTSTRAP_INTENT_CONFIDENCE_CALIBRATION,
        [CalibrationObservation(0.9, True, "same")],
        acceptance_threshold=0.72,
        split="same",
    )

    with pytest.raises(ValueError, match="must be distinct"):
        CalibrationReport(
            calibration_version="candidate-v1",
            classifier_contract_version=(INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION),
            model_key=_MODEL_KEY,
            production_fit=False,
            fit_split="same",
            evaluation_split="same",
            fit_metrics=metrics,
            evaluation_metrics=metrics,
        )


def test_observation_loader_and_cli_emit_only_numeric_aggregate_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observations_path = tmp_path / "observations.json"
    artifact_path = tmp_path / "artifact.json"
    report_path = tmp_path / "report.json"
    observations_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "observations": [
                    {"rawConfidence": 0.2, "correct": False, "split": "calibration"},
                    {"rawConfidence": 0.8, "correct": True, "split": "calibration"},
                    {"rawConfidence": 0.9, "correct": True, "split": "holdout"},
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_calibration_observations(observations_path)
    assert len(loaded) == 3
    exit_code = calibration_main(
        [
            str(observations_path),
            str(artifact_path),
            "--version",
            "offline-test-v1",
            "--model-key",
            _MODEL_KEY,
            "--fit-split",
            "calibration",
            "--evaluation-split",
            "holdout",
            "--bin-count",
            "2",
            "--report-output",
            str(report_path),
        ]
    )

    assert exit_code == 0
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    stdout = json.loads(capsys.readouterr().out)
    assert artifact["calibrationVersion"] == "offline-test-v1"
    assert artifact["classifierContractVersion"] == (
        INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION
    )
    assert artifact["modelKey"] == _MODEL_KEY
    assert artifact["productionFit"] is False
    assert report["evaluationIsHoldout"] is True
    assert report["fitMetrics"]["sampleCount"] == 2
    assert report["evaluationMetrics"]["sampleCount"] == 1
    assert stdout == report
    serialized = json.dumps({"artifact": artifact, "report": report})
    assert "prompt" not in serialized
    assert "private request" not in serialized
