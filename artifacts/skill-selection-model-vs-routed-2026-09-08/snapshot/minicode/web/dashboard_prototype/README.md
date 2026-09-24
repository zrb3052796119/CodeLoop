# MiniCode Dashboard Prototype

Throwaway, read-only UI prototype. It uses mock data and does not call MiniCode, edit configuration, or persist anything.

From the repository root:

```bash
python minicode/web/dashboard_prototype/server.py
```

Open <http://127.0.0.1:8765/>.

- The selected A direction now uses a Waku-inspired three-column shell: compact navigation, one narrative main column, and a persistent session dock.
- Use the left navigation and bookmarkable hash routes to move between Overview, Runs, Sessions, Memory, Skills, Connections, Ops, and System.
- Memory follows MiniCode's real model: User / Project / Local scopes, per-entry lifecycle tiers, canonical retrieval diagnostics, and the four-method `MemoryPipeline` interface.
- The page remains mock-backed and read-only; it does not call the MiniCode runtime yet.

The design verdict is recorded in `NOTES.md`. The next architectural step is replacing the mock `DATA` contract with a read-only MiniCode snapshot API.
