"""Demo: BM25 vs BM25 + Domain + Reranker — memory retrieval quality comparison.

Creates a realistic project memory set (mixed frontend/backend/database/devops),
then runs sample queries to compare what each pipeline returns.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")


class FakeModel:
    """Simulates a reranker LLM without real API calls."""
    def __init__(self):
        self.call_count = 0

    def generate(self, prompt: str) -> dict:
        self.call_count += 1
        # Extract task_description from the prompt to make smart-ish selections
        task = ""
        for line in prompt.split("\n"):
            if line.startswith("Current task:"):
                task = line.split(":", 1)[1].strip().lower()

        # Parse candidate IDs from prompt
        ids = []
        for line in prompt.split("\n"):
            if line.strip().startswith("[m") and "]" in line.split("]")[0]:
                eid = line.strip().split("]")[0].replace("[", "")
                ids.append(eid)

        # Smart selection based on task keywords
        selected = []
        task_frontend = any(w in task for w in ["react", "component", "css", "form", "ui", "button", "modal", "login", "register", "frontend", "style"])
        task_backend = any(w in task for w in ["api", "server", "endpoint", "request", "http", "backend", "route", "fastapi"])
        task_database = any(w in task for w in ["database", "migration", "schema", "sql", "query", "table", "postgres"])
        task_devops = any(w in task for w in ["docker", "deploy", "ci", "pipeline", "kubernetes"])

        for eid in ids:
            content = ""
            for line in prompt.split("\n"):
                if f"[{eid}]" in line:
                    content = line.split("] ", 1)[1] if "] " in line else ""
            content_lower = content.lower()

            score = 0
            if task_frontend and any(w in content_lower for w in ["react", "component", "css", "form", "ui", "tailwind", "zustand", "redux", "hook"]):
                score += 3
            if task_backend and any(w in content_lower for w in ["api", "server", "endpoint", "fastapi", "jwt", "auth", "route", "request"]):
                score += 3
            if task_database and any(w in content_lower for w in ["database", "migration", "schema", "sql", "query", "postgres", "table", "alembic"]):
                score += 3
            if task_devops and any(w in content_lower for w in ["docker", "deploy", "ci", "pipeline", "kubernetes"]):
                score += 3

            if score >= 2:
                selected.append(eid)

        if len(selected) < 2:
            selected = ids[:3]
        if len(selected) > 5:
            selected = selected[:5]

        summary_map = {
            "react": "This project uses React 18 with TypeScript, Zustand for state, and react-hook-form+zod for forms. Follow the component-first pattern established in src/components/.",
            "api": "API follows FastAPI conventions with JWT auth and async handlers. Endpoints are under /api/v1/. Use Pydantic for request/response models.",
            "database": "Database is PostgreSQL managed via Alembic migrations. Never edit schema directly — always create a migration file.",
            "docker": "Deployment uses Docker Compose with multi-stage builds. Config is in docker-compose.yml and .env files.",
        }

        summary = ""
        for kw, s in summary_map.items():
            if kw in task:
                summary = s
                break
        if not summary:
            summary = "Follow existing project conventions. Check related memories for specific patterns."

        result = {
            "selected": selected,
            "rejected": [{"id": eid, "reason": "Domain mismatch"} for eid in ids if eid not in selected],
            "conflicts": [],
            "summary": summary,
        }
        return {"content": json.dumps(result)}


def create_memories():
    """Create a realistic set of 12 project memories across 4 domains."""
    from minicode.memory import MemoryEntry, MemoryScope, MemoryFile

    memories = [
        ("m1", "React components use functional style with hooks. Prefer composition over inheritance. Use TypeScript for all new components.", ["frontend"]),
        ("m2", "API routes are defined in FastAPI using async handlers. All endpoints return JSON with Pydantic models for validation.", ["backend"]),
        ("m3", "React forms use react-hook-form with zod validation schemas. Avoid using controlled components directly — use Controller wrapper.", ["frontend"]),
        ("m4", "Database migrations are handled by Alembic. Run 'alembic upgrade head' after pulling changes. Never edit tables directly.", ["database"]),
        ("m5", "CSS styling uses Tailwind utility classes. Avoid inline styles and CSS modules. Custom theme defined in tailwind.config.ts.", ["frontend"]),
        ("m6", "Docker deployment uses docker-compose with multi-stage builds. Environment variables are in .env files, never commit secrets.", ["devops"]),
        ("m7", "State management migrated from Redux to Zustand in Q1 2026. New features must use Zustand stores, not Redux slices.", ["frontend"]),
        ("m8", "JWT authentication uses refresh token rotation. Access tokens expire in 15min, refresh tokens in 7 days. Implement silent refresh in axios interceptor.", ["backend", "security"]),
        ("m9", "API rate limiting is 100 req/min per user by default. Use redis-based sliding window. Admin endpoints have separate 30 req/min limit.", ["backend"]),
        ("m10", "Database connection pooling uses PgBouncer with transaction mode. Max 20 connections. Query timeout is 30s, kill long-running queries.", ["database", "devops"]),
        ("m11", "CI/CD pipeline runs on GitHub Actions. Tests must pass, lint must pass, and build must succeed before merge. Deploy to staging automatically on main.", ["devops"]),
        ("m12", "PostgreSQL 16 with PostGIS extension. Use JSONB for semi-structured data, not EAV patterns. Full-text search via tsvector.", ["database"]),
    ]

    mf = MemoryFile(scope=MemoryScope.PROJECT)
    for eid, content, domains in memories:
        mf.add_entry(MemoryEntry(
            id=eid, scope=MemoryScope.PROJECT,
            category="pattern", content=content,
            domains=domains,
        ))
    return mf


def run_query(mf, query, active_domains, use_reranker=False):
    """Run a query and return (results, metadata)."""
    from minicode.memory_reranker import MemoryReranker

    # BM25 search
    bm25_results = mf.search(query, active_domains=active_domains)
    meta = {"bm25_total": len(bm25_results), "bm25_top3": [e.id for e in bm25_results[:3]]}

    if use_reranker:
        fake_llm = FakeModel()
        reranker = MemoryReranker(model_adapter=fake_llm)
        rerank_result = reranker.curate(
            candidates=bm25_results,
            task_description=query,
            active_domains=active_domains,
        )
        meta["rerank_selected"] = rerank_result.selected_ids
        meta["rerank_summary"] = rerank_result.summary
        meta["rerank_conflicts"] = rerank_result.conflicts
        meta["llm_calls"] = fake_llm.call_count

    return bm25_results, meta


def print_comparison(query, active_domains, bm25_results, meta, with_rerank):
    """Pretty-print comparison."""
    print(f"\n{'='*70}")
    print(f"Query: {query}")
    print(f"Active domains: {active_domains}")
    print(f"{'='*70}")

    print(f"\n-- BM25 Top 5 --")
    for i, entry in enumerate(bm25_results[:5]):
        domain_tag = f"[{','.join(entry.domains)}]" if entry.domains else "[general]"
        print(f"  {i+1}. {domain_tag} {entry.content[:100]}...")

    if with_rerank and "rerank_selected" in meta:
        print(f"\n-- Reranker Selected ({len(meta['rerank_selected'])} items) --")
        for eid in meta["rerank_selected"]:
            for entry in bm25_results:
                if entry.id == eid:
                    domain_tag = f"[{','.join(entry.domains)}]" if entry.domains else "[general]"
                    print(f"  [OK] {domain_tag} {entry.content[:100]}...")
                    break
        if meta.get("rerank_summary"):
            print(f"\n-- Curator Summary --")
            print(f"  {meta['rerank_summary']}")

    # Domain relevance analysis
    print(f"\n-- Domain Relevance --")
    for i, entry in enumerate(bm25_results[:10]):
        domain_overlap = bool(set(entry.domains) & set(active_domains))
        marker = "[MATCH]" if domain_overlap else "[MISS] "
        if not with_rerank:
            selected = "-> injected" if i < 5 else ""
            print(f"  {marker} [{entry.id}] [{','.join(entry.domains) or 'general'}] {selected}")
        else:
            in_selection = entry.id in meta.get("rerank_selected", [])
            selected = "-> injected" if in_selection else "-> filtered out"
            print(f"  {marker} [{entry.id}] [{','.join(entry.domains) or 'general'}] {selected}")


def main():
    mf = create_memories()
    print("Memory database: 12 memories (5 frontend, 3 backend, 2 database, 1 devops, 1 cross-domain)")

    test_cases = [
        ("Create a registration form with email and password validation", ["frontend"]),
        ("Add rate limiting to the user API endpoints", ["backend"]),
        ("Write a database migration to add a users.avatar_url column", ["database"]),
        ("Set up GitHub Actions CI to run tests on every pull request", ["devops"]),
    ]

    for query, active_domains in test_cases:
        # BM25 only
        bm25_results, meta_bm25 = run_query(mf, query, active_domains, use_reranker=False)
        print_comparison(query, active_domains, bm25_results, meta_bm25, with_rerank=False)

        # BM25 + Reranker
        bm25_results, meta_rerank = run_query(mf, query, active_domains, use_reranker=True)
        print_comparison(query, active_domains, bm25_results, meta_rerank, with_rerank=True)

        print("\n" + "-" * 70)


if __name__ == "__main__":
    main()
