from __future__ import annotations

import math
from pathlib import Path

import pytest

from minicode.embeddings import (
    EmbeddingUnavailable,
    OpenAICompatibleEmbeddingClient,
    OpenAICompatibleEmbeddingEncoder,
    create_openai_compatible_embedding_client,
)
from minicode.env_file import update_private_env_file


class _FakeEmbeddingClient:
    model = "text-embedding-v3"
    endpoint = "https://example.invalid/v1/embeddings"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts):
        self.calls.append(tuple(texts))
        return [[3.0, 4.0] for _ in texts]


def test_remote_encoder_hides_transport_and_returns_normalized_vectors() -> None:
    client = _FakeEmbeddingClient()
    encoder = OpenAICompatibleEmbeddingEncoder(client, provider="qwen")

    identity = encoder.identity
    queries = encoder.encode_queries(["repair the lease renewal"])
    documents = encoder.encode_documents(["renew the lease before expiry"])

    assert identity == {
        "provider": "qwen",
        "model_id": "text-embedding-v3",
        "endpoint": "https://example.invalid/v1/embeddings",
        "dimension": 2,
        "representation_version": "memory-structured-v1",
        "canary_version": "embedding-canary-v1",
        "canary_fingerprint": identity["canary_fingerprint"],
    }
    assert len(identity["canary_fingerprint"]) == 64
    assert queries == ((0.6, 0.8),)
    assert documents == ((0.6, 0.8),)
    assert math.isclose(sum(value * value for value in queries[0]), 1.0)
    assert all("api" not in key and "key" not in key for key in identity)


def test_remote_encoder_identity_probe_is_cached() -> None:
    client = _FakeEmbeddingClient()
    encoder = OpenAICompatibleEmbeddingEncoder(client, provider="qwen")

    assert encoder.identity == encoder.identity
    assert len(client.calls) == 1


def test_shared_client_factory_resolves_user_qwen_configuration(
    tmp_path, monkeypatch
) -> None:
    for name in (
        "MINICODE_EMBEDDING_API_KEY",
        "MINICODE_EMBEDDING_BASE_URL",
        "MINICODE_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    user_env = Path.home() / ".mini-code" / ".env"
    update_private_env_file(
        user_env,
        {
            "MINICODE_EMBEDDING_API_KEY": "synthetic-test-key",
            "MINICODE_EMBEDDING_MODEL": "text-embedding-v3",
        },
    )

    client = create_openai_compatible_embedding_client(tmp_path)

    assert client is not None
    assert client.model == "text-embedding-v3"
    assert (
        client.endpoint
        == "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    )


def test_workspace_env_cannot_redirect_user_embedding_credential(
    tmp_path, monkeypatch
) -> None:
    for name in (
        "MINICODE_EMBEDDING_API_KEY",
        "MINICODE_EMBEDDING_BASE_URL",
        "MINICODE_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    user_env = Path.home() / ".mini-code" / ".env"
    update_private_env_file(
        user_env,
        {
            "MINICODE_EMBEDDING_API_KEY": "user-global-key",
            "MINICODE_EMBEDDING_BASE_URL": "https://trusted.example/v1",
        },
    )
    (tmp_path / ".env").write_text(
        "MINICODE_EMBEDDING_BASE_URL=https://attacker.invalid/v1\n",
        encoding="utf-8",
    )

    client = create_openai_compatible_embedding_client(tmp_path)

    assert client is not None
    assert client.endpoint == "https://trusted.example/v1/embeddings"


def test_workspace_embedding_configuration_alone_is_ignored(
    tmp_path, monkeypatch
) -> None:
    for name in (
        "MINICODE_EMBEDDING_API_KEY",
        "MINICODE_EMBEDDING_BASE_URL",
        "MINICODE_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "MINICODE_EMBEDDING_API_KEY=workspace-key\n"
        "MINICODE_EMBEDDING_BASE_URL=https://workspace.invalid/v1\n",
        encoding="utf-8",
    )

    assert create_openai_compatible_embedding_client(tmp_path) is None


@pytest.mark.parametrize(
    "base_url",
    [
        "http://attacker.invalid/v1",
        "https://user:password@example.invalid/v1",
        "https://example.invalid/v1?key=secret",
        "https://example.invalid/v1#fragment",
    ],
)
def test_embedding_client_rejects_unsafe_credential_destinations(base_url) -> None:
    with pytest.raises(EmbeddingUnavailable):
        OpenAICompatibleEmbeddingClient("test-key", base_url=base_url)


def test_embedding_client_allows_loopback_http_for_local_models() -> None:
    client = OpenAICompatibleEmbeddingClient(
        "test-key",
        base_url="http://127.0.0.1:11434/v1",
    )

    assert client.endpoint == "http://127.0.0.1:11434/v1/embeddings"
