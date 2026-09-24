from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest

import scripts.analyze_memory_retrieval_quality_v3 as analyzer
from scripts.analyze_memory_retrieval_quality_v3 import (
    BOOTSTRAP_SAMPLES,
    AttemptRecord,
    CaseOutcome,
    FigureBackend,
    PressureProjection,
    ReportProjection,
    _load_figure_backend,
    build_analysis,
    compare_reports,
    exact_mcnemar,
    grouped_paired_bootstrap,
    load_attempt_history,
    one_sided_exact_binomial_upper,
    project_report,
    project_resolver_pressure_report,
    render_analysis_report,
    render_figures,
    render_stats_appendix,
    require_final_bundle_authority,
    summarize_report,
    wilson_interval,
    write_bundle,
)


def _report(label: str, rows: tuple[CaseOutcome, ...]) -> ReportProjection:
    return ReportProjection(
        label=label,
        scope="synthetic development (spent)",
        source=f"{label}.json",
        schema_version="test",
        synthetic=True,
        one_shot=False,
        embedding_identity="qwen / text-embedding-v3 / 1024d",
        verifier_identity="qwen3.7-plus",
        challenger_identity="qwen3.7-plus",
        acceptance_passed=None,
        usage={"model_call_count": 2, "input_tokens": 100},
        cases=rows,
        reported_rendered_precision=1.0,
    )


def test_exact_statistics_are_deterministic_and_use_exact_zero_over_n_bound() -> None:
    assert exact_mcnemar(0, 4) == 0.125
    assert exact_mcnemar(2, 2) == 1.0
    assert exact_mcnemar(0, 0) is None
    assert one_sided_exact_binomial_upper(0, 60) == pytest.approx(
        1.0 - 0.05 ** (1.0 / 60)
    )
    assert one_sided_exact_binomial_upper(0, 60) < 0.05

    pairs = (("group-a", 0.0, 1.0), ("group-a", 1.0, 1.0), ("group-b", 1.0, 0.0))
    first = grouped_paired_bootstrap(pairs, samples=250, seed=17)
    second = grouped_paired_bootstrap(pairs, samples=250, seed=17)
    assert first == second
    assert first["pair_count"] == 3
    assert first["group_count"] == 2
    assert first["estimate"] == pytest.approx(0.0)


def test_report_comparison_joins_only_same_ids_and_keeps_polarity_semantics() -> None:
    before = _report(
        "before",
        (
            CaseOutcome("p1", "positive", "g1", False, False, False),
            CaseOutcome("p2", "positive", "g1", True, True, True),
            CaseOutcome("n1", "hard_negative", "g2", None, True, False),
            CaseOutcome("old-only", "positive", "g3", True, True, True),
        ),
    )
    after = _report(
        "after",
        (
            CaseOutcome("p1", "positive", "g1", True, True, True),
            CaseOutcome("p2", "positive", "g1", True, False, False),
            CaseOutcome("n1", "hard_negative", "g2", None, False, True),
            CaseOutcome("new-only", "positive", "g3", True, True, True),
        ),
    )

    comparison = compare_reports(before, after)

    assert comparison is not None
    assert comparison["matched_case_count"] == 3
    assert comparison["decision_transitions"] == {
        "both_correct": 0,
        "before_only": 1,
        "after_only": 2,
        "both_wrong": 0,
    }
    assert comparison["candidate_matched_positive_count"] == 2
    assert comparison["candidate_transitions"] == {
        "both_hit": 1,
        "before_only": 0,
        "after_only": 1,
        "both_miss": 0,
    }
    assert comparison["decision_bootstrap"]["samples"] == BOOTSTRAP_SAMPLES


