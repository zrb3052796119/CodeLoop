# Working Notes: Batch 9D-1A.1

## Frozen boundaries

- Prototype-only directory; must remain absent from wheels.
- No production, test, manifest, semantic-gold, evaluator, threshold,
  `pyproject.toml` or roadmap-state edits.
- No real API, persistence, network asset or external request.

## Evidence log

### Starting frozen state

- Active production baseline: `memory-retrieval-production-v34`.
- Generator result: current matches active, candidate matches active,
  56/56 files match, 34 integrity entries all pass.
- v34 lineage: only `minicode/web/static/index.html`,
  `minicode/web/static/assets/styles.css`, and
  `minicode/web/static/assets/app.js` differ from v33.
- Production entry SHA-256 / bytes:
  - `index.html`: `d00d29b0df3cd2f284a524edef6ad7f5a22e541aa2c9a2740ddc1ea907b01afa` / 5,574
  - `styles.css`: `59eb5cab22b6a705ce2fee135635552b3acbc5d39f72d661e774d8c2a8ed1ed4` / 70,416
  - `app.js`: `5082899135487a2722830d365df8107119788ab3745ad01bc783840c80b3b91f` / 296,952
  - `cost-format.js`: `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916` / 1,208
- Control SHA-256 / bytes:
  - `pyproject.toml`: `1d6a9df71501c1e614e97a23f84b5d977ece3dcb48ede78e4f8f324f4a6a347f` / 854
  - v34 manifest: `3136e096a97192de5078882523106f5179cb20a3e9885c050fd187038f815cbb` / 6,357
  - accepted semantic gold:
    `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`
    / 3,033,592 / mtime_ns `1784135857000000000`
- `pyproject.toml` has no runtime dependencies. Package data is explicitly
  limited to `minicode.web` static HTML/CSS/JS, so the repository-level
  `dashboard_prototype/` directory is outside the wheel boundary.

### Current formal UI audit

The existing installed-page screenshots and source establish the following
specific problems for this Spike to test:

1. Navigation, content and Chat Dock have nearly equal visual weight; borders
   divide columns, but hierarchy does not clearly establish where live work is.
2. Overview begins with two repeated metric grids, so Agent activity is delayed
   below inventory-like summaries.
3. Repeated cards, nested cards, pills and similar radii make unrelated facts
   look equally important.
4. Small labels and frequent monospace treatment weaken typographic contrast.
5. Run, Model, Tool and Memory activity are facts, not one readable execution
   narrative.
6. Runtime signals are dispersed across metric tiles, coverage copy and run
   details instead of forming a single observation surface.
7. The idle Chat Dock has a large inactive gap between its initial state and
   composer.
8. A pending permission is readable, but it occupies most of the Dock and
   competes with the conversation instead of reading as a deliberate interrupt.
9. Repeated status pills give routine and urgent states similar emphasis.
10. Source-authority and generated-state copy consumes prime header bandwidth.
11. The warm shell is coherent, but still resembles a general operations admin
    UI more than an Agent workbench.
12. Desktop and 480 px views preserve function, yet do not create a stronger
    mobile execution/chat reading order.

### Waku reference boundary

- Preserve the approved Waku cues: calm three-column shell, compact navigation,
  warm neutral foundation, restrained borders, native-feeling controls and a
  persistent conversation surface.
- Do not copy its weaker patterns: uniform card density, low activity priority
  and insufficient distinction between observation, conversation and approval.

### Shared prototype contract

- One route with `?variant=A|B|C`; identical DOM, mock data, navigation,
  activity, Run ledger, Chat, Permission and composer for all variants.
- A grid areas: `"focus activity" "signals activity" "ledger ledger"`.
- B grid areas: `"activity focus" "activity signals" "ledger ledger"`.
- C grid areas: `"focus focus" "activity signals" "ledger ledger"`.
- All state remains in page memory. Source and rendered DOM contain no fetch,
  EventSource, WebSocket, storage, external asset, user path or secret.

### Browser and screenshot evidence

- Static local server: `127.0.0.1:8765`.
- Six reviewed PNGs exist at exact 1280×900 and 480×900 dimensions.
- Exact 1280×900 document/client evidence:
  - A: document 1280; rail 212; main client/scroll 700/700; Dock track/client
    368/367.
  - B: document 1280; rail 224; main client/scroll 678/678; Dock track/client
    378/377.
  - C: document 1280; rail 194; main client/scroll 736/736; Dock track/client
    350/349.
- Responsive A:
  - 1024×768: document 1024, Dock 368 fixed overlay, composer bottom 768.
  - 700×900: document 700, Dock 420 and navigation 290 fixed overlays,
    composer bottom 900.
  - 480×900: document 480, Dock 480 full-width, composer bottom 900.
- Allow and Block remained visible at every responsive viewport. Escape closed
  Dock and navigation overlays. Long Run, Session and Skill identifiers did not
  change document width.
- All eight navigation buttons switched visible/semantic selection. Current Run
  selection, Permission Allow/Block/reset, Dock collapse/reopen and composer
  draft preservation passed.
- Keyboard focus had a visible outline. Reduced-motion preference reduced
  animation duration to 0.001 ms.
- HTTP: HTML/CSS/JS returned 200 with `text/html`, `text/css` and
  `text/javascript`.
- Browser console warnings/errors: 0. Page errors: 0. Failed requests: 0.
  External requests: 0.
- During visual review, B exposed a real 31 px component overflow caused by a
  fixed Signal minimum. The prototype-only grid was corrected and reverified at
  equal client/scroll width. An initial Dock transition race was also removed
  so all screenshot widths settle deterministically.

### Final frozen-boundary evidence

- Fresh wheel: `minicode_py-0.1.0-py3-none-any.whl`, 218 entries.
- Production static entries: 4. Prototype/visual-direction entries: 0.
- Final production SHA/bytes equal the starting values:
  - `index.html`: `d00d29b0df3cd2f284a524edef6ad7f5a22e541aa2c9a2740ddc1ea907b01afa` / 5,574
  - `styles.css`: `59eb5cab22b6a705ce2fee135635552b3acbc5d39f72d661e774d8c2a8ed1ed4` / 70,416
  - `app.js`: `5082899135487a2722830d365df8107119788ab3745ad01bc783840c80b3b91f` / 296,952
  - `cost-format.js`: `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916` / 1,208
  - `pyproject.toml`: `1d6a9df71501c1e614e97a23f84b5d977ece3dcb48ede78e4f8f324f4a6a347f` / 854
  - v34: `3136e096a97192de5078882523106f5179cb20a3e9885c050fd187038f815cbb` / 6,357
  - accepted gold:
    `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`
    / 3,033,592 / mtime_ns `1784135857000000000`.
- Final verifier: active v34; candidate/current/matches true; 56/56;
  mismatches empty; every v1–v34 integrity value true; v34 lineage remains
  exactly the three production frontend files.

### Cleanup

- Local HTTP server stopped.
- In-app browser viewport override reset and task tabs finalized.
- Headless Chrome closed by the verifier harness.
- Temporary browser harness, fresh wheel, pip cache and diagnostic screenshot
  removed. The six requested screenshots remain as deliverables.
