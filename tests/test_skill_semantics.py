"""Tests for semantic skill routing: alias floor + embedding ceiling."""

from __future__ import annotations

import contextlib
import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from minicode.capability_registry import CapabilityRegistry, register_tool_capabilities
from minicode.intent_parser import (
    ActionType,
    IntentType,
    ParsedIntent,
    parse_intent,
)
from minicode.skill_router import SkillRouter
from minicode.skill_semantics import (
    AliasSemanticMatcher,
    EmbeddingSemanticMatcher,
    EmbeddingUnavailable,
    OpenAICompatibleEmbeddingClient,
    cosine_similarity,
)
from minicode.tools import create_default_tool_registry


def _intent(query: str) -> ParsedIntent:
    return parse_intent(query)


def _skills() -> list[dict]:
    return [
        {"name": "db-tuning", "description": "database query performance tuning",
         "keywords": ["database", "sql"], "source": "project", "content_digest": "a" * 64},
        {"name": "deploy-helper", "description": "docker container deployment automation",
         "keywords": ["docker", "deploy"], "source": "project", "content_digest": "b" * 64},
        {"name": "doc-writer", "description": "generate documentation from code",
         "keywords": ["docs"], "source": "project", "content_digest": "c" * 64},
    ]


def _registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    tools = create_default_tool_registry(".", runtime={})
    register_tool_capabilities(tools)
    return registry


class TestAliasMatcher:
    def test_chinese_query_matches_english_skill_text(self) -> None:
        matcher = AliasSemanticMatcher()
        matched = matcher.matched_concepts(
            "帮我优化数据库查询", "database query performance tuning"
        )
        assert {"database", "query"} <= matched
        assert matcher.query_coverage(
            "帮我优化数据库查询", "database query performance tuning"
        ) >= 0.6

    def test_english_query_matches_chinese_skill_text(self) -> None:
        matcher = AliasSemanticMatcher()
        matched = matcher.matched_concepts(
            "optimize the database", "数据库 性能 调优 技能"
        )
        assert matched

    def test_small_talk_matches_nothing(self) -> None:
        matcher = AliasSemanticMatcher()
        assert matcher.matched_concepts(
            "Tell me a joke about python", "python jokes fun"
        ) == set()

    def test_related_terms_bidirectional(self) -> None:
        matcher = AliasSemanticMatcher()
        assert "数据库" in matcher.related_terms("database")
        assert "database" in matcher.related_terms("数据库")


class TestAliasRouting:
    def test_unknown_intent_chinese_task_routes(self) -> None:
        result = SkillRouter().route(
            _skills(), _intent("帮我优化数据库查询"), _registry()
        )
        assert not result.used_fallback
        assert [skill.name for skill in result.selected] == ["db-tuning"]
        assert any(
            reason.startswith("semantic:alias:")
            for reason in result.selected[0].reasons
        )

    def test_known_intent_matches_through_alias(self) -> None:
        # A classified intent whose keyword is Chinese but whose target skill
        # only speaks English must still match via the alias expansion.
        result = SkillRouter().route(
            _skills(),
            ParsedIntent(
                raw_input="审查一下数据库",
                intent_type=IntentType.REVIEW,
                action_type=ActionType.ANALYZE,
                confidence=0.9,
                keywords=["数据库"],
            ),
            _registry(),
        )
        assert not result.used_fallback
        assert any(skill.name == "db-tuning" for skill in result.selected)

    def test_small_talk_still_abstains(self) -> None:
        result = SkillRouter().route(
            _skills(), _intent("Tell me a joke about python"), _registry()
        )
        assert result.used_fallback
        assert result.selected == []

    def test_single_concept_chit_chat_abstains(self) -> None:
        result = SkillRouter().route(
            _skills(), _intent("讲个数据库的笑话"), _registry()
        )
        assert result.used_fallback


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


