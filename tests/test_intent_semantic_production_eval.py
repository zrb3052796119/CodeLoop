from __future__ import annotations

import hashlib
import json
from collections import deque
from types import SimpleNamespace

import pytest

from minicode.intent_parser import ActionType, IntentType
from minicode.intent_semantic_calibration import (
    INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
    load_calibration_observations,
)
from minicode.intent_semantic_production_eval import (
    AdjudicationStatus,
    IntentActionLabel,
    LabelAdjudication,
    LabelAnnotation,
    LabelDisposition,
    LabelProvenance,
    LocalOpenAIIntentPredictor,
    LocalOpenAIReplayConfig,
    PredictionStatus,
    ProductionIntentLabel,
    ProductionIntentLabelManifest,
    ProductionIntentSourceManifest,
    ReconstructionKind,
    ReplayPrediction,
    ReplayRequest,
    SourceKind,
    calibration_observations,
    decode_semantic_classifier_content,
    deterministic_session_split,
    discover_session_turn_source,
    evaluate_production_intents,
    load_label_manifest,
    reconstruct_session_turn,
    stable_case_id,
    stable_session_group_id,
    validate_loopback_openai_base_url,
    wilson_interval,
    write_calibration_observations,
)

_ID_KEY = b"production-label-key-material"
_SPLIT_KEY = b"production-split-key-material"


def _operation(
    intent: IntentType = IntentType.QUESTION,
    action: ActionType = ActionType.READ,
) -> IntentActionLabel:
    return IntentActionLabel(intent, action)


def _annotation() -> LabelAnnotation:
    return LabelAnnotation(
        annotators=("annotator-a", "annotator-b"),
        blind_to_prediction=True,
        adjudication=LabelAdjudication(
            AdjudicationStatus.NOT_REQUIRED,
            None,
            None,
        ),
    )


def _label(
    source,
    *,
    disposition: LabelDisposition = LabelDisposition.ELIGIBLE,
    expected: tuple[IntentActionLabel, ...] | None = None,
) -> ProductionIntentLabel:
    if expected is None:
        expected = (_operation(),) if disposition is LabelDisposition.ELIGIBLE else ()
    return ProductionIntentLabel(
        case_id=source.case_id,
        session_group_id=source.session_group_id,
        disposition=disposition,
        expected_operations=expected,
        provenance=LabelProvenance(
            SourceKind.GATEWAY_TURN,
            ReconstructionKind.FINGERPRINT_COMMIT_MARKER,
            True,
        ),
        annotation=_annotation(),
    )


def _source(
    session_id: str,
    message: str,
    *,
    index: int = 0,
    prior_messages: list[dict[str, str]] | None = None,
):
    messages = list(prior_messages or [])
    while len(messages) < index:
        messages.append({"role": "assistant", "content": "prior"})
    messages.append({"role": "user", "content": message})
    session = SimpleNamespace(messages=messages)
    source = discover_session_turn_source(
        session_id,
        index,
        hmac_key=_ID_KEY,
        session_loader=lambda requested: session if requested == session_id else None,
    )
    return source, session


class _QueuePredictor:
    model_key = "local-fixture-model"

    def __init__(
        self,
        predictions: list[ReplayPrediction],
        *,
        max_input_chars: int = 4000,
    ) -> None:
        self.predictions = deque(predictions)
        self.seen_text: list[str] = []
        self.max_input_chars = max_input_chars

    def bounded_payload(self, request: ReplayRequest) -> str:
        from minicode.intent_semantic_classifier import _bounded_payload

        return _bounded_payload(
            request.as_semantic_request(),
            self.max_input_chars,
        )

    def predict(self, request: ReplayRequest) -> ReplayPrediction:
        self.seen_text.append(request.text)
        return self.predictions.popleft()


def test_hmac_ids_are_stable_domain_separated_and_content_free() -> None:
    request_hash = hashlib.sha256(b"private request").hexdigest()
    first = stable_case_id(
        _ID_KEY,
        session_id="session-1",
        user_message_index=3,
        request_sha256=request_hash,
    )
    second = stable_case_id(
        _ID_KEY,
        session_id="session-1",
        user_message_index=3,
        request_sha256=request_hash,
    )
    group = stable_session_group_id(_ID_KEY, "session-1")

    assert first == second
    assert first != group
    assert first.startswith("h1_") and len(first) == 67
    assert "session" not in first


def test_label_manifest_is_strict_and_rejects_text_field(tmp_path) -> None:
    source, _session = _source("session-1", "private prompt marker")
    manifest = ProductionIntentLabelManifest(
        dataset_id="production-pilot-v1",
        classifier_contract_version=INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
        cases=(_label(source),),
    )
    payload = manifest.to_dict()
    payload["cases"][0]["prompt"] = "private prompt marker"
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly the documented fields"):
        load_label_manifest(path)


