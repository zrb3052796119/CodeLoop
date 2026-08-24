from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "model-routing-live-acceptance-2026-08-23.json"


def test_model_routing_acceptance_projection_is_bounded_and_complete() -> None:
    document = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    assert document["schemaVersion"] == 1
    assert document["artifactType"] == "sanitized-model-routing-acceptance-projection"
    assert document["verdict"] == {
        "status": "passed",
        "usableActors": 4,
        "expectedActors": 4,
        "repeatRounds": 2,
    }
    actors = {item["actor"]: item for item in document["actors"]}
    assert set(actors) == {
        "parent",
        "subagent:explore",
        "subagent:plan",
        "subagent:general",
    }
    for actor in actors.values():
        assert actor["requestedModel"] == actor["providerReportedModel"]
        assert actor["httpStatuses"] == [200, 200]
        assert actor["outcomes"] == ["completed", "completed"]
        assert actor["requestCountPerRound"] == [1, 1]
        assert len(actor["inputTokens"]) == 2
        assert all(value > 0 for value in actor["inputTokens"])


def test_model_routing_acceptance_projection_contains_no_credentials_or_content() -> None:
    document = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    encoded = json.dumps(document, ensure_ascii=False).lower()

    assert "api_key" not in encoded
    assert "authorization" not in encoded
    assert "bearer " not in encoded
    assert document["scope"]["credentialsCommitted"] is False
    assert document["scope"]["rawPromptsCommitted"] is False
    assert document["scope"]["rawHeadersCommitted"] is False