def test_semantic_v2_projection_uses_target_ids_without_projecting_text(
    tmp_path: Path,
) -> None:
    path = tmp_path / "semantic.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "memory-retrieval-semantic-gap-baseline-v2",
                "synthetic_data": True,
                "evaluation_passed": False,
                "remote_call_count": 0,
                "overall_metrics": {"metrics": {"rendered_precision": 0.5}},
                "per_case_results": [
                    {
                        "case_id": "p1",
                        "category": "translation",
                        "polarity": "positive",
                        "primary_entry_ids": ["memory-p1"],
                        "must_exclude_ids": [],
                        "canonical_production": {
                            "candidate_ids_top20": ["memory-p1"],
                            "rendered_ids": ["memory-p1"],
                        },
                    },
                    {
                        "case_id": "n1",
                        "category": "opposite",
                        "polarity": "hard_negative",
                        "primary_entry_ids": [],
                        "must_exclude_ids": ["memory-n1"],
                        "canonical_production": {
                            "candidate_ids_top20": ["memory-n1"],
                            "rendered_ids": [],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    projected = project_report(path, label="baseline", kind="baseline")
    summary = summarize_report(projected)

    assert summary["candidate_recall_at_20"] == 1.0
    assert summary["rendered_positive_recall"] == 1.0
    assert summary["hard_negative_target_render_rate"] == 0.0
    assert summary["decision_accuracy"] == 1.0
    assert projected.usage == {"remote_call_count": 0}


def test_v3_hybrid_projection_and_reports_remain_content_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "independent-report.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "3.0",
                "synthetic_data": True,
                "one_shot": True,
                "model": {
                    "provider": "qwen",
                    "model_id": "text-embedding-v3",
                    "dimension": 1024,
                },
                "verifier": {"model_id": "qwen3.7-plus"},
                "challenger": {"model_id": "qwen3.7-plus"},
                "usage": {
                    "embedding": {
                        "completed_call_count": 3,
                        "input_tokens": 120,
                        "output_tokens": 0,
                    },
                    "verifier": {
                        "completed_call_count": 2,
                        "input_tokens": 80,
                        "output_tokens": 12,
                    },
                },
                "acceptance_gate": {"passed": True},
                "global_dense": {
                    "cases": [
                        {"case_id": "p1", "target_in_top_20": True},
                        {"case_id": "n1", "target_in_top_20": True},
                    ]
                },
                "canonical": {
                    "metrics": {"rendered_precision": 1.0},
                    "cases": [
                        {
                            "case_id": "p1",
                            "target_id": "m-p1",
                            "polarity": "positive",
                            "relation_sha256": "a" * 64,
                            "target_candidate": True,
                            "target_rendered": True,
                            "rendered_ids": ["m-p1"],
                        },
                        {
                            "case_id": "n1",
                            "target_id": "m-n1",
                            "polarity": "hard_negative",
                            "relation_sha256": "b" * 64,
                            "target_candidate": True,
                            "target_rendered": False,
                            "rendered_ids": [],
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        analyzer, "_validate_formal_independent_report", lambda report: report
    )
    projection = project_report(
        path,
        label="independent-one-shot",
        kind="hybrid",
        scope="synthetic independent one-shot",
    )
    analysis = build_analysis((projection,))
    report = render_analysis_report(analysis)
    appendix = render_stats_appendix(analysis)

    assert projection.embedding_identity == "qwen / text-embedding-v3 / 1024d"
    assert projection.acceptance_passed is True
    assert projection.usage["embedding.completed_call_count"] == 3
    assert projection.usage["verifier.output_tokens"] == 12
    assert "0/1" in report
    assert "qwen3.7-plus" in appendix
    assert "query" not in report.casefold()
    assert "memory_text" not in appendix


def test_analyzer_rejects_holdout_and_case_text_fields_before_projection(
    tmp_path: Path,
) -> None:
    holdout = tmp_path / "holdout.json"
    holdout.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="never holdout"):
        project_report(holdout, label="bad", kind="hybrid")

    report = tmp_path / "leaking-report.json"
    report.write_text(
        json.dumps(
            {
                "canonical": {
                    "cases": [
                        {
                            "case_id": "case-1",
                            "target_id": "memory-1",
                            "polarity": "positive",
                            "query": "secret query",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="forbidden.*text"):
        project_report(report, label="bad", kind="hybrid")


def test_independent_scope_cannot_be_attached_to_a_non_one_shot_report(
    tmp_path: Path,
) -> None:
    report = tmp_path / "ordinary-report.json"
    report.write_text(
        json.dumps(
            {
                "synthetic_data": True,
                "one_shot": False,
                "canonical": {
                    "cases": [
                        {
                            "case_id": "case-1",
                            "target_id": "memory-1",
                            "polarity": "positive",
                            "target_candidate": True,
                            "target_rendered": True,
                            "rendered_ids": ["memory-1"],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="promotion report schema mismatch"):
        project_report(
            report,
            label="independent-one-shot",
            kind="hybrid",
            scope="synthetic independent one-shot",
        )


def test_zero_over_sixty_bound_matches_documented_threshold() -> None:
    bound = one_sided_exact_binomial_upper(0, 60)
    assert math.isclose(bound, 0.048702913310097576)


def test_figure_backend_is_dependency_free_svg() -> None:
    def must_not_import(_name: str):
        raise AssertionError("SVG backend must not import optional plotting packages")

    assert _load_figure_backend(must_not_import) == FigureBackend(
        name="svg-stdlib",
        api={},
    )


def test_pillow_backend_draws_actual_aggregate_values_without_pillow_dependency(
    tmp_path: Path,
) -> None:
    class FakeImage:
        def __init__(self) -> None:
            self.saved: list[tuple[str, str]] = []
            self.texts: list[str] = []

        def save(self, path: Path, *, format: str, **_kwargs) -> None:
            self.saved.append((str(path), format))

    class FakeImageApi:
        def __init__(self) -> None:
            self.images: list[FakeImage] = []

        def new(self, *_args, **_kwargs) -> FakeImage:
            image = FakeImage()
            self.images.append(image)
            return image

    class FakeDraw:
        def __init__(self, image: FakeImage) -> None:
            self.image = image

        def textbbox(self, _xy, text: str, *, font) -> tuple[int, int, int, int]:
            size = font[1]
            return (0, 0, max(1, len(text) * size // 2), size)

        def text(self, _xy, text: str, **_kwargs) -> None:
            self.image.texts.append(text)

        def line(self, *_args, **_kwargs) -> None:
            pass

        def rectangle(self, *_args, **_kwargs) -> None:
            pass

    class FakeDrawApi:
        @staticmethod
        def Draw(image: FakeImage) -> FakeDraw:
            return FakeDraw(image)

    class FakeFontApi:
        @staticmethod
        def truetype(_family: str, *, size: int):
            return ("font", size)

        @staticmethod
        def load_default(*, size: int = 12):
            return ("font", size)

    before = _report(
        "before",
        (
            CaseOutcome("p1", "positive", "g1", False, False, False),
            CaseOutcome("n1", "hard_negative", "g2", None, True, False),
        ),
    )
    after = _report(
        "after",
        (
            CaseOutcome("p1", "positive", "g1", True, True, True),
            CaseOutcome("n1", "hard_negative", "g2", None, False, True),
        ),
    )
    image_api = FakeImageApi()
    backend = FigureBackend(
        name="pillow",
        api={
            "Image": image_api,
            "ImageDraw": FakeDrawApi(),
            "ImageFont": FakeFontApi(),
        },
    )

    catalog = render_figures(
        build_analysis((before, after)),
        tmp_path / "figures",
        backend=backend,
    )

    assert [item["id"] for item in catalog] == [
        "Figure 1",
        "Figure 2",
        "Supporting paired table",
    ]
    assert all(item["backend"] == "pillow" for item in catalog)
    assert len(image_api.images) == 2
    assert {
        format_name for image in image_api.images for _path, format_name in image.saved
    } == {
        "PNG",
        "PDF",
    }
    all_text = {text for image in image_api.images for text in image.texts}
    assert "100%" in all_text
    assert "matched N=2" in all_text
    assert (tmp_path / "figures/figure-01-stage-recall.svg").is_file()
    assert (tmp_path / "figures/figure-02-gate-safety.svg").is_file()
    ElementTree.parse(tmp_path / "figures/figure-01-stage-recall.svg")
    ElementTree.parse(tmp_path / "figures/figure-02-gate-safety.svg")


def test_wilson_interval_is_reported_for_binary_rates() -> None:
    low, high = wilson_interval(0, 60)
    assert low == 0.0
    assert high == pytest.approx(0.0601718521420898)
    low, high = wilson_interval(60, 60)
    assert low == pytest.approx(0.9398281478579102)
    assert high == 1.0
    with pytest.raises(ValueError, match="Wilson"):
        wilson_interval(0, 0)


def test_hybrid_projection_keeps_dense_candidate_admitted_and_rendered_separate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "development.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "development-v1",
                "synthetic_data": True,
                "one_shot": False,
                "holdout_version": "same-suite",
                "global_dense": {
                    "cases": [
                        {"case_id": "p1", "target_in_top_20": False},
                        {"case_id": "n1", "target_in_top_20": True},
                    ]
                },
                "canonical": {
                    "counts": {
                        "false_positive_admissions": 1,
                        "false_positive_renders": 0,
                    },
                    "metrics": {"rendered_precision": 1.0},
                    "cases": [
                        {
                            "case_id": "p1",
                            "target_id": "m-p1",
                            "polarity": "positive",
                            "target_candidate": True,
                            "target_admitted": True,
                            "target_rendered": True,
                            "rendered_ids": ["m-p1"],
                        },
                        {
                            "case_id": "n1",
                            "target_id": "m-n1",
                            "polarity": "hard_negative",
                            "target_candidate": True,
                            "target_admitted": False,
                            "target_rendered": False,
                            "rendered_ids": [],
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    projection = project_report(path, label="dev", kind="hybrid")
    summary = summarize_report(projection)

    assert summary["global_dense_recall_at_20"] == 0.0
    assert summary["canonical_candidate_positive_recall"] == 1.0
    assert summary["admitted_positive_recall"] == 1.0
    assert summary["admitted_precision"] == 0.5
    assert summary["rendered_positive_recall"] == 1.0
    assert summary["rendered_precision"] == 1.0
    assert summary["rates"]["admitted_hard_negative_target_rate"]["successes"] == 0
    assert summary["rates"]["admitted_hard_negative_target_rate"]["trials"] == 1
    assert (
        summary["rates"]["canonical_candidate_hard_negative_target_rate_at_most_32"][
            "one_sided_exact_95_upper"
        ]
        == 1.0
    )


def test_pairing_is_development_only_same_suite_and_uses_fixed_seed() -> None:
    rows = (CaseOutcome("p1", "positive", "g1", False, False, False),)
    before = replace(_report("before", rows), suite_id="suite-a")
    after = replace(
        _report(
            "after",
            (CaseOutcome("p1", "positive", "g1", True, True, True),),
        ),
        suite_id="suite-a",
    )
    comparison = compare_reports(before, after)
    assert comparison is not None
    assert comparison["paired_risk_difference"]["seed"] == 20260903
    assert comparison["paired_risk_difference"]["samples"] == 10_000
    assert compare_reports(before, replace(after, suite_id="suite-b")) is None
    assert (
        compare_reports(
            before,
            replace(after, scope="synthetic independent one-shot"),
        )
        is None
    )


def test_attempt_history_is_closed_and_content_free(tmp_path: Path) -> None:
    path = tmp_path / "attempts.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "memory-retrieval-attempt-history-v1",
                "attempts": [
                    {
                        "attempt_id": "pressure-v2",
                        "suite": "resolver-pressure",
                        "generation": "v2",
                        "status": "failed",
                        "stage": "model-output-validation",
                        "reason_code": "redundant_contested_reason_echo_mismatch",
                        "authority": False,
                        "note": "Spent attempt; not rerun and not production authority.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert load_attempt_history(path) == (
        AttemptRecord(
            attempt_id="pressure-v2",
            suite="resolver-pressure",
            generation="v2",
            status="failed",
            stage="model-output-validation",
            reason_code="redundant_contested_reason_echo_mismatch",
            authority=False,
            note="Spent attempt; not rerun and not production authority.",
        ),
    )

    leaking = tmp_path / "leaking-attempts.json"
    leaking.write_text(
        json.dumps(
            {
                "schema_version": "memory-retrieval-attempt-history-v1",
                "attempts": [{"query": "forbidden"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="forbidden sealed-text"):
        load_attempt_history(leaking)


def test_final_bundle_fails_before_writes_without_both_formal_authorities(
    tmp_path: Path,
) -> None:
    development = _report(
        "dev",
        (CaseOutcome("p1", "positive", "g1", True, True, True),),
    )
    analysis = build_analysis((development,))
    output = tmp_path / "bundle"
    with pytest.raises(ValueError, match="independent"):
        write_bundle(analysis, output)
    assert not output.exists()

    independent = replace(
        development,
        label="independent",
        scope="synthetic independent one-shot",
        one_shot=True,
        acceptance_passed=True,
        formal_authority=True,
    )
    with pytest.raises(ValueError, match="resolver-pressure"):
        require_final_bundle_authority(build_analysis((independent,)))
    pressure = PressureProjection(
        label="resolver-pressure-one-shot",
        scope="synthetic independent one-shot safety pressure",
        source="pressure.json",
        schema_version="pressure-v4",
        synthetic=True,
        one_shot=True,
        acceptance_passed=True,
        report_fingerprint="a" * 64,
        case_count=60,
        raw_relevant_count=0,
        approved_count=0,
        protocol_failure_cases=0,
        usage={"model_call_count": 8, "input_tokens": 100, "output_tokens": 10},
    )
    ready = build_analysis((independent,), resolver_pressure=pressure)
    require_final_bundle_authority(ready)
    outputs = write_bundle(ready, output)
    assert {path.name for path in outputs} == {
        "analysis-report.md",
        "stats-appendix.md",
        "figure-catalog.md",
    }
    assert (
        (output / "figures/figure-01-stage-recall.svg")
        .read_text(encoding="utf-8")
        .startswith("<svg")
    )
    assert (
        (output / "figures/figure-02-gate-safety.svg")
        .read_text(encoding="utf-8")
        .startswith("<svg")
    )
    catalog = (output / "figure-catalog.md").read_text(encoding="utf-8")
    assert "Caption requirements" in catalog
    assert "Allowed Claim Candidate" in catalog
    assert "Forbidden Claim Candidate" in catalog
    report = (output / "analysis-report.md").read_text(encoding="utf-8")
    assert "resolver-pressure-v2-original-one-shot" in report
    assert "claim_order_source_fix_changed_freeze" in report
    assert "independent-v1-original-one-shot" in report
    assert "independent-current-authority" in report


def test_formal_independent_projection_calls_validator_fingerprint_and_release_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minicode.memory_hybrid as hybrid
    import minicode.memory_hybrid_release as release
    import scripts.memory_hybrid_qwen_v2_evaluator as evaluator

    calls: list[str] = []
    report = {"report_fingerprint": "f" * 64}

    def validate(value):
        calls.append("validate")
        return value

    def build(value):
        assert value is report
        calls.append("build-production-evidence")
        return {"report_fingerprint": "e" * 64}

    monkeypatch.setattr(evaluator, "validate_promotion_report", validate)
    monkeypatch.setattr(evaluator, "build_qwen_v2_production_evidence", build)
    monkeypatch.setattr(
        hybrid,
        "evidence_fingerprint",
        lambda value: value["report_fingerprint"],
    )
    monkeypatch.setattr(
        release,
        "HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        "e" * 64,
    )

    assert analyzer._validate_formal_independent_report(report) is report
    assert calls == ["validate", "build-production-evidence"]

    monkeypatch.setattr(
        release,
        "HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        "0" * 64,
    )
    with pytest.raises(ValueError, match="release-allowlisted"):
        analyzer._validate_formal_independent_report(report)


def test_pressure_projection_calls_official_loader_validator_and_release_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minicode.memory_hybrid as hybrid
    import scripts.memory_hybrid_qwen_v2_evaluator as evaluator

    path = tmp_path / "pressure-report.json"
    report = {
        "report_fingerprint": "a" * 64,
        "schema_version": "pressure-v4",
        "acceptance_gate": {"passed": True},
        "resolver_pressure": {
            "counts": {
                "case_count": 60,
                "raw_relevant_count": 0,
                "approved_count": 0,
                "protocol_failure_cases": 0,
            },
            "usage": {
                "model_call_count": 8,
                "input_tokens": 100,
                "output_tokens": 10,
            },
        },
    }
    path.write_text(json.dumps(report), encoding="utf-8")
    receipt = {"report_fingerprint": "a" * 64}
    calls: list[str] = []

    def load(_path):
        calls.append("safe-loader")
        return receipt

    def validate(value):
        calls.append("validate")
        return value

    def build(value, *, artifact):
        assert value is not None
        assert artifact == hybrid.HYBRID_RESOLVER_PRESSURE_ARTIFACT
        calls.append("build-receipt")
        return receipt

    def require(value):
        assert value is receipt
        calls.append("release-check")
        return receipt

    monkeypatch.setattr(evaluator, "load_resolver_pressure_report", load)
    monkeypatch.setattr(evaluator, "validate_resolver_pressure_report", validate)
    monkeypatch.setattr(evaluator, "build_resolver_pressure_receipt", build)
    monkeypatch.setattr(evaluator, "require_resolver_pressure_release_anchor", require)
    monkeypatch.setattr(
        hybrid,
        "evidence_fingerprint",
        lambda value: value["report_fingerprint"],
    )

    projection = project_resolver_pressure_report(path)
    assert projection.case_count == 60
    assert projection.approved_count == 0
    assert calls == ["safe-loader", "validate", "build-receipt", "release-check"]
