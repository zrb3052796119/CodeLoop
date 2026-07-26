from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "minicode/web/static/index.html"
STYLES = ROOT / "minicode/web/static/assets/styles.css"
APP = ROOT / "minicode/web/static/assets/app.js"


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def _styles() -> str:
    return STYLES.read_text(encoding="utf-8")


def _app() -> str:
    return APP.read_text(encoding="utf-8")


def _view_block(name: str, next_name: str) -> str:
    javascript = _app()
    return javascript[
        javascript.index(f"  {name}(") : javascript.index(f"\n  {next_name}(")
    ]


def test_agent_observatory_shell_has_direction_a_landmarks() -> None:
    html = _html()
    stylesheet = _styles()

    assert 'id="page-kicker"' in html
    assert 'id="page-deck"' in html
    assert html.count('class="nav-icon"') == 8
    for token in (
        "--rail-text",
        "--rail-muted",
        "--rail-hover",
        "--rail-selected",
        "--rail-border",
        "--rail-accent",
    ):
        assert token in stylesheet
    assert "background: var(--surface-nav)" in stylesheet
    assert "background: var(--surface-dock)" in stylesheet


def test_overview_observatory_uses_only_existing_real_run_read_contracts() -> None:
    javascript = _app()
    overview = _view_block("overview", "runs")

    assert "const observatoryStore" in javascript
    assert "function loadObservatory" in javascript
    assert "fetch('/api/v1/runs?limit=6'" in javascript
    assert "fetch(`/api/v1/runs/${encodeURIComponent(runId)}?limit=50`" in javascript
    assert "observatoryStore.listRequestId" in javascript
    assert "observatoryStore.detailRequestId" in javascript
    assert "refreshObservatoryFromChangeFeed" in javascript
    assert "observatory-overview" in overview
    assert "observatory-band" in overview
    assert "observatory-run-focus" in overview
    assert "observatory-activity" in overview
    assert "observatory-signals" in overview
    assert "observatory-ledger" in overview
    assert "snapshotStore.data" in overview
    assert "observatoryStore" in overview
    assert "DATA.runs" not in overview
    assert "mock" not in overview.lower()


def test_agent_observatory_refreshes_from_existing_runs_invalidation_only() -> None:
    javascript = _app()
    refresh = javascript[
        javascript.index("async function refreshChangedResources") :
        javascript.index("\nconst esc =")
    ]
    route_loading = javascript[
        javascript.index("function loadRouteData") :
        javascript.index("\nfunction handleRouteChange")
    ]

    assert "if (view === 'overview') tasks.push(refreshObservatoryFromChangeFeed())" in refresh
    assert "if (view === 'overview' && observatoryStore.phase === 'idle') loadObservatory()" in route_loading
    assert javascript.count("new EventSource('/api/v1/events')") == 1
    assert re.findall(r"setInterval\(([^,\n]+)", javascript) == ["tickMeta"]


def test_core_pages_share_observatory_hierarchy_without_replacing_hooks() -> None:
    javascript = _app()
    runs = _view_block("runs", "sessions")
    sessions = _view_block("sessions", "memory")
    memory = _view_block("memory", "skills")

    assert 'class="core-page runs-observatory"' in runs
    assert 'class="core-page sessions-observatory"' in sessions
    assert 'class="core-page memory-observatory"' in memory
    assert "runs-master-detail" in javascript
    assert "run-detail" in javascript
    assert "selectRun" in javascript
    assert "sessions-master-detail" in javascript
    assert "session-detail" in javascript
    assert "selectHistoricalSession" in javascript
    assert "openConversationDeletion" in javascript
    assert "openProjectMemoryDeletion" in javascript
    assert "renderMemoryApprovals" in javascript


def test_observatory_layout_has_distinct_focus_activity_signals_and_ledger_regions() -> None:
    stylesheet = _styles()

    assert re.search(
        r"\.observatory-grid\s*\{[^}]*grid-template-columns:"
        r"[^;]*minmax\([^;]+minmax\(",
        stylesheet,
        re.DOTALL,
    )
    assert ".observatory-run-focus" in stylesheet
    assert ".observatory-activity" in stylesheet
    assert ".observatory-signals" in stylesheet
    assert ".observatory-ledger" in stylesheet
    medium = stylesheet[stylesheet.index("@media (max-width: 900px)") :]
    assert ".observatory-grid { grid-template-columns: 1fr; }" in medium
    assert ".observatory-band { grid-template-columns: 1fr; }" in medium
    narrow = stylesheet[stylesheet.index("@media (max-width: 760px)") :]
    assert ".observatory-grid" in narrow
    assert "grid-template-columns: 1fr" in narrow
    assert "body:not(.dock-closed) #nav-reopen { display: none; }" in narrow


def test_memory_defaults_to_overview_and_ops_refreshes_lifecycle_consumer() -> None:
    javascript = _app()
    memory = _view_block("memory", "skills")
    ops_loader = javascript[
        javascript.index("async function loadOps") :
        javascript.index("\nfunction refreshOps")
    ]

    assert "sub = sub || 'overview';" in memory
    assert "function renderOpsConsumers" in javascript
    assert ops_loader.count("renderOpsConsumers();") == 2


def test_theme_metadata_matches_agent_observatory_paper_surface() -> None:
    html = _html()
    root = _styles()[_styles().index(":root {") : _styles().index("}\n\n@media")]
    theme = re.search(r'<meta name="theme-color" content="(#[0-9a-fA-F]{6})"', html)
    main_surface = re.search(r"--surface-main:\s*(#[0-9a-fA-F]{6})", root)

    assert theme
    assert main_surface
    assert theme.group(1).lower() == main_surface.group(1).lower()
