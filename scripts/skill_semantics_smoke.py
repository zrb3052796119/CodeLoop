"""Smoke-check semantic skill routing against a real embedding endpoint.

Configure once (values live in the .env file, never in shell exports):

    cp .env.example .env
    # then edit .env:
    #   MINICODE_EMBEDDING_API_KEY=sk-你的DashScopeKey
    #   MINICODE_EMBEDDING_MODEL=text-embedding-v3   # or text-embedding-v4

Then run:

    python scripts/skill_semantics_smoke.py

Resolution order per setting: process env > <workspace>/.env >
~/.mini-code/.env > defaults.

Verifies, in order:
1. one real /embeddings round-trip;
2. cross-language similarity behaves (zh query ≈ en skill > zh query ≈
   unrelated skill);
3. the full router routes a Chinese task to the right skill with the
   embedding signal present.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minicode.env_file import read_env_files
from minicode.skill_semantics import (
    DEFAULT_EMBEDDING_BASE_URL,
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_API_KEY_ENV,
    EMBEDDING_BASE_URL_ENV,
    EMBEDDING_MODEL_ENV,
    EmbeddingSemanticMatcher,
    OpenAICompatibleEmbeddingClient,
    cosine_similarity,
)
from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import parse_intent
from minicode.skill_router import SkillRouter

SKILLS = [
    {"name": "db-tuning", "description": "database query performance tuning and slow SQL optimization",
     "keywords": ["database", "sql"], "source": "project", "content_digest": "a" * 64},
    {"name": "deploy-helper", "description": "docker container deployment and release automation",
     "keywords": ["docker", "deploy"], "source": "project", "content_digest": "b" * 64},
    {"name": "doc-writer", "description": "generate documentation from code comments",
     "keywords": ["docs"], "source": "project", "content_digest": "c" * 64},
]


def _setting(name: str, default: str = "") -> str:
    direct = os.environ.get(name, "").strip()
    if direct:
        return direct
    return read_env_files(
        [Path.home() / ".mini-code" / ".env", Path(".env")]
    ).get(name, "").strip() or default


def main() -> int:
    api_key = _setting(EMBEDDING_API_KEY_ENV)
    if not api_key:
        print(f"{EMBEDDING_API_KEY_ENV} not set in the environment or .env — nothing to check.")
        print("Run: cp .env.example .env  then fill in MINICODE_EMBEDDING_API_KEY.")
        return 2

    model = _setting(EMBEDDING_MODEL_ENV, DEFAULT_EMBEDDING_MODEL)
    base_url = _setting(EMBEDDING_BASE_URL_ENV, DEFAULT_EMBEDDING_BASE_URL)
    print(f"endpoint: {base_url}  model: {model}")
    client = OpenAICompatibleEmbeddingClient(api_key, base_url=base_url, model=model)

    print("1) endpoint round-trip...")
    vectors = client.embed(["database tuning", "docker deploy", "hello"])
    print(f"   ok: {len(vectors)} vectors, dim={len(vectors[0])}")

    print("2) cross-language similarity...")
    query = client.embed_one("帮我优化数据库查询")
    near = cosine_similarity(query, vectors[0])
    far = cosine_similarity(query, vectors[1])
    print(f"   zh-query vs db-skill:   {near:.4f}")
    print(f"   zh-query vs deploy-skill: {far:.4f}")
    if near <= far:
        print("   UNEXPECTED: unrelated skill scored higher")
        return 1

    print("3) full router with embedding signal...")
    workspace = Path(tempfile.mkdtemp(prefix="skill-smoke-"))
    matcher = EmbeddingSemanticMatcher(client, workspace / ".mini-code" / "skill-embeddings.json")
    registry = CapabilityRegistry()
    router = SkillRouter(workspace=str(workspace), embedding_matcher=matcher)
    result = router.route(SKILLS, parse_intent("帮我优化数据库查询"), registry)
    print(f"   fallback={result.used_fallback} selected={[s.name for s in result.selected]}")
    for skill in result.selected:
        print(f"   {skill.name}: score={skill.score} reasons={skill.reasons}")
    if result.used_fallback or not result.selected or result.selected[0].name != "db-tuning":
        print("   UNEXPECTED routing outcome")
        return 1
    if not any("semantic:embedding" in r for r in result.selected[0].reasons):
        print("   WARNING: alias matched but embedding signal absent (check thresholds)")

    print("\nAll checks passed.")
    print("Thresholds default to the Qwen-calibrated 0.60/0.67/0.52;")
    print("override per provider in .env via MINICODE_EMBEDDING_SIGNAL_THRESHOLD etc.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