def test_discovery_and_reconstruction_verify_exact_turn_without_serializing_text() -> (
    None
):
    marker = "SECRET-PRODUCTION-PROMPT-f58f"
    prior = [
        {"role": "user", "content": "earlier user context"},
        {"role": "assistant", "content": "earlier assistant context"},
    ]
    source, session = _source(
        "session-2",
        marker,
        index=2,
        prior_messages=prior,
    )

    serialized_source = json.dumps(source.to_dict(), sort_keys=True)
    assert marker not in serialized_source
    replay = reconstruct_session_turn(
        source.locator,
        session_loader=lambda _session_id: session,
    )
    assert replay.text == marker
    assert [turn.content for turn in replay.context.recent_turns] == [
        "earlier user context",
        "earlier assistant context",
    ]

    tampered = SimpleNamespace(
        messages=prior + [{"role": "user", "content": marker + " changed"}]
    )
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        reconstruct_session_turn(
            source.locator,
            session_loader=lambda _session_id: tampered,
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://localhost.evil.example/v1",
        "http://user:secret@127.0.0.1:11434/v1",
        "http://127.0.0.1:11434/v1?next=https://example.com",
    ],
)
def test_remote_or_credentialed_endpoint_is_rejected_before_opener(url: str) -> None:
    opened = 0

    def opener_factory():
        nonlocal opened
        opened += 1
        raise AssertionError("opener must not be constructed")

    with pytest.raises(ValueError, match="loopback"):
        config = LocalOpenAIReplayConfig(base_url=url, model="qwen-local")
        LocalOpenAIIntentPredictor(config, opener_factory=opener_factory)
    assert opened == 0


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:11434/v1/", "http://localhost:11434/v1"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://[::1]:8080/v1", "http://[::1]:8080/v1"),
    ],
)
def test_loopback_endpoint_variants_are_accepted(url: str, expected: str) -> None:
    assert validate_loopback_openai_base_url(url) == expected


def test_semantic_decoder_is_strict_and_preserves_order() -> None:
    content = json.dumps(
        {
            "schemaVersion": 1,
            "operations": [
                {"intent": "search", "action": "read", "confidence": 0.9},
                {
                    "intent": "document",
                    "action": "create",
                    "confidence": 0.8,
                },
            ],
        }
    )
    prediction = decode_semantic_classifier_content(
        content,
        acceptance_threshold=0.72,
        max_output_bytes=4096,
    )

    assert prediction.status is PredictionStatus.ACCEPTED
    assert prediction.minimum_raw_confidence == pytest.approx(0.8)
    assert prediction.operations == (
        _operation(IntentType.SEARCH, ActionType.READ),
        _operation(IntentType.DOCUMENT, ActionType.CREATE),
    )

    duplicate = '{"schemaVersion":1,"schemaVersion":1,"operations":[]}'
    assert (
        decode_semantic_classifier_content(
            duplicate,
            acceptance_threshold=0.72,
            max_output_bytes=4096,
        ).status
        is PredictionStatus.INVALID_RESPONSE
    )


def test_low_confidence_retains_categorical_prediction_for_calibration() -> None:
    prediction = decode_semantic_classifier_content(
        json.dumps(
            {
                "schemaVersion": 1,
                "operations": [
                    {
                        "intent": "question",
                        "action": "read",
                        "confidence": 0.61,
                    }
                ],
            }
        ),
        acceptance_threshold=0.72,
        max_output_bytes=4096,
    )
    assert prediction.status is PredictionStatus.LOW_CONFIDENCE
    assert prediction.operations == (_operation(),)
    assert prediction.minimum_raw_confidence == pytest.approx(0.61)


