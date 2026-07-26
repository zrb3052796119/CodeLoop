# MiniCode Dashboard Batch 9D-1A Waku UI Audit

## Audit boundary

This audit is read-only. No Waku Agent or retained prototype file was modified.
The available evidence is sufficient to ground the “Waku-inspired” direction in
actual source rather than memory:

- Waku Agent project: `/Users/zhourunbo/code/Waku Agent`
- Waku Dashboard shell: `waku/ops/static/index.html`
- Waku visual rules: `waku/ops/static/style.css`
- Waku interaction code: `waku/ops/static/app.js`
- Waku Dashboard server: `waku/ops/dashboard.py`
- Retained MiniCode prototype: `minicode/web/dashboard_prototype/`
- Prototype design record: `minicode/web/dashboard_prototype/NOTES.md`
- Prototype usage boundary: `minicode/web/dashboard_prototype/README.md`

The Waku tree contains architecture whiteboards under `docs/`, but no retained
Dashboard screenshot was found. Visual claims below therefore come from the
actual HTML/CSS plus the user-approved MiniCode prototype, not an invented image
reference.

## 1. Page skeleton

Waku uses a fixed-height, three-column application shell:

1. a left `nav`;
2. a 5 px navigation resizer;
3. a flexible `main` with a sticky page header and one page view;
4. a 5 px Dock resizer;
5. a right chat `aside`;
6. small fixed reopen controls for collapsed side panels.

The body owns the viewport and hides overflow. Navigation, main content and Dock
manage their own vertical overflow. MiniCode's retained prototype deliberately
selected the same compact three-column hierarchy over a global top bar, icon-only
rail, giant hero, command palette or persistent detail inspector.

## 2. Navigation width and information hierarchy

The Waku navigation defaults to 208 px. It presents a quiet brand row, a small
secondary model/status line, an uppercase section label, then one text route per
row. Counts use smaller tabular numerals and muted color so they do not compete
with the route name. The active route uses a soft accent surface and accent text;
hover uses a neutral surface.

For MiniCode, the useful principle is fast scanning through stable text labels,
small counts and one restrained selected state. The eight MiniCode routes and all
existing count hooks remain authoritative.

## 3. Page titles, groups and data layout

Waku uses a compact 17 px page title, a small monospace subtitle and 11 px
uppercase section headings. Page content follows one narrative flow rather than
placing every fragment in an independent card. Data-heavy surfaces use tabular
numerals, compact tables, small tiles and explicit sub-tabs.

MiniCode should preserve this compact hierarchy while retaining its existing
page-specific business structures for later Batch 9D-1B/1C work.

## 4. Status labels and color strategy

Waku defines a small palette: neutral surfaces and borders, one indigo accent,
green success and red failure, each with a soft background companion. Status
labels are small, not headline-sized. Dots and short text commonly appear
together.

MiniCode needs a wider semantic set because it exposes live, partial, warning,
unavailable, error and authority states. Those meanings must use one shared token
system and visible text or markers; color alone is not sufficient. A layout class
must not reuse `live` as a business-state name.

## 5. Right Dock treatment

Waku's Dock defaults to 380 px, has a fine left border and uses a column layout:
compact header, session actions, scrollable chat log and a bottom composer. The
collapse control sits in the header; the reopen action becomes a restrained
floating control. Message and Tool content remain dense and readable.

MiniCode has substantially stronger authority semantics than Waku. It must retain
Session/Turn status, Assistant/Tool streaming, permission review, cancellation,
retry/reconciliation and SSE-with-polling-fallback truth. The visual refactor may
reduce initial noise, but may not hide or alter those facts.

## 6. Borders, radii, shadows and whitespace

Waku relies on one-pixel borders, 6–9 px radii and moderate 8–16 px internal
spacing. Ordinary cards and columns use no shadow. Shadows are reserved for
menus and floating reopen controls. The result reads as a professional local
tool rather than a stack of promotional cards.

MiniCode should use the same restraint: thin boundaries, limited shape tokens,
fewer nested card surfaces and elevation only for overlays, dialogs and floating
controls.

## 7. Typography and numeric formatting

Waku uses the local system sans stack and a local `ui-monospace` stack. Titles
are compact, section labels use tracked uppercase text, and metrics use tabular
numerals. No network font is required.

MiniCode must add clear Chinese fallbacks while keeping IDs, timestamps, tokens,
costs and other data in a reliable monospace/tabular stack.

## 8. Reusable design principles

- Keep the application shell quiet so operational state has priority.
- Use one narrative main column and explicit section hierarchy.
- Make side-panel boundaries and resizing discoverable without making them loud.
- Prefer fine borders and selected surfaces to heavy shadows.
- Keep route names stable and counts subordinate.
- Reserve accent and semantic colors for meaning.
- Use local typography and tabular numeric alignment.
- Keep overlay elevation and motion sparse, short and predictable.
- Preserve independently understandable text alongside state color.

## 9. Brand elements that must not be copied

MiniCode must not copy the `Waku わく` name, Waku model line, product-specific
route taxonomy, indigo-as-brand assumption, voice/microphone behavior, gateway
source labels, architecture animation or other Waku-specific copy and identity.
The selected direction is an interpretation of information hierarchy and visual
restraint, not a skin or brand clone.

## 10. MiniCode architecture and functions that must remain

MiniCode retains its own eight primary routes: Overview, Runs, Sessions, Memory,
Skills, Connections, Ops and System. It also retains:

- five Memory subroutes and all existing read authorities;
- real Session, Turn, Run, Memory, Skill and MCP projections;
- the single EventSource plus the existing polling fallback;
- Chat POST/stream, draft, history, cancellation and reconciliation behavior;
- Tool permission and Memory approval as distinct authorities;
- Workspace-local Session and Project Memory deletion authorities;
- Data Health, pricing/cost and read-only status semantics;
- skip-link, dialog focus, keyboard, reduced-motion and narrow-layout behavior;
- standard-library packaging with no network assets or frontend build chain.

Batch 9D-1A changes only the visual-system and Shell presentation seam. It does
not rewrite page internals or introduce a new application architecture.

## 11. MiniCode design verdict

Use Waku's proven proportions and restraint as a starting point, then express a
distinct “MiniCode Local Agent Control Room” through warm neutral surfaces,
stronger semantic-state rigor, compact Chinese-aware typography, clearer
authority disclosure and a more accessible responsive Shell. Preserve the
existing MiniCode hooks and behavior; defer page-internal redesign to
Batch 9D-1B and 9D-1C.
