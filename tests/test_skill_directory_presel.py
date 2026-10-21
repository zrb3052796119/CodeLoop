"""Unit contracts for directory-level semantic skill preselection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from minicode.capability_registry import CapabilityRegistry
from minicode.config import load_runtime_config
from minicode.intent_parser import (
    ActionType,
    IntentPolarity,
    IntentType,
    ParsedIntent,
)
from minicode.skill_directory_presel import (
    DirectoryPreselMatcher,
    blend_directory_suggestions,
    directory_presel_text,
)
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)
from minicode.skill_router import (
    SkillAuthorityResolution,
    SkillRoutingResult,
)
from minicode.skill_semantics import (
    EmbeddingSemanticMatcher,
    EmbeddingUnavailable,
)


# ---------------------------------------------------------------------------
# Fixtures mirroring test_skill_routing_turn's model-mode helpers
# ---------------------------------------------------------------------------


def _catalog_skill(name: str) -> dict[str, object]:
    return {
        "name": name,
        "qualified_name": name,
        "description": f"{name} description",
        "path": f"/skills/{name}/SKILL.md",
        "source": "project",
    }


def _directoried_skill(name: str, directory: str) -> dict[str, object]:
    skill = _catalog_skill(name)
    skill["qualified_name"] = f"{directory}/{name}"
    skill["directory"] = directory
    skill["directory_description"] = f"{directory} area"
    return skill


def _capturing_router() -> object:
    result = SkillRoutingResult(
        intent_type=IntentType.REVIEW.value,
        action_type=ActionType.ANALYZE.value,
        capability_domains=[],
        capability_scopes=[],
        selected=[],
        total_skills=3,
        used_fallback=False,
        authority_resolution=SkillAuthorityResolution(),
    )
    return type("_NoopRouter", (), {"route": lambda self, *a, **k: result})()


def _hint_intent() -> ParsedIntent:
    return ParsedIntent(
        raw_input="review the parser code",
        intent_type=IntentType.REVIEW,
        action_type=ActionType.ANALYZE,
        confidence=0.95,
        operations=(
            type(
                "Op",
                (),
                {
                    "polarity": IntentPolarity.AFFIRMED,
                    "clause": "review the parser code",
                    "span": (0, 21),
                },
            )(),
        ),
    )


def _hint_catalog() -> list[dict[str, object]]:
    catalog = [
        _directoried_skill("alpha", "reviewing"),
        _directoried_skill("beta", "reviewing"),
        _directoried_skill("gamma", "docs"),
    ]
    for skill in catalog:
        if skill["directory"] == "reviewing":
            skill["directory_description"] = "code review and audit helpers"
        else:
            skill["directory_description"] = "documentation work"
    return catalog


class _RecordingPresel:
    """Fake matcher recording calls and returning fixed scores."""

    def __init__(
        self,
        scores: dict[str, float] | None = None,
        error: Exception | None = None,
        signal_threshold: float = 0.60,
    ) -> None:
        self.scores = scores or {}
        self.error = error
        self.signal_threshold = signal_threshold
        self.calls: list[tuple[str, frozenset[str]]] = []

    def rank(self, query, by_directory, *, call_context=None):
        self.calls.append((query, frozenset(by_directory)))
        if self.error is not None:
            raise self.error
        return self.scores


def _presel_decision(catalog, controls, presel):
    return route_skill_turn(
        SkillRoutingTurnRequest(
            prompt="review the parser code",
            skills=catalog,
            registry=CapabilityRegistry(),
            parsed_intent=_hint_intent(),
        ),
        SkillRoutingTurnAdapters(
            router=_capturing_router(), directory_presel=presel
        ),
        controls=controls,
    )


_HIERARCHICAL_HINT_CONTROLS = {
    "skill_selection_mode": "model",
    "skill_flat_catalog_chars": 10,
    "skill_intent_hints": True,
    "skill_directory_presel": True,
}


# ---------------------------------------------------------------------------
# Text builder and blend
# ---------------------------------------------------------------------------


class TestDirectoryPreselText:
    def test_includes_names_and_description(self) -> None:
        text = directory_presel_text(
            "reviewing", "code review helpers", ["reviewing/a", "reviewing/b"]
        )
        assert "reviewing" in text
        assert "code review helpers" in text
        assert "reviewing/a" in text
        assert "reviewing/b" in text

    def test_bounded_by_char_budget(self) -> None:
        text = directory_presel_text(
            "docs",
            "d" * 100,
            [f"docs/skill-{index:04d}" for index in range(200)],
        )
        assert len(text) <= 1600


class TestBlendDirectorySuggestions:
    def test_semantic_only_candidate_joins_with_provenance(self) -> None:
        ranked, sources = blend_directory_suggestions(
            {"reviewing": 0.5},
            {"docs": 0.72},
            signal_threshold=0.60,
            limit=3,
        )
        assert ranked == ["docs", "reviewing"]
        assert sources == {"docs": "semantic", "reviewing": "lexical"}

    def test_below_threshold_semantic_is_excluded(self) -> None:
        ranked, sources = blend_directory_suggestions(
            {"reviewing": 0.5},
            {"docs": 0.48},
            signal_threshold=0.60,
            limit=3,
        )
        assert ranked == ["reviewing"]
        assert sources == {"reviewing": "lexical"}

    def test_both_signals_mark_combined_provenance(self) -> None:
        _ranked, sources = blend_directory_suggestions(
            {"reviewing": 0.5},
            {"reviewing": 0.7},
            signal_threshold=0.60,
            limit=3,
        )
        assert sources == {"reviewing": "lexical+semantic"}

    def test_limit_and_ordering(self) -> None:
        ranked, _sources = blend_directory_suggestions(
            {},
            {"a": 0.9, "b": 0.8, "c": 0.7, "d": 0.61},
            signal_threshold=0.60,
            limit=3,
        )
        assert ranked == ["a", "b", "c"]

    def test_zero_and_negative_scores_yield_empty(self) -> None:
        ranked, sources = blend_directory_suggestions(
            {"x": 0.0}, {"y": -0.2}, signal_threshold=0.60, limit=3
        )
        assert ranked == []
        assert sources == {}


# ---------------------------------------------------------------------------
# Turn-seam integration
# ---------------------------------------------------------------------------


class TestPreselTurnIntegration:
    def test_semantic_candidate_fills_lexical_gap(self) -> None:
        # "docs" has zero lexical coverage for the review query but the fake
        # semantic matcher scores it above threshold: it must be suggested.
        presel = _RecordingPresel(scores={"docs": 0.75})
        decision = _presel_decision(_hint_catalog(), _HIERARCHICAL_HINT_CONTROLS, presel)

        hints = decision.routing.intent_hints
        assert hints is not None
        assert "reviewing" in hints["suggested_directories"]
        assert "docs" in hints["suggested_directories"]
        assert hints["suggested_directory_sources"]["docs"] == "semantic"
        # prompt projection stays untouched by the advisory signal.
        assert decision.prompt_skills

    def test_control_off_never_calls_matcher(self) -> None:
        presel = _RecordingPresel(scores={"docs": 0.9})
        controls = {
            "skill_selection_mode": "model",
            "skill_flat_catalog_chars": 10,
            "skill_intent_hints": True,
        }
        decision = _presel_decision(_hint_catalog(), controls, presel)

        assert presel.calls == []
        hints = decision.routing.intent_hints
        assert hints is not None
        assert "suggested_directory_sources" not in hints

    def test_hints_off_never_calls_matcher(self) -> None:
        presel = _RecordingPresel(scores={"docs": 0.9})
        controls = {
            "skill_selection_mode": "model",
            "skill_flat_catalog_chars": 10,
            "skill_directory_presel": True,
        }
        _presel_decision(_hint_catalog(), controls, presel)
        assert presel.calls == []

    def test_embedding_failure_fails_closed_to_lexical(self) -> None:
        presel = _RecordingPresel(
            error=EmbeddingUnavailable("fake outage")
        )
        decision = _presel_decision(_hint_catalog(), _HIERARCHICAL_HINT_CONTROLS, presel)

        hints = decision.routing.intent_hints
        assert hints is not None
        # The lexical path is exactly what ran before presel existed.
        assert hints["suggested_directories"] == ["reviewing"]
        assert "suggested_directory_sources" not in hints

    def test_low_semantic_score_does_not_displace_lexical_only_list(self) -> None:
        presel = _RecordingPresel(scores={"docs": 0.10})
        decision = _presel_decision(_hint_catalog(), _HIERARCHICAL_HINT_CONTROLS, presel)

        hints = decision.routing.intent_hints
        assert hints is not None
        assert hints["suggested_directories"] == ["reviewing"]

    def test_provenance_survives_result_projection(self) -> None:
        presel = _RecordingPresel(scores={"docs": 0.75})
        decision = _presel_decision(_hint_catalog(), _HIERARCHICAL_HINT_CONTROLS, presel)

        projected = decision.routing.to_dict()
        hints = projected["intent_hints"]
        assert hints["suggested_directory_sources"] == {
            "reviewing": "lexical",
            "docs": "semantic",
        }


# ---------------------------------------------------------------------------
# Matcher over the shared embedding seam
# ---------------------------------------------------------------------------


class FakeEmbeddingClient:
    """Deterministic stand-in for the /embeddings endpoint."""

    def __init__(self, vectors: dict[str, list[float]], fail: bool = False) -> None:
        self._vectors = vectors
        self._fail = fail
        self._model = "fake-model"
        self._endpoint = "fake://embeddings"
        self.embed_calls = 0

    def embed(self, texts):
        self.embed_calls += 1
        if self._fail:
            raise EmbeddingUnavailable("fake outage")
        return [self._vectors.get(text, [0.0, 0.0, 0.0]) for text in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


def _unit(axis: int) -> list[float]:
    vector = [0.0, 0.0, 0.0]
    vector[axis] = 1.0
    return vector


class TestDirectoryPreselMatcher:
    def _matcher(self, tmp_path: Path, client) -> DirectoryPreselMatcher:
        embedding = EmbeddingSemanticMatcher(
            client, tmp_path / "dir-embeddings.json"
        )
        return DirectoryPreselMatcher(embedding, signal_threshold=0.60)

    def test_rank_scores_directories(self, tmp_path: Path) -> None:
        by_directory = {
            "reviewing": "reviewing code review helpers",
            "docs": "docs documentation work",
        }
        client = FakeEmbeddingClient(
            {
                "帮我审查代码": _unit(0),
                "reviewing code review helpers": _unit(0),
                # Slightly correlated with the query so the positive-only
                # similarity filter still reports it, at a low score.
                "docs documentation work": [0.09, 1.0, 0.0],
            }
        )
        matcher = self._matcher(tmp_path, client)

        scores = matcher.rank("帮我审查代码", by_directory)

        assert scores["reviewing"] > 0.9
        assert scores["docs"] < 0.1

    def test_rank_uses_separate_cache_file(self, tmp_path: Path) -> None:
        client = FakeEmbeddingClient({})
        matcher = self._matcher(tmp_path, client)
        matcher.rank("anything", {"docs": "docs documentation work"})
        assert (tmp_path / "dir-embeddings.json").exists()

    def test_transport_failure_fails_closed_and_cools_down(
        self, tmp_path: Path
    ) -> None:
        client = FakeEmbeddingClient({}, fail=True)
        matcher = self._matcher(tmp_path, client)

        assert matcher.rank("q", {"docs": "docs documentation"}) == {}
        # The cooldown short-circuits before any further endpoint traffic.
        calls_after_failure = client.embed_calls
        assert matcher.rank("q", {"docs": "docs documentation"}) == {}
        assert client.embed_calls == calls_after_failure

    def test_warms_all_directories_within_elevated_cap(
        self, tmp_path: Path
    ) -> None:
        # 66 directories would take three default-capped turns; the presel
        # cap warms them in one pass.
        by_directory = {
            f"dir-{index:02d}": f"dir-{index:02d} description"
            for index in range(66)
        }
        client = FakeEmbeddingClient(
            {
                "q": _unit(0),
                **{text: _unit(0) for text in by_directory.values()},
            }
        )
        matcher = self._matcher(tmp_path, client)

        scores = matcher.rank("q", by_directory)

        assert len(scores) == 66

    def test_rank_skips_empty_texts_and_empty_query(self, tmp_path: Path) -> None:
        client = FakeEmbeddingClient({})
        matcher = self._matcher(tmp_path, client)
        assert matcher.rank("", {"docs": "docs documentation"}) == {}
        assert matcher.rank("q", {"docs": ""}) == {}

    def test_similarities_respects_max_new_vectors_override(
        self, tmp_path: Path
    ) -> None:
        pairs = [(f"digest-{i}", f"text-{i}") for i in range(3)]
        client = FakeEmbeddingClient(
            {
                "q": _unit(0),
                "text-0": _unit(0),
                "text-1": [0.2, 1.0, 0.0],
                "text-2": [0.1, 0.0, 1.0],
            }
        )
        embedding = EmbeddingSemanticMatcher(client, tmp_path / "cache.json")

        first = embedding.similarities("q", pairs, max_new_vectors=1)
        # Only text-0 is warmed; the still-missing vectors score 0.0.
        assert first == [1.0, 0.0, 0.0]
        embedding.similarities("q", pairs, max_new_vectors=1)
        warmed = embedding.similarities("q", pairs, max_new_vectors=1)
        # text-1 and text-2 now have real (non-missing) low scores, not 0.0.
        assert warmed == pytest.approx([1.0, 0.196, 0.0995], abs=1e-2)
        cache = json.loads((tmp_path / "cache.json").read_text())
        assert len(cache["vectors"]) == 3


# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------


class TestDirectoryPreselConfig:
    def test_default_off(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("MINI_CODE_SKILL_DIRECTORY_PRESEL", raising=False)
        monkeypatch.delenv("MINI_CODE_SKILL_INTENT_PREFETCH", raising=False)
        monkeypatch.delenv("MINI_CODE_SKILL_INTENT_HINTS", raising=False)
        (tmp_path / "workspace").mkdir()
        runtime = load_runtime_config(tmp_path / "workspace")
        assert runtime["skillSelection"]["directoryPresel"] is False

    def test_env_override_enables(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("MINI_CODE_SKILL_DIRECTORY_PRESEL", "1")
        (tmp_path / "workspace").mkdir()
        runtime = load_runtime_config(tmp_path / "workspace")
        assert runtime["skillSelection"]["directoryPresel"] is True

    def test_summary_projects_directory_presel(
        self, tmp_path, monkeypatch
    ) -> None:
        from minicode import config as config_module

        monkeypatch.delenv("MINI_CODE_SKILL_DIRECTORY_PRESEL", raising=False)
        (tmp_path / "workspace").mkdir()
        runtime = load_runtime_config(tmp_path / "workspace")
        summary = config_module.safe_runtime_summary(runtime)
        assert summary["skillSelection"]["directoryPresel"] is False


# ---------------------------------------------------------------------------
# from_environment fail-closed paths
# ---------------------------------------------------------------------------


class TestFromEnvironment:
    def test_unconfigured_environment_returns_none(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.delenv("MINICODE_EMBEDDING_API_KEY", raising=False)
        monkeypatch.delenv("MINICODE_EMBEDDING_BASE_URL", raising=False)
        (tmp_path / "workspace").mkdir()
        assert DirectoryPreselMatcher.from_environment(tmp_path / "workspace") is None
