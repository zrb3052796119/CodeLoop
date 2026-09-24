# Prototype verdict

Question: Which information hierarchy and visual direction should the eventual MiniCode dashboard use?

Status: A direction selected and rebuilt after studying Waku Agent.

## Decision

- Winning variant: A, refined into a quiet local operations console.
- Reference: Waku Agent's fixed three-column shell, compact typography, neutral panels, sub-tabs, and one narrative per page.
- Keep: cost, run, memory, skill, gateway/MCP, and trace visibility.
- Remove: global top bar, icon-only rail, giant page hero, command palette, decorative topology, and persistent detail inspector.
- Required MVP modules: Overview, Runs, Sessions, Memory, Skills, Connections, Ops, and System.
- Memory terminology comes from MiniCode itself: User / Project / Local are durable scopes; Working / Short-term / Long-term / Archival are entry tiers. `WorkingMemoryTracker` is shown separately as compaction protection.
- The retrieval UI exposes candidates, selected, rendered, and suppressed states without presenting optional reranker/vector implementations as the default path.

This prototype is intentionally disposable. Do not promote it directly to production.