def test_evaluation_retains_abstentions_and_invalids_and_is_content_free() -> None:
    private_texts = [
        "SECRET-CASE-ONE-33d1",
        "SECRET-CASE-TWO-22a4",
        "SECRET-CASE-THREE-a921",
        "SECRET-CASE-FOUR-f901",
        "SECRET-EXCLUDED-8888",
    ]
    source_sessions = [
        _source(f"session-{index}", text) for index, text in enumerate(private_texts)
    ]
    sources = tuple(item[0] for item in source_sessions)
    sessions = {
        source.locator.session_id: session for source, session in source_sessions
    }
    label_manifest = ProductionIntentLabelManifest(
        dataset_id="production-pilot-v1",
        classifier_contract_version=INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
        cases=tuple(_label(source) for source in sources[:4])
        + (_label(sources[4], disposition=LabelDisposition.EXCLUDED),),
    )
    source_manifest = ProductionIntentSourceManifest(
        dataset_id="production-pilot-v1",
        cases=sources,
    )
    wrong = _operation(IntentType.CHAT, ActionType.READ)
    predictor = _QueuePredictor(
        [
            ReplayPrediction(PredictionStatus.ACCEPTED, (_operation(),), 0.9),
            ReplayPrediction(PredictionStatus.ACCEPTED, (wrong,), 0.8),
            ReplayPrediction(
                PredictionStatus.LOW_CONFIDENCE,
                (_operation(),),
                0.6,
            ),
            ReplayPrediction(PredictionStatus.INVALID_RESPONSE),
        ]
    )

    report = evaluate_production_intents(
        label_manifest,
        source_manifest,
        predictor=predictor,
        split_key=_SPLIT_KEY,
        reconstruct=lambda locator: reconstruct_session_turn(
            locator,
            session_loader=lambda session_id: sessions.get(session_id),
        ),
    )

    assert predictor.seen_text == private_texts[:4]
    assert report.metrics.labeled_case_count == 5
    assert report.metrics.eligible_case_count == 4
    assert report.metrics.accepted_count == 2
    assert report.metrics.correct_count == 1
    assert report.metrics.exact_match_accuracy == pytest.approx(0.25)
    assert report.metrics.coverage == pytest.approx(0.5)
    assert report.metrics.selective_accuracy == pytest.approx(0.5)
    assert report.metrics.low_confidence_count == 1
    assert report.metrics.invalid_response_count == 1
    assert report.metrics.brier_score == pytest.approx((0.01 + 0.64 + 0.16) / 3)
    assert report.cases[-1].prediction_status is PredictionStatus.NOT_EVALUATED

    serialized = json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
    for private_text in private_texts:
        assert private_text not in serialized
    assert "session-0" not in serialized


def test_ordered_exact_match_does_not_award_reversed_compound_prediction() -> None:
    source, session = _source("compound-session", "private compound request")
    first = _operation(IntentType.SEARCH, ActionType.READ)
    second = _operation(IntentType.DOCUMENT, ActionType.CREATE)
    labels = ProductionIntentLabelManifest(
        "compound-v1",
        INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
        (_label(source, expected=(first, second)),),
    )
    sources = ProductionIntentSourceManifest("compound-v1", (source,))
    predictor = _QueuePredictor(
        [ReplayPrediction(PredictionStatus.ACCEPTED, (second, first), 0.95)]
    )
    report = evaluate_production_intents(
        labels,
        sources,
        predictor=predictor,
        split_key=_SPLIT_KEY,
        reconstruct=lambda locator: reconstruct_session_turn(
            locator, session_loader=lambda _session_id: session
        ),
    )
    assert report.metrics.correct_count == 0
    assert report.metrics.exact_match_accuracy == 0.0
    assert report.cases[0].raw_exact_match is False


def test_split_is_deterministic_and_session_disjoint() -> None:
    group = stable_session_group_id(_ID_KEY, "shared-session")
    first = deterministic_session_split(group, key=_SPLIT_KEY)
    second = deterministic_session_split(group, key=_SPLIT_KEY)
    alternate_key = deterministic_session_split(
        group,
        key=b"another-valid-split-key",
    )

    assert first == second
    assert first in {"fit", "holdout"}
    assert alternate_key in {"fit", "holdout"}