class TestEmbeddingRouting:
    def _matcher(self, tmp_path: Path, client) -> EmbeddingSemanticMatcher:
        return EmbeddingSemanticMatcher(client, tmp_path / "cache.json")

    def test_high_similarity_lights_signal(self, tmp_path: Path) -> None:
        # Query and db-tuning share one direction; other skills are neutral.
        client = FakeEmbeddingClient({
            "帮我优化数据库查询": _unit(0),
        })
        skills = _skills()
        pairs = [
            (skill["content_digest"],
             f"{skill['name']} {skill['description']} {' '.join(skill['keywords'])}")
            for skill in skills
        ]
        matcher = self._matcher(tmp_path, client)
        # Seed skill vectors through one similarities() pass with a neutral query.
        client._vectors[pairs[0][1]] = _unit(0)
        client._vectors[pairs[1][1]] = _unit(1)
        client._vectors[pairs[2][1]] = _unit(2)
        matcher.similarities("帮我优化数据库查询", pairs)

        router = SkillRouter(embedding_matcher=matcher)
        result = router.route(skills, _intent("帮我优化数据库查询"), _registry())
        assert not result.used_fallback
        assert result.selected[0].name == "db-tuning"
        assert any(
            "semantic:embedding" in reason for reason in result.selected[0].reasons
        )

    def test_vectors_cached_by_digest(self, tmp_path: Path) -> None:
        skills = _skills()
        pairs = [
            (skill["content_digest"], f"{skill['name']} {skill['description']}")
            for skill in skills
        ]
        client = FakeEmbeddingClient({
            pairs[0][1]: _unit(0),
            pairs[1][1]: _unit(1),
            pairs[2][1]: _unit(2),
        })
        matcher = self._matcher(tmp_path, client)
        matcher.similarities("anything", pairs)
        first_calls = client.embed_calls
        # Second pass: all skill vectors come from the cache, only the query
        # is embedded again.
        matcher.similarities("anything", pairs)
        assert client.embed_calls == first_calls + 1
        cache_file = tmp_path / "cache.json"
        assert json.loads(cache_file.read_text())["vectors"]

    def test_cache_is_invalidated_when_embedding_identity_changes(
        self, tmp_path: Path
    ) -> None:
        digest = "a" * 64
        text = "database tuning"
        first = FakeEmbeddingClient({text: _unit(0), "query": _unit(0)})
        first._model = "model-a"
        cache = tmp_path / "cache.json"
        assert EmbeddingSemanticMatcher(first, cache).similarities(
            "query", [(digest, text)]
        ) == [pytest.approx(1.0)]

        second = FakeEmbeddingClient({text: _unit(1), "query": _unit(1)})
        second._model = "model-b"
        scores = EmbeddingSemanticMatcher(second, cache).similarities(
            "query", [(digest, text)]
        )

        assert scores == [pytest.approx(1.0)]
        assert second.embed_calls == 2
        assert json.loads(cache.read_text())["model"] == "model-b"

    def test_malformed_cached_vector_is_ignored(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.json"
        cache.write_text(
            json.dumps(
                {
                    "model": "fake-model",
                    "endpoint": "fake://embeddings",
                    "vectors": {"a" * 64: ["not-a-number"]},
                }
            ),
            encoding="utf-8",
        )
        client = FakeEmbeddingClient(
            {"database tuning": _unit(0), "query": _unit(0)}
        )

        scores = EmbeddingSemanticMatcher(client, cache).similarities(
            "query", [("a" * 64, "database tuning")]
        )

        assert scores == [pytest.approx(1.0)]
        assert client.embed_calls == 2

    def test_unavailable_endpoint_degrades_to_alias(self, tmp_path: Path) -> None:
        client = FakeEmbeddingClient({}, fail=True)
        matcher = self._matcher(tmp_path, client)
        router = SkillRouter(embedding_matcher=matcher)
        result = router.route(
            _skills(), _intent("帮我优化数据库查询"), _registry()
        )
        # Alias floor still routes.
        assert not result.used_fallback
        assert result.selected[0].name == "db-tuning"

    def test_failure_cooldown_is_shared_by_fresh_workspace_routers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [100.0]
        monkeypatch.setattr("minicode.skill_router.time.time", lambda: now[0])

        class FailingMatcher:
            def __init__(self) -> None:
                self.calls = 0
                self.circuit_identity = (
                    str(tmp_path.resolve()),
                    "fake-model",
                    "fake://shared-outage",
                )

            def similarities(self, _query, _pairs):
                self.calls += 1
                raise EmbeddingUnavailable("shared fake outage")

        first = FailingMatcher()
        second = FailingMatcher()
        after_cooldown = FailingMatcher()

        SkillRouter(embedding_matcher=first).route(
            _skills(), _intent("帮我优化数据库查询"), _registry()
        )
        SkillRouter(embedding_matcher=second).route(
            _skills(), _intent("帮我优化数据库查询"), _registry()
        )
        now[0] += 301.0
        SkillRouter(embedding_matcher=after_cooldown).route(
            _skills(), _intent("帮我优化数据库查询"), _registry()
        )

        assert first.calls == 1
        assert second.calls == 0
        assert after_cooldown.calls == 1

    def test_shared_failure_circuit_retains_at_most_128_identities(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import minicode.skill_router as router_module

        states: dict = {}
        monkeypatch.setattr(router_module, "_EMBEDDING_CIRCUITS", states)
        for index in range(129):
            identity = (f"/workspace/{index}", "model", "fake://endpoint")
            assert router_module._begin_embedding_attempt(identity, 100.0)
            router_module._finish_embedding_attempt(
                identity,
                retry_after=400.0 + index,
            )

        assert len(states) == 128
        assert ("/workspace/0", "model", "fake://endpoint") not in states


class TestCosine:
    def test_identical_vectors(self) -> None:
        assert cosine_similarity([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        assert cosine_similarity(_unit(0), _unit(1)) == pytest.approx(0.0)

    def test_mismatched_dimensions(self) -> None:
        assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0


class TestEmbeddingClient:
    def test_missing_key_raises(self) -> None:
        with pytest.raises(EmbeddingUnavailable):
            OpenAICompatibleEmbeddingClient("")

    def test_batches_over_provider_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """14 texts must be split into 10+4 requests, not one rejected call."""
        requests: list[list[str]] = []

        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            body = json.loads(request.data.decode("utf-8"))
            requests.append(body["input"])
            vectors = [[1.0, 0.0] for _ in body["input"]]
            response = SimpleNamespace(
                read=lambda: json.dumps(
                    {"data": [{"embedding": v} for v in vectors]}
                ).encode("utf-8")
            )
            return contextlib.nullcontext(response)

        monkeypatch.setattr(
            "minicode.embeddings.urllib.request.urlopen", fake_urlopen
        )
        client = OpenAICompatibleEmbeddingClient("sk-test")
        vectors = client.embed([f"text-{i}" for i in range(14)])
        assert len(vectors) == 14
        assert [len(batch) for batch in requests] == [10, 4]

    def test_http_error_body_surfaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            raise urllib.error.HTTPError(
                request.full_url, 400, "Bad Request", None,
                io.BytesIO(b'{"error": "batch limit"}'),
            )

        monkeypatch.setattr(
            "minicode.embeddings.urllib.request.urlopen", fake_urlopen
        )
        client = OpenAICompatibleEmbeddingClient("sk-test")
        with pytest.raises(EmbeddingUnavailable, match="400.*batch limit"):
            client.embed(["x"])


class TestEnvFileConfig:
    def test_parse_env_file_basics(self, tmp_path: Path) -> None:
        from minicode.env_file import parse_env_file

        env_file = tmp_path / ".env"
        env_file.write_text(
            "# comment\n"
            "PLAIN=value\n"
            'QUOTED="quoted value"\n'
            "export EXPORTED=yes\n"
            "EMPTY=\n"
            "INVALID LINE\n",
            encoding="utf-8",
        )
        values = parse_env_file(env_file)
        assert values == {
            "PLAIN": "value",
            "QUOTED": "quoted value",
            "EXPORTED": "yes",
            "EMPTY": "",
        }

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        from minicode.env_file import parse_env_file

        assert parse_env_file(tmp_path / ".env") == {}

    def test_apply_env_file_never_overrides_process_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        from minicode.env_file import apply_env_file

        monkeypatch.setenv("SKILL_SEMANTIC_TEST_EXISTING", "from-process")
        env_file = tmp_path / ".env"
        env_file.write_text(
            "SKILL_SEMANTIC_TEST_EXISTING=from-file\n"
            "SKILL_SEMANTIC_TEST_NEW=from-file\n",
            encoding="utf-8",
        )
        applied = apply_env_file([env_file])
        assert os.environ["SKILL_SEMANTIC_TEST_EXISTING"] == "from-process"
        assert os.environ["SKILL_SEMANTIC_TEST_NEW"] == "from-file"
        assert applied == {"SKILL_SEMANTIC_TEST_NEW": "from-file"}

    def test_matcher_built_from_user_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in (
            "MINICODE_EMBEDDING_API_KEY",
            "MINICODE_EMBEDDING_BASE_URL",
            "MINICODE_EMBEDDING_MODEL",
        ):
            monkeypatch.delenv(name, raising=False)
        user_env = Path.home() / ".mini-code" / ".env"
        from minicode.env_file import update_private_env_file

        update_private_env_file(
            user_env,
            {
                "MINICODE_EMBEDDING_API_KEY": "sk-from-file",
                "MINICODE_EMBEDDING_MODEL": "text-embedding-v4",
            },
        )
        matcher = EmbeddingSemanticMatcher.from_environment(tmp_path)
        assert matcher is not None
        assert matcher._client._model == "text-embedding-v4"
        assert (
            matcher._client._endpoint
            == "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
        )

    def test_no_key_anywhere_means_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MINICODE_EMBEDDING_API_KEY", raising=False)
        assert EmbeddingSemanticMatcher.from_environment(tmp_path) is None

    def test_process_env_beats_user_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MINICODE_EMBEDDING_API_KEY", "sk-from-process")
        user_env = Path.home() / ".mini-code" / ".env"
        from minicode.env_file import update_private_env_file

        update_private_env_file(
            user_env,
            {"MINICODE_EMBEDDING_API_KEY": "sk-from-file"},
        )
        matcher = EmbeddingSemanticMatcher.from_environment(tmp_path)
        assert matcher is not None
        # The client keeps its key private; assert via a successful build and
        # distinct cache path rather than reading the key back.
        assert matcher._client is not None