def test_evaluation_split_groups_sessions_and_identical_bounded_payloads() -> None:
    shared_prefix = "x" * 256
    first_session_id = "component-session-a"
    first_group = stable_session_group_id(_ID_KEY, first_session_id)
    first_old_split = deterministic_session_split(first_group, key=_SPLIT_KEY)
    second_session_id = next(
        candidate
        for index in range(100)
        if deterministic_session_split(
            stable_session_group_id(
                _ID_KEY,
                (candidate := f"component-session-b-{index}"),
            ),
            key=_SPLIT_KEY,
        )
        != first_old_split
    )

    first_session = SimpleNamespace(
        messages=[
            {"role": "user", "content": shared_prefix + "LEFT-PRIVATE-SUFFIX"},
            {"role": "assistant", "content": "private intermediate context"},
            {"role": "user", "content": "different request in same Session"},
        ]
    )
    second_session = SimpleNamespace(
        messages=[
            {"role": "user", "content": shared_prefix + "RIGHT-PRIVATE-SUFFIX"}
        ]
    )
    first_source = discover_session_turn_source(
        first_session_id,
        0,
        hmac_key=_ID_KEY,
        session_loader=lambda _session_id: first_session,
    )
    same_session_source = discover_session_turn_source(
        first_session_id,
        2,
        hmac_key=_ID_KEY,
        session_loader=lambda _session_id: first_session,
    )
    duplicate_payload_source = discover_session_turn_source(
        second_session_id,
        0,
        hmac_key=_ID_KEY,
        session_loader=lambda _session_id: second_session,
    )
    all_sources = (first_source, same_session_source, duplicate_payload_source)
    labels = ProductionIntentLabelManifest(
        "component-split-v2",
        INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
        tuple(_label(source) for source in all_sources),
    )
    sources = ProductionIntentSourceManifest("component-split-v2", all_sources)
    predictor = _QueuePredictor(
        [
            ReplayPrediction(PredictionStatus.ACCEPTED, (_operation(),), 0.9),
            ReplayPrediction(PredictionStatus.ACCEPTED, (_operation(),), 0.9),
            ReplayPrediction(PredictionStatus.ACCEPTED, (_operation(),), 0.9),
        ],
        max_input_chars=256,
    )
    sessions = {
        first_session_id: first_session,
        second_session_id: second_session,
    }

    report = evaluate_production_intents(
        labels,
        sources,
        predictor=predictor,
        split_key=_SPLIT_KEY,
        reconstruct=lambda locator: reconstruct_session_turn(
            locator,
            session_loader=lambda session_id: sessions.get(session_id),
        ),
    )

    assert len({case.split for case in report.cases}) == 1
    first_payload = predictor.bounded_payload(
        reconstruct_session_turn(
            first_source.locator,
            session_loader=lambda _session_id: first_session,
        )
    )
    same_session_payload = predictor.bounded_payload(
        reconstruct_session_turn(
            same_session_source.locator,
            session_loader=lambda _session_id: first_session,
        )
    )
    duplicate_payload = predictor.bounded_payload(
        reconstruct_session_turn(
            duplicate_payload_source.locator,
            session_loader=lambda _session_id: second_session,
        )
    )
    assert first_payload != same_session_payload
    assert first_payload == duplicate_payload
    public = report.to_dict()
    assert public["splitPolicy"] == {
        "algorithm": "hmac-sha256-session-bounded-payload-components-v2",
        "fitSplit": "fit",
        "evaluationSplit": "holdout",
        "evaluationFraction": 0.4,
    }
    assert set(public) == {
        "schemaVersion",
        "datasetId",
        "classifierContractVersion",
        "modelKey",
        "splitPolicy",
        "metrics",
        "cases",
    }

    def nested_keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from nested_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from nested_keys(child)

    assert all(
        "payload" not in key.casefold()
        and "token" not in key.casefold()
        and "hash" not in key.casefold()
        for key in nested_keys(public)
    )
    serialized = json.dumps(public, ensure_ascii=False, sort_keys=True)
    assert "LEFT-PRIVATE-SUFFIX" not in serialized
    assert "RIGHT-PRIVATE-SUFFIX" not in serialized
    assert "private intermediate context" not in serialized


def test_calibration_export_has_only_exact_numeric_schema(tmp_path) -> None:
    source, session = _source("calibration-session", "NEVER-SERIALIZE-ME-771")
    labels = ProductionIntentLabelManifest(
        "calibration-v1",
        INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
        (_label(source),),
    )
    sources = ProductionIntentSourceManifest("calibration-v1", (source,))
    report = evaluate_production_intents(
        labels,
        sources,
        predictor=_QueuePredictor(
            [ReplayPrediction(PredictionStatus.ACCEPTED, (_operation(),), 0.83)]
        ),
        split_key=_SPLIT_KEY,
        reconstruct=lambda locator: reconstruct_session_turn(
            locator, session_loader=lambda _session_id: session
        ),
    )

    observations = calibration_observations(report)
    assert len(observations) == 1
    assert set(observations[0]) == {"rawConfidence", "correct", "split"}
    assert observations[0]["rawConfidence"] == pytest.approx(0.83)
    assert observations[0]["correct"] is True

    path = tmp_path / "observations.json"
    write_calibration_observations(path, report)
    serialized = path.read_text(encoding="utf-8")
    assert "NEVER-SERIALIZE-ME-771" not in serialized
    loaded = load_calibration_observations(path)
    assert loaded[0].raw_confidence == pytest.approx(0.83)
    assert loaded[0].correct is True


def test_wilson_interval_handles_empty_and_known_boundary() -> None:
    assert wilson_interval(0, 0) is None
    lower, upper = wilson_interval(1, 4) or (None, None)
    assert lower == pytest.approx(0.0455872608)
    assert upper == pytest.approx(0.6993581574)
