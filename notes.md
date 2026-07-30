# Reliability 1B-1C.1 Working Notes

## Scope

- Harden only Phase 2A certification infrastructure.
- Preserve real wall-clock measurement and the strict 5.0 ms gate while
  removing wall-clock pass/fail from default pytest/default CLI authority.
- Preserve v39, all `minicode/` bytes, web-search behavior, Functional Audit,
  accepted Phase 2A/2B artifacts and semantic gold.
- Apply only the minimal Phase 2A→Phase 2B→semantic pin cascade.

## Evidence

- v39 verifier complete output captured before edits:
  `memory-retrieval-production-v39`, parent v38, 62 files,
  candidate/current/matches true, exact one-changed/two-added lineage and
  every v1–v39 manifest integrity value true.
- v39 manifest SHA:
  `9bcf038d20aa7c044f4db613626b484e2fa89819929be20b46390bca00a99d6e`.
- Accepted gold SHA/size/mtime_ns:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  `3033592`, `1784135857000000000`.
- Functional Audit: 185 capabilities; pass 124, partial 44, fail 7,
  unavailable 1, blocked 6, not reachable 3; issues SEC-002, SEC-004,
  TOOL-001, TOOL-002, TOOL-003, SEC-005, MEM-001.
- Runtime dependencies remain `[]`.

### Phase 2A frozen eight-file set

| Path | SHA-256 |
| --- | --- |
| `artifacts/memory-retrieval-phase2a.json` | `2f488120e4016d9fafb275cd2b22b7e978ddf8f4039b990aeff1724e00759327` |
| `docs/memory-retrieval-phase2a-comparison.md` | `4c148cbe54f4e3d39ed5f2e1726f8ba7ee465b93d9329d7f39d884c0fa66e3fe` |
| `docs/memory-retrieval-phase2a.md` | `7414300118d678bbf7d1e1c9eba91c473d11044b83fc19d4ebc7f705d702b09b` |
| `scripts/evaluate_memory_retrieval_phase2a.py` | `6371ea3da21fe40845c588ece56679d451ab087d9acf8fa64aa8691a4fbae1ad` |
| `scripts/memory_retrieval_evaluator.py` | `70178d0bda4f705ff59ecb31602179cdb1f3901896aa688f00d95ddf88701389` |
| `scripts/memory_retrieval_phase2a_evaluator.py` | `f0ac492f8ab0d83055cc1e78ada4d38fa249276228e57f3dfc5fd6eacdd3ca3e` |
| `tests/test_memory_retrieval_phase2a.py` | `f5ec44edf9cac7191fc0960dec5992814899864a4fdeb4600dcfcef5fdd25f6f` |
| `tests/test_memory_retrieval_phase2a_evaluator.py` | `ad4693f597b1dbb754520ee883b36fc78b9d4f9e257f79e0b88a6251dd45b0ae` |

Accepted Phase 2A artifact triples:

| Path | Size | mtime_ns |
| --- | ---: | ---: |
| `artifacts/memory-retrieval-phase2a.json` | 2374133 | 1784121750613180196 |
| `docs/memory-retrieval-phase2a-comparison.md` | 914 | 1784121750613434490 |
| `docs/memory-retrieval-phase2a.md` | 1935 | 1784121750613367490 |

### Phase 2B frozen twelve-file set

| Path | SHA-256 |
| --- | --- |
| `artifacts/memory-retrieval-phase2b.json` | `2d082e1aa50c1461a78ef5e18c56b59533460a140634effb911fd6c5b4bd3996` |
| `artifacts/memory-retrieval-phase2b.schema.json` | `a0a9a8093e9970d1fcd275f9d7670804b8b2ecd67ec468b45c13b5ee3390820a` |
| `docs/memory-retrieval-phase2b-comparison.md` | `6e2649e0345f6ec58433d3863a160e8cceb8e8828253cfec842faf35951113e5` |
| `docs/memory-retrieval-phase2b-performance.md` | `3cff028426be913baa06cacbd2eff69b3141f74ff16528d5e44b4f37416a5235` |
| `docs/memory-retrieval-phase2b.md` | `9ec83beff0ab5a5c0b2af3fd65e62f37b441a4416e556b98c751032e51027da9` |
| `scripts/evaluate_memory_retrieval_phase2b.py` | `841883544b031ff5b58ea759a2688413637e70143cd231708514843700ed05dd` |
| `scripts/memory_retrieval_phase2b_evaluator.py` | `d7ab07c72795b2cb49afd1b7235d88ab94dbb2ca60258540b3d84d17f93de785` |
| `tests/fixtures/memory_retrieval_phase2b_holdout.json` | `5ceb46134d0d17060c7b635bb99aeae8a43c799a3f6dd40a07d65978930b1136` |
| `tests/fixtures/memory_retrieval_phase2b_holdout.schema.json` | `c1d4461fcf2e23949585d0742fd20af4d2486d05f1406ad3469c204a21a83ae4` |
| `tests/test_memory_candidate_consolidation.py` | `4c7011ba7168388b88fc58a3fe253366a3d5c19dd68dac36c50c8febdf4de67c` |
| `tests/test_memory_retrieval_phase2b.py` | `496882681aaa5d3281b66669d4d4b8a31a785386400d02a1009e6cee59b8548b` |
| `tests/test_memory_retrieval_phase2b_evaluator.py` | `828bf028c91ed00c6d3d103d4d84e8c5632a0fddd28022b0c6cc11af3f8537c3` |

Accepted Phase 2B artifact/schema/doc triples:

| Path | Size | mtime_ns |
| --- | ---: | ---: |
| `artifacts/memory-retrieval-phase2b.json` | 94181 | 1784815255303450427 |
| `artifacts/memory-retrieval-phase2b.schema.json` | 6408 | 1784815045149215200 |
| `docs/memory-retrieval-phase2b-comparison.md` | 547 | 1784815255324455321 |
| `docs/memory-retrieval-phase2b-performance.md` | 789 | 1784815255324553279 |
| `docs/memory-retrieval-phase2b.md` | 1660 | 1784815255314381393 |

### Frozen call graph and production identity

```text
Phase 2A CLI
  -> evaluate_phase2a_dataset (real perf_counter latency)
  -> write_phase2a_reports (defaults currently point at accepted files)
  -> deterministic_phase2a_view (drops latency, not derived gate booleans)
  -> exit = correctness + quality + all performance_gates

default Phase 2A pytest
  -> cached real report
  -> all performance_gates
  -> direct canonical P95 <= 5
  -> two real evaluations -> timing-free projection equality
  -> CLI default exit == 0

Phase 2B evaluator
  -> imports/evaluates Phase 2A
  -> verifies PHASE2A_FROZEN_HASHES (8)
  -> uses its own existing advisory/strict performance policy

semantic evaluator
  -> imports PHASE2A pins through Phase 2B evaluator
  -> verifies PHASE2B_FROZEN_HASHES (12)
  -> hashes production/Phase1/Phase2A/Phase2B before and after
```

- Full 62-file production hashes were captured and match the v39 manifest.
- Web-search production hashes:
  - `minicode/tools/http_utils.py`:
    `d677707fe69f25147fe98ad51f6bd733276191ff05988c3b32d6734db6d1bd84`
  - `minicode/tools/search_providers.py`:
    `e0baa6e1924feb90d422c2d6fb211c69213a8d9d0a18da49543009ffdc4643d5`
  - `minicode/tools/web_search.py`:
    `c2c2912914ef76024dd4e768bba73ea41fce6dbbc758943d6ed310e07ebbb187`

## RED/GREEN log

- Pure policy REDs first failed on missing function, then on accepted
  bool/NaN/infinity/negative/non-numeric metrics. GREEN rejects all invalid
  inputs with stable fail-closed behavior and passes exact 5.0 ms / saves 2/3
  boundaries.
- The synthetic timing-free RED used two reports that differed only in P95,
  wall-clock gate, strict result and timing-derived acceptance. The old
  projection differed; GREEN normalizes every wall-clock derivative while
  preserving deterministic-gate differences.
- Report RED failed on missing `performancePolicy`. GREEN composes
  correctness, quality, integrity/no-network and policy acceptance, keeps real
  latency and marks legacy `performance_gates` as observation-only.
- CLI RED proved accepted output defaults and wall-clock exit authority.
  GREEN defaults to advisory generated paths, adds explicit strict
  enforcement, rejects unknown arguments and refuses every accepted output
  path before writes.
- Pin RED reported exactly the changed Phase 2A CLI/evaluator/test. GREEN
  updates only those three Phase 2A pins and the consequent Phase 2B evaluator
  pin in semantic certification. Controlled tampering reports one target at
  each layer without rewriting accepted artifacts.

## Final certification evidence

- Current Phase 2A changed SHA values:
  - CLI `24caf504c1b7965cb4ad69e539091a7d741eb4f0a00b9903d1d6a289a48185b5`;
  - evaluator `e65b6ecb59804d7ff5aa04113f6028b64d546c2abf75436175dc40bf39c4a404`;
  - test `bb8193c5c60b4025f96908251c0af8594764dff66c6c80d48e7e780fb4748759`.
- Phase 2B evaluator pin-only SHA:
  `e8c075c3e114c2c5f9c1645e1b53ea365973de883eb3f6a8b2c833ecbef0765d`.
  The semantic evaluator changed only that Phase 2B pin and now hashes
  `188565e6be9e5d36f1af6863cd2ada2afccd4fc164eaadab402c433dc1c35d16`.
- Directed results: Phase 2A 105 passed; Phase 2B 56 passed; semantic freeze
  34 passed.
- The one strict run used `/tmp` only. Pre-run samples were 79.75% and 85.31%
  CPU idle with load 2.37/2.61/2.41. Real canonical P50/P95 were
  1.748625/2.768958 ms; strict, deterministic and final acceptance were true,
  remote calls were zero and exit was 0. No retry occurred.
- First final full suite: 3355 passed, 2 skipped, 3 existing warnings in
  207.13 s.
- Official evaluator: 108 cases, 37 confirmed gaps, Phase 3B true, zero remote
  calls and evaluation passed. Phase 2A/2B frozen gates are true. Behavior
  projection and per-case fingerprint remain `b9fabf0a...bbd60` and
  `b73da444...8667`.
- Second final full suite: 3355 passed, 2 skipped, 3 existing warnings in
  207.33 s.
- Accepted Phase 2A/2B SHA/size/mtime triples and semantic gold are unchanged.
  v39 remains 62/62 with all v1-v39 integrity flags true. Functional Audit
  remains 185 capabilities / 124 pass / 7 issues, with no WEB issue.
- Scoped Ruff, py_compile, compileall and production JavaScript syntax pass.
  pyright, mypy and pip-audit are not installed. Dependencies remain empty.
- The official flow refreshed only generated semantic evaluation JSON and
  three generated semantic Markdown reports. The accepted gold was not
  written.
- The strict temporary output directory was removed. No v40 was created and
  Reliability 1B-2 was not entered.

## Errors encountered

- No unexpected blocker. All failures recorded during implementation were
  intentional TDD REDs.

---

# Reliability 1B-1C Working Notes

## Scope

- Repair only built-in `web_search`.
- Add fixed Baidu/DuckDuckGo HTML providers, deterministic fallback, bounded
  parsing/result projection and truthful low-cardinality failure taxonomy.
- Reuse the v38 safe transport boundary without changing current web_fetch or
  http_request contracts.
- Leave archive, Memory, Agent Loop, Session, RunJournal, MCP, Dashboard and
  Reliability 1B-2 unchanged.

## Frozen evidence

- Default generator/verifier: active `memory-retrieval-production-v38`,
  parent v37; candidate/current true; 60/60; v1–v38 manifest integrity all
  true.
- v38 manifest SHA:
  `49f3319b06289ef23ab8c2f40bc3da0deaf443cb365f654cd2d1683a42b727f3`.
- Accepted gold SHA/size/mtime_ns:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  `3033592`, `1784135857000000000`.
- Runtime `dependencies=[]`.
- Pre-change certified Functional Audit: 185 capabilities; pass 123,
  partial 44, fail 8, unavailable 1, blocked 6, not reachable 3; 9 issues:
  WEB-001, WEB-002, SEC-002, SEC-004, TOOL-001, TOOL-002, TOOL-003,
  SEC-005, MEM-001. `tool.web_search` is fail with deterministic/safety
  partial, truthfulness fail and issues WEB-001/WEB-002.
- Pre-change full suite: `3147 passed, 2 skipped, 3 warnings` in 206.87s.
  The warnings are the existing unregistered benchmark markers.
- Pre-change hashes:
  - `web_search.py`:
    `d559bdaea7b6db3c1874658cf45ce53500db0094e2d57c2b95a783385ae5b50c`
  - `http_utils.py`:
    `3ae9e530039de323c098b7647d1e22e96de5251190d8b75cd5ed65ce79712dde`
  - `network_safety.py`:
    `43a2a82b31f10456178a50ed6eb25028061eacf783bd8c81e815eaf598d3b312`
  - `bounded_resolver.py`:
    `55275c0acb527036f6691646bd689d9be54e1877774c371daf28d59ee89f5523`

## Original web_search graph

```text
ToolRegistry.execute("web_search", input)
  -> web_search._validate
     -> dict.get(query); permissive int(num_results)
  -> web_search._run
     -> urllib.parse.quote(query)
     -> one fixed DuckDuckGo HTML Request
     -> urllib.request.urlopen(timeout=15)
        -> implicit DNS/TLS/redirect transport
     -> response.read() with no byte bound
     -> whole-document regex parser
     -> success/failure output echoes query and raw exception/reason
```

There is no provider abstraction, fallback, shared resolver, destination
validation, IP pinning, per-hop redirect validation, total monotonic deadline,
bounded response read, explicit HTTP-status taxonomy, or challenge/markup-drift
distinction.

## Frozen safe transport seam

```text
normalize_http_request
  -> validate_destination -> one process-local 4/8/12 BoundedResolver
  -> execute_safe_get / execute_safe_http
     -> _open_no_redirect with validated pinned IP and original TLS hostname
     -> explicit per-hop normalize + validate + redirect loop/limit
     -> read_bounded_response (1 MiB, <=64 KiB/read, shared deadline)
     -> SafeHttpResponse
```

The v38 seam deliberately maps final HTTP status >=400 to `http_error`.
Reliability 1B-1C may add a GET-only final-status response interface but must
retain that existing behavior for `web_fetch` and `http_request`.

## Frozen registration path

```text
minicode.tools._CORE_TOOLS
  -> create_default_tool_registry(profile=core)
     -> ToolRegistry
        ├─ main classic non-TTY -> run_agent_turn
        ├─ main TTY -> run_tty_app -> tui.input_handler -> run_agent_turn
        ├─ Headless -> create_agent_turn_runtime -> run_agent_turn
        ├─ Gateway POST /run -> run_headless -> Headless path
        └─ Dashboard Chat -> ConversationTurnService
             -> create_agent_turn_runtime -> run_agent_turn
```

`web_search_tool` is registered exactly once in `_CORE_TOOLS`; the same Tool
definition reaches core TUI, Headless, Gateway and Dashboard composition.

## TDD evidence

- Initial production-entry fallback RED:
  `tests/test_web_search.py::test_web_search_falls_back_once_without_echoing_query`
  failed because the old Tool called only `html.duckduckgo.com`; expected
  `www.baidu.com` followed once by `html.duckduckgo.com`.
- First GREEN establishes the shared GET-only final-status seam, immutable
  provider outcome/result types, fixed Baidu→DuckDuckGo chain, separate
  streaming parsers and thin Tool adapter. The same test now passes and its
  success output omits both the private query and the first provider's failure.
- Strict-input RED: leading newline, trailing tab and a lone surrogate were
  not safely rejected. The first two reached network execution after strip;
  the surrogate caused ToolRegistry to echo codec/input details. GREEN checks
  controls before trim and catches UTF-8 encoding failure as fixed
  `invalid_search_request`.
- Provider/parser/input/fallback/status/URL/transport focused suite:
  `159 passed`.
- Shared-seam compatibility RED: the first refactor exposed
  `response_too_large` for an oversized `urllib.HTTPError` body where the v38
  web_fetch/http_request wrapper contract requires fixed `http_error`. GREEN
  separates the status-observing search seam from the legacy wrapper mode;
  search retains truthful response-budget classification while existing Tools
  retain their exact output.
- Code review RED found Unicode C1 controls were not covered by the prior
  ASCII-only control predicate. GREEN uses Unicode `Cc` classification for
  queries, parsed text and result URLs; the focused control matrix is
  `40 passed`.
- Search/provider/parser + web_fetch/http_request + Tool registration +
  Functional Audit focused compatibility: `333 passed` in the approved
  loopback environment.
- Final v39 manifest SHA:
  `9bcf038d20aa7c044f4db613626b484e2fa89819929be20b46390bca00a99d6e`;
  active/current/candidate match, 62 files, exact delta is changed
  `minicode/tools/http_utils.py` plus added
  `minicode/tools/search_providers.py` and `minicode/tools/web_search.py`;
  v1–v39 integrity is true.
- Complete production-baseline and semantic test matrix:
  `239 passed`.
- Resolver/Permission/Agent/Gateway compatibility:
  `632 passed, 2 skipped`.
- Installed-wheel/non-source-cwd packaging matrix: `9 passed`.
- Functional Audit final generated state: 185 capabilities; pass 124,
  partial 44, fail 7, unavailable 1, blocked 6, not reachable 3; 7 issues:
  SEC-002, SEC-004, TOOL-001, TOOL-002, TOOL-003, SEC-005, MEM-001.
  `tool.web_search` is pass in every required deterministic/installed/safety/
  truthfulness/status field, issues empty, optional live blocked. Audit exit 1
  is retained by design for the seven open issues.
- Interim pre-final-review full pytest:
  `3313 passed, 2 skipped, 3 warnings` in 206.69s.
- Official evaluator: 108 cases, 37 confirmed gaps, Phase 3B true,
  remote calls 0, evaluation passed.
- Post-evaluator gold SHA/size/mtime_ns exactly:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  `3033592`, `1784135857000000000`.
- Interim pre-final-review second full pytest:
  `3313 passed, 2 skipped, 3 warnings` in 206.37s.
- Scoped Ruff, explicit `py_compile`, compileall, JavaScript syntax and
  sensitive/dangerous-shape scans passed. Pyright, mypy and pip-audit are not
  installed.
- Optional external live search was not run. Temporary servers and isolated
  packaging resources are context/temporary-directory owned; no task listener
  was retained.
- Final review RED found a DuckDuckGo `uddg` target could introduce a
  percent-decoded C1 control after the initial URL check. The final production
  code now reapplies the URL text contract after decoding; the RED failed with
  an unsafe projected URL and the GREEN matrix is included in the 159 focused
  tests. This changed the final v39 SHA to
  `9bcf038d20aa7c044f4db613626b484e2fa89819929be20b46390bca00a99d6e`.
- Post-review first complete pytest after the decoded-target fix:
  `3314 passed, 2 skipped, 3 warnings` in 205.85s.
- The post-review official evaluator again passed 108/37/Phase 3B/remote 0,
  and gold remained exact.
- Post-review second complete pytest did **not** pass:
  `2 failed, 3312 passed, 2 skipped, 3 warnings` in 208.12s. Both failures
  were in `tests/test_memory_retrieval_phase2a_evaluator.py`: the deterministic
  view observed different values of
  `canonical_p95_at_most_5_ms`, and the CLI returned 1. Its written report
  records canonical retrieval P95 `5.269083 ms` against the frozen `5.0 ms`
  threshold. A subsequent read-only system sample was `84.38% CPU idle`.
  No production Memory code, threshold, test, manifest or gold was changed,
  and the test was not rerun to select a lucky outcome.
- A subsequent final static review found controls at the beginning/end of a
  raw result URL could be stripped before validation. The trailing-newline RED
  reproduced the acceptance; GREEN checks raw URL controls before trim and
  again after provider redirect decoding. Final scoped, compatibility, wheel,
  baseline and static gates passed; the failed full suite was deliberately not
  rerun.

## Errors encountered

- The declared-oversize test initially expected one body read, while the
  certified reader rejects an oversized `Content-Length` before any read. The
  test oracle was corrected without changing the production limit.
- The first C1 parser oracle required an exact 300-character result after a
  terminal control was replaced and whitespace-trimmed. The contract is a
  maximum, so the oracle now asserts a non-empty length at or below the cap.

---

# Reliability 1B-1B Working Notes

## Scope

- Repair only built-in `web_fetch`.
- Reuse the existing bounded resolver, destination validator, IP-pinned
  HTTP/HTTPS transport and bounded response reader.
- Leave `web_search`, archive, Permission, UI, Agent Loop, Memory, Session,
  RunJournal and MCP unchanged.

## Frozen evidence

- Default generator/verifier: active `memory-retrieval-production-v37`;
  candidate/current true; 59/59; v1–v37 manifest integrity all true.
- v37 manifest SHA:
  `27dda6944d88016ceabcd08960b3b2ef230df7460590d1165b3195ed23adb67b`.
- Gold SHA/size/mtime_ns:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  `3033592`, `1784135857000000000`.
- Runtime `dependencies=[]`.
- Pre-change full suite: `3062 passed, 2 skipped, 3 warnings` in 203.89s;
  warnings are the existing unregistered benchmark markers.
- Functional Audit: 185 capabilities; pass 122, partial 44, fail 9,
  blocked 6, not-reachable 3, unavailable 1; 10 issues:
  WEB-001, WEB-002, SEC-002, SEC-003, SEC-004, TOOL-001, TOOL-002,
  TOOL-003, SEC-005, MEM-001.
- Pre-change hashes:
  - `web_fetch.py`:
    `1e77b1332ada7fb5c5464c201892a1e8a73e0d7ca53908712804efc875a22a58`
  - `http_utils.py`:
    `f7466e280cf897ebf3856a9918a662f24e914b79e96bd61030c0d7b2d05488f4`
  - `network_safety.py`:
    `43a2a82b31f10456178a50ed6eb25028061eacf783bd8c81e815eaf598d3b312`
  - `bounded_resolver.py`:
    `55275c0acb527036f6691646bd689d9be54e1877774c371daf28d59ee89f5523`

## Original call graphs

```text
web_fetch ToolRegistry.execute
  -> web_fetch._validate (prefix-only URL / int(max_chars))
  -> web_fetch._run
    -> _is_safe_url (partial string-prefix block)
    -> urllib.build_opener(LimitedRedirectHandler)
    -> opener.open(timeout=30)
       -> implicit hostname DNS + implicit redirect transport
    -> response.read() with no byte limit
    -> decode/extract/truncate; success/error output echoes URL/details
```

```text
http_request Tool
  -> normalize_http_request
  -> validate_destination -> shared 4/8/12 BoundedResolver
  -> mutation Permission only
  -> explicit redirect loop
     -> per-hop validate_destination
     -> _open_no_redirect with validated pinned IP and TLS hostname
     -> read_bounded_response (1 MiB, 64 KiB reads, shared deadline)
  -> low-cardinality NetworkSafetyError projection
```

## TDD evidence

- Slice 1 initial destination RED: production ToolRegistry returned
  `ok=true`, called the legacy opener and echoed the full
  `172.17.0.1?...fixture-secret` URL. GREEN blocks before transport with
  `destination_blocked`.
- Input RED: 15 failed and 2 already passed. Failures included raw
  AttributeError/OverflowError, input echo, accepted extra fields, string and
  fractional `max_chars`, and NaN/Infinity conversion. GREEN: 18/18.
- Pinned transport RED: initial resolver ran once but `pinned_calls == 0`;
  legacy urllib opener handled the request. GREEN passes the validated
  `93.184.216.34` destination to `_open_no_redirect`, preserves the normalized
  TLS hostname and strips fragment.
- Redirect RED: a safe relative Location returned `redirect_blocked`. GREEN
  revalidates/re-pins every hop, allows exactly three redirects and rejects the
  fourth, detects loops, blocks unsafe/mixed/rebound targets before a second
  send, and shares one deadline.
- Rendering RED: mixed-case `Charset` produced `request_failed`; uppercase
  SCRIPT/STYLE would not be removed. GREEN uses standard-library MIME parsing,
  case-insensitive removal and HTML entity decoding.
- Post-refactor focused result: 76 web_fetch tests; combined web_fetch +
  http_request 144; combined with BoundedResolver 159, all passed.

## Current deep module

```text
web_fetch validator
  -> normalize_http_request(GET, fixed identity headers, 30 s total budget)
  -> execute_safe_get
     -> execute_safe_http
        -> validate_destination -> shared 4/8/12 BoundedResolver
        -> _open_no_redirect -> pinned IP / TLS original hostname
        -> per-hop normalize + validate + loop/limit
        -> read_bounded_response (1 MiB, <=64 KiB/read, same deadline)
        -> SafeHttpResponse(status, content_type, content_encoding, payload)
  -> bounded HTML/JSON/text rendering + max_chars
  -> content-free ToolResult

http_request
  -> existing normalization and mutation Permission
  -> the same execute_safe_http seam
  -> existing safe response renderer
```

## Final Reliability 1B-1B outcome

- Production delta is exactly `minicode/tools/http_utils.py` changed and
  `minicode/tools/web_fetch.py` newly protected. `network_safety.py`,
  `bounded_resolver.py`, `web_search.py` and archive implementations are
  unchanged.
- `tests/test_web_fetch_safety.py` finishes at 78 passed. The combined
  web_fetch/http_request/bounded-resolver suite is 161 passed; the broader
  Tool/TUI/Permission/Gateway Chat compatibility matrix is 391 passed.
- Installed wheel/packaging/Gateway smoke is 9 passed from a non-source cwd,
  with core discovery, JSON/HTML/text, private/mixed/redirect-private,
  dns_error/timeout/resolver_busy and oversized-response cases.
- Functional Audit contract is 4 passed. Final matrix: 185 capabilities,
  pass 123, partial 44, fail 8, unavailable 1, blocked 6, not reachable 3,
  9 issues. SEC-003 is absent; SEC-004 is archive-only; web_fetch is all-pass.
- v38 parent is v37; 60/60, candidate/current true, v1-v38 integrity true.
  Manifest SHA is
  `49f3319b06289ef23ab8c2f40bc3da0deaf443cb365f654cd2d1683a42b727f3`.
  The controlled tamper reports only `minicode/tools/web_fetch.py` and does
  not rewrite the manifest.
- Baseline/core tests: 196 passed. Semantic evaluator tests: 32 passed.
  Official evaluator: 108 cases, 37 confirmed gaps, Phase 3B true, remote
  calls 0, evaluation passed.
- Accepted gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3033592 and mtime_ns 1784135857000000000.
- Scoped Ruff, py_compile, full compileall and all formal JavaScript syntax
  checks passed. pyright, mypy and pip-audit are not installed. Dependencies
  remain `[]`.
- Two complete pytest runs passed at `3147 passed, 2 skipped, 3 warnings` in
  210.38s and 210.29s. Warnings are the three existing benchmark marks.
- Optional live external-network smoke was not run. No live result is claimed.
- All temporary servers/threads/wheels/install targets/HOMEs/workspaces are
  context-managed or temporary and no task listener remains.

---

# Reliability 1B-1A.1 Working Notes

## Immutable start

- Active verifier: `memory-retrieval-production-v36`; manifest SHA
  `7d576aed1594c58e96d3125c28e2556ffab7bb60ccdd43c97b462201456a678a`;
  58/58 protected; candidate/current true; v1–v36 integrity true.
- Pre-change hashes:
  - `minicode/tools/network_safety.py`:
    `a8d3d09ef54f85a17f57e3457589292eb698f83f588a6396a0eb00f42bde0914`
  - `minicode/tools/http_utils.py`:
    `f7466e280cf897ebf3856a9918a662f24e914b79e96bd61030c0d7b2d05488f4`
- Accepted gold: SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3,033,592, mtime_ns 1,784,135,857,000,000,000.
- `pyproject.toml` runtime `dependencies = []`.
- Pre-change gates: HTTP 69, Permission/TUI/Dashboard 113, packaging 9,
  Functional Audit 4; full suite 3,042 passed, two skipped and the three
  existing benchmark-marker warnings in 201.71 seconds.
- Restricted-sandbox runs that bind loopback failed only with
  `PermissionError`; identical authorized-localhost commands passed.

## Original DNS-001 reproduction

- One process started with one live thread.
- Twenty-five sequential `validate_destination()` calls used a controlled
  blocking `getaddrinfo()` and a 10 ms monotonic deadline.
- All 25 returned `timeout`, all 25 bottom resolver calls entered, and the
  process had 26 live threads afterward: retained growth exactly 25.
- Releasing the fixture reduced retained test threads to zero.
- The first Slice 1 test failed at `assert entered <= 4` with `25 <= 4`.

## Chosen resolver seam

- `BoundedResolver.resolve(hostname, port, deadline)`, `snapshot()` and
  non-blocking `close()` hide a fixed daemon-worker implementation.
- Initial fixed limits: four workers, eight queued work items, twelve maximum
  outstanding items. Worker names contain only a fixed ordinal.
- `network_safety.validate_destination()` remains the public destination seam
  and translates content-free `ResolverError` codes to `NetworkSafetyError`.
- Slice 1 GREEN: 25 timeouts enter at most four bottom resolver calls and
  retain at most four fixed workers.
- Slice 2 direct saturation evidence: with 2 workers and 3 queued items, 100
  additional calls all returned `resolver_busy`; active+queued stayed 5 and
  bottom resolver entry count stayed 2.

## Final Reliability 1B-1A.1 outcome

- Production seam: one process-local `BoundedResolver`, four daemon workers,
  eight queued work items and twelve outstanding items; saturation is the
  low-cardinality `resolver_busy` error.
- Twenty-five controlled timeouts entered no more than four bottom resolver
  calls. One hundred additional controlled timeouts on the same resolver did
  not add workers or grow the thread set.
- Queued expiry, active abandonment, result discard, recovery, idempotent
  non-blocking close, concurrent deadlines, close/submit races, exception
  redaction and real child-process exit all passed deterministic tests.
- Focused final suites: resolver/HTTP/packaging 92;
  Permission/TUI/Dashboard 113; Gateway/Chat/Cancel/Turn 153;
  Functional Audit 4; baseline 189; semantic evaluator tests 32.
- Final Functional Audit: 185 capabilities, 10 open issues;
  `tool.http_request` pass with bounded resolver evidence and no issue;
  SEC-001 absent/closed; WEB-001, WEB-002, SEC-002, SEC-003, SEC-004,
  SEC-005, MEM-001, TOOL-001, TOOL-002 and TOOL-003 remain.
- v37: parent v36, 59/59, candidate/current true, v1–v37 integrity true,
  manifest SHA
  `27dda6944d88016ceabcd08960b3b2ef230df7460590d1165b3195ed23adb67b`,
  exact one changed + one added + zero removed.
- Official evaluator: 108 cases, 37 confirmed gaps, Phase 3B true,
  zero remote calls and evaluation passed.
- Final full suites: `3062 passed, 2 skipped, 3 warnings` in 203.80s and
  203.66s. Warnings are the existing unregistered benchmark markers.
- Gold remained byte-identical: SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3033592, mtime_ns 1784135857000000000.
- Scoped Ruff, py_compile, full compileall, both JavaScript syntax checks and
  dangerous-pattern scans passed. pyright, mypy and pip-audit are not
  installed.
- Runtime dependencies remain empty. `http_utils.py` retained SHA
  `f7466e280cf897ebf3856a9918a662f24e914b79e96bd61030c0d7b2d05488f4`.
- This batch did not wire `web_fetch` or `web_search`, change archive behavior,
  or enter Reliability 1B-1B.

---

# Functional Reliability Audit 1A Working Notes

## Immutable starting point

- No `AGENTS.md` or `CONTEXT.md` exists in the project tree.
- Baseline verifier: v35 active, 56/56 protected files, candidate/current true,
  and every v1-v35 manifest pin valid.
- Pre-audit full pytest: 2956 passed, 2 skipped, 3 existing unknown benchmark
  mark warnings, 192.28 seconds.
- Accepted semantic gold SHA/size/mtime_ns:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  `3033592`, `1784135857000000000`.
- Python 3.13.13 on macOS 15.5 arm64. Runtime dependencies are empty. Ruff and
  Node exist; pyright, mypy and pip-audit do not. There is no Git metadata.

## Required pre-audit hashes

- `pyproject.toml`:
  `1d6a9df71501c1e614e97a23f84b5d977ece3dcb48ede78e4f8f324f4a6a347f`
- active v35 manifest:
  `bc2f16ee8f19dc7d59b878e35324486acd0cd110f16602ed722d3f4163572fc4`
- formal index/styles/app:
  `49c991efa9b10344a7272113a2177f9b64929d1b73fbf84b595153dd0d44a38b`,
  `bc9b13b94354650ad549c8c96f8285984ba4d6d48f914de7c737f04d19686255`,
  `ec43c62349dca520cd9e4ce5c42bba16638dc2e7d230431b43bcbf45cc3fa001`
- `web_search.py`:
  `d559bdaea7b6db3c1874658cf45ce53500db0094e2d57c2b95a783385ae5b50c`
- `web_fetch.py`:
  `1e77b1332ada7fb5c5464c201892a1e8a73e0d7ca53908712804efc875a22a58`
- `tooling.py`:
  `267bead40137dfb0133b4dae797830b0150deb9ac744f7bd66b5d6cfbe7a0d88`
- `tools/__init__.py`:
  `7ae5a05d64801ac5faee5f1d2547a7c6d3f8cdbe9c6f6a041a64351bb9a1ae0d`

## Seed Web evidence

- The production `web_search` is a built-in ToolDefinition, not MCP. It has one
  hard-coded DuckDuckGo HTML provider, a 15-second request timeout, a single
  regex parser and no fallback or structured failure taxonomy.
- Same-process live probe: DuckDuckGo DNS resolves but both HTML and main HTTPS
  endpoints time out; Baidu returns HTTP 200 in about 92 ms.
- Therefore MiniCode is not globally offline. The current built-in search
  provider path is unavailable; `web_fetch` remains live for reachable hosts.

## Runtime capability discovery

- Isolated `create_default_tool_registry()` authority: 26 default core Tools
  and 27 full-profile utility Tools, 53 unique registered Tools total.
- Static AST discovery: 59 named `ToolDefinition` calls plus one dynamic MCP
  construction site. The difference is explained by four fixed MCP Tools, the
  dynamic MCP factory, and source-only `modify_file`, which the registry
  explicitly removes as duplicate behavior.
- Formal console scripts: `minicode-py`, `minicode-headless`,
  `minicode-gateway`, and `minicode-cron`.
- The audit runner currently discovers 27 literal HTTP route strings; route
  families are additionally reconciled to focused Gateway/Dashboard tests.
- The runner is default-offline, removes credential-like environment
  variables, creates a private HOME/MINI_CODE_DIR/Workspace, and never imports
  user configuration before isolation.

## Deterministic findings reproduced

- `web_search`: DuckDuckGo HTML is the only provider; fixed parser treats
  challenge and changed markup as empty.
- `http_request`: on an isolated loopback fixture, a POST occurs without
  permission review or destination restriction (requires localhost-capable
  test sandbox).
- Archive creation: `gzip_compress`, `tar_create`, and `zip_create` accept
  `../` destinations and create sibling files outside the isolated Workspace.
- `web_fetch`: 172.17.0.1 passes the private-address guard and redirect targets
  are not revalidated.
- `read_file`: a missing file is reported as successful zero-length content.
- Tool crash diagnostics include the isolated absolute Workspace path.
- Memory: “小花是我唯一的好朋友。” produces no persisted/searchable fact;
  a separate `web_search` timeout trace produces an inactive pending
  `error_pattern` entry. These are distinct capabilities.

## Installed and live evidence

- Focused regressions: entrypoint/Tool/TTY/packaging 97 passed;
  Session/Turn/RunJournal/Memory 304 passed; Skill/fake-MCP/Permission 197
  passed; Dashboard REST/SSE/Chat/actions/data health/visual 277 passed.
- Standard `python -m build` is unavailable because this environment's `build`
  package lacks `build.__main__`. `pip wheel --no-deps
  --no-build-isolation` produced `minicode_py-0.1.0-py3-none-any.whl`, SHA-256
  `ea08c57653d51c414a73b0471a79f790794d7d7044f34378236b9838133db7b9`.
- An isolated venv imported `minicode` from site-packages while cwd was
  `/private/tmp`; all 53 Tools and the final 185 capabilities were discovered.
- The final certified wheel SHA-256 is
  `1f8df972a37a0bd22eaa9e0135c97636f912aae851bab75dcaa4c6e8842f4670`;
  the packaged audit runner SHA matches the workspace runner exactly at
  `81474b59cc9e8c60e07535c9005223297778a03fa33fe6fa98245a55dd558b2a`.
- Final corrected live smoke at `2026-07-24T17:15:15Z`: installed
  `web_search` timed out after 9012 ms; Example HTTPS returned 200 in 660 ms;
  Baidu search returned 200 in 357 ms; installed `web_fetch` returned 200 in
  793 ms; installed full-profile `http_request` returned 200 in 1376 ms.
  Total 12201 ms. Earlier harness-correction attempts included one genuine
  `web_fetch` timeout, so stability remains partial even though the final
  point-in-time result passed.
- Installed-wheel browser: all 8 primary routes and 6 Memory subroutes loaded.
  At 1280, 700 and 480 px, document scroll width equalled viewport width.
  Narrow overlays closed cleanly and restored the main panel to 0–480 px.
  Browser warning/error log count was zero.

## Original Audit 1A certification (pre-Reliability 1B-1A)

- Final matrix: 185 unique capability IDs; pass 121, partial 44, fail 10,
  unavailable 1, blocked 6, not_reachable 3, not_tested 0.
- Issues: P0 3, P1 5, P2 2, P3 1.
- Final full suite: 2960 passed, 2 skipped, 3 existing benchmark-mark
  warnings in 199.44 seconds.
- Ruff, py_compile, compileall, both JavaScript checks, matrix contract,
  redaction scan and dangerous-pattern scan pass.
- v35 remains 56/56, candidate/current true, v1-v35 integrity true. Gold and
  every frozen production hash remain byte-identical.
- All named `/tmp/minicode-functional-audit*` artifacts are removed; port
  18791 refuses connections and no listener remains.

---

# Reliability 1B-1A Working Notes

## Immutable start

- v35 verifier: active `memory-retrieval-production-v35`, 56/56, candidate and
  current true, all historical manifest pins true.
- Gold SHA/size/mtime_ns:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  `3033592`, `1784135857000000000`.
- Pre-change production hashes:
  - `http_utils.py`: `d3c7bac7488124b7f55de672035a23442ac1375a030ea40a9d3095fa34fbdc6b`
  - `tooling.py`: `267bead40137dfb0133b4dae797830b0150deb9ac744f7bd66b5d6cfbe7a0d88`
  - `permissions.py`: `707e51cc32959f1a50297932bf2a2d01a4881af21752127a4f03c820fa8bbc45`
  - `permission_approval.py`: `62bbdeac5df8057fd819f6fc79d2dddbcaef2db319f99fc72a3392c670fc1991`
  - `permission_event_contract.py`: `a6c6883e4a9b7f0ec19fb3fcbf100c92f0484468f2417cb1eb7c1fab956e3881`
  - `web/permission_http.py`: `bfa2f062135a77652b7911d80db547f39db6e7237c840906e2f1dc30533944dc`
  - `tui/tool_lifecycle.py`: `ad4aef325c1e3d8fb24a105fe83e2953b5e657c19a35b6745432ed3273e2713d`
  - `tui/tool_helpers.py`: `b590ff5051074d7a784819b0aa6b814377e706522034f6745432ed3273e2713d`
  - formal `app.js`: `ec43c62349dca520cd9e4ce5c42bba16638dc2e7d230431b43bcbf45cc3fa001`
  - `pyproject.toml`: `1d6a9df71501c1e614e97a23f84b5d977ece3dcb48ede78e4f8f324f4a6a347f`
- Non-sandbox full suite: 2960 passed, 2 skipped, 3 existing benchmark-mark
  warnings in 235.59 seconds.
- Focused suites: Permission 100, Tooling 67, Gateway/Chat 172, Packaging 9,
  Functional Audit 4.

## Original SEC-001 truth

- Full-profile production `http_request`, real random loopback fixture,
  `permissions=None`.
- Input: POST with a small JSON string body and 3-second timeout.
- Result: `ToolResult.ok == true`.
- Fixture calls: exactly `[{"method": "POST", "path": "/mutation"}]`.
- Pending approvals: 0; there was no PermissionManager or broker in the call.

## Existing seam

- `ToolRegistry.execute` validates and invokes `ToolDefinition.run`.
- `ToolContext.permissions` carries the current PermissionManager.
- Gateway chat replaces `PermissionManager.prompt` with the Turn-scoped
  `PermissionApprovalSession.prompt` and sets `operation_checkpoint` to
  `PermissionApprovalSession.check_operation`.
- File writes and commands already call a final operation checkpoint immediately
  before the side effect.
- The approval broker has process-local one-operation records, versioned safe
  projections, cancellation/timeout/close state, strict HTTP decisions and
  content-free Run events. It currently accepts only edit/command/path kinds.
- Dashboard `app.js` has a strict union validator and deny-only behavior for
  unsafe reviews; it currently accepts only edit/command/path.

## Implementation seam

- Add `minicode/tools/network_safety.py` as the deep module:
  normalization, budgets, URL/DNS/address policy, immutable review/fingerprint,
  redirect policy, pinned standard-library transport, bounded streaming and
  stable safe error projection.
- `http_utils.py` becomes a thin Tool adapter:
  normalize → destination validate → permission → bind/revalidate → final
  checkpoint → execute bounded request → safe ToolResult.
- `PermissionManager` adds one operation-only network authorization interface.
- Broker/TUI/Web receive only the strict network review projection; response
  data and Tool inputs never enter approval or Run events.

## Final Reliability 1B-1A outcome

- Production boundary: immutable request normalization, public-only
  IPv4/IPv6/DNS classification, DNS pinning, fresh mutation approval,
  post-approval destination/fingerprint binding, final cancellation checkpoint,
  manual GET/HEAD redirects and bounded safe response projection.
- Final focused tests: HTTP 69; Permission/TUI/Dashboard 113;
  Gateway/Chat/Cancel 150; Tooling/RunJournal 65; Functional Audit 4;
  packaging 9; installed-wheel smoke 1; baseline/semantic 215.
- Final full suites: `3042 passed, 2 skipped, 3 warnings` in 201.97s and
  202.00s. Warnings are the existing unregistered benchmark markers.
- v36: parent v35, 58/58, candidate/current true, v1–v36 integrity true,
  manifest SHA
  `7d576aed1594c58e96d3125c28e2556ffab7bb60ccdd43c97b462201456a678a`,
  exact 5 changed + 2 added + 0 removed.
- Official evaluator: 108 cases, 37 confirmed gaps, Phase 3B true,
  zero remote calls. Gold SHA/size/mtime_ns remains
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  `3033592`, `1784135857000000000`.
- Final Functional Audit: 185 capabilities, 122 pass, 44 partial, 9 fail,
  10 open issues. SEC-001 is absent; `tool.http_request` is pass with no issue;
  SEC-004 remains only for `web_fetch` and archive.
- Browser: 1280×900 and 700×900 had no horizontal overflow, unsafe network
  review was deny-only, valid POST review showed only safe fields plus
  Allow-once/Deny, console warning/error count was zero.
- Static: scoped Ruff, py_compile, full compileall, app.js/cost-format.js
  syntax and scoped security scan passed. pyright, mypy and pip-audit are not
  installed.
- Scope: `web_fetch`, `web_search`, archive, Agent Loop, Memory, Session, MCP,
  runtime dependencies, semantic gold and performance thresholds were not
  changed. Reliability 1B-1B was not entered.

---

# Batch 9D-1B Working Notes

## Final outcome

- The user-selected A / Agent Observatory is now the formal Overview hierarchy.
  Runs, Sessions and all six Memory routes share the same editorial core-page
  system without changing any Store, action or backend authority.
- Production delta is exactly `index.html`, `styles.css` and `app.js`.
  `cost-format.js` and every Python runtime source remain byte-identical.
- The Overview projection reuses the existing Runs list/detail REST routes and
  existing single EventSource invalidation. It adds no timer, poller, write path
  or mock fallback.
- Browser acceptance covered 1920/1280/768/375 px, all eight primary routes and
  all six Memory routes. Final 1280×900 three-column view had no document
  overflow, no stuck loading and an empty page console.
- Focused Observatory + visual tests passed 35; frontend action/SSE compatibility
  passed 75; Dashboard web + visual + Observatory passed 100.
- Full pytest passed `2956 passed, 2 skipped, 3 existing warnings` in 192.76s.
  Default Phase 2B evaluator passed 28, the combined Phase 2B tests passed 36,
  and baseline/semantic tests passed 209.
- v35 manifest SHA is
  `bc2f16ee8f19dc7d59b878e35324486acd0cd110f16602ed722d3f4163572fc4`;
  active verification is 56/56 with v1-v35 integrity and exact three
  changed/zero added/zero removed files.
- Official evaluator remains 108 cases, 37 gaps, Phase 3B true, zero remote
  calls and passed. Accepted gold SHA/size/mtime_ns remain
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  `3033592`, `1784135857000000000`.
- Final wheel SHA is
  `f028318b6ebf2c7286faef45e6d79fb5a86f857910b95f77b1f705f270970516`.
  Its isolated non-source Gateway served exact packaged resource hashes,
  no-store headers, health aliases, structured API 404 and traversal rejection.
- Ruff, full compileall and both JavaScript checks pass. Batch 9D-1B is closed;
  9D-1C is next, while 9A-2/9A-3/9B/9C remain deferred.

---

# Batch 9D-1A Working Notes

## Final outcome

- Formal production changes are exactly `index.html`, `styles.css` and
  `app.js`; `cost-format.js` remains byte-identical. No backend, Store,
  transport, authority, evaluator threshold, dependency or page-internal
  business structure changed.
- The visual contract started at `19 failed, 8 passed` and reached 28 passes.
  The final broad Dashboard/Chat/Permission/Memory approval/deletion/SSE/Data
  Health/packaging matrix passed 215 tests; baseline tests passed 171, and the
  active-v34 semantic/baseline correction rerun passed 172.
- Final wheel SHA is
  `b472c5a9bbbb1f195a10673c5ad8cedf9ea1520820d33c9257bb08bbeb2ac61a`.
  Its isolated non-source Gateway served exact source hashes, correct
  no-store/Content-Type headers, both health routes, safe structured 404s and
  path-traversal rejection.
- Real installed browser evidence covers all routes, Memory subroutes, Light and
  Dark, 1280/1024/700/480 widths, real long Run/Session/Memory content,
  Memory/Tool approvals, deletion dialog, Data Health, resize, collapse/reopen,
  Draft preservation, focus return and SSE fallback. Page console logs were
  empty and no overflow, `[object Object]`, secret or formal absolute path was
  found.
- The first full attempt exposed one stale active-v33 semantic certification
  assertion (`2942 passed, 1 failed`). It was updated to retain v1-v33 as
  history and v34 as active. The two required final suites then passed
  `2943 passed, 2 skipped, 3 existing warnings` in 193.74s and 194.66s.
- v34 SHA is
  `3136e096a97192de5078882523106f5179cb20a3e9885c050fd187038f815cbb`;
  parent v33 remains
  `a5a6c84205d68c6c30f85724f1091d06593cf203dc8390514731d1b65e995313`.
  Verifier: 56/56, candidate/current true, v1-v34 integrity true, exact three
  changed/zero added/zero removed. Phase 2B passed 28 tests.
- Official evaluation remains 108 cases, 37 confirmed gaps, Phase 3B true,
  zero remote calls and passed. Accepted gold SHA/size/mtime_ns remain
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  `3033592`, `1784135857000000000`.
- Scoped Ruff, `py_compile`, full `compileall` and both JavaScript syntax checks
  pass. pyright and mypy are not installed. Batch 9D-1A is complete; 9D-1B is
  next, while 9A-2/9A-3/9B/9C remain deferred and 9D-2 remains a future Visual
  RC only.

## Pre-edit certification and evidence

- Active baseline v33 revalidated with parent v32, 56/56 protected files,
  candidate/current equality and v1-v33 manifest integrity.
- v33 SHA is
  `a5a6c84205d68c6c30f85724f1091d06593cf203dc8390514731d1b65e995313`.
  Accepted semantic gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime_ns `1784135857000000000`.
- Phase 2B passed 28 tests. The pre-edit full suite passed
  `2909 passed, 2 skipped, 3 warnings` under the localhost-capable profile.
- Pre-edit formal frontend SHA-256:
  - `index.html`:
    `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`
  - `styles.css`:
    `1e69af9631580c313822924caa593ab479312423b7559a482cf858996d4f9ac0`
  - `app.js`:
    `4fdf949e96483ce4e2de83a509de5cd61b6fc3bdad6227519049f37d1fe2838e`
  - `cost-format.js`:
    `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`
- Runtime dependencies remain `[]`.
- `python -m build --wheel` is unavailable because the installed `build`
  package has no `build.__main__`. The repository-compatible pre-edit wheel was
  built with `pip wheel --no-deps --no-build-isolation`; SHA-256
  `70ae2785ddd504d1d87f0f9312b61a89c4ac01ccb1a34e0c19ef9cd4fc80177b`.
  It was installed into an isolated virtual environment and served the formal
  Dashboard from a non-source working directory.

## Waku and before-browser findings

- Actual Waku source exists at `/Users/zhourunbo/code/Waku Agent`; the audit is
  recorded in `docs/minicode-dashboard-batch-9d-1a-waku-audit.md`.
- Its directly evidenced Shell defaults are 208 px navigation, 380 px Dock,
  5 px resizers, warm neutral surfaces, compact typography, fine borders and
  shadow only for overlays/floating controls.
- The retained MiniCode prototype confirms the selected hierarchy but remains
  disposable and untouched.
- Before screenshots are under `/tmp/minicode-9d1a-visual-evidence/before`.
  At 1280×900 the measured widths were nav 208, main 682 and Dock 380 with no
  document overflow. At 700 px the Dock correctly became an overlay; at 480 px
  both side panels remained reopenable.
- Primary visual debt: weak contrast/hierarchy, excessive nested boxes, noisy
  Dock authority copy, inconsistent control language and ungrouped legacy
  tokens. Draft text survived narrow Dock close/reopen before the refactor.

---

# Batch 9A-1 Working Notes

## Implementation outcome

- Added `PersistenceHealthReader.snapshot()` as the only persistence-health
  authority. It scans 25 fixed Store projections without constructing managers,
  following symlinks, accepting paths, acquiring write locks, or invoking
  migration/recovery/retention/cleanup behavior.
- Session ownership is established from the canonical shared index before a base
  or delta is parsed. Foreign Workspace Session bodies are not read or counted;
  unattributable base files produce an honest `index_drift` partial result.
- Added strict query-free `GET /api/v1/data-health`, schema v1 validation,
  `no-store`, bounded safe errors and installed-wheel/non-source-cwd coverage.
- System now renders read-only Data Health loading/empty/live/partial/error/retry
  states and uses the existing EventSource only to trigger GET refresh.
- Browser inspection caught a CSS class collision between Store status `live` and
  the global `.live` indicator rule. Status classes are now prefixed
  `status-*`; the 1280px and 700px layouts are visually stable and overflow-free.
- Production baseline v33 contains exactly four changed files and two added files,
  protects 56 files, and has manifest SHA
  `a5a6c84205d68c6c30f85724f1091d06593cf203dc8390514731d1b65e995313`.

## Pre-edit baseline evidence

- Active verifier: `memory-retrieval-production-v32`, candidate/current matches,
  54/54 files, v1-v32 integrity true, exact v31→v32 frontend-only delta.
- v32 manifest SHA-256:
  `9680f6f4bb61d3489a98fd63cff01d99f6a5af2c98891befbfb6c513fc023fb1`.
- Accepted semantic gold SHA-256:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime_ns `1784135857000000000`.
- Official evaluator: 108 cases, 37 confirmed gaps, Phase 3B true, zero remote
  calls, evaluation passed.
- Formal frontend SHA-256: index
  `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`,
  app `62815e4b3bfe79f498e6426a184f7bd256131bd8e52296d401c40160e1f07126`,
  styles `647c5a63d1552e2b4f1b8a0edfe3a14b8b1abfa66189028d6b93f4d1b212d376`,
  cost format
  `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`.
- Runtime dependencies remain exactly `[]`.
- Full-suite execution under the restricted profile is invalid for HTTP tests
  because loopback bind is forbidden. Under the approved profile, two attempts
  produced 2853/2854 passes solely because the same Phase 2B wall-clock
  performance gate exceeded its threshold; immediate targeted rerun passed 2/2.
  Treat this as recorded starting scheduling noise, not a certified clean suite.

## Batch 9A-1.2 final deterministic certification

- RED evidence is the same-code adjacent observation pair `2.837334 ms` /
  `3.006917 ms` around the unchanged `2.866455 ms` material limit. This proved
  that the prior default wall-clock release assertion was not deterministic on a
  shared desktop.
- Phase 2B evaluator version 1.1.0 now exposes a pure
  `evaluate_performance_policy()` seam. It validates finite non-negative timing
  values, non-negative integer counts and explicit `advisory|strict` mode;
  bool, NaN, Infinity, negative, fractional-count and invalid-mode inputs fail
  closed. Threshold comparisons remain inclusive and unchanged.
- `acceptance_passed` now represents correctness, integrity, deterministic
  holdout equality, no-network and candidate-cap invariants. Real P50/P95 and
  peak-memory observations remain in the JSON/performance Markdown.
  `performance.enforcementMode`, `strictPassed`, `deterministicPassed`,
  classified gate maps and observations make the distinction explicit.
- The frozen default advisory artifact recorded real canonical P95
  `2.917250 ms`, `strictPassed=false`, `acceptance_passed=true`, proving the
  report does not conceal the timing miss. The sole explicit strict benchmark
  later passed honestly at `2.770667 ms` versus reference `2.1233 ms` and limit
  `2.866455 ms`; it was not retried.
- Default Phase 2B tests passed three consecutive runs at `26 passed`.
  The default CLI's two deterministic-core outputs are byte-identical, and the
  explicit strict CLI flag is the only way to make real wall-clock gates affect
  its exit status.
- Full pytest passed twice at `2907 passed, 2 skipped, 3 existing warnings`.
  v33 remains active with parent v32, 56/56 files, candidate/current matches and
  v1-v33 integrity. Official semantic evaluation remains 108 cases, 37 gaps,
  Phase 3B true, zero remote calls and passed.
- Accepted gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime_ns `1784135857000000000`. Accepted/generated complete
  projection SHA remains
  `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`;
  per-case fingerprint remains
  `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
- Scoped Ruff, targeted `py_compile`, full `compileall`, and both frontend
  `node --check` commands pass. pyright and mypy are not installed. No
  production/frontend byte changed, so the existing wheel and browser evidence
  is reused. No v34 was created and Batch 9A-2/9A-3 was not implemented.

## Batch 9A-1.2.1 residual default wall-clock assertion removal

- The pre-edit audit reconfirmed active v33, parent v32, 56/56 protected files,
  candidate/current matches, v1-v33 manifest integrity, v33 SHA
  `a5a6c84205d68c6c30f85724f1091d06593cf203dc8390514731d1b65e995313`
  and the accepted gold SHA/size/mtime triple. The RED was the remaining direct
  default assertion that the real consolidator-100 P95 must be at most 10 ms.
- That assertion was replaced only in
  `tests/test_memory_retrieval_phase2b_evaluator.py`. The real observation must
  exist, be a finite non-negative non-bool number, match the formal report, and
  produce the exact consolidator and canonical wall-clock gates.
  `strictPassed` must equal all wall-clock gates; advisory exit ignores a
  wall-clock miss while strict exit enforces it.
- Pure synthetic policy tests now cover canonical-pass/consolidator-fail,
  canonical-fail/consolidator-pass, both-fail, both-at-threshold,
  `network_call_count=1`, `retained_count_500=257`, and
  `retained_count_1000=257`. Any deterministic failure rejects both advisory and
  strict modes. The scoped Phase 2B/semantic-gap/baseline default-test audit
  found no second real benchmark result controlling pytest success.
- The test SHA advanced from
  `fc36869382c4f8a41b33188374543b68eedae4d14ed5fd50cfb31c97a158706d`
  to
  `828bf028c91ed00c6d3d103d4d84e8c5632a0fddd28022b0c6cc11af3f8537c3`
  with reason `remove_remaining_default_wall_clock_assertion`. Only its entry in
  `PHASE2B_FROZEN_HASHES` changed; all other 11 pins match their prior values and
  their files.
- Three consecutive default Phase 2B runs each passed 28 tests. The single,
  non-retried strict run exited 0 with canonical P95 `2.794834 ms`,
  consolidator-100 P95 `2.680833000340499 ms`, reference `2.1233 ms`, material
  limit `2.866455 ms`, `strictPassed=true`, and zero remote calls.
- Full pytest passed twice at `2909 passed, 2 skipped, 3 existing warnings`
  (191.41s and 191.78s). v33 verification and the official 108-case/37-gap/
  Phase-3B/remote-0 evaluator passed with `phase2b_assets_unchanged=true`.
  Scoped Ruff, four-file `py_compile`, full `compileall`, and both JavaScript
  syntax checks passed.
- Accepted/generated semantic projection remains
  `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`;
  per-case fingerprint remains
  `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
  The strict temporary and frozen formal deterministic cores are byte-identical
  at `f47002d15be904b9f73953a0e7a537c1fd14c327810129bafb8fcb6c51873559`.
- The formal Phase 2B artifact remains SHA
  `2d082e1aa50c1461a78ef5e18c56b59533460a140634effb911fd6c5b4bd3996`,
  size `94181`, mtime_ns `1784815255303450427`. Production/frontend bytes
  match v33, so the prior isolated-wheel and browser evidence is reused. No
  production, algorithm, threshold, fixture, manifest, gold or accepted truth
  changed; no v34 exists and Batch 9A-2/9A-3 was not entered.

---

# Batch 8D Planning Notes

## Batch 8D-1 implementation outcome

- Added deep Conversation and Project Memory deletion authorities, a shared
  content-free fence/finite-receipt ledger, and one strict thin HTTP adapter.
- Conversation deletion is fence -> terminal Turns -> terminal Runs -> Session
  delta/base/index last -> verify -> receipt. Session save, Turn Session attach
  and linked Run creation honor the fence.
- Project deletion revalidates inside the existing Memory RLock/flock writer,
  removes target audit records and backlinks, rebuilds Project indexes, writes
  Memory then audit, verifies, and completes its receipt while still locked.
- GET preview remains no-write. Partial deletion, Gateway restart, lost response,
  duplicate POST, stale revision, active writer, orphan representations and
  forged-ID distinction have direct tests.
- Added approval-audit stat observation to the existing `memory` Change Feed
  resource; no SSE schema/connection or frontend byte changed.
- v31 protects the exact 7 changed + 4 added production files. Current manifest
  SHA is `d0ea9a10ccd45d6f8e7807f92acfc38afce801f22e8be0967897653aed82fbae`;
  active verifier matches 54/54 and v1-v31 integrity is true.
- Final focused certification passed 555 tests. Two post-evaluator, post-fix
  full suites passed 2845 tests with 2 existing skips and 3 existing benchmark
  mark warnings in 187.31s and 187.91s.
- Final wheel SHA is
  `d52d98d3c6eb124eb24661bf85b7bb3c91271970e4cbd9f9e33d2af4c71b6726`.
  Its isolated installation served all four routes, deleted one real
  Session/Turn/Run graph and one real Project Memory, converged existing read
  APIs, and preserved both health routes and `/run` compatibility.
- Deterministic tests additionally prove a cross-process fenced Session save,
  two-process same-target Project deletion, process-exit recovery and lost HTTP
  response reconciliation. The concurrency RED exposed a missing finite-receipt
  handoff for the second Project deleter; the authority now returns
  `already_absent` after the winner completes.

- The existing `delete_session()` already coordinates its base, delta directory and
  shared index through the Session cross-process transaction, but no public Web
  deletion contract, preview revision or Dashboard action exists.
- A visible Dashboard conversation spans three authorities: Session content, Turn
  identity/status, and RunJournal summaries/events. Deleting only Session content
  recreates the user-observed problem where Runs still shows the old prompt summary.
- The safe product boundary is therefore one selected Session plus only terminal
  Turns/Runs linked by its Session ID and current Workspace. Unlinked and unrelated
  Runs are outside the action; active work blocks deletion.
- Turn and Run stores do not currently expose targeted deletion. Batch 8D-1 needs a
  deep conversation-deletion authority rather than raw filesystem work in HTTP.
- Cross-store deletion cannot be a single atomic filesystem transaction. The honest
  contract is bounded preflight, deterministic delete steps, safe partial status and
  idempotent retry/reconciliation after restart or response loss.
- `MemoryManager.delete_entry()` already uses the coordinated scope writer, but it
  leaves entry-specific approval audit records and `related_to` backlinks. Project
  Memory deletion must remove all three representations and rebuild indexes.
- Memory deletion is restricted to the current Workspace Project scope. User and
  Local scopes, whole-scope clearing and arbitrary storage paths remain excluded.
- Existing frontend request/action generations, Session selection reconciliation,
  Memory approval validators and `resources.sessions`/`resources.memory` SSE
  invalidation are reusable. Destructive POST actions must never be auto-replayed.
- Recommended implementation order: 8D-1 backend authorities and strict HTTP first,
  then 8D-2 confirmation UI and browser reconciliation, then resume Batch 9A-1.

---

# Batch 9 Roadmap Review Notes

- Batch 8C is closed at active production baseline v30; Batch 9 has not started.
- Existing stores already have uneven local lifecycle features: Session has
  consolidation/old-session cleanup, RunJournal has bounded retention, TurnStore
  has terminal cleanup, and Memory has integrity recovery. There is no single
  authority that explains the active Workspace, previews affected records, or
  performs a narrow verified reset.
- The recent cleanup exercise exposed the user-facing consequence: Sessions,
  Runs, Turns and Project Memory are separate authorities, so deleting one does
  not make the Dashboard look fresh. Batch 9A should make that distinction
  explicit and safe.
- The Dashboard explicit conversational user-fact Memory gap is confirmed but
  deferred by user direction. Current reflection is execution-evidence focused;
  do not conflate that future feature with Batch 9 storage lifecycle work.
- Recommended critical path: 9A storage lifecycle/recovery, 9B measured
  performance/durability, 9C localhost security/packaging/startup, 9D final UI
  and release acceptance.

---

# MiniCode Dashboard Batch 8C-2 Working Notes

## Scope and invariants

- Add a standalone, in-memory `memoryApprovalStore`; do not merge it with Memory, Permission, Chat, or runtime trace stores and do not persist approval content/revisions/results in browser storage.
- The existing Batch 8C-1/8C-1.1 GET/POST authority and existing `resources.memory` EventSource invalidation are fixed seams. No new backend business API, EventSource, polling timer, WebSocket, database, daemon, or automatic decision retry is authorized.
- Every authority payload and decision response must be exact-key validated; one unsafe item invalidates the entire pending payload and no preview from it may render.
- Item identity is `(memoryId, reviewRevision)`. GET completions use request generation fencing and actions use a separate action generation fence before an authoritative GET reconciliation.
- Preserve all v1-v29 manifests and accepted semantic gold. The expected stabilized production delta is only `minicode/web/static/assets/app.js` and `minicode/web/static/assets/styles.css`.

## Pre-edit certification evidence

- The unprivileged diagnostic full suite could not bind loopback sockets and is invalid as a product result: `107 failed, 2569 passed, 2 skipped, 3 warnings, 97 errors`; representative root cause was `PermissionError: [Errno 1] Operation not permitted` at `127.0.0.1` bind.
- The identical suite with loopback permission is the valid baseline: `2773 passed, 2 skipped, 3 warnings in 176.25s`. The three warnings are the existing unregistered benchmark markers.
- Default read-only verifier: active `memory-retrieval-production-v29`, `matches=true`, `candidateMatches=true`, current files `50/50`, and manifest integrity true for v1-v29.
- v29 manifest SHA-256: `e43777832841629549d180e039d40ac54209c5f15a3581e9bdf09b308592d4d1`.
- Accepted semantic gold: SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime_ns `1784135857000000000`.
- Initial frontend hashes: index `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`; app `1508700d7d75d99f6a5c166172c89f761e81100bc6d89f6b2873731c1e747ccb`; styles `092dd3279f613f802a050276db833d386c30663e6277fb5152597d966149d3e8`; cost formatter `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`.
- Runtime dependency list remains exactly `[]`.

## Source audit

- The Memory authority already owns every persistence, safety, review-revision,
  cross-process lock, and audit decision. Its GET projection has exact top-level
  keys `schemaVersion/generatedAt/mode/source/revision/items/diagnostics`; its
  item/review and POST result unions are bounded and stable. No backend change
  is needed.
- The existing Change Feed observes all three Memory scope files as the single
  `memory` resource; the single SSE transport publishes that resource without
  content. The frontend dispatcher already refreshes REST Memory from it.
- The frontend has useful request/action-generation precedent in independent
  Permission and Chat stores. Memory itself remains a separate read store with
  five subtabs, so Batch 8C-2 can be contained to app.js/styles.css.

## Initial RED

- Added `tests/test_dashboard_memory_approval_frontend.py` before production
  edits. Focused result: `6 failed in 0.07s` for the six intended missing
  contracts: independent store/route, strict pending validator, decision
  validator, fenced authoritative action, existing-memory SSE integration, and
  bounded accessible responsive UI.
- Failures were exact missing symbols/branches/styles, not unrelated regressions.

## Implemented frontend contract

- Added an independent, volatile `memoryApprovalStore` with the exact phase/items/revision/diagnostics/error/request/action/selection/update fields. No approval content, revision, or result enters localStorage/sessionStorage.
- Added exact-key, byte-bounded, fail-closed pending/item/review and decision validators. Reviewability, risk/safety, scope/scopeKind, fixed hidden previews, choices, IDs, timestamps, and revisions must all agree before any item renders.
- Added `#memory/approvals` as the sixth Memory tab with loaded count, exact read-write persistent meta, fixed Scopes handoff copy, bounded Waku master/detail UI, safe escaping, deny-only presentation, keyboard focus, aria live/busy, and single-column narrow layout.
- Approve/Reject use one POST, `(memoryId, reviewRevision)` identity, action generations, disabled in-flight controls, complete response validation, and authority GET reconciliation. Approve additionally refreshes existing Memory REST and Dashboard snapshot; no local optimistic deletion or POST retry exists.
- Fixed stale/not-found/already-decided/not-reviewable/write-conflict and invalid-request families reconcile by GET; busy preserves the verified review without retry; a lost response remains explicitly unconfirmed. Raw server messages/exceptions never render.
- The existing single EventSource and `resources.memory` dispatcher refresh the existing Memory store and coalesce approval authority refreshes. No second stream, resource, timer, WebSocket, database, or daemon was added.

## Development GREEN and browser evidence

- Memory approval frontend/live tests pass `13 passed`; the expanded frontend file contains nine formal tests, including the release-before-render regression found by the real browser.
- Real browser at 1280×900 approved a safe Project item, rejected a suspicious User item, and exposed only Reject for redacted/truncated deny-only items. Each disappeared only after authority GET.
- External-process create appeared without manual refresh and external reject disappeared by the existing SSE path. Restart preserved three remaining pending items and did not revive the approved/rejected items.
- At 700×900 the approval workspace computed one `479px` column, master above detail, `body.scrollWidth=innerWidth=700`; preview remained internally bounded. Development console had no application warning/error.
- A concurrent old-review decision plus external content update left the item pending and the page converged to its new review. Exact stale/error/busy/network messages and no-resend behavior are independently exercised by the formal frontend harness and real backend authority tests.

## v30 evidence in progress

- v30 is `memory-retrieval-production-v30`, parent v29, reason code `memory_approval_store_ui`, protected files 50, and exact delta app.js/styles.css with no added/removed file.
- Current v30 manifest SHA-256 is `55654b2b979812440514686b44c5bf09b5a0527a59709d37907ffb7ffd9c5edd`; baseline plus semantic-evaluator test modules pass `187 passed`.
- Final frontend hashes: app.js `3673a3e0d34f718611cea826afe5bdb4cbb8fbfd8711498721fe17cac9e03b80`; styles.css `a825a19437f1b532195ce6c9785313c08054f8c5830103c0a30474d9ba029d75`. index.html and cost-format.js retain their initial hashes.

## Final Batch 8C-2 certification

- Focused matrices passed: frontend/web `80`, Memory approval authority/HTTP/no-write `59`, Permission/File Review `338`, Change Feed/SSE/live refresh `62`, Memory Retrieval/Injection/Pipeline/reflection `335`, Session/Chat/Cancel/Turn/Gateway `243`, packaging/wheel `9`, and baseline/evaluator contract modules `187`.
- Both complete regression suites passed: `2788 passed, 2 skipped, 3 warnings` in `178.55s` and `177.37s`. The warnings are only the three existing unregistered benchmark markers.
- Scoped Ruff, selected `py_compile`, repository `compileall`, and both production JavaScript `node --check` commands passed. Runtime dependencies remain exactly `[]`; pyright and mypy are not installed in this workspace.
- Default v30 verification reports candidate/current equality, `50/50` protected files, the exact app.js/styles.css delta, and all v1–v30 integrity flags true. The official evaluator passed 108 cases / 37 confirmed gaps / Phase 3B true / zero remote calls. Accepted gold stayed SHA `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime_ns `1784135857000000000`.
- Final isolated in-app-browser acceptance covered eight main routes, six Memory routes, six exact Memory tabs, safe approve, suspicious reject, redacted deny-only, external SSE create/remove, disconnect preservation, Gateway restart recovery, visible keyboard focus, 1280×900 and 700×900 layouts, zero horizontal overflow, empty application warning/error console, and no local path/secret/object leakage.
- Production changed only app.js and styles.css. No backend production file, dependency, alternate live transport, persistent browser approval storage, Memory edit/delete, Allow always, Tool Permission merge, Batch 8B, or Batch 9 behavior was added. Batch 8C is complete.

---

# MiniCode Dashboard Active Handoff: 8A-2.2 before 8C-2

## Roadmap state
- Batch 8C-1 and 8C-1.1 are certified through active v27.
- Batch 8C-2 remains pending; Batch 8C is therefore not yet closed.
- Batch 8A-2.2 is now the active repair. Resume 8C-2 only after it passes.

## Confirmed 8A-2.2 symptom and cause
- Real file Tools share `apply_reviewed_file_change()` and build the unified
  Diff from the original Tool `path` string, while `ensure_edit()` separately
  receives the resolved absolute target.
- If the Model supplies a workspace-local absolute path, Diff headers contain
  that absolute path. The broker projects the target itself to `code/hello.py`
  but `_redact_review_text()` replaces the workspace text in the headers with
  `[LOCAL_PATH]`, sets `redacted=true`, and makes the card deny-only.
- Direct reproduction in the current Workspace returned:
  `reviewable=false`, `redacted=true`, and headers
  `--- a/[LOCAL_PATH]/code/hello.py` / `+++ b/[LOCAL_PATH]/code/hello.py`.
- A temp-directory alias reproduction (`/var/...` input versus canonical
  `/private/var/...` workspace) showed the inverse failure: the absolute path
  was not matched by exact replacement and remained visible in an allowable
  review. This is both a usability and local-path privacy defect.

## Required invariant
- Unified Diff labels for a proven workspace-local resolved target must be the
  canonical POSIX relative path, independent of the original input spelling.
- Diff content is not normalized or made safer by changing labels. A real
  absolute path/secret inside changed content remains redacted and deny-only.
- External, ambiguous, truncated, or unreviewable changes remain deny-only.
- Do not fix this by weakening `permissionReviewConsistent()` or allowing
  redacted reviews in the frontend.

---

# MiniCode Dashboard Batch 8C-1 Working Notes

## Scope and invariants

- Automatic reflection uses an explicit typed review-required policy; explicit
  user saves retain their existing safe-approved/suspicious-pending/unsafe-
  rejected semantics.
- Persistent pending state lives only in the existing Memory files. One shared
  transaction seam must cover every durable writer before cross-process safety
  can be claimed.
- Snapshot reviews are bounded safe projections. Decision revision binds
  semantic content and approval state, never wall clock or public raw hashes.
- Pending/rejected items remain outside search, canonical retrieval, injection,
  WorkingMemory, promotion, and curator auto-approval until a typed decision
  commits under the shared lock.
- Existing `resources.memory` is the only realtime invalidation seam. Formal
  frontend bytes, Tool permission authority, REST/SSE schemas, RunJournal, and
  accepted semantic gold remain frozen unless audit proves an unavoidable gap.
- v25 and every earlier manifest are immutable. No Git, new dependency,
  database, daemon, UI, Batch 8B, Batch 8C-2, or Batch 9 is permitted.

## Evidence log

- Untouched approved-loopback suite: `2445 passed, 2 skipped, 3 warnings in
  151.32s`; warnings are the existing benchmark markers.
- Active verifier is v25 with candidate/current equality, 45/45 protected
  files, true v1-v25 integrity, exact one-file v24→v25 lineage, and manifest SHA
  `c431a30e03e12aab5085f49eab22a86aa57c99190fb93fb7fcb0c207c4a22aef`.
- Accepted semantic gold is SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3,033,592, mtime_ns `1784135857000000000`. Dependencies are `[]`.
- Frozen frontend hashes: HTML
  `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`,
  app.js `1508700d7d75d99f6a5c166172c89f761e81100bc6d89f6b2873731c1e747ccb`,
  CSS `092dd3279f613f802a050276db833d386c30663e6277fb5152597d966149d3e8`,
  cost-format.js
  `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`.
- Saved REDs: a real accepted `MemoryPipeline.write()` reflection persisted as
  `approved`; `minicode.memory_approval` and both HTTP routes were absent; a
  stale manager approved and overwrote a newer pending content revision; and
  two spawn-process writers gated by multiprocessing events (no sleep) retained
  only writer beta, losing writer alpha.

## Durable-write call graph

- Explicit user `# ...` and `/memory add ...` call
  `MemoryManager.handle_user_memory_input()` → `add_entry()`; safe is currently
  approved, suspicious pending, unsafe rejected. This remains USER_EXPLICIT.
- Automatic `MemoryPipeline.write()` and legacy
  `ReflectionEngine._persist_reflection()` call `add_entry(source="reflection")`;
  safe is currently approved and injectable. Both require an explicit
  USER_REVIEW_REQUIRED policy rather than source inspection.
- Curator-generated insights call `add_entry(source="curator")`; this is also an
  automatic write and must use the review-required policy.
- Content/approval mutations are `add_entry` (including duplicate merge),
  `update_entry`, `delete_entry`, tag changes, `approve_entry`, `reject_entry`,
  `restore_entry`, `clear_scope`, `compress_scope`, `promote_memories`, curator
  duplicate/stale rewrites and insight linking. Counter/graph mutations are
  search usage, retrieval/injection/feedback counters, decay, and link updates.
- All paths converge today on `_save_scope()` atomic per-file replace; approval
  audit separately converges on `_save_approval_audit()`. There is no shared
  process lock or stale-manager detection, so atomic replacement prevents torn
  JSON but not lost updates.
- Curator phases mutate manager entries directly before calling private save
  methods. They therefore need the same manager transaction seam (or a loud
  optimistic conflict), not an HTTP-only mutex.
- Existing semantic identity `_approval_hash_payload()` binds content,
  category, tags, domains, source, provenance, and metadata. Retrieval/usage
  counters and timestamps are intentionally excluded; normal counter writes
  must not stale a review.
- `MemoryEntry.is_active` already requires approved + non-unsafe + active
  lifecycle + unlocked + non-archival. `MemoryFile.search`, canonical
  retrieval, manager context, and injection select through this invariant, so
  persisted pending is already excluded if automatic writes enter pending.
- Existing `resources.memory` observes user/project/local `memory.json` and
  `MEMORY.md`; candidate creation, content changes, approve, reject, and restore
  all rewrite those files. No new SSE resource is required.

## Implemented contract and current GREEN evidence

- `MemoryApprovalPolicy.USER_EXPLICIT` preserves explicit safe-approved
  behavior; `USER_REVIEW_REQUIRED` makes safe/suspicious automatic writes
  pending and unsafe writes rejected. Pipeline reflection, legacy reflection,
  curator insight and compression rewrites use the typed automatic policy.
- `MemoryStoreCoordinator` is the one RLock→POSIX flock seam. It uses a
  monotonic five-second deadline, 0600 empty persistent lock file,
  O_CLOEXEC/O_NOFOLLOW where supported, regular-file/inode checks, authority
  reload and atomic per-file replace. It is local macOS/Linux coordination, not
  Windows/NFS/distributed locking.
- `MemoryApprovalAuthority.snapshot()/revision()/decide()` projects at most 20
  safe bounded items, returns no metadata/provenance/internal hashes, and binds
  each public `memoryreviewrev_*` to ID, scope, approval/lifecycle/safety,
  approval content hash, current content hash and projection version. Redacted,
  truncated, incomplete and unsafe reviews are deny-only.
- HTTP routes are strict no-store UTF-8 JSON, reject query/duplicate JSON
  keys/extra fields/wrong types/wrong MIME/unacceptable Accept/oversize bodies,
  apply same-origin plus loopback fencing, expose fixed safe errors, and emit no
  CORS headers.
- Final core approval matrix: 50 passed. Memory/Gateway/Chat/Permission/Session/SSE
  compatibility matrix: 600 passed. Production-baseline tests: 134 passed;
  standalone installed-wheel/Gateway smoke: 9 passed after updating the
  pre-existing empty-Memory expectation for the newly seeded approved fixture.
- v26 protects 50 production files. Exact v25→v26 delta is changed:
  `gateway.py`, `memory.py`, `memory_pipeline.py`, `web/http.py`; newly protected:
  `agent_reflection.py`, `memory_approval.py`, `memory_curator_agent.py`,
  `memory_store.py`, `web/memory_approval_http.py`. Manifest SHA is
  `b44abf36befb98723b26036530296f8675a0d92ae59884956767b352445ed936`.
- The semantic evaluator now excludes only the exact isolated-case
  `home/.mini-code/memory-store.lock` after asserting it is regular, 0600 and
  zero bytes. This keeps the business side-effect projection identical without
  weakening detection or rewriting the accepted gold; its 32 tests pass.
- Final static gates pass: scoped Ruff, selected `py_compile`, complete
  `compileall`, and `node --check` for both formal JavaScript files. pyright and
  mypy are not installed. Runtime dependencies remain `[]`.
- Final full suite 1: `2500 passed, 2 skipped, 3 warnings in 167.48s`.
  Official evaluator: 108 cases, 37 confirmed gaps, Phase 3B true, zero remote
  calls, evaluation passed. Accepted gold remains exact by SHA/size/mtime_ns.
  Final full suite 2: `2500 passed, 2 skipped, 3 warnings in 167.73s`.
- An earlier full attempt exposed a test-only global-state leak: spawned tests
  assigned the module-level `MINI_CODE_DIR` in the parent process. They now use
  pytest `monkeypatch`; the reproducer (all cross-process tests followed by the
  formerly polluted Retrieval test) passes 8 tests. That failed attempt is not
  counted as either final suite.

---

# MiniCode Dashboard Batch 8A-2.1 Working Notes

## Scope and invariants

- Expected production delta is exactly `minicode/web/static/assets/app.js`.
  Formal HTML/CSS and every backend authority/transport file remain frozen.
- Hidden-placeholder and internally contradictory review contracts must be
  rejected by validation and independently remain non-allowable at the action
  guard.
- Every completed/cancelled/failed/interrupted/missing Turn must retire its old
  permission actions before active identity is cleared, fence stale GET/POST,
  and reconcile once from pending REST.
- A fresh authority read may expose a different Turn; neither SSE nor local
  history may revive the retired Turn before that read succeeds.
- v24 and accepted semantic gold are immutable. No Git, Batch 8B/9, new
  authority, persistence, timer, EventSource, or dependency is permitted.

## Evidence log

- Untouched approved-loopback suite: `2437 passed, 2 skipped, 3 warnings in
  191.38s`; warnings are the repository's existing benchmark markers.
- Active verifier is v24 with `candidateMatches=true`, 45/45 protected files,
  every v1-v24 integrity pin true, and v24 manifest SHA
  `f6022dec899fbf083db090385dd4358560673817e25764e469d97548e827307f`.
- Accepted semantic gold is unchanged at SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3,033,592, mtime ns `1784135857000000000`.
- Untouched formal assets: HTML
  `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`,
  app.js `9a83aad4c3f25f8af38fd3ea34f069e7b4ded91048f01a24cbfb59cc06c1b0ac`,
  CSS `092dd3279f613f802a050276db833d386c30663e6277fb5152597d966149d3e8`.
  Runtime dependencies remain `[]`.
- Review RED: the old executable bundle accepted a forged command whose fixed
  hidden placeholder was paired with safe booleans and returned it rather than
  `null`; the targeted pytest failed exactly at that assertion.
- Final security review added the same invariant for an edit `diffPreview`.
  Before the final three-line production tightening, that deterministic RED
  likewise failed because the forged edit payload was accepted; the shared
  consistency rule now rejects the fixed placeholder for both preview unions.
- Terminal RED: after `finishCancelledTurn()` cleared `activeTurnId`, the old
  `permissionActionAvailable()` returned `true` for the retained item. The
  deterministic stale-GET harness failed `true !== false` before production
  hardening.
- GREEN now centralizes review shape/visibility/reviewable/choice consistency
  in `permissionReviewConsistent()`. The pending validator and independent
  Allow guard both use it; safe edit and command remain allowable.
- `retirePermissionTurn()` is the sole terminal permission entrypoint. It
  tombstones the Turn, fences request/action generations, removes local items,
  clears acting state, and immediately starts pending REST reconciliation.
  Executable harnesses pass for stale GET, stale decision POST, fresh other-Turn
  recovery, Cancel 404/cancelled/failed/interrupted, status
  404/cancelled/failed/interrupted/completed, NDJSON cancelled, JSON success,
  and JSON interrupted paths, with no automatic decision or Chat replay.
- Focused matrices pass: Permission frontend `100 passed in 11.10s`,
  Chat/Cancel/Turn `150 passed in 36.27s`, Change Feed/SSE/live refresh `46
  passed in 0.62s`, and Dashboard Web/HTTP/packaging `76 passed in 78.31s`.
  Baseline certification tests pass 130 tests.
- Active v25 manifest SHA is
  `c431a30e03e12aab5085f49eab22a86aa57c99190fb93fb7fcb0c207c4a22aef`.
  It certifies the exact v24→v25 one-changed/zero-added/zero-removed delta,
  45/45 protected files, candidate/current equality, and true v1-v25 integrity.
  Final app.js SHA is
  `1508700d7d75d99f6a5c166172c89f761e81100bc6d89f6b2873731c1e747ccb`.
- Scoped Ruff, selected py_compile, full compileall, and app.js plus
  cost-format.js node checks pass. `python -m build`, pyright, and mypy are not
  installed. The offline no-dependency wheel packages the certified app.js and
  isolated installed-Gateway smoke passes `9 passed in 12.52s`.
- First complete suite: `2445 passed, 2 skipped, 3 warnings in 191.28s`.
  Official evaluator: 108 cases / 37 confirmed gaps / Phase 3B true / remote 0
  / pass. Accepted gold remains exact by SHA, size, and mtime_ns. Second
  complete suite: `2445 passed, 2 skipped, 3 warnings in 191.56s`.
- Real in-app browser acceptance against an isolated real Gateway at 1280x900
  proved safe command/edit Allow+Deny, path/hidden/truncated deny-only,
  immediate Cancel retirement before the next SSE, fresh other-Turn authority,
  and no revival after an empty-authority Gateway restart. There was no
  horizontal overflow or permission/composer overlap, normal and restarted
  console warning/error counts were zero, and seeded secret/HOME/absolute
  path/Tool input-output/`[object Object]` values were absent from rendered DOM.
- Task-owned Gateway/browser resources were closed. Dependencies remain `[]`.
  Batch 8A is closed, and Batch 8B/9 were not entered.

---

# MiniCode Dashboard Batch 8A-2 Working Notes

## Scope and invariants

- One existing `PermissionApprovalBroker` remains the authority used by
  Conversation, pending/decision HTTP, Change Feed revision observation, and
  shutdown. Non-loopback composition remains `None` and fail-closed.
- Change Feed/SSE v2 has exactly seven ordered resources, with `permissions`
  last. Public revisions remain `rev_<64 hex>` and events contain no reviews,
  IDs, Turn/Run identity, Tool data, path, command, reason, or decision.
- The browser has one in-memory permission Store, one existing EventSource, and
  no dedicated polling timer. Pending REST is current-process truth; decision
  REST is write truth; RunJournal permission events are historical only.
- Review content must never enter browser storage, URL/hash, console, telemetry,
  Session, or RunJournal. Restart/refresh recovery uses only current Gateway GET.
- v23 and accepted semantic gold are immutable; active certification advances
  only after the exact seven-file production delta is green.

## Untouched evidence

- Approved-loopback full suite: `2420 passed, 2 skipped, 3 warnings in 148.03s`.
  The three warnings are the existing unregistered benchmark markers.
- A sandbox-only attempt first reported `2252 passed, 72 failed, 96 errors`;
  sampled errors were all loopback `socket.bind()` permission failures. No file
  had been modified, and the approved rerun above is the actual baseline.
- Active verifier: v23, candidate/current equality true, 45 protected files,
  v1-v23 integrity true. v23 manifest SHA is
  `c6cab0e867db309f9ddfbaf3034e269f4f65ce7b1c66e155997c0697b3388aa8`.
- Accepted gold before changes: SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, filesystem mtime seconds `1784135857`; exact `mtime_ns`
  will be re-recorded with Python during final certification.
- Formal assets before changes: HTML
  `a5ea78536ba8af424bcb655eaeddea0d8b64e071623a5b3665f014a2855d1fdd`,
  app.js `88c5ca1eed348cd84453681c05224c6512fc0d2b66e0786295020c6c490ddfe9`,
  CSS `49f8a84f9bf2b8f6beae21a4b3fd4fde0dc260d3f26c44e54d558cd57b9e6d16`,
  cost-format.js `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`.
- Scoped Ruff, targeted `py_compile`, full `compileall`, app.js and
  cost-format.js `node --check` all pass. Runtime dependencies are `[]`.

## Final evidence

- RED proved the missing broker-revision Change Feed mapping, absent schema-v2
  permissions SSE event, and absent formal permission Store/panel before the
  respective production slices were implemented.
- Final Change Feed/SSE contract is schema v2 with seven ordered resources.
  Faults are resource-local and the public permission revision is content-free.
- Focused Permission/Change Feed/SSE/frontend matrix: `292 passed in 82.98s`.
  Gateway/Conversation/RunJournal/Session: `321 passed in 67.38s`.
  TUI/Headless/Memory/Skill/MCP/Pricing: `315 passed in 9.07s`.
- Baseline tests: `126 passed`; semantic evaluator tests: `32 passed`;
  installed-wheel tests: `9 passed in 49.98s`.
- The first final full attempt found one stale semantic-certification test still
  naming active v23. The test now certifies every historical hash plus exact
  v23→v24 lineage. Two subsequent full suites both pass `2437 passed,
  2 skipped, 3 warnings` in 151.56s and 151.51s.
- Active v24 manifest SHA is
  `f6022dec899fbf083db090385dd4358560673817e25764e469d97548e827307f`.
  Candidate/current equality, 45/45 files, exact seven changed/zero added/zero
  removed, and all v1-v24 integrity pins pass. v23 remains unchanged.
- Official semantic evaluation passes twice at 108 cases / 37 gaps / Phase 3B
  true / remote 0. Accepted gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3,033,592 and mtime_ns 1784135857000000000.
- Final formal hashes: HTML
  `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`,
  app.js `9a83aad4c3f25f8af38fd3ea34f069e7b4ded91048f01a24cbfb59cc06c1b0ac`,
  CSS `092dd3279f613f802a050276db833d386c30663e6277fb5152597d966149d3e8`;
  cost-format.js remains byte-identical.
- Real in-app browser acceptance at 1280x900 and 430x900 proved pending refresh
  recovery, exact-once Allow, no-effect Deny/Cancel, restart clearing, SSE
  reconnect, all eight main and five Memory routes, no horizontal overflow or
  card/composer overlap, no absolute-path/object disclosure, and zero page
  console warnings/errors. Deterministic tests supplement forced polling-only,
  multi-pending, timeout, long command, and keyboard-edge cases.
- Scoped Ruff, explicit py_compile, full compileall, and every formal JavaScript
  node check pass. pyright/mypy are not installed. Dependencies remain `[]`.
- Batch 8A is closed. Batch 8B optional local management was not implemented.

---

# MiniCode Dashboard Batch 8A-1.1 Working Notes

## Scope and invariants

- Expected production delta is exactly `minicode/permission_approval.py`; all
  token-aware classification and UTF-8 budgeting should retain the existing
  broker/session/HTTP interfaces.
- Any detected credential, local absolute path, complex shell form, truncation,
  ambiguity, or incomplete review must be content-free, redacted,
  `reviewable=false`, and deny-only.
- Safe simple argv and Workspace-relative arguments must remain reviewable and
  operation-scoped allow must retain exact-once/no-cache behavior.
- Formal HTML/CSS/JS, state machine, events, Agent Loop, TUI, Headless,
  non-loopback composition, Session/Run/Memory/Skill/MCP/Pricing and Chat/SSE
  contracts remain frozen.
- v22 manifests/pins and accepted semantic gold must remain byte-identical;
  active certification advances to v23 only after the production fix is green.

## Evidence log

- Complete required-source audit: PermissionManager already supplies structured
  `command`, `args`, `cwd`, and `reason`. The broker currently flattens these
  through `shlex.join()` and then applies only bearer/key/assignment regexes.
  HTTP serializes broker snapshot directly; RunJournal/ReadModel use an exact
  content-free permission event contract and do not need production changes.
- Untouched full suite: `2377 passed, 2 skipped, 3 warnings in 146.79s`.
  Warnings remain the three historical unregistered benchmark markers.
- Untouched verifier: active v22, candidate and current match, 45/45 protected,
  v1–v22 integrity true. v22 SHA is
  `a47b1e5f203371e9ced01fed01e6df37947a2a0e891c1bee6c2ed43a51e59906`.
- Accepted gold before changes: SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns 1784135857000000000. Dependencies are `[]`.
- Formal assets before changes: HTML
  `a5ea78536ba8af424bcb655eaeddea0d8b64e071623a5b3665f014a2855d1fdd`,
  JS `88c5ca1eed348cd84453681c05224c6512fc0d2b66e0786295020c6c490ddfe9`,
  CSS `49f8a84f9bf2b8f6beae21a4b3fd4fde0dc260d3f26c44e54d558cd57b9e6d16`.
- RED: `22 failed, 36 passed in 7.77s`. Eight currently unrecognized
  credential forms stayed reviewable; six local absolute-path forms leaked or
  received only partial `[LOCAL_PATH]` substitution; the real pending HTTP JSON
  leaked both seeded values; strict ASCII/Chinese/emoji/tiny byte budgets
  failed; the public command preview was 4097 bytes against a 4096-byte limit.
- GREEN: command/argv are classified before flattening. Credential flags,
  headers, environment assignments, URL userinfo, local absolute paths, and
  ambiguous shell forms collapse to one fixed redacted deny-only projection.
  UTF-8 truncation reserves marker bytes and remains within zero/tiny/
  multibyte budgets. Safe simple argv, relative paths, and ordinary HTTP URLs
  remain reviewable. The expanded Permission/HTTP/Conversation/Event suite is
  `73 passed in 9.99s`; deny, timeout, and cancel start zero sensitive
  subprocesses, while safe allow starts exactly once and writes no command
  allow cache.
- Final projection review also classifies credential-bearing free-text reason
  tokens before returning the reason. The final focused Permission/Event/v23
  matrix is `196 passed in 12.01s`; the broader compatibility evidence remains
  445 focused and 656 Session/TUI/Memory/Skill/MCP/Pricing/Chat/SSE passes.
- Scoped Ruff, explicit `py_compile`, full `compileall`, and both formal
  JavaScript `node --check` commands pass. `pyright` and `mypy` are not
  installed. Runtime dependencies remain `[]`.
- Active v23 manifest SHA is
  `c6cab0e867db309f9ddfbaf3034e269f4f65ce7b1c66e155997c0697b3388aa8`.
  It protects 45/45 sources, candidate/current equality is true, every v1-v23
  integrity pin is true, and v22→v23 is exactly one changed
  `minicode/permission_approval.py`, zero added, zero removed. v22 remains
  `a47b1e5f203371e9ced01fed01e6df37947a2a0e891c1bee6c2ed43a51e59906`.
- Final wheel is 733,226 bytes, SHA
  `4cb206ef9522965ffb4db626aebd76a057dd6d12c6667eabd214b05459332a77`.
  It contains approval/event/HTTP modules and all four formal static resources;
  isolated install plus real Gateway smoke passes health, static, `/run`,
  Chat/Status/Cancel, SSE/read APIs, safe command allow, sensitive command
  allow refusal, and content-free permission events.
- Official semantic evaluation passes twice at 108 cases / 37 gaps / Phase 3B
  true / remote 0. Accepted gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns 1784135857000000000.
- Final full suites are `2420 passed, 2 skipped, 3 warnings` in 186.80s and
  147.47s. Warnings are the three historical unregistered benchmark markers.
- Formal assets remain byte-identical: HTML
  `a5ea78536ba8af424bcb655eaeddea0d8b64e071623a5b3665f014a2855d1fdd`,
  JavaScript
  `88c5ca1eed348cd84453681c05224c6512fc0d2b66e0786295020c6c490ddfe9`,
  CSS `49f8a84f9bf2b8f6beae21a4b3fd4fde0dc260d3f26c44e54d558cd57b9e6d16`.
  No browser UI acceptance is claimed because this batch makes no UI change.
- All `minicode-batch8a11-*` temporary HOME, JUnit, wheel/source/install, and
  smoke resources were removed. Final process filtering found no task-owned
  pytest, Gateway, HTTP/SSE server, or worker process.

---

# MiniCode Dashboard Batch 8A-1 Working Notes

## Scope and invariants

- PermissionManager remains the only permission judge. The Gateway authority may
  supply its synchronous prompt but cannot bypass `ensure_path_access()`,
  `ensure_command()`, or `ensure_edit()`.
- Browser `allow_once`/`deny_once` must map to internal operation-only decisions
  that never write allow/deny caches, `permissions.json`, Session summaries, or
  future-Turn state.
- One deep core module owns identity, Tool thread context, waits, timeout,
  cancellation, capacity, close, review projection, tombstones, revision, and
  safe event emission. Core code cannot depend on `minicode.web`.
- Approval is enabled only for a loopback-composed Gateway. Formal HTML/CSS/JS,
  SSE resources, Change Feed mapping, 7C NDJSON, and browser timers should remain
  byte-identical.
- v22 must preserve v1-v21 pins and accepted semantic gold byte-for-byte.

## Evidence log

- Untouched full suite: `2338 passed, 2 skipped, 3 warnings in 137.53s`.
- RED command: `pytest -q tests/test_permission_approval.py tests/test_permission_http.py`.
  It failed during collection with two expected
  `ModuleNotFoundError: minicode.permission_approval` errors, fixing the missing
  authority and routes before implementation.
- `AgentLoop._execute_single_tool()` invokes the start callback and then runs the
  real Tool in a nested one-worker executor. Ordinary thread-local state cannot
  cross this boundary; the operation binding therefore needs an explicitly
  copied context while preserving per-worker isolation for same-name tools.
- `file_review.apply_reviewed_file_change()` writes immediately after
  `ensure_edit()` and `run_command._run()` starts a subprocess immediately after
  `ensure_command()`. Both are final protected-side-effect checkpoints.
- GREEN core and HTTP suites pass 34 tests. A 466-test Permission/Tool/Runtime/
  Conversation/Gateway/Run/compatibility matrix also passes.
- `PermissionApprovalBroker` now centralizes unpredictable identities,
  operation binding, monotonic wait, cancel/timeout/capacity/close transitions,
  review projection, idempotent decisions, bounded scrubbed tombstones,
  revisions, and content-free events. Its default limits are 300 seconds, 16
  pending, 128 KiB snapshot, and 256 tombstones retained for 600 seconds.
- Browser `allow_once` and `deny_once` map only to PermissionManager's internal
  `allow_operation` and `deny_operation`; real same-Turn same-file tests prove a
  second edit creates a new permission ID. Existing TUI choice behavior and
  Headless prompt-unavailable behavior remain unchanged.
- Real Gateway Chat tests use the real AgentTurnRuntime, scripted Model, real
  Tool registry, real PermissionManager, Session commit, Run observation, and
  HTTP requests from another thread. Writes and subprocesses remain absent
  before approval and after deny/timeout/cancel/close; allowed work executes
  exactly once.
- Scoped Ruff, explicit `py_compile`, full `compileall`, and every formal
  JavaScript `node --check` pass. Repository-wide Ruff remains exactly 686
  historical findings. `pyright` and `mypy` are unavailable. Dependencies
  remain `[]`.
- The isolated installed-wheel test passes and proves packaged approval/event/
  HTTP modules plus pending/decision, JSON/NDJSON Chat, Status, Cancel, Runs,
  safe permission events, SSE, changes, health, `/run`, and unchanged assets.
- Active v22 manifest SHA is
  `a47b1e5f203371e9ced01fed01e6df37947a2a0e891c1bee6c2ed43a51e59906`.
  It protects 45/45 sources, has exact 7 changed + 7 added protected entries,
  candidate/current equality, exact authority/event-contract tamper reporting,
  and true v1–v22 integrity pins. v21 SHA remains
  `5a6422b0ae18649166e3e8d28c990a9736f457093f105db661f7ff4b40d8a8ff`.
- First final full suite: `2377 passed, 2 skipped, 3 warnings in 146.12s`.
  Official semantic evaluation passes 108 cases / 37 gaps / Phase 3B true /
  remote 0. Accepted gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns 1784135857000000000.
- Formal assets remain byte-identical to v21: HTML
  `a5ea78536ba8af424bcb655eaeddea0d8b64e071623a5b3665f014a2855d1fdd`,
  JavaScript
  `88c5ca1eed348cd84453681c05224c6512fc0d2b66e0786295020c6c490ddfe9`,
  and CSS
  `49f8a84f9bf2b8f6beae21a4b3fd4fde0dc260d3f26c44e54d558cd57b9e6d16`.
- Second final full suite: `2377 passed, 2 skipped, 3 warnings in 186.08s`.
  Final isolated real Gateway HTTP approval acceptance passes 4/4 in 2.25s:
  allow, deny, exact-operation reapproval, and cancel/late-allow behavior.
- Task-owned HTTP servers close through their context managers. Isolated HOME,
  JUnit, wheel/install, Workspace, and pytest temporary resources were removed;
  no task-owned listener or worker remains.

---

# MiniCode Dashboard Batch 7C Working Notes

## Scope and invariants

- The only new content-bearing transport is the existing Chat request with
  `Accept: application/x-ndjson`; the global `/api/v1/events` stream remains
  content-free invalidation and the JSON Chat contract remains compatible.
- A three-method optional presentation interface may observe genuine provider
  Assistant deltas and safe Tool names, but cannot become runtime, Run, Turn,
  Session, persistence, or HTTP truth.
- Final Session REST messages remain the only conversation authority. Partial
  text is connection-scoped memory only and cannot be replayed after refresh or
  disconnect.
- Writer, presentation callback, RunObservation, provider, Tool, cancellation,
  and Session commit failures must be isolated so presentation can never alter
  execution semantics.
- v21 must preserve every v1–v20 pin and the accepted semantic gold byte-for-byte.

## Evidence log

- Untouched isolated full suite: `2305 passed, 2 skipped, 3 warnings in
  133.68s`; the warnings are the three existing benchmark markers.
- Active baseline is v20 with manifest SHA
  `4104965fd30bdfeb06910701be6b53d0a623607f3965b15ed8f9d80809baca05`,
  36/36 protected files, candidate/current equality, and every v1–v20 integrity
  pin true. Runtime dependencies are `[]`.
- Official untouched evaluator: 108 cases, 37 confirmed gaps, Phase 3B true,
  zero remote calls, pass. Accepted gold is still SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns 1784135857000000000.
- Scoped Ruff, py_compile, full compileall, and all three formal JavaScript
  checks pass. Repository-wide Ruff remains exactly 686 existing findings (448
  F401, 73 F541, 57 F841, 46 F821, 19 E402, 18 E741, 12 E712, 7 F811, 4 E702,
  2 E731).
- Untouched offline wheel built and installed at 873,475 bytes, SHA
  `2805714cf67eb8ebdad14f8f6a3b9d5d4098ca373cfe1cc77ae51d65cde66940`.
- Browser baseline at 1280×900 showed `实时（SSE）`, exactly one
  `/api/v1/events`, and zero `/api/v1/changes` after initial load. The formal
  Chat client still sends `Accept: application/json` and exposes no pre-response
  Assistant or Tool presentation.

## Source audit

- OpenAI enables provider streaming only when `on_stream_chunk` is non-null and
  forwards only `choices[0].delta.content`; Tool call arguments remain in a
  separate accumulator.
- Anthropic enables the same optional callback and forwards only
  `content_block_delta` with `text_delta`. `thinking_delta` is sent exclusively
  to the separate `on_thinking_delta` callback and must remain disconnected.
- Agent Loop already passes `on_assistant_stream_chunk` through `_model_next()`
  on every model attempt. A ModelSwitcher fallback replaces the local adapter
  and re-enters the same callback path, so no ModelSwitcher production change is
  needed.
- `on_assistant_message` also receives fallbacks, progress-adjacent final text,
  and Tool-await-user output; it is not a safe delta source and will not be used.
- `_execute_single_tool()` invokes start immediately before execution and finish
  immediately after. Concurrent-safe calls execute these callbacks from
  `mc-tool` worker threads and may finish out of order; same-name pairing,
  sequence allocation, and line writes therefore need one writer lock and FIFO.
- Current `AgentTurnRuntime.execute()` only composes RunObservation callbacks and
  exposes no presentation argument. `ConversationTurnService._runtime_execute()`
  currently signature-checks only cancellation. `serve_chat_turn()` always
  waits for completion and sends one JSON body. Browser refresh recovery reads
  only durable Turn/Session state, which is the required non-replay baseline.

## Final implementation evidence

- Core/Web split is `ConversationPresentation` plus `ChatStreamWriter`. Genuine
  provider text and safe Tool names are the only temporary inputs; a unified
  writer lock owns sequence, FIFO pairing, budgets, and complete-line writes.
- NDJSON is optional on the existing POST. JSON callers remain byte-contract
  compatible; invalid pre-header requests stay JSON, while post-header failures
  are fixed safe terminal frames.
- The frontend store/parser is memory-only, generation-fenced, exact-schema,
  bounded, gap-aware, rAF-coalesced, and finalizes only through Sessions REST.
  Disconnect never re-POSTs and refresh never restores provisional text.
- v21 SHA is
  `5a6422b0ae18649166e3e8d28c990a9736f457093f105db661f7ff4b40d8a8ff`.
  It changes six and adds two production files, protects 38/38, matches its
  candidate, and retains every v1-v21 pin.
- Official evaluation remains 108 cases / 37 gaps / Phase 3B true / remote 0 /
  pass. Accepted gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns 1784135857000000000.
- Final wheel is 882,256 bytes, SHA
  `68dba349533ef1206dfcbe85f5099855cebef35a063fd1306ae37d747a28059d`;
  isolated JSON/NDJSON Chat, Status, Cancel, SSE, changes, static, health, and
  `/run` smoke passed. Dependencies remain `[]`.
- Three clean post-fix full runs passed `2338 passed, 2 skipped, 3 warnings` in
  137.53s, 137.44s, and 138.49s. Scoped static checks pass; repo-wide Ruff stays
  at the same 686 historical findings.
- Browser acceptance captured three pre-terminal Assistant states, Tool running
  then success/error, truthful disconnect partial state, final REST replacement,
  refresh non-replay, cancel_requested plus late delta, durable cancellation,
  one SSE / zero healthy changes polling / one POST, all 8+5 routes, 208/682/380
  px non-overlapping columns, zero page console issues, and zero seeded leaks.

---

# MiniCode Dashboard Batch 7B Working Notes

## Scope and invariants

- Primary transport becomes exactly one formal
  `new EventSource('/api/v1/events')`; `/api/v1/changes` remains the only polling
  fallback and must be stopped/aborted while SSE is healthy.
- `stream.ready` and `stream.reset` enqueue a full six-resource REST resync;
  `resources.changed` enqueues only validated resources unless sequence gap
  requires full resync.
- Event data never becomes business store or DOM content. Run, Session, Turn,
  Memory, Skill, MCP, Ops, and all existing REST loaders remain authoritative.
- Chat submit/cancel/status and durable Turn generation semantics cannot be
  called or weakened by the realtime controller.
- Protected backend sources should remain byte-identical. Expected v20 is a
  frontend-only delta unless a separately proven v19 backend blocker appears.

## Evidence log

- Untouched isolated full suite: `2296 passed, 2 skipped, 3 warnings in
  133.60s`; active v19 matched 36/36 protected sources, its candidate, lineage,
  and every v1–v19 pin. v19 SHA:
  `9c48c5c0f02f48c49a31411292b1d65b1e52de4667c2048477343ff64eaa82c6`.
- Accepted semantic gold stayed SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns 1784135857000000000. Runtime dependencies: `[]`.
- RED: the new two-test contract failed because formal `app.js` had zero
  EventSource instances and no event byte-limit validator. GREEN now covers 24
  strict validation checks, 10 queue checks, and more than 30 coordinator checks.
- Formal integration owns exactly one `new EventSource('/api/v1/events')` and
  routes both it and the certified `/api/v1/changes` adapter through one
  `createResourceRefreshQueue()`. Valid SSE data can only name existing REST
  loaders; it never enters business stores or the DOM.
- Focused Dashboard/SSE/Change Feed/cross-process/Gateway/wheel compatibility:
  `119 passed in 84.69s`. Production JavaScript syntax also passes.
- Final strict contract matrix covers 25 event-validation cases, 10 queue cases,
  and more than 30 coordinator checks. It includes exact UTC timestamp
  canonicalization, pre-parse 4 KiB rejection, BigInt sequence handling,
  duplicate/stale/gap/reset paths, native reconnect, permanently closed-source
  replacement, malformed-source generations, visibility, stop, and fallback.
- Final focused matrix passed `228 passed in 46.35s`. Scoped Ruff, py_compile,
  full compileall, and every formal `node --check` passed. Repository-wide Ruff
  remains exactly 686 pre-existing findings (448 F401, 73 F541, 57 F841, 46
  F821, 19 E402, 18 E741, 12 E712, 7 F811, 4 E702, 2 E731). Dependencies remain
  `[]`.
- Active v20 manifest SHA is
  `4104965fd30bdfeb06910701be6b53d0a623607f3965b15ed8f9d80809baca05`.
  It protects 36/36 sources and changes exactly formal `app.js`, `styles.css`,
  and `index.html`; added/removed sets are empty. Candidate/current equality,
  controlled three-file tamper reporting, exact v19→v20 lineage, and every
  v1–v20 integrity pin pass. Gateway, HTTP, Event Stream, and Change Feed hashes
  remain the v19 bytes.
- The official evaluator passed 108 cases with 37 confirmed gaps, Phase 3B true,
  zero remote calls, and `evaluation_passed=true`. Accepted gold remained SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns 1784135857000000000.
- Both authoritative full suites passed: `2305 passed, 2 skipped, 3 warnings` in
  134.19s and 134.58s. The first pre-final repetition correctly exposed one
  stale test expectation naming active v19; updating that test to the required
  v20 contract produced the two clean final repetitions without a production
  behavior change.
- Final offline wheel SHA is
  `363bb1d525390db71f2e870a64e4b286a7cf8adc5a19e0c52f32486e7c48169c`.
  Its installed assets contain exactly one formal EventSource plus the polling
  fallback, and installed HTTP smoke covers static files, health, `/run`, SSE
  ready/changed/replay, changes, Chat, Turn status/cancel, and Sessions.
- Browser acceptance used an isolated real Gateway at 1280×900. Healthy state
  recorded one `/api/v1/events` and zero `/api/v1/changes` after startup; one
  persistent browser SSE connection was present. External Runs, an open Run
  timeline, completion, Sessions/Dock, and an active Turn updated through real
  REST rereads while draft/focus/selection/current Run/current Session remained.
- Disabling only SSE produced truthful polling fallback and still discovered an
  external Session. Re-enabling SSE returned to realtime and stopped polling;
  full Gateway stop retained old data, restart recovered via new-epoch full
  sync, and page reload ready-sync remained correct. All 8 main routes plus 5
  Memory routes rendered in 208/682/380 px columns with no horizontal overflow,
  console warning/error, path/secret/transport-ID/revision disclosure, or
  `[object Object]`. In-app visibility could not be driven faithfully, so the
  deterministic controller tests are the authority for hidden/resume behavior.
- Direct security/lifecycle/scope review found no blocking issue. All task-owned
  Gateways, clients, browser tabs/viewport overrides, isolated homes/workspaces,
  wheels, reports, and fixture scripts were stopped or removed. No Batch 7C,
  Batch 8A, token streaming, new backend event, or backend authority change was
  implemented.

---

# MiniCode Dashboard Batch 7A.1 Working Notes

## Frozen starting evidence

- Before production edits the isolated suite passed `2252 passed, 2 skipped, 3
  warnings in 121.26s`; warnings were the existing benchmark markers.
- Active v18 matched its deterministic candidate and all 35 protected files;
  every v1–v18 pin was valid. v18 SHA was
  `515d3cacd96365bc09bfb608df59ff1bfcc4b0c10cff1d1e4e114cb8ef6ecee5`.
- The official evaluator passed 108 cases with 37 confirmed gaps, Phase 3B true,
  zero remote calls, and `evaluation_passed=true`. Accepted gold remained SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns 1784135857000000000.
- Scoped static checks passed; repository-wide Ruff had the frozen 686 unrelated
  findings (448 F401, 73 F541, 57 F841, 46 F821, 19 E402, 18 E741, 12 E712,
  7 F811, 4 E702, 2 E731). Runtime dependencies were `[]`.

## Implementation evidence

- `DashboardEventStream` owns one sampler, one process epoch, a 256-item changed
  ring, eight subscriber slots, 15-second content-free heartbeats, and strict
  schema/cursor/frame limits. It merges six resource invalidations in fixed
  order and contains source failures without serializing exceptions.
- `GET /api/v1/events` is query-free, no-store, non-buffered SSE with strict
  pre-header 400/406/503 responses and a five-second per-client write timeout.
  Gateway composes exactly one existing Change Feed and one Event Stream, then
  closes the stream before its server.
- Deterministic module/HTTP tests cover ready, changed, retained replay without
  cursor duplication, old/future/expired cursor reset, epoch restart, heartbeat,
  ring overflow, slow client, shared sampler, subscriber budget, post-header
  timeout, idempotent close, and close wakeup.
- Independent Python processes mutate real Run, Turn, Session, Memory, Skill, and
  MCP persistence. Events identify only the affected fixed resource and contain
  no body, identifier, path, secret, diagnostic, or client metadata.
- Focused compatibility currently passes 244 tests across SSE, Change Feed,
  formal polling, Dashboard HTTP/UI, Chat/Cancel/Status, cancellation, Turns, and
  installed-wheel routes. The wheel smoke proves ready, changed, disconnect,
  Last-Event-ID replay, and the unchanged route surface outside the source tree.

## v19 certification

- Active v19 protects 36 files. Exact v18→v19 lineage is changed
  `minicode/gateway.py` and `minicode/web/http.py`, added
  `minicode/web/event_stream.py`, removed none.
- Single reason: `Batch 7A.1 versioned SSE event transport`; reason code:
  `dashboard_sse_event_transport`.
- Manifest SHA is
  `9c48c5c0f02f48c49a31411292b1d65b1e52de4667c2048477343ff64eaa82c6`.
  Candidate equality, all 36 current hashes, exact lineage, and every v1–v19 pin
  pass. Baseline tests pass 101; semantic contract/isolation tests pass 34.

## Final acceptance

- Official evaluation stayed 108 cases / 37 gaps / Phase 3B true / remote 0 /
  pass. Accepted gold SHA, 3,033,592-byte size, and mtime_ns were unchanged.
- Full suite passed twice around the evaluator: `2296 passed, 2 skipped, 3
  warnings` in 133.84s and 133.58s. The warnings remain the original benchmark
  markers.
- Scoped Ruff, py_compile, compileall, production JS checks, security scans,
  offline wheel content, installed-wheel smoke, dependencies `[]`, and the exact
  repository-wide 686 pre-existing Ruff findings were certified.
- Isolated 1280×900 browser covered 8 main + 5 Memory routes, 208/682/380 px
  columns, no overflow, no console issues, no disclosure/object text, external
  Run live polling, Gateway loss, and restart recovery. The browser sandbox did
  not expose a constructible EventSource, so the specified standard-library
  fallback proved the real Gateway SSE ready/changed/two-client/replay/reset/
  heartbeat contract; deterministic tests cover expired ring and slow clients.
- Direct review found no blocking lifecycle, cursor, security, performance, or
  scope issue. Formal app.js stayed polling-only with no EventSource.
- All task-owned listeners, processes, browser tabs/viewport, temporary homes,
  reports, wheels, and scripts were removed. Port 18973 is closed. The user's
  separate `python -m minicode.gateway` process remains alive and untouched.

Batch 7A.1 is complete; Batch 7B, 7C, and 8A remain unimplemented.

---

# MiniCode Dashboard Batch 7A Working Notes

## Phase 1 — frozen baseline and source mapping (complete)

- Before production edits: full suite `2218 passed, 2 skipped, 3 warnings`; repository-wide Ruff remains the frozen 686 pre-existing findings; `compileall`, explicit `py_compile`, and both production JS syntax checks pass.
- Active source baseline is v17 (`candidateMatches=true`, 33/33 current files, every v1-v17 manifest pin and lineage valid). Accepted semantic gold is unchanged at SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime ns `1784135857000000000`.
- Official isolated evaluator: 108 cases, 37 confirmed gaps, zero remote calls, phase3b true, `evaluation_passed=true`.
- Persistence map: workspace Run and Turn stores are already isolated by `stable_workspace_id`; Session persistence is global and the feed will conservatively invalidate the current workspace from stat-only facts while existing REST remains the filtering authority; Memory uses six fixed legal files; Skills uses the four existing roots and only legal summary files; Connections combines two fixed config paths with a stable projection of current registry state.
- Interface decision: `DashboardChangeFeed.snapshot()` is the sole observation interface. HTTP only serializes it; Gateway only composes it. Revisions are deterministic opaque hashes of bounded, content-free facts and never expose source paths, names, identifiers, timestamps, or contents.
- Frontend decision: one recursive `setTimeout` controller owns `/api/v1/changes`, visibility pause/resume, one in-flight request, generation/abort fencing, bounded retry, and dispatch to existing REST loaders. REST stores remain authoritative; Turns may invoke only the existing status operation and never resend.

## Phases 2–4 — backend and frontend implementation

- First RED failed at collection because `minicode.web.change_feed` did not exist. The public contract now returns stable `rev_<64 hex>` markers for exactly Runs, Sessions, Turns, Memory, Skills, and Connections while `generatedAt` changes independently.
- Stat-only resource tests cover corrupt/binary persisted bytes, Run metadata/event changes, Session base/delta/index changes, Turn transitions, six legal Memory files, legal Skill summaries with ordinary-file exclusion, fixed MCP configs, stable process-state projection, workspace isolation, unsafe symlinks, global scan exhaustion, source-local failure, no content/write leakage, and Python hash-seed determinism.
- The strict `/api/v1/changes` adapter is no-store, accepts no query fields, and converts unexpected source failure to a fixed `changes_failed` response. Gateway composition injects the same current-state registry used by existing Connection projections.
- Directory enumeration is included in the global scan budget, uses a no-follow directory descriptor, and makes budget-exhausted revisions independent of filesystem enumeration order.
- The frontend RED drove a pure controller with injected scheduler, transport, abort factory, visibility source, refresh dispatcher, and state sink. It now proves first-response baselining, changed-resource coalescing, one in-flight request, pause/abort/immediate-resume, 2/4/8/16/30-second retry, success reset, and stale-generation rejection.
- Resource dispatch reuses existing REST loaders, preserves selected Run/Session detail while refreshing, keeps draft/focus/selection/main/chat scroll, invalidates inactive stores for their next route, and performs Turn status GET only when the Turns revision changes. A terminal auto-status fences the original POST generation, so it cannot duplicate completion or resend content.

## Phases 5–7 — final certification

- MCP current-state invalidation is selected before probes using the effective
  Workspace's bounded opaque server keys. The installed-wheel cross-Workspace
  sentinel remained at zero probe calls; configuration commands, args, env, and
  credentials did not reach the Change Feed or DOM.
- Code review hardened focus/scroll restoration: render-reset state is restored,
  but user focus/scroll actions during an active fetch win. A fake-DOM behavior
  test and the virtual scheduler suite now total 4 focused controller tests.
- Active v18 is exact: manifest SHA
  `515d3cacd96365bc09bfb608df59ff1bfcc4b0c10cff1d1e4e114cb8ef6ecee5`,
  35/35 current protected files, five changed + two newly protected + zero
  removed from immutable v17, candidate equality, and all v1–v18 pins true.
- Final required sequence passed: full `2252 passed, 2 skipped, 3 warnings in
  162.85s`; official evaluator 108 cases / 37 gaps / Phase 3B true / remote 0 /
  pass; evaluator-after full `2252 passed, 2 skipped, 3 warnings in 163.03s`.
- Accepted gold remained SHA `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3,033,592, mtime_ns 1784135857000000000. Dependencies remain `[]`.
- Scoped Ruff, py_compile, complete compileall, and formal JS node checks pass;
  repo-wide Ruff remains the unchanged 686 pre-existing findings.
- Isolated 1280×900 browser: external Session/Run appeared in about three
  seconds; an already-open Run detail advanced 2→4→5 rows and completed without
  reload; disconnect retained old data and showed reconnecting; restart restored
  the feed and preserved the draft. Eight main/five Memory routes passed,
  208/682/380 px columns had no horizontal overflow, page console was empty, and
  no secret/path/object leak appeared. The browser backend could not make the
  controlled page hidden, so visibility has deterministic-test rather than visual
  evidence. Production Provider Chat was not submitted; installed fake-runtime
  wheel and complete Chat/Turn tests cover active/terminal/no-resend behavior.
- All Batch 7A temporary listeners, processes, browser state, homes, workspaces,
  evaluator/wheel reports, and test artifacts were cleaned. The user's separate
  live Gateway was preserved. Batch 7B was not entered.

---

# MiniCode Dashboard Batch 6B-2B Working Notes

## Scope and initial audit

- Scope is only cooperative cancellation, `cancel_requested`/`committing`/
  `cancelled`, a process-local token, safe Agent checkpoints, strict Cancel HTTP,
  Dock state, restart reconciliation, v16, and required certification. Forced
  thread/provider/tool termination, side-effect rollback, polling/push/streaming,
  management controls, distributed coordination, and Batch 7 remain excluded.
- The attached 814-line specification was read completely. No AGENTS.md,
  CONTEXT.md, CLAUDE.md, or CODEX.md was found in this workspace.
- Deep-module plan: extend the existing Turn Store instead of creating another
  state database; add one optional token interface at the Agent seam; keep HTTP
  as a strict adapter and Gateway as composition.
- Pre-edit baseline and source-boundary audit are complete; no production file
  had been edited when the evidence below was captured.

## Untouched Batch 6B-2B baseline

- The first sandboxed full run was intentionally non-authoritative: local socket
  binding was denied and produced 49 failures plus 55 errors. Re-running the
  identical command with localhost permission passed `2144 passed, 2 skipped, 3
  warnings in 106.69s`; the warnings are the existing benchmark markers.
- The read-only v15 verifier reported active v15, `candidateMatches=true`,
  `currentFiles.matches=true` at 30/30, and every v1-v15 manifest integrity pin
  true.
- The isolated official evaluator passed 108 cases with 37 confirmed gaps,
  Phase 3B true, and zero remote calls. The accepted gold was not an output and
  remains SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime ns `1784135857000000000`.
- `pyproject.toml` runtime dependencies are `[]`.

---

# MiniCode Dashboard Batch 6B-2A Working Notes

## Scope and pre-edit baseline

- Scope is only durable `turn_<32 lowercase hex>` identity, a safe Turn Store,
  duplicate-execution prevention, exact Session-marker crash reconciliation,
  strict POST/GET contracts, and one-refresh/manual frontend recovery. Cancellation,
  queues, polling, SSE/WebSocket, streaming, live tool state, Provider deduplication,
  multi-machine coordination, Batch 6B-2B, and Batch 7 remain excluded.
- Required planning records, Batch 6B-1/6A.2 docs, Conversation/Gateway/Session/
  Run/Web production seams, current tests, formal JS, packaging, v14 generator and
  semantic evaluator were audited before production edits. No AGENTS.md or CONTEXT
  file exists in this workspace.
- The unchanged pre-edit suite passed `2095 passed, 2 skipped, 3 warnings in
  138.08s`. The warnings are the existing unregistered benchmark marks.
- Default production verification is green: active v14, candidate equality,
  current protected files 26/26, and v1-v14 manifest integrity all true.
- The official semantic evaluator passed with 108 cases, 37 confirmed gaps, zero
  remote calls, and `evaluation_passed=true`. Accepted gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime ns `1784135857000000000`. Runtime dependencies are `[]`.
- Current confirmed gap: POST has no turn identity, Conversation always creates a
  runtime/Run for a repeat request, Session has no authoritative per-turn marker,
  Gateway exposes no status lookup, and the independent frontend Chat store has no
  active-turn recovery record. These are now being fixed test-first.

## RED, implementation, and recovery evidence

- The first Turn Store test collection failed with `ModuleNotFoundError`; the
  first Conversation/HTTP collections failed on absent durable identity/status
  types; the focused frontend contract failed two assertions for the missing
  synchronous/recoverable label and active-turn reconciliation functions.
- `ConversationTurnStore` now owns the closed ID/fingerprint/schema/state machine,
  atomic no-follow files, local claim set, and bounded 10,000-record/20,000-scan/
  90-day-terminal/one-day-temp retention policy.
- Session schema remains backward compatible while persisting an internal exact
  turn/user/assistant index marker. Public Session projections and DOM remain
  unchanged and never expose it.
- Conversation claims before any Session/Run/Agent work. Duplicate live requests
  return in-progress, different fingerprints conflict, and terminal records never
  execute again. Restart reconciles an exact Session marker to completed;
  otherwise an abandoned accepted/running turn becomes interrupted.
- The status endpoint is strict, read-only, no-store, workspace scoped, and
  allowlisted. The formal client creates IDs with Web Crypto, persists no content,
  performs one refresh lookup, and exposes only manual checks for unresolved turns.

## Final certification

- Focused: Turn/identity/Conversation 34 passed; Chat HTTP/restart 40;
  compatibility 133; Dashboard Web 62; all Dashboard 234; packaging/wheel 9;
  v15 baseline 63; semantic evaluator contracts 32.
- Static: scoped Ruff, py_compile, repository compileall, and both JavaScript
  checks pass. Repository-wide Ruff retains 82 unrelated pre-existing findings.
- v15 is active with 30/30 files, candidate equality, all v1-v15 pins true, exact
  two changed/four newly protected/zero removed lineage, and manifest SHA
  `f9e6254c59f8e7b4065c70aba28c20e8d53361e252866a1519264be92704df7a`.
- Official evaluator remains 108 cases / 37 gaps / 0 remote calls. Accepted gold
  remains SHA `5629d6...fdd3b`, 3,033,592 bytes, mtime ns
  `1784135857000000000`; behavior/per-case fingerprints remain pinned.
- Final full suite passed `2144 passed, 2 skipped, 3 warnings in 107.18s`.
- Isolated 1280×900 browser acceptance covered response-loss refresh, one
  reconciliation, running/manual completed recovery, fixed interrupted/failed,
  no resend/polling, all routes, XSS, disclosure, layout, and zero console
  warnings/errors. The listener, browser tab/viewport, HOME, workspace, and
  temporary acceptance script were cleaned.
- Detailed contracts: `docs/minicode-dashboard-batch-6b-2a.md` and
  `docs/memory-retrieval-production-baseline-v15.md`. Cancellation, push,
  streaming, background queues, Provider deduplication, distributed coordination,
  Batch 6B-2B behavior, and Batch 7 remain unimplemented.

---

# MiniCode Dashboard Batch 6B-1 Working Notes

## Scope and pre-edit baseline

- Authorized path is synchronous Dock → strict Chat HTTP → real Agent → one
  `source=gateway` Run linked to a real Session → one finished-turn Session save
  → response and explicit read-store refresh. No streaming, polling, cancellation,
  authentication, background job, real-time transport, or Batch 6B-2 work is allowed.
- Pre-edit sandbox run reached `1988 passed, 2 skipped` with `48 failed + 16
  errors` solely at denied localhost bind. The approved unchanged rerun passed
  `2052 passed, 2 skipped, 3 warnings in 84.56s`.
- Active v13 is green: deterministic candidate equality, current protected
  sources 23/23, and every v1–v13 manifest pin true. Accepted gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime ns `1784135857000000000`; dependencies are `[]`.
- Gateway, Headless, Run lifecycle, Agent Loop, Run events, Session persistence,
  TUI finished-turn flow, Web HTTP/read model, formal assets, related tests, Batch
  6A docs, and v13 generator/manifest were audited before production edits.
- The Session store already supplies bounded POSIX busy and stale-revision conflict
  errors. The Chat transaction must not acquire its writer lock until after Agent
  execution, and must map those existing errors without retry, merge, or rerun.
- v13 protects Gateway and Headless. The new Chat execution composition will be
  protected in a new exact v14 lineage; v13 and the semantic artifact stay frozen.

## Stabilized implementation

- `minicode.agent_runtime` is the shared, production Agent composition used by
  Headless and Chat. `minicode.conversation.ConversationTurnService` owns the
  Web-independent transaction; `minicode.web.chat_http` owns strict transport.
- One request loads/creates a scoped Session, opens exactly one linked Gateway
  Run, executes Agent once without a Session flock, then commits once. Conflict
  and busy preserve the winning bytes and never rerun; Agent/no-assistant failure
  has no fake assistant and attempts a truthful user-only commit.
- The formal Dock now uses an independent request-generation `chatStore`, keeps
  failure/conflict drafts, prevents duplicate submit, never auto-resends, and
  refreshes existing read stores only after success. Chat content is not written
  to browser storage and DOM content stays escaped.
- `/run` compatibility tests remain green: its response shape and one Gateway
  Run with null Session association are unchanged.

## Certification evidence

- Conversation service 11 passed; strict Chat HTTP/restart 24 passed; broad
  Chat/Session/Run/Headless/TUI/Dashboard/MCP matrix passed after updating one
  old composition fixture; installed-wheel packaging smoke passed 9.
- Ruff, modified-file `py_compile`, repository `compileall`, and both formal JS
  syntax checks passed. dependencies remain `[]`.
- v14 protects 26 files. Exact lineage is three changed files
  (`gateway.py`, `headless.py`, `run_lifecycle.py`) plus three additions
  (`agent_runtime.py`, `conversation.py`, `web/chat_http.py`), with no removals.
  Manifest SHA is `c00bff9983800f3d1ae579aaa5ed20de2671b3e3162aa8942db709b91d5093ce`;
  candidate equality and every v1-v14 pin pass while v13 remains byte-identical.
- Semantic evaluation passed 108 cases / 37 gaps / zero remote calls. Accepted
  gold remained SHA `5629d6...fdd3b`, 3,033,592 bytes, mtime ns
  `1784135857000000000`.
- Evaluator-after final full suite passed `2095 passed, 2 skipped, 3 warnings in
  98.01s`; the warnings are the existing unregistered benchmark markers.
- The isolated 1280×900 browser run covered new/continued/restarted/history/new
  Session flows, submitting, Agent failure, manual recovery, real conflict, all
  required routes, Run linkage, XSS text rendering, layout and console. Columns
  measured 208/682/380 px, with no overlap/overflow and zero warnings/errors.
  All browser/Gateway/HOME/workspace/test resources were cleaned.
- Detailed interface and deferred-scope record:
  `docs/minicode-dashboard-batch-6b-1.md`. Streaming, cancel, background jobs,
  authentication, controls, polling, and all Batch 6B-2 work remain deferred.

---

# MiniCode Dashboard Batch 6A.2 Working Notes

## Scope and pre-edit baseline

- Scope is strictly local POSIX cross-process Session writer coordination. No
  Dashboard Chat/write route, polling/push, Run/MCP control, Agent/Memory/TUI
  behavior change, frontend feature, Batch 6B, database, or dependency is allowed.
- Required source, tests, Batch 6A docs, and current durable planning records were
  audited before production edits. The current writer order is only public
  `RLock → _*_locked()`; save/full/delta/cleanup/index, delete/index, and old
  cleanup all need the same added cross-process transaction seam.
- Restricted pre-edit suite: `1972 passed, 2 skipped`, then 48 failures and 16
  errors solely at denied localhost bind. Approved unchanged rerun:
  `2036 passed, 2 skipped, 3 warnings in 84.15s`.
- Accepted baseline remains v13 at 23/23 and semantic gold is expected to remain
  SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime ns `1784135857000000000`; these will be independently
  checked in the mandated final order. Runtime dependencies are `[]`.

## Planned deep module and revision contract

- One `session_store_transaction(data_dir)` interface will dynamically open
  `<data_dir>/session-store.lock` as a regular no-follow `0600` file, acquire an
  exclusive non-blocking flock with monotonic bounded retry, and release by
  unlock/close only. The persistent file contains no payload and is never unlinked.
- Every writer will acquire exactly once in order: process-local RLock, then the
  cross-process transaction, then complete state check plus multi-file mutation.
- A Session caller revision must include base-presence, bounded persistence
  generation, and legal delta next-sequence. Acquired-lock disk state must match
  before any save; mismatch raises a low-information `SessionWriteConflictError`
  without merge, overwrite, sequence reuse, rollback, or automatic stale full save.
- Reads remain lock-free and per-file atomic, not a multi-file snapshot transaction.

## RED/GREEN evidence and stabilized behavior

- Original real spawned-process RED: both different-Session writers were paused
  after loading the same empty shared index, then released; both base files were
  complete but the final index retained only one ID.
- Original same-Session RED: two independently loaded generation-1/tail-0 writers
  ran in sequence; the second returned success, reused/overwrote
  `delta_0000.json`, and replaced the first writer's message. The two-test RED
  result was exactly `2 failed in 0.23s`.
- The first transaction/revision GREEN passed both tracers. The stable lock module
  owns dynamic path resolution, restrictive creation, CLOEXEC/NOFOLLOW flags,
  descriptor/path regular-file identity validation, monotonic nonblocking flock
  retries, low-information errors, and unlock/close without unlink.
- `SessionData` now retains an internal base-presence bit in addition to bounded
  generation and legal delta next-sequence. After the writer obtains flock it
  rereads those disk facts; any mismatch raises `SessionWriteConflictError`
  before metadata mutation or file I/O. New, legacy generation-zero, incremental,
  full-save, cleanup-failure, and retry states remain distinguishable.
- All public writers acquire exactly once in the same order: process-local RLock,
  `session_store_transaction(MINI_CODE_DIR)`, then the complete helper transaction.
  Reads remain unchanged and lock-free.
- Cross-process suite: `16 passed`; it covers different Sessions, save/delete,
  stale force-full rejection, sequential reload/append, real holder timeout with
  base/delta/index byte equality, Autosave dirty retry, abrupt `os._exit`, symlink,
  directory, FIFO, open denial, empty 0600 persistent lock visibility, cleanup,
  dynamic data roots, control-flow identity, and deterministic injected monotonic
  timeout.
- Session/TUI/Dashboard/HTTP focus: `199 passed in 29.47s`; narrower
  Session/cross-process/TUI/page projection focus was `122 passed`.
- Installed-wheel smoke initially exposed only an outer/inner `\n` fixture escape
  error. After fixing the test string, the wheel smoke passed; the installed
  module ran two synchronized independent Session writers outside source cwd,
  retained both index entries, and served compatible Gateway/API/static routes.
- Production HTML/CSS/JS SHA-256 values remain exactly the recorded Batch 6A.1
  values (`8af1580a...afba`, `beeba02c...8962`, `1c4dfb34...f68e`,
  `194e6b99...2916`). No browser visual rerun is planned because no UI byte changed.

## Final Batch 6A.2 certification

- Modified Python files passed Ruff and `py_compile`; `compileall -q minicode
  scripts tests` and both production JavaScript syntax checks passed.
- Full packaging/wheel matrix: `9 passed in 3.72s`. Installed two-process writes,
  Gateway `/run`, `/health`, Sessions API, and static resources all passed outside
  source cwd with isolated HOME/workspace and user site disabled.
- Default verifier stayed on `memory-retrieval-production-v13` with candidate
  equality, current protected source 23/23, and all v1-v13 integrity flags true.
- Official evaluator passed 108 cases with 37 confirmed gaps, Phase 3B true,
  zero remote calls, and evaluation true. Accepted gold before/after remained SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime_ns `1784135857000000000`.
- Evaluator-after full regression passed `2052 passed, 2 skipped, 3 warnings in
  85.01s`; warnings are the same three benchmark-marker warnings.
- Dependencies remain `[]`. Static assets are byte-identical, so this no-UI batch
  did not repeat browser visual acceptance and claims no viewport/DOM/console run.
- Bounded spawned processes, wheel source/install, isolated HOME/workspace,
  synchronization files, HTTP listeners, and pytest directories were cleaned;
  no workspace `session-store.lock` or atomic `.tmp` remains. No Git repository
  was initialized. Batch 6B and Dashboard Chat were not entered.

---

# Notes: Memory Retrieval Phase 1

# MiniCode Dashboard Batch 6A.1 Working Notes

## Scope start

- Authorized repair: generation-authoritative full base, stale-delta recovery,
  collision-free delta sequences, and in-process shared-index consistency.
- Existing finished-turn semantics, Dashboard schema/store/Dock design, Agent
  Loop, `tui/input_handler.py`, Memory, Skills, MCP, RunJournal, `/run`, semantic
  gold, cross-process writer locking, Batch 6B, and Batch 7 are out of scope.
- Baseline, current call graph, RED/GREEN evidence, wheel, v13/gold, two full
  suites, browser compatibility, and cleanup will be recorded here.

## Phase 1 baseline and call-graph audit

- Pre-edit full regression: `1996 passed, 2 skipped, 3 warnings in 122.06s`.
- Active verifier: `memory-retrieval-production-v13`; candidate equals active,
  current protected source is 23/23, and every v1-v13 integrity pin is true.
- Accepted semantic gold: SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime_ns `1784135857000000000`.
- Packaging dependencies remain `[]`.
- Current 1280x900 formal screenshot:
  `artifacts/minicode-dashboard-batch-6a-sessions.jpg`, SHA-256
  `82b40f77b866cc115ac4d82afa8151ff067512c9b684ae8961f9f399ad1bc39b`,
  JPEG, size 68035. Visual inspection confirms the accepted Waku-style
  three-column real read-only Sessions page and Dock.
- Current static fingerprints: `index.html` SHA `8af1580a...afba` / 3191
  bytes; `app.js` SHA `beeba02c...0962` / 137410 bytes; `styles.css` SHA
  `1c4dfb...f68e` / 38510 bytes; `cost-format.js` SHA
  `194e6b...2916` / 1208 bytes.
- Base/delta/index call graph: `save_session()` selects full or `_save_delta()`;
  full replacement calls `_consolidate_deltas()`; all paths then independently
  `_load_session_index()` -> mutate -> `_save_session_index()` ->
  `_atomic_write_text()`. `delete_session()` owns a second unlocked index RMW;
  `cleanup_old_sessions()` reaches it through `delete_session()`.
- Production root cause: `_consolidate_deltas()` swallows each unlink failure and
  then always resets `_delta_save_count` to zero. `load_session()` applies every
  syntactically parseable delta, including its complete `session_state`, with no
  full-base authority generation. A retained pre-full delta can therefore roll
  history/metadata back after a successful base replacement.
- Dashboard root cause: `DashboardReadModel._read_session_data()` independently
  applies every bounded four-digit delta to base messages without generation
  validation, so it can disagree with the hardened Session loader unless aligned.
- Concurrency root cause: shared `sessions_index.json` RMW has no process-local
  lock; atomic replacement prevents torn bytes but not lost updates.
- `_atomic_write_text()` catches only `FileNotFoundError` during temporary cleanup,
  so another cleanup `OSError` can mask the original write/flush/fsync/replace
  exception.
- Protected-scope audit: v13 protects 23 files including Gateway and
  `tui/input_handler.py`, but not `minicode/session.py` or
  `minicode/web/read_model.py`; no v14 is warranted for the expected fix.
- Workspace has no Git metadata; no repository initialization or commit will be
  attempted.

## RED/GREEN and stabilized production contract

- Mandatory consolidation RED failed on `history`: after the force-full base
  contained A/B/C, retained `delta_0000.json` reset the reload to A/B. Messages
  happened to remain A/B/C only because overlap suppressed duplication; the same
  stale state also carried B-era metadata, permissions, Skills, and MCP state.
- Mandatory shared-index RED used a Barrier at the index RMW boundary. Both
  Session base files existed but the final index contained only one ID.
- Additional real REDs proved that NaN/Infinity state timestamps applied a whole
  invalid delta, temporary-file cleanup could mask the original replace error,
  and Dashboard's independent reader appended a stale-generation message that
  `load_session()` ignored.
- Successful full saves now write `persistence_generation = N + 1` and mutate the
  in-memory generation only after atomic base replacement. New deltas carry the
  current generation and Session ID. Legacy missing fields mean generation zero;
  missing-generation deltas apply only to a generation-zero base.
- One shared `validate_session_delta()` enforces bounded non-bool generations,
  Session identity, finite timestamps, offsets, list/state/metadata types, and
  coherent metadata message count before either Session or Dashboard mutates its
  projection. Stale generations return without applying messages, transcripts,
  or state.
- Cleanup now returns internal remaining filenames and next sequence state.
  It attempts every legal delta independently, starts at zero only after complete
  cleanup, and otherwise advances above the maximum retained sequence. Directory
  scan failure remains explicitly incomplete and conservatively forces another
  full save.
- A process-local `RLock` covers Session save, delete, old-Session cleanup, and
  every complete shared-index RMW. `list_sessions()` remains an unlocked atomic
  reader. Cross-process writers sharing HOME remain unsupported and are now
  documented accurately in `docs/minicode-dashboard-batch-6a.md`.
- `_atomic_write_text()` cleanup catches every `OSError` best-effort, preserving
  the original write/flush/fsync/replace exception.
- New Session tests cover retained first/middle/all deltas, partial cleanup,
  repeated full failures/restart, collision-free numbering, legacy upgrade,
  invalid base/delta generation, stale/current/state-only deltas, invalid state
  timestamp atomicity, Session ID/path validation, cleanup exception precedence,
  save/save concurrency, and save/delete consistency.
- Focused Session/TTY/ReadModel matrix: `103 passed`. Dashboard/HTTP/read-model
  matrix: `144 passed`; the initial restricted run failed only because all local
  socket binds were denied, and the approved localhost rerun was fully green.
- Installed-wheel tracer passed with generation-one current base/delta reload,
  legacy generation-zero base/delta reload and normal upgrade to generation one,
  a fresh-process reload, installed Gateway Sessions API, and packaged static
  resources.

## Final Batch 6A.1 certification

- A final edge-case RED proved delta root `ts` still accepted NaN, Infinity,
  negative Infinity, and bool even though state timestamps were protected. The
  final validator now rejects all four atomically, requires timestamp + coherent
  state on versioned deltas, and derives message metadata for pre-6A legacy
  deltas whose historical format had no state block.
- Final Session/TTY/Dashboard delta focus: `107 passed`.
- Modified Python files are Ruff-clean and `py_compile`-clean; complete
  `compileall -q minicode scripts tests` and both production `node --check`
  commands pass.
- v13 final verifier: candidate match true, current 23/23, all v1-v13 integrity
  true. No protected production file changed and no v14 was created.
- Official evaluator final result: 108 cases, 37 confirmed gaps, Phase 3B true,
  zero remote calls. Accepted gold stayed SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime_ns `1784135857000000000`.
- Final installed wheel/package/Gateway matrix: `9 passed`. It includes current
  generation base/delta, legacy zero reload + normal upgrade, fresh-process
  reload, Sessions API, and packaged static assets.
- Two final complete regressions after the last production edit:
  `2036 passed, 2 skipped, 3 warnings` in 83.00s and 82.97s. The warnings are the
  same three unregistered benchmark markers.
- Final 1280x900 browser smoke on an isolated HOME/Workspace rendered all eight
  main routes plus five Memory routes. A true incremental current-generation
  delta appeared exactly once in both Sessions and the real read-only Dock.
  Columns remained 208/682/380 with no overlap or horizontal overflow; disabled
  send controls remained disabled; console warning/error, absolute fixture-path
  disclosure, and object-coercion leaks were zero.
- Formal static assets and the accepted Batch 6A screenshot remain byte-identical.
  Dependencies remain `[]`. Both isolated browser fixture directories, Gateway
  listeners, tabs, and viewport overrides were removed. No Git repository or
  commit was created. Batch 6B was not entered.

---

# MiniCode Dashboard Batch 6A Working Notes

## Scope start

- The authorized chain is TUI finished turn → one bounded Session commit → atomic persistence → existing safe Sessions ReadModel → Sessions page plus shared real read-only Dock.
- Dashboard writes, `/run` from the Dock, polling, SSE/WebSocket, Agent Loop changes, Session write APIs, multi-process same-Session coordination, Batch 6B, and Batch 7 are out of scope.
- Baseline, call-graph, RED, focused, wheel, semantic/gold, full-suite, browser, and cleanup evidence will be recorded here as each phase completes.

## Independent baseline and call graph

- Pre-edit full suite: `1985 passed, 2 skipped, 3 warnings in 83.12s` after rerunning the unchanged suite with required loopback permission. The sandbox-only attempt failed solely on denied `socket.bind()`.
- Active verifier: `memory-retrieval-production-v13`, candidate match true, 23/23 protected files match, every v1-v13 manifest-integrity flag true. v13 SHA-256 is `ef295a3aa3dcfc522d4cc421310434de3013772122f3b913b6b137144a96fc2c`.
- Accepted semantic gold start: SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime ns `1784135857000000000`. Runtime dependencies are `[]`.
- TUI call graph before repair: `_handle_input()` marks autosave dirty, appends the user message, and publishes `{messages, done}` from a background thread; `run_tty_app()` only copied returned messages and cleared `done`; `finalize_tty_session()` alone copied messages/transcript/history/permissions/skills/MCP into `SessionData` and forced a save.
- Persistence before repair used direct `Path.write_text()` for base Session, each delta, and `sessions_index.json`; readers could therefore observe partially written JSON and a failed write could destroy the last valid file.
- Dashboard before repair already had safe bounded schema-v1 read-only Session interfaces, but `refreshSessions()` cleared selection, initial list loading selected nothing, no workspace-scoped browser preference existed, and the Dock consumed `DATA.sessions`, generated local fake messages, and offered a working simulated form.
- v13 protects `tui/input_handler.py` but does not protect `session.py`, `tty_app.py`, `tui/session_flow.py`, the Web static assets, or their tests. The current deep seam avoids `input_handler.py`; if this remains true after stabilization, Batch 6A must keep active baseline v13 and must not fabricate v14.

## First tracer

- RED failed at collection on missing `consume_finished_tty_turn`.
- GREEN added one main-loop consumption seam plus `commit_finished_tty_turn` and dirty-aware `AutosaveManager.save_now()`. A successful returned message list now synchronizes and reloads without `finalize_tty_session()`; the tracer plus existing Session suite passed 11 tests.

## Completed implementation

- `tty_app.py` now delegates every background completion observation to `consume_finished_tty_turn()`. The seam rechecks `done` under the existing lock, adopts a valid returned message list, clears `done` before persistence, then commits once. Failure/interrupt results with no returned list preserve the real user-only messages.
- `session_flow.py` owns coherent copying of messages, transcript, history, permissions, Skills, and MCP summaries. Immediate save failure keeps the Agent result, marks autosave dirty for retry, and exposes only `Session save deferred; will retry.` Exit full save is a safe final retry.
- `session.py` uses same-directory temporary files, flush + fsync, and `os.replace()` for base, delta, and index. The original delta branch was unreachable because a zero counter forced and reset full save; first-save detection now uses base-file existence. Delta state includes metadata and all non-message Session state. Corrupt/gapped deltas are skipped without sequence reuse, overlap is idempotent, and resume can continue without duplication.
- The storage contract remains single writer per Session. No multi-process lock or conflict resolution was added.
- Session API schema v1 did not change. Existing workspace isolation, strict IDs, symlink/file/delta/message/response bounds, cursor binding, role filtering, redaction, and escaping remain the only Dashboard projection path.
- `app.js` now has one shared Sessions/detail store for main page and Dock. It restores only `{workspaceId, sessionId}` from `sessionStorage`, auto-selects latest when needed, preserves valid selection on Refresh, rejects foreign/expired preferences, guards list/detail races with request and selection revisions, and deduplicates message pagination by index.
- The Dock renders only real safe Session history with live/partial/error/empty states, Refresh/Retry/history/Load More, and disabled `Dashboard 发送功能尚未接入` controls. Mock Session data, `openMockSession()`, simulated replies, local fake messages, and Dashboard `/run` use were removed.
- Linked TUI Runs reuse their safe `sessionId` for `查看 Session`; null Headless/Gateway associations display `未关联 Session`.

## Final Batch 6A evidence

- Expected RED sequence covered missing seam, direct-write failure injection, unreachable delta path, incomplete delta metadata, raw exit-save propagation, and old mock frontend assertions.
- Focused Session/TUI/Dashboard/HTTP matrix passed 224 tests. Dashboard HTTP passed 61; packaging passed 9. Modified Python files pass Ruff and `py_compile`; full compileall and both JavaScript syntax checks pass. Full-repository Ruff still reports 85 unrelated legacy diagnostics.
- Wheel isolation runs outside source cwd with isolated HOME/workspace and disabled user site. It commits two turns through the installed public seam without finalizer, reloads them in a new process, and verifies the installed Gateway Session list/detail plus static resources.
- Both final full suites passed `1996 passed, 2 skipped, 3 warnings` in 82.90s and 82.99s. Only the three existing unknown benchmark marks remain.
- v13 stayed active: candidate match true, 23/23 files match, all v1-v13 integrity flags true. Batch 6A did not touch protected `tui/input_handler.py` or Memory Retrieval sources, so no v14 exists.
- Official evaluator passed 108 cases, 37 gaps, zero remote calls, and evaluation true. Accepted gold remained SHA `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size 3033592, mtime ns 1784135857000000000.
- Browser fixture at 1280×900 used real public commits, three Sessions (including 60 visible messages), a failure-once detail source, a delayed stale response, invalidation control, linked TUI Run, and unlinked Gateway Run. All eight main and five Memory routes rendered; 50+10 pagination produced no duplicates; console warning/error was zero; columns 208/682/380 had no overlap or horizontal overflow.
- DOM and seven API responses contained no injected secret, absolute injected path, hidden system message, or `[object Object]`. The verified 1280×900 JPEG is `artifacts/minicode-dashboard-batch-6a-sessions.jpg`, SHA-256 `82b40f77b866cc115ac4d82afa8151ff067512c9b684ae8961f9f399ad1bc39b`.
- Browser tab, viewport override, temporary Gateway, fixture HOME/workspace/controls, and fixture script were removed. No Git repository or commit was created. Dependencies remain `[]`; Batch 6B/7 remain out of scope.

---

## Baseline
- Production SHA-256 snapshot: `/tmp/memory-retrieval-production-before.sha256`.
- Formal memory SHA-256 snapshot: `/tmp/memory-retrieval-formal-memory-before.sha256`.
- Formal memory mtime/size snapshot: `/tmp/memory-retrieval-formal-memory-before.stat`.

## Audit Findings

- `MemoryManager.search(scope=None)` searches each scope, saves every scope with hits, then applies `_global_rank` and content deduplication (`minicode/memory.py:2278-2340`).
- Query-aware `get_relevant_context` does not use that global result. It searches and budgets scopes sequentially in `LOCAL -> PROJECT -> USER` order (`minicode/memory.py:2409-2450`).
- `MemoryFile.search` excludes non-active entries, mutates `retrieval_count` for its top ten, and returns all scored entries (`minicode/memory.py:1287-1370`). `MemoryManager.search` then persists those mutations.
- stdin and TUI rebuild their system prompt with query-aware manager context before entering `run_agent_turn` (`minicode/main.py:349-372`, `minicode/tui/input_handler.py:399-412`).
- headless injects no-query manager context before the user prompt (`minicode/headless.py:83-108`).
- When a context manager exists, `run_agent_turn` creates a second `MemoryManager`, wires `MemoryPipeline`, and performs a second task-aware injection (`minicode/agent_loop.py:755-836`). Thus TUI/stdin can use two manager instances and two injection paths in one turn.
- `MemoryPipeline.inject` bypasses `MemoryPipeline.read` and calls `MemoryInjector.inject_for_task` directly (`minicode/memory_pipeline.py:311-372`). It records all returned IDs but formats only `injected[:5]`.
- `MemoryInjector` searches each scope separately, discards BM25/global score ordering in favor of `_calculate_relevance`, and iterates up to `2 * max_memories` without a final hard slice before tag additions (`minicode/memory_injector.py:228-319`).
- `MemoryPipeline.read` is a public but unreferenced production capability with query reformulation, optional vector RRF, optional reranking, and graph spreading (`minicode/memory_pipeline.py:243-307`). Vector-only IDs cannot survive current RRF because `merge_bm25_vector` can return only entries present in the BM25 input map (`minicode/vector_memory.py:196-221`).
- Pipeline feedback uses `_last_injected_ids`, while the agent declares success only when `tool_error_count == 0`; a recovered task with tool errors is therefore negative feedback (`minicode/agent_loop.py:1616-1632`).
- Context compaction independently calls no-query `get_relevant_context(max_tokens=6000)` (`minicode/context_compactor.py:453-473`).
- Timeline-memory code exists but has no non-test production caller; it is not part of persistent-memory injection.
- The compatibility helper `inject_memory_into_prompt` independently calls no-query manager context (`minicode/memory.py:3381-3398`).

## Final Verification

- New retrieval tests: 42 passed.
- Related memory/agent/TUI/headless/compactor tests: 355 passed, 3 warnings.
- Full suite with normal configured HOME: 1202 passed, 2 skipped, 3 warnings.
- Full suite with an isolated HOME containing only a copied `settings.json`:
  1202 passed, 2 skipped, 3 warnings. A completely empty isolated HOME instead
  exposes the unrelated existing config-diagnostic dependency: 1 failed, 1201
  passed because `Tool Profile:` is omitted when configuration has errors.
- Compileall and Ruff for all new Python files passed; mypy and pyright are unavailable.
- Two evaluator timing-free cores were byte-identical, both SHA-256
  `4463fe4f0e3f98e180195a4fc8054342c5f8f766177c3576ea6ed457a58e48ec`.
- Frozen production source verification passed byte-for-byte.
- Evaluator formal-memory before/after snapshots matched and remote call count was zero.
- Whole-suite isolation failed: full pytest changed
  `~/.mini-code/memory/{memory.json,MEMORY.md,approval_audit.json}` and
  `~/.mini-code/sessions_index.json`. Root causes are global USER path resolution
  and unisolated integration session tests. Files were left in place because the
  task-start snapshot did not include recoverable byte copies.

---

# MiniCode Dashboard Batch 5C-2B.1 Workspace Isolation Notes

## Scope start

- The confirmed defect is pre-filter leakage: the zero-argument Dashboard loader obtains a global Registry snapshot, so unmatched workspace probes, global diagnostics, and global response limits can affect a visible workspace before projector key matching occurs.
- Required repair seam: a bounded Registry scoped snapshot selected by opaque server-key allowlist before probe/reconciliation/grouping/budgets/diagnostics. Existing global `snapshot()` must remain behavior-compatible.
- Required downstream change: projector computes bounded configured keys first and calls one scoped loader with an exact frozen key set; Gateway passes the same owned Registry to POST `/run` and the scoped loader.
- Batch 6, current persistence, heartbeat, polling/push, process controls, Agent Loop, RunJournal, Memory, Session, TUI, and MCP request timing remain out of scope.

## Pre-change certification baseline

- Pre-change full regression independently passed `1970 passed, 2 skipped, 3 warnings in 122.29s`; the warnings are only the three existing unknown benchmark markers.
- The active v12 verifier passed with candidate equality, 23/23 current protected files, all v1-v12 integrity flags true, and exact v12 manifest SHA-256 `a8fba6ed9134b465167525f4b8c81de2369363ad0527f6368527de0369bd05a7` (size 2667, mtime ns `1784373446000000000`).
- The independent official evaluator passed 108 cases with 37 confirmed gaps, zero remote calls, and Phase 3B true. Accepted gold remained SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size 3033592, mtime ns `1784135857000000000` before and after.
- Packaging metadata still declares `dependencies = []`.
- Production audit confirmed the root cause: `McpCurrentStateRegistry.snapshot()` probes and reconciles every ready instance, applies the global response budget, and projects accumulated global diagnostics before the Dashboard projector suppresses unmatched keys.
- The mandatory tracer RED failed for the intended reason: `AttributeError: 'McpCurrentStateRegistry' object has no attribute 'snapshot_for'` in `test_scoped_snapshot_does_not_probe_unmatched_workspace_instances`.
- The projector RED then failed for the intended zero-argument seam: the new scoped loader was never successfully called and the visible projection became the fixed `mcp_current_source_failed` error result. This reproduces why the loader contract itself must carry the bounded allowlist.

## Final implementation and certification

- `McpCurrentStateRegistry.snapshot_for(Collection[str])` now validates a finite bounded allowlist and selects before probes, reconciliation, grouping, response budgets, and request-local diagnostics. Existing global `snapshot()` behavior is preserved.
- The Dashboard projector calls one scoped loader with the exact bounded `frozenset` of configured opaque keys while retaining configured display order. The Gateway captures the same Registry used by POST `/run`.
- Focused evidence: 42 Registry/projection tests, 20 Dashboard current/client tests, 7 composition tests, and 129 related MCP/current/ReadModel/Gateway/HTTP tests passed.
- Installed-wheel isolation proved live matching ready counts `1/1/1`, post-close exact `0/0/0`, and zero calls to an unmatched throwing probe outside the source cwd and user site-packages.
- v13 protects the same 23 files and records exactly `minicode/gateway.py` and `minicode/mcp_current_state.py` as changed from immutable v12. Manifest SHA-256 is `ef295a3aa3dcfc522d4cc421310434de3013772122f3b913b6b137144a96fc2c`; all v1-v13 pins pass.
- The official evaluator passed 108 cases with 37 confirmed gaps, zero remote calls, and Phase 3B true. Accepted gold remained SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime ns `1784135857000000000`.
- Two final full regressions passed `1985 passed, 2 skipped, 3 warnings` in 84.00s and 82.44s. Ruff, `py_compile`, `compileall`, JavaScript syntax checks, static scans, packaging, and dependency `[]` checks passed; pyright and mypy are unavailable.
- Browser acceptance at 1280×900 covered all eight main routes, five Memory subroutes, all required MCP current-state scenarios, Retry, limited/error/empty/ready transitions, and unmatched-workspace isolation. Console problems, horizontal overflow, fact-column overlap, forbidden DOM disclosure, and unmatched probe calls were all zero.
- The verified screenshot is `artifacts/minicode-dashboard-batch-5c-2b-1-connections.jpg`. Browser tabs, viewport overrides, fixture server, isolated HOME/workspace, and temporary directories were cleaned.
- Batch 6 remains explicitly deferred: no persistence, cross-process truth, heartbeat, polling/push, process controls, or Agent/RunJournal/Memory/Session/TUI behavior was added.

---

# Notes: Phase 1.5 Global-State Isolation And Recovery Audit

## Safety Baseline

- Real HOME: `/Users/zhourunbo`; determined before any test HOME is installed.
- Start formal metadata: `/tmp/minicode-phase15-formal-start.json` and
  `/tmp/minicode-phase15-formal-start.sha256`.
- Current-state backup:
  `/Users/zhourunbo/.mini-code/recovery-backups/memory-test-isolation-20260715-193534`.
- Backup kind: `current_post_contamination_state`; four source hashes equal four
  backup hashes. Directory mode is `0700`; copied files and manifest are `0600`.
- Frozen fixture, report, and production snapshots are under
  `/tmp/minicode-phase15-*-before.sha256`.

## Isolation Audit

- Root `conftest.py` is loaded before test modules and previously imported no
  MiniCode module; it is the earliest reliable repository-local installation point.
- Config, memory, session, context, history, logging, task tracker/graph,
  supervisor, and user profile all derive global paths from import-time
  `minicode.config.MINI_CODE_DIR`.
- Session-level HOME alone is insufficient because history caches and logging
  handlers survive file deletion. The autouse reset closes handlers, clears known
  caches, removes all worker-home state, and regenerates only a secret-free
  settings file.
- Real provider credentials are scrubbed. Tests use mock model mode, a known model,
  and a fixed non-secret auth sentinel; `ANTHROPIC_API_KEY` remains absent so live
  network tests stay skipped.
- Initial isolation verification: 15 passed; formal four-file start hashes all OK.

## Read-Only Inventory

- Audit tests: 7 passed.
- Formal source counts: 3 memory entries, 312 approval records used only as
  evidence, and 850 session-index records.
- Classification: 21 confirmed test artifacts (3 memory, 18 session), 805
  probable test sessions, 0 ambiguous, and 27 protected session records.
- All 805 probable records have only one evidence group (`pytest_workspace`) and
  therefore remain manual review; the tool does not inflate them to confirmed.
- Proposed actions: 3 memory removals, 18 confirmed session-index removals, 805
  manual reviews, 27 no-action protections, one conditional Markdown
  regeneration, and one retain-audit/append-cleanup action. All 855 are unapproved
  and unexecuted.
- Actual dry-run left the four formal files hash-identical to the phase start.

## Runtime Verification

- Related isolation/memory/session suites: 207 passed.
- First exploratory full suite: 1 failed and 1223 passed because the required
  process-level core tool profile overrode one test's explicit runtime full
  profile. The test now removes that override locally without weakening assertions.
- Two subsequent plain full suites: 1224 passed, 2 skipped, 3 existing benchmark
  marker warnings in 12.83s and 12.94s.
- Both full runs used direct `python3 -m pytest -q`, with no HOME wrapper or copied
  real configuration.
- After each related/full run, all four formal files matched start exists/hash/
  size/mtime_ns exactly.

## Final Verification

- Final isolation tests: 16 passed; audit tests: 7 passed.
- Final two plain full suites after all code changes: 1225 passed, 2 skipped,
  3 existing benchmark marker warnings in 12.92s and 12.94s.
- The acceptance command was then repeated literally as `python -m pytest -q`
  twice: 1225 passed, 2 skipped, 3 warnings in 13.12s and 13.16s; final formal
  exists/hash/size/mtime_ns remained exactly equal.
- Recursive real `~/.mini-code` tree guard is enabled in addition to explicit
  high-risk paths, so newly created unknown files are also detected.
- Compileall passed; Ruff passed all seven new/modified Python files.
- Inventory, Phase 1 baseline, and backup manifest JSON all parsed.
- Inventory/report secret scan passed; session raw IDs, workspaces, messages, and
  provenance values are absent; all actions remain unapproved.
- Retrieval/Reflection frozen fixtures and reports passed SHA-256 verification.
- All 12 frozen production files passed SHA-256 verification; no production code changed.
- Final formal four-file exists/hash/size/mtime_ns comparison: all equal.
- `pytest-xdist` is not installed. Worker-specific path behavior is unit tested,
  but a real parallel `pytest -n` run is unavailable and is not claimed.

---

# Notes: Memory Retrieval Phase 2A

## Formal Data Start Snapshot

- `/Users/zhourunbo/.mini-code/memory/memory.json`: exists, SHA-256 `5236e66fbfffd6b61bf7f0060a7d1786f17efa389005dd84b6bb139c66305d76`, size `3255`, mtime_ns `1784113833026526065`.
- `/Users/zhourunbo/.mini-code/memory/MEMORY.md`: exists, SHA-256 `a2a68e8e6c9b4c086126a24dd66839d4be87c74eeac0b798700d4088791a1a5b`, size `116`, mtime_ns `1784113833026654606`.
- `/Users/zhourunbo/.mini-code/memory/approval_audit.json`: exists, SHA-256 `694c96793f28f20dde0584fe860b8135d0ea2c02d846f621ee4cbee427e21a20`, size `209458`, mtime_ns `1784113833026328898`.
- `/Users/zhourunbo/.mini-code/sessions_index.json`: exists, SHA-256 `51de6579dd45fae04285791899863251224298e14c535bc4d3af60bf222eabe6`, size `331163`, mtime_ns `1784113828123668004`.
- Snapshot was made with raw byte reads only; no MiniCode memory module was imported and no backup or recovery action was performed.

## Phase 2A Constraints

- Frozen Phase 1 labels/baselines/reports and Phase 1.5 inventory/report must remain byte-identical.
- Reflection, evidence, synthesizer, claim/value gates, safety, approval, curator, dedupe, and entry-ID write semantics are out of scope.
- The canonical path is deterministic and local: no embeddings, remote reranking, LLM summaries, query rewrite, or provider call.

## Frozen Asset Start Snapshot

- Phase 1: 15 files, manifest `/tmp/minicode-phase2a-phase1-frozen.sha256`, manifest SHA-256 `48fd0a451089c472cb1d39bed5d684a420fe10e614a21bb2c185103e78247997`.
- Phase 1.5: 8 files, manifest `/tmp/minicode-phase2a-phase15-frozen.sha256`, manifest SHA-256 `1dd40c659938f9b1d7d2501950e5b80599c6bf24cd83488ad64c663c52f40de9`.

## Production Read Audit

- stdin and TUI rebuild a query-aware system prompt through `MemoryManager.get_relevant_context`, then `run_agent_turn` can create a second manager and inject again.
- headless builds a no-query memory context and does not pass its manager into `run_agent_turn`.
- `MemoryPipeline.inject` bypasses `read`; `MemoryInjector` searches each scope separately, replaces BM25 relevance with a fixed-base score, may return above the final limit, records before rendering, and can inject a free-text reranker summary.
- `MemoryFile.search` mutates the first ten retrieval counters; `MemoryManager.search` saves every touched scope during candidate generation.
- final feedback uses `_last_injected_ids` or the Injector cache and classifies any turn with an intermediate tool error as failed, even when `turn_outcome` is success.
- session-memory compaction calls no-query `get_relevant_context`, so active memory can become an unrelated compaction summary.

## TDD Baseline

- `python -m pytest -q tests/test_memory_retrieval_phase2a.py` failed at collection with `ModuleNotFoundError: minicode.memory_retrieval`, as expected before implementation.

## Phase 2A Final Verification In Progress

- The authoritative start hash above is corroborated by `/tmp/minicode-phase15-formal-start.sha256`, the frozen Phase 1 baseline, and the frozen Phase 1.5 contamination inventory. An earlier handwritten note used `...d6f61...`; that typo was corrected without touching formal data.
- The canonical evaluator passes all correctness, quality, and performance gates on 80 cases and five arms; remote model calls are zero.
- Two final evaluator runs produced byte-identical deterministic cores with SHA-256 `824446d8f1d53e2e28b1a2e58058420352933343f02ec64106534e6e7ad73f0b`.
- Phase 2A targeted tests: 65 passed.
- `compileall`, Ruff on all changed Python files, JSON readback, report secret scan, and both frozen manifests pass.
- Two literal `python -m pytest -q` runs passed: `1290 passed, 2 skipped, 3 warnings` in `15.73s` and `15.60s`. The warnings are the existing unregistered `benchmark` marks.
- The four formal files matched their authoritative start SHA-256, size, and mtime_ns after each full suite and in the final check.
- Final status: all Phase 2A correctness, quality, performance, determinism, isolation, privacy, and frozen-asset gates passed.

# Notes: Memory Retrieval Phase 2B

## Start Integrity Snapshot

- Complete formal `~/.mini-code` tree: 864 files; snapshot `/tmp/minicode-phase2b-formal-tree-start.json`; snapshot SHA-256 `1045d27719299c654e237883ff68d5ecbc5bfcb02f3a730ebfe2eaa9e36ad372`.
- Formal USER memory JSON SHA-256: `5236e66fbfffd6b61bf7f0060a7d1786f17efa389005dd84b6bb139c66305d76`.
- Formal MEMORY.md SHA-256: `a2a68e8e6c9b4c086126a24dd66839d4be87c74eeac0b798700d4088791a1a5b`.
- Formal approval audit SHA-256: `694c96793f28f20dde0584fe860b8135d0ea2c02d846f621ee4cbee427e21a20`.
- Formal sessions index SHA-256: `51de6579dd45fae04285791899863251224298e14c535bc4d3af60bf222eabe6`.
- Phase 1 frozen manifest: 15 files at `/tmp/minicode-phase2b-phase1-frozen.sha256`, manifest SHA-256 `9bc641e076ed7a3b29af1698ffc9e13577b98017d9d2bd92feb2fb7d6aaf4f27`.
- Phase 2A frozen manifest: 8 files at `/tmp/minicode-phase2b-phase2a-frozen.sha256`, manifest SHA-256 `f2fc92330dff7f1740b9b594c550fe624a9ee0c0110f2d765c68dc85c98f7953`.

## Audit Findings

- Phase 2A has 17 rendered must-exclude IDs across 16 cases: weak cross-context noise, lower-authority direct conflicts, and unverified/obsolete recovery records.
- Current rendered R@5 is `0.9514`, so suppressing any labelled relevant secondary can violate the `0.95` floor. Consolidation must be conservative and evidence-driven.
- Exact duplicate content is already removed after global ranking. Phase 2B must handle structured near-duplicates and conflicts without replacing retrieval or ranking.
- Root `conftest.py` initializes an isolated HOME before MiniCode imports and guards the entire real `.mini-code` root at pytest session boundaries.

## Implementation And Calibration

- Added one side-effect-free `CandidateConsolidator` call after exact post-gate dedupe and before controller/budget rendering.
- Chain evidence is limited to explicit relations, shared concrete files, or at least two shared informative query terms. Domain, scope, category, recency, and one generic term cannot establish a chain.
- Equal-authority direct conflicts suppress both candidates with `unresolved_conflict`; only retained/rendered IDs can reach counters and feedback.
- Initial broad domain-gap suppression removed two relevant budget secondaries and reduced frozen Recall@5 from `0.9514` to `0.9474`; the rule was removed and Recall@5 returned to `0.9514`.
- Initial authority extraction treated `test/verify` instructions and the noun phrase `canonical dictionaries` as authority. Red tests narrowed authority to explicit structured signals or unambiguous completed/prefix declarations; frozen primary hit returned to `0.9859`.
- Phase 2B holdout: 33 cases, 11 categories, 69 globally unique synthetic memory IDs. JSON Schema validation passes.

## Evaluation Before Final Full Suite

- Frozen 80 cases: P@1 `0.8750`, R@5 `0.9514`, primary hit `0.9859`, rendered precision `0.9812`, must-exclude `0.0375`, negative false injection `0`, duplicate render `0`, and all ID/budget disagreement rates `0`.
- Remaining must-exclude cases: `mr-domain-06-noise`, `mr-domain-07-noise`, and `mr-recovery-06-unverified`.
- Holdout: candidate recall, post-gate recall, post-consolidation precision/recall, rendered precision, complementary retention, and reason accuracy are all `1.0`; incorrect suppression, must-exclude, duplicate render, and unresolved unsafe render are all `0`.
- Holdout rendered recall is `0.9231` because three labelled complementary memories are intentionally omitted by `max_memories=1`; post-consolidation recall remains `1.0` and incorrect suppression remains `0`.
- Official performance run: 100 candidates P95 `2.8544 ms`; 500 P95 `12.1528 ms`; 1000 P95 `8.6128 ms`; full canonical P95 `1.9111 ms` versus Phase 2A `2.1233 ms`.
- Consolidation cap is 256 before pairwise work; reported bound is `O(N log N + P + B^2)` with `B<=256`.
- Evaluator network count is zero. Phase 1 (15 files) and Phase 2A (8 files) frozen hashes match. The full 864-file formal tree remained equal during the official evaluation.

## Final Verification

- Final related agent/context/memory/retrieval/TUI matrix: `467 passed`, with three pre-existing unregistered benchmark-mark warnings.
- Two final literal `python -m pytest -q` runs: `1328 passed, 2 skipped, 3 warnings` in `20.13s` and `19.83s`.
- The skipped tests are existing optional/live-provider cases; the three warnings are existing `pytest.mark.benchmark` registration warnings.
- `python -m compileall minicode tests scripts -q` passed. Ruff passed all seven added/modified Python files.
- Holdout and machine artifact both parse and validate against their JSON Schemas.
- `mypy` and `pyright` are not installed and no project configuration exists, so type checking was not run or claimed.

---

# Notes: MiniCode Dashboard Batch 2B-1

## Source audit

- `SessionMetadata` contains bounded first/last message previews, timestamps, count, and workspace; full `SessionData` also contains transcript, permissions, Skills, and MCP data that must never be projected wholesale.
- `load_session()` reads the base file and all delta files without Dashboard byte/path budgets. The Dashboard needs its own no-write bounded parser while preserving the established base/delta schema.
- The Session index loader converts corrupt JSON into an empty list, so the Dashboard must parse the configured index itself to distinguish a real empty list from source failure and to isolate malformed metadata records.
- `MemoryEntry` provides real tier, lifecycle, safety, approval, retrieval, injection, and usefulness fields. `MemoryManager` construction can migrate, recover, back up, and save, so page reads must reuse the Batch 2A no-side-effect parser.
- `MemoryPipeline.read/inject/write/maintain` and `WorkingMemoryTracker` are runtime/mutable interfaces. The Dashboard will not invoke them; Retrieval, Injection, and the runtime WorkingMemoryTracker snapshot remain unavailable.
- Page interfaces will use opaque bounded cursors, fixed workspace ownership, 2 MiB source budgets, per-text and total-content budgets, generic diagnostics, and final recursive redaction.

## Final evidence

- Public page seams are `sessions(limit, cursor)`, `session_detail(session_id, limit, cursor)`, and `memory(scope, tier, category, limit, cursor)` behind three versioned GET routes; the HTTP adapter does not read source files.
- Sessions are current-workspace only, stable newest-first, cursor-paged, role-filtered to user/assistant, and bounded/redacted. Memory scopes fail independently and expose real scope/tier/category aggregates plus safe item metadata/content.
- Root-level and file-level symlink escapes, oversized sources, invalid IDs/filters/limits/cursors (including boolean timestamps), corrupt entries/deltas, secret leakage, side effects, and response budgets all have isolated regressions.
- Related matrix: `198 passed`. Final full suite: `1498 passed, 2 skipped, 3 warnings` in `51.60s`; warnings are the existing unregistered benchmark marks.
- Ruff, `py_compile`, full `compileall`, production `node --check`, and the 9-test isolated wheel build/install/smoke suite passed. `pyright` and `mypy` are unavailable.
- Browser fixtures verified Sessions 21-item paging/detail, Memory 23-item scope/tier counts and paging, hidden unsafe content, truthful unavailable runtime sections, all deferred routes, no overflow, failure/retry recovery, and zero console warning/error logs.
- No Git metadata exists. No repository was initialized, no commit was attempted, and unrelated workspace changes were preserved.

---

# Notes: MiniCode Dashboard Batch 2B-2

## Scope guard

- Realize only Skills, Connections, and System through new read-only schema-version-1 page projections.
- Keep Runs, Ops telemetry, Memory runtime events, live MCP telemetry, writes, external processes, realtime transports, Agent Loop, MemoryPipeline, TUI, Session persistence, and the mock right Dock out of scope.
- Tests and browser fixtures must use isolated HOME/workspace data and must not start MCP servers or read the user's real data.

## Source audit

- `SkillSummary` has the required safe metadata fields, but `discover_skills()` reads complete `SKILL.md`/`SKILL_DIR.md` files without Dashboard byte/root limits and silently skips several failures. The page adapter must preserve its four-root precedence and frontmatter semantics through bounded, root-anchored reads; it must never call `load_skill()` or return `path`/`content`.
- Skill root precedence is project `.mini-code/skills`, user `.mini-code/skills`, compat project `.claude/skills`, compat user `.claude/skills`. For an injected non-default `skill_loader`, the read model can project supplied `SkillSummary` values directly; production filesystem discovery must be safe and independent per file/root.
- `load_effective_settings()` merges Claude settings, global MCP, project MCP, and MiniCode settings, but page scope is explicitly only user `mcp.json` plus project `.mcp.json`. Existing MCP server merging is global then project override, with per-server top-level merge and nested env merge.
- `StdioMcpClient`, `create_mcp_backed_tools()`, `ToolRegistry`, and capability registration are runtime adapters that may create clients/processes or expose mutable in-process state. Connections will not construct or consult them; `configured` is never `live`, and live count remains null/unavailable.
- `AppState` is an in-process mutable singleton with no stable Gateway-wide persisted snapshot. System cannot use it for Runs/usage/health truth. Safe runtime fields are package version, Python version, platform family, architecture, process mode, workspace identity/status, and statuses derived from the same side-effect-free source adapters.
- `importlib.metadata.version("minicode-py")` is the wheel/source metadata seam. Source execution needs a bounded static `0.1.0` fallback because `minicode.__init__` exposes no version.

## Final evidence

- Public seams: `skills(source, directory, limit, cursor)`, `connections()`, and `system()` behind three UTF-8/no-store versioned GET routes.
- Skills: 21-item controlled browser fixture paged 20+1; source counts 15/3/2/1 and engineering directory filter 15 were correct. Bodies, paths, credential descriptions, and metadata secrets were absent.
- Connections: user then project effective merge produced 3 configuration-only records; Gateway remained live and MCP runtime/live count unavailable. No MCP client, ToolRegistry, subprocess, command, args, env, latency, or capability counts were used.
- System: package version, Python, platform, architecture, Gateway process mode, hashed workspace identity, and semantic source states passed the whitelist tests; no HOME/executable/sys.path/argv/provider/config/content fields appeared.
- Review RED/GREEN: malformed nested MCP env originally escaped source isolation during project override; it now becomes a generic diagnostic and legal partial projection.
- New catalog tests: 24 passed. Related matrix: 244 passed. Full suite: 1531 passed, 2 skipped, 3 existing warnings in 55.26s.
- Ruff, `py_compile`, full `compileall`, `node --check`, wheel build/install/all-read-route smoke, static secret/path scan, and browser success/error/retry/console/layout checks passed.
- No Git metadata exists; no initialization, commit, or adjacent-repository operation occurred.

---

# Notes: MiniCode Dashboard Batch 1

## Initial Constraints
- Production Dashboard must remain mock/read-only in Batch 1.
- Gateway must retain `/health`, `/run`, host, and port compatibility.
- Static files must be import/package-relative, traversal-safe, correctly typed, and `no-store`.
- No new runtime/framework/build dependency is allowed.
- The approved prototype directory must remain intact.

## Repository State
- The requested workspace contains no `.git` directory and is not a Git worktree.
- Existing cumulative planning files belong to earlier tasks and are being appended to, not overwritten.

## Source Audit
- `minicode/gateway.py` currently maps both `/` and `/health` to `{"ok": true, "service": "minicode-gateway"}` and keeps `/run` as a lazy import of `minicode.headless.run_headless`.
- `tests/test_packaging.py` has seven passing baseline tests, including exact `/health` payload, `/run` success, and `SystemExit` JSON conversion.
- `pyproject.toml` uses setuptools package discovery from the repository root and has no package-data declaration.
- Before this change, setuptools discovers `minicode` but not `minicode.web` because `minicode/web/__init__.py` does not exist.
- Approved prototype assets are `index.html`, `styles.css`, and `app.js`; navigation is bookmarkable hash routing across eight main pages and five Memory subpages.
- The prototype currently links relative `styles.css`/`app.js`, embeds `/Users/zhourunbo/...` in mock data, and labels the header `live`; production assets must instead use `/assets/...`, a generic mock workspace, and persistent mock/read-only labeling.
- Baseline verification: `tests/test_packaging.py` is 7/7 passing and prototype `app.js` passes `node --check`.

## Implemented Web Seam
- `minicode.web.MiniCodeWebHandler` is the single GET interface used by the Gateway; it owns `/`, `/assets/...`, `/health`, `/api/v1/health`, structured API 404s, MIME selection, `no-store`, and traversal rejection.
- `MiniCodeGatewayHandler` subclasses that handler and retains only `/run`, its existing response shapes, a 1 MiB request limit, and server startup.
- Production assets live under `minicode/web/static/`; prototype source files remain under `minicode/web/dashboard_prototype/` and were not edited.
- `pyproject.toml` declares HTML/CSS/JS package data for `minicode.web`.
- The production UI keeps the confirmed hash routes and Waku three-column shell, but persistent labels now say `mock / read-only` and `data not connected`; the production mock workspace contains no `/Users/...` path.

## Targeted Verification
- Dashboard + packaging suites: 23 passed.
- Installed-wheel test builds from a temporary source copy, verifies all three assets in the wheel, installs into an isolated target, starts the installed Gateway, and loads `/` plus `/assets/app.js`.

## Final Verification
- Final full suite: 1420 passed, 2 skipped, 3 existing `pytest.mark.benchmark` warnings in 42.97s.
- `py_compile` for all changed Python files, `compileall` for `minicode` + `tests`, production `node --check`, and Ruff all passed.
- `pyright` and `mypy` are unavailable in this workspace and were not claimed.
- Final live HTTP smoke passed for `/`, CSS, JS, `/health`, `/api/v1/health`, structured API 404, and encoded traversal rejection.
- Browser checked 8 main routes and all 5 Memory subroutes. Default three-column widths were 208/682/380 px with no horizontal overflow.
- Overview and Memory Retrieval were visually inspected; the final browser reload showed four retrieval funnel stages and no console warnings/errors.
- Self-review has no remaining blocking or important findings.

---

# Notes: MiniCode Dashboard Batch 2A

## Scope Guard
- Implement only `DashboardReadModel`, `/api/v1/snapshot`, real Overview projection, redaction, source freshness/failures, tests, packaging, and documentation.
- RunJournal, real Runs/Ops, SSE, writes, interactive sessions, MCP startup/editing, and Agent/MemoryPipeline/TUI changes are explicitly deferred.

## Source Audit
- `session.list_sessions()` returns `SessionMetadata`, sorted newest first, but `_load_session_index()` catches malformed JSON/type/key errors and returns `{}`. A read-only preflight is required to distinguish a corrupt index from zero sessions.
- `MemoryManager.__init__()` loads all scopes, safety-migrates entries, auto-recovers integrity, creates backups, and may call `_save_scope()`. It is not a safe Dashboard read adapter. `MemoryEntry.from_dict`, `MemoryFile`, `MemoryScope`, and `MemoryTier` can support a side-effect-free projection.
- Memory scope directories are `data_dir/memory`, `workspace/.mini-code-memory`, and `workspace/.mini-code-memory-local`; JSON contains `entries`, each with explicit `scope`, `category`, and `tier`.
- `discover_skills(workspace)` returns summary objects without content, but discovery internally reads SKILL.md. The snapshot will expose only total/source counts—not names, paths, descriptions, examples, or content.
- MCP configuration can be safely summarized from `mcp.json` and workspace `.mcp.json` by counting unique `mcpServers` keys. Command, args, env, and values must never leave the read model.
- `AppState` contains runtime counters, but no stable cross-process Gateway store exists and RunJournal is deferred; no AppState Run/usage values will be presented as authoritative.
- The legacy frontend uses one global mock `DATA`; only `VIEWS.overview` and page metadata will switch to a separate snapshot store. All other pages keep the existing hash-route mock functions for Batch 2A.

## Read Model Contract Implemented
- One public method: `DashboardReadModel.snapshot()`.
- Real projections: workspace identity/status, workspace-filtered session count/latest timestamp, Memory totals/scopes/tiers/categories, Skill total/source counts, Gateway live state, MCP configured count.
- Deferred truth: Runs and usage/cost/tokens/tool/error fields are `status=unavailable` with `null` values.
- Every Memory scope is isolated. A corrupt scope yields `totalCount=null`, a bounded `knownCount`, per-scope error, and no `.bak` or file rewrite.
- Final recursive redaction covers credential assignments, bearer values, `sk-*` tokens, sensitive dictionary keys, depth, string length, and collection bounds.
- Current dedicated read-model tests: 11 passed, all under temporary paths or injected loaders.

## Final Verification
- Dedicated read-model tests grew to 13; combined Dashboard/read-model/packaging suite is 40 passed.
- Installed-wheel smoke uses an isolated HOME and workspace, loads `/`, `/assets/app.js`, and `/api/v1/snapshot` from the installed wheel.
- Final full suite: 1437 passed, 2 skipped, 3 existing unregistered benchmark-marker warnings in 45.42s.
- `py_compile`, full `compileall`, Ruff, production `node --check`, seeded-secret scan, and frontend debug-statement scan passed. `pyright` and `mypy` remain unavailable.
- Controlled HTTP fixture returned Session=1, Memory=3, Skill=1, MCP configured=1; Run and usage fields were null/unavailable; fixture transcript/MCP secrets were absent.
- Browser: Overview loaded the controlled snapshot; all seven other main routes and five Memory routes rendered; three-column width stayed 208/682/380 with no overflow.
- Browser error path: first snapshot returned 500, Overview and nav showed snapshot error, one Retry button restored the page on the next request, and both success/recovery consoles had zero warning/error entries.
- Code review verdict: approved after adding bounded source reads/root constraints and explicit workspace-error diagnostics; no remaining blocking or important findings.

# Notes: Memory Retrieval Phase 3B

## Start Integrity Snapshot

- Production `minicode/` source/resource snapshot: 142 files, `/tmp/minicode-phase3b-production-start.json`, manifest SHA-256 `b8e33844a635e1abe611234667639ce8983baccb6210124e2cd038d2f8379abd`.
- Phase 1: 15 files, manifest SHA-256 `a1a6ad1019133f2581365b166a5e9ea4db4d9635b93c5228fd9d14c42e1d1e94`.
- Phase 2A: 8 files, manifest SHA-256 `c8547d2142a95b9ef7c405d4abf0ed259a36d4f0b659fe2f4822f498c0060054`.
- Phase 2B: 12 files, manifest SHA-256 `e75adc0188a068773285ad2c69f094ee7f0fd077250a54969e88e8443f39cef3`.
- Phase 3A: 27 files, manifest SHA-256 `225472080f652e049dcfb5a162b7967f4c9d8a962efce256e667999c3638f7c1`.
- Formal `~/.mini-code`: 864 files, `/tmp/minicode-phase3b-formal-tree-start.json`, snapshot SHA-256 `d66c3c19a1c6c3ad9f1574f7137e57dd92d14ea6c166c119430e83d4918c2399`.
- Snapshots were made with raw file reads before any Phase 3B production-module import or experiment.

## Local Model Availability

- The default Python environment has no numpy, torch, transformers, sentence-transformers, tokenizers, ONNX Runtime, or cached NLP embedding model.
- The bundled Codex Python runtime has numpy 2.3.5 but no embedding inference runtime.
- Existing Hugging Face cache contains only Paddle OCR/document-orientation models and is not usable for retrieval quality.
- A fake adapter cannot substitute for a real model result. Any model acquisition must be explicit, local-only at inference, and stored outside the project and formal MiniCode directories.

## Independent Holdout Freeze

- The Phase 3B holdout was authored and validated before any embedding or hybrid result: 60 cases, 36 positives, 24 hard negatives, and 64 unrelated background memories.
- Coverage includes 11 positive semantic categories and 7 hard-negative categories; query languages are 37 English and 23 Chinese, with 24 zero-overlap, 21 low-overlap, 11 medium-overlap, and 4 high-overlap cases.
- Schema, exact counts, category distribution, unique/disjoint IDs, target eligibility, negative exclusions, overlap annotations, bounded metadata/provenance, and secret/path patterns passed.
- Frozen file hashes are recorded in `tests/fixtures/memory_retrieval_phase3b_holdout/frozen.sha256`; no hybrid run occurred before this freeze.

## Phase 3B Final Results

- Local model: `Xenova/multilingual-e5-small` revision `761b726dd34fb83930e26aab4e9ac3899aa1fa78`, MIT, CPU ONNX, 384 dimensions, fingerprint `cb55c8134bf02eeff414a6fcb53a88e5160e45cf74e7a7cf1befbc5a9fa2b230`.
- Frozen config payload SHA-256 `3440fd98e1fa37d861d4baeabfe723015b8a06d5cbf48bfa1971ec48a8a19c5a`; 2,880 analysis-only attempts, selected structured RRF and a 0.89 dense threshold with 0.03 margin.
- Sealed final: candidate Recall@20 22/24 (91.67%), post-Gate/rendered 1/24 (4.17%), precision 1/28 (3.57%), hard-negative rendered 7/12 (58.33%).
- Independent holdout final: candidate Recall@20 33/36 (91.67%), post-Gate/rendered 7/36 (19.44%), precision 7/40 (17.50%), hard-negative rendered 13/24 (54.17%).
- Dense-only candidate Recall@20 is 24/24 sealed and 33/36 holdout, confirming candidate-generation value, but hard-negative rendering remains 75% and 79.17% without a selective Gate.
- Final decision: fail. Production connection, production interface design based on this configuration, and real-user shadow are all prohibited.
- Performance warm total P95: 4.04 ms at 1,000 entries and 39.36 ms at 10,000 entries. The performance target passes, but quality gates dominate the decision.
- Dedicated tests: 47 passed. Related retrieval/memory matrix: 383 passed. Final full suite twice: 1404 passed, 2 skipped, 3 existing warnings in 32.04s and 32.04s.
- All 142 snapshotted production files, Phase 1/2A/2B/3A sets, and 864 formal files are byte/stat identical. Six dashboard prototype files appeared concurrently outside the production start set and were left untouched.
- Different `PYTHONHASHSEED` values `1`, `7`, and `123` produce identical consolidation output.
- Two evaluator deterministic cores are byte-identical with SHA-256 `3edb666e3492acba6bcb5129b2a4bbeb6040e61002918b2bcae6ca3128d47225`.
- Final official performance: consolidator 100-candidate P95 `2.7782 ms`; full canonical P95 `1.8567 ms`; evaluator network calls `0`.
- Phase 1 and Phase 2A frozen manifests pass after all tests. The real 864-file `~/.mini-code` tree remains exactly equal in file set, SHA-256, size, and mtime_ns.
- No formal contamination cleanup, approval, deletion, migration, Markdown regeneration, or session mutation was performed.

# Notes: Memory Retrieval Phase 3A

## Start Integrity Snapshot

- Complete formal `~/.mini-code` tree: 864 files; snapshot `/tmp/minicode-phase3a-formal-tree-start.json`; snapshot SHA-256 `d66c3c19a1c6c3ad9f1574f7137e57dd92d14ea6c166c119430e83d4918c2399`.
- Formal USER memory JSON SHA-256: `5236e66fbfffd6b61bf7f0060a7d1786f17efa389005dd84b6bb139c66305d76`.
- Formal MEMORY.md SHA-256: `a2a68e8e6c9b4c086126a24dd66839d4be87c74eeac0b798700d4088791a1a5b`.
- Formal approval audit SHA-256: `694c96793f28f20dde0584fe860b8135d0ea2c02d846f621ee4cbee427e21a20`.
- Formal sessions index SHA-256: `51de6579dd45fae04285791899863251224298e14c535bc4d3af60bf222eabe6`.
- Production retrieval snapshot: 10 files, `/tmp/minicode-phase3a-production-start.json`, manifest SHA-256 `fb86dd4ce4333f16bfd0c05a92c11428747e532d7b4c545359795ef14b2e428c`.
- Phase 1 snapshot: 15 files, manifest SHA-256 `a1a6ad1019133f2581365b166a5e9ea4db4d9635b93c5228fd9d14c42e1d1e94`.
- Phase 2A snapshot: 8 files, manifest SHA-256 `c8547d2142a95b9ef7c405d4abf0ed259a36d4f0b659fe2f4822f498c0060054`.
- Phase 2B snapshot: 12 files, manifest SHA-256 `e75adc0188a068773285ad2c69f094ee7f0fd077250a54969e88e8443f39cef3`.
- Snapshots were created with raw byte reads before importing any MiniCode production module. No formal data was read into fixtures or modified.

## Frozen Dataset And Baseline

- Dataset: 108 wholly synthetic cases: 72 positives and 36 hard negatives; analysis/sealed split is 72/36.
- Positive categories: 12 with 6 cases each; negative categories: 12 with 3 cases each.
- Dataset freeze manifest covers 18 files and has SHA-256 `59638f40dc76df881c63804275eda5cf137679b77b72916694635b5c51ac9f8b`.
- The freeze was generated after Schema/ID/label/safety/resource/overlap validation and before the first retrieval baseline. No post-baseline fixture or label changes were made.
- Three arms are Manager global search with `record_usage=False`, wide Canonical diagnostic retrieval, and default production `MemoryPipeline.inject` plus rendered-only success feedback in a temporary HOME.
- Overall Canonical diagnostic Recall@1/3/5/10/20: `0.2222 / 0.2778 / 0.2917 / 0.3611 / 0.4722`; sealed Recall@20 is `0.2083`.
- First-loss attribution: 38 candidate-top20 misses, 23 Gate drops, 0 Consolidator drops, 0 controller-disabled drops, 0 budget-only drops, and 11 positive cases with no earlier loss.
- Strict confirmed semantic gaps: 37 overall, 19 analysis and 18 sealed, spanning 11 categories and `en->en`, `en->zh`, `zh->en`, and `zh->zh`.
- Hard negatives: 27/36 appear in diagnostic top20, 20/36 pass Gate and render; forbidden lifecycle/safety candidate leakage remains zero. This is evidence that indiscriminate lexical widening creates substantial noise.
- Phase 3B sealed gate passes all seven criteria. The decision is restricted to an offline BM25 + embedding prototype/A-B evaluator; production enablement remains forbidden.
- Diagnostic arms changed zero counters and zero files. Rendered/injection/feedback and selected/retrieval IDs have zero disagreements; remote call count is zero.
- `PYTHONHASHSEED=1` and `777` produced identical full-case fingerprints: `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
- Dedicated Phase 3A tests: 29 passed. Related MemoryManager/Pipeline/Injector/Phase 1/2A/2B matrix: 324 passed.

## Final Verification

- Two literal `python -m pytest -q` runs: `1357 passed, 2 skipped, 3 warnings` in `31.72s` and `31.67s`.
- The three warnings are the existing unregistered `pytest.mark.benchmark` warnings. The two skips are existing optional/live-provider cases.
- `python -m compileall minicode tests scripts -q` passed. Ruff passed all four added Python files.
- Dataset Schema and all 108 cases validate; artifact and all JSON fixtures parse; artifact and Markdown secret scans pass.
- Final 100/500/1000 Canonical P95: `55.2212 / 335.3346 / 366.8600 ms`; Manager P95: `1.0250 / 6.8111 / 13.0391 ms`; CandidateConsolidator stays capped at 256.
- Full evaluation peak traced memory is `10,253,361` bytes. Scale peaks are `2,726,929 / 13,331,929 / 15,506,560` bytes.
- Dataset freeze, production retrieval 10-file set, Phase 1 15-file set, Phase 2A 8-file set, and Phase 2B 12-file set are byte/stat-identical to start.
- The real 864-file `~/.mini-code` tree remains identical to the stage-start snapshot in file set, SHA-256, size, and mtime_ns.
- `mypy` and `pyright` are not installed and no project configuration exists, so type checking was not run or claimed.
# MiniCode Dashboard Batch 3A Notes

## Scope audit

- Fully read the rollout plan, all Batch 1/2 implementation notes, current Web/Gateway/read model/static assets, Agent Loop, Headless, CLI/TUI call sites, config/types, and all requested Dashboard/packaging tests.
- Existing stable workspace identity is `ws_` plus the first 16 hex characters of SHA-256 over the resolved workspace path.
- Current Runs renderer is the only main read page still backed by `DATA.runs` and `DATA.runSteps`; Overview correctly keeps Runs/usage unavailable, and the Dock has an independent mock store.
- `run_agent_turn()` currently has presentation callbacks but no structured event sink. Batch 3B can add one optional sink without changing those callbacks; Batch 3A will not edit execution code.
- Existing HTTP query parsing already rejects unknown, repeated, and blank fields and returns structured `DashboardReadError` envelopes.

## RunJournal design decisions

- New deep module: `minicode/run_journal.py` with versioned `RunRecord`, `RunEvent`, `RunPage`, `EventPage`, retention result, and domain errors.
- Storage: `<data_dir>/dashboard/workspaces/<stable-id>/runs/<run-id>/{metadata.json,events.ndjson,.writer.lock}` plus a disposable `runs/index.json` cache.
- One writer token and owner PID per Run; separate Run files prevent cross-Run interleaving. A terminal transition releases ownership.
- Lifecycle transitions append their event before atomically replacing metadata. Readers scan canonical Run directories and reconcile effective status, event count, and sequence from valid events without writing.
- Middle corrupt lines are skipped with bounded diagnostics; an incomplete final line is ignored. Later valid strictly increasing sequences remain visible.
- Read methods never create directories, refresh indexes, run retention, or change mtimes.
- Retention is explicit and only removes validated, non-symlink terminal Run directories.

## Final implementation and verification

- `RunJournal` now exposes versioned Run/Event records, strict lifecycle transitions, bounded payload/metadata validation, redaction-before-write, per-Run ownership, cursor pagination, workspace isolation, recovery diagnostics, and explicit retention. Canonical list/detail reads scan per-Run directories; `index.json` is only a disposable best-effort cache.
- Dashboard list/detail projections expose only safe fixed fields. They never return event payloads, workspace paths, writer data, metadata internals, or fabricated usage/cost/tool/error metrics.
- `GET /api/v1/runs` and `GET /api/v1/runs/{run_id}` use strict parameter whitelists and generic secret-free failures. No Runs POST, SSE, cancellation, deletion, retry, or execution instrumentation was added.
- The Runs page now has independent request stores, stale-response guards, status/source filters, list and event pagination, local retry/error handling, and truthful coverage copy. Ops and runtime metrics remain unavailable; the separate right-side Dock remains mock/read-only.
- Dedicated RunJournal + Runs read-model tests: 29 passed. Related Dashboard/read-model/packaging matrix: 173 passed. Final full suite: 1570 passed, 2 skipped, with the same 3 existing unregistered benchmark-marker warnings in 60.67s.
- Ruff passed all touched Python files; `py_compile`, full `compileall`, production `node --check`, dependency inspection, installed-wheel tests, and local HTTP smoke passed. Runtime dependency additions are `[]`; pyright/mypy are unavailable and are not claimed.
- Whole-repository `ruff check .` still reports 681 pre-existing findings outside Batch 3A, chiefly benchmarks, legacy modules, and the `ts-src` mirror. No out-of-scope mass cleanup was performed.
- Browser acceptance covered empty, populated, corrupt-record, and fail-once fixtures; list/detail paging, filters, 58-event detail, error/retry recovery, all main routes, all Memory routes, no horizontal overflow, no seeded-secret exposure, and zero console warning/error entries.
- No Git commit was created because the workspace has no Git metadata. No repository was initialized.

## Batch 3B seam

- The only intended next connection is an optional, best-effort structured event sink at execution composition boundaries, backed by `RunJournal.create_run()`, `append_event()`, and `transition()`.
- Batch 3B may map TUI, Headless, and Gateway execution into the established event vocabulary, but must not make Journal failure fatal to an Agent run and must keep presentation callbacks independent.
- Real usage/cost/tokens, tool/model events, Memory retrieval/rendering, Skill routing, MCP runtime freshness, Ops aggregation, SSE, and write controls remain explicitly deferred.

# MiniCode Dashboard Batch 3B-1 Notes

## Actual execution call graph

- Direct production `run_agent_turn()` calls are `headless.run_headless()`, the non-TTY loop in `main.main()`, the event-driven TTY background worker in `tui.input_handler._handle_input()`, and the internal `tools/task.py` sub-agent tool. Benchmarks and tests are not product task composition paths.
- Gateway `POST /run` validates Content-Length, body size, JSON, and a non-empty prompt, then calls `run_headless(prompt)`. It does not call Agent Loop directly. The unique Gateway strategy is therefore `run_headless(prompt, run_source="gateway")`; Headless owns lifecycle creation and Gateway must not create another Run.
- Direct Headless validates/obtains a non-empty prompt, resolves `Path.cwd()`, then loads runtime config, constructs tools/permissions/Memory/model/routing/system messages, and calls Agent Loop. `tools.dispose()` is in an existing `finally`. The lifecycle will start after input/workspace validation and before runtime initialization so initialization failures are observable.
- Classic non-TTY CLI in `main.main()` filters `/exit`, transcript-save, Memory commands, local commands, and direct tool shortcuts before the Agent branch. The unique lifecycle seam is only around that branch's `run_agent_turn()` call. This path has no `SessionData`; its honest `sessionId` is null.
- Interactive TTY loads or creates a real `SessionData` in `run_tty_app()`, stores it at `ScreenState.session`, and passes Agent inputs through `_handle_event()` to `_handle_input()`. Memory commands, `/tools`, local commands, direct shortcuts, unknown slash commands, empty input, and rerenders exit before the Agent branch. The unique seam is inside `_run_agent_background()` around its one Agent Loop call, using `state.session.session_id` when present.
- The TTY background worker catches ordinary `Exception` into its existing local error state, always calls `permissions.end_turn()`, restores idle/tool/status fields, marks the shared result done, and rerenders. `SystemExit` and `KeyboardInterrupt` are not converted there; Python still runs the existing `finally` before they terminate the background thread.
- `tools/task.py` is an internal tool-created sub-agent loop within an already-running top-level user task. Batch 3B-1 deliberately does not create a second top-level Run for it.
- Agent Loop internally converts several model/network/tool failures to normal assistant fallback returns. Batch 3B-1 will therefore mark any normal top-level return completed; it will not inspect assistant text or modify Agent Loop to infer task quality.

## Lifecycle seam decision

- Add `minicode/run_lifecycle.py` as a deep module with one caller interface, `observe_run(...)`, accepting workspace/source/title/session plus optional enabled/factory test seams.
- Context entry creates the queued Run and transitions it to running immediately before the enclosed task initialization/execution. Normal exit transitions completed; ordinary `Exception` transitions failed with fixed `execution_failed`; `KeyboardInterrupt` or `SystemExit` transitions interrupted with fixed `execution_interrupted`, then re-raises the original object.
- Every create/transition/logging failure is locally isolated. A create or running-transition failure turns the observer into a no-op for later phases; a terminal failure never replaces a success or business exception.
- Title preparation will only normalize and bound the task summary; persisted redaction remains owned by RunJournal. No prompt or exception body is emitted by lifecycle logging.

## Batch 3B-1 final evidence

- `observe_run(...)` is the sole lifecycle adapter. Direct Headless uses `headless`, Gateway overrides that same call to `gateway`, TTY uses `tui` plus `state.session.session_id`, and classic non-TTY uses `tui` with null Session ID. No execution path calls `append_event()`.
- Healthy, disabled, and every injected failing-Journal path preserve response/messages, exception identity, permissions, disposal, Session/context save, and TTY completion state in automated equivalence tests.
- Browser HTTP fixture produced exactly three completed Runs: one real direct Headless Run, one real Gateway `/run` Run without a duplicate Headless record, and one legal TUI display fixture backed by separate real TTY integration proof. Each detail contained only queued/started/completed events; seeded credential shapes were redacted.
- Runs/Overview coverage is `{journal,tui,headless,gateway}=live`, `historical=partial`, `scope=lifecycle-only`. Usage/cost/tokens/tools/errors, model/tool/assistant, Memory/Skill runtime, MCP runtime, Ops, SSE, and writes remain unavailable.
- Eight main routes and five Memory subroutes passed browser navigation. Skill Routing remained unavailable, Connections stayed configuration-only, the Dock stayed mock/read-only, fail-once Runs retry recovered, and both browser tabs reported zero console warning/error entries.
- A 1280 px visual pass found and then fixed vertical row-text compression by stacking Runs/Sessions master-detail views at `max-width: 1400px`. Final computed Run row width was 602 px with no document overflow. Before/after evidence is in `/tmp/minicode-dashboard-batch3b1-runs-before.png` and `/tmp/minicode-dashboard-batch3b1-runs-after.png`.
- Dedicated lifecycle/entrypoint: 34 passed. Related matrix: 265 passed, 2 skipped. Full suite: 1605 passed, 2 skipped, 2 failures from the pre-existing semantic-gap production hash freeze including the intentionally changed Headless/main/TTY entrypoints. Phase 1/2A/2B and the other seven production hashes remain exact; no baseline was rewritten.
- Touched Ruff, `py_compile`, full `compileall`, `node --check`, nine-test wheel/isolation/install smoke, local HTTP smoke, static dependency/boundary scans, and final code review passed. Runtime dependency additions remain empty; pyright/mypy are unavailable.

# MiniCode Dashboard Batch 3B-1.1 Notes

## Failure reproduction and freeze classification

- Exact feedback loop: `python3 -m pytest -q tests/test_memory_retrieval_semantic_gap_evaluator.py` reproducibly returns `25 passed, 2 failed` in about 12 seconds. The failures are `test_network_formal_state_and_frozen_assets_are_unchanged` and `test_prior_frozen_assets_and_production_files_match_recorded_hashes`; both fail because the active evaluator still uses the historical production v1 source hashes.
- Current `hash_paths()` evidence: the 10-file v1 production set has exactly three mismatches: `minicode/headless.py`, `minicode/main.py`, and `minicode/tui/input_handler.py`. The other seven v1 production files match byte-for-byte.
- Phase 1: 15 files, matches; Phase 2A: 8 files, matches; Phase 2B: 12 files, matches.
- Semantic-gap dataset: 18 files, frozen manifest SHA-256 `59638f40dc76df881c63804275eda5cf137679b77b72916694635b5c51ac9f8b`, matches with no mismatches.
- No fourth mismatch exists, so versioned re-certification may proceed. The existing v1 constants remain the only byte-level old-source evidence; no v1 source-body backup exists in this workspace or `/tmp`. `/tmp/minicode-phase3b-production-start.json` is a hash/stat snapshot, not a source backup.
- Existing accepted artifact `artifacts/memory-retrieval-semantic-gap-baseline.json` contains the v1 deterministic per-case fingerprint `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667` for both `PYTHONHASHSEED=1` and `777`. Recomputing from current v2 production behavior returns the same fingerprint.

## Evidence classification for the entrypoint audit

- Direct evidence: the seven unchanged production files include `agent_loop.py`, `memory.py`, `memory_pipeline.py`, `memory_retrieval.py`, `memory_injector.py`, `memory_candidate_consolidation.py`, and `context_compactor.py`; their v1 hashes still match. The current entrypoints call the same `run_agent_turn()` boundary and current lifecycle tests compare enabled, disabled/no-op, healthy, and failing Journal modes for messages, return/exception behavior, permissions, disposal, and TTY Session/context state.
- Direct current-source evidence: all three mismatched entrypoints import/call `observe_run`; Headless supplies source override support, classic CLI supplies `source=tui` and null Session, and TTY supplies `source=tui` plus `state.session.session_id`. The lifecycle adapter only calls RunJournal creation/transitions and never invokes Memory retrieval APIs.
- Inference, explicitly bounded: because no v1 source bodies are retained, a textual v1-to-v2 diff cannot be reconstructed. The conclusion that the three byte changes are lifecycle-only relies jointly on the preserved v1 hashes, Batch 3B-1 implementation/audit record, unchanged downstream hashes, current source inspection, behavior-equivalence tests, and the identical 108-case semantic fingerprint. It does not claim an unavailable line-by-line historical diff.

## Errors encountered

- A documentation read command used an unquoted decorative separator in shell output and zsh interpreted it as a command. The command made no changes; the three documents were reread directly without separators.

## Batch 3B-1.1 final certification

- Historical v1 manifest raw SHA: `b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417`; active v2 manifest raw SHA: `15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab`.
- Active v2 verifier: pinned v1/v2 true, candidate match true, 12/12 protected current files match, changed common files exactly Headless/main/TTY input, added files exactly Run lifecycle/RunJournal, removed files empty.
- Accepted behavior artifact raw SHA: `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`; complete deterministic behavior projection SHA: `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`; per-case fingerprint remains `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
- Certification tests: 39 passed. Lifecycle: 34 passed. RunJournal/Dashboard Runs: 29 passed. Requested Memory matrix: 137 passed. Final full suite: 1619 passed, 2 skipped, 0 failed, with three existing marker warnings in 63.78 seconds.
- Ruff, compile checks, JSON/pinned-manifest validation, deterministic repeat, controlled tamper failure, generic path-free CLI failure, safety scan, and empty runtime dependency list passed.
- Two intermediate full runs hit existing machine-timing gates (one Phase 2A P95 and one Phase 2B report performance gate); both focused suites immediately passed without source, threshold, or test changes. The final unloaded full run is green.

# MiniCode Dashboard Batch 3B-2B Notes

## Scope lock

- Only Model request boundary events are new: `model.started`, `model.completed`, and `model.failed` around actual `_model_next()` invocations.
- Explicitly excluded: usage/cost/cache/duration, prompt/messages/output/thinking/stream/error bodies, Memory/Skill/Context/Recovery events, Ops/SSE/writes, and any Tool/Assistant duplicate wiring.
- `agent_loop.py` may change only at the `run_agent_turn(..., event_sink=None)` interface and the immediate `_model_next()` call boundary.
- RunJournal format/state machine should remain unchanged; v4 must preserve v1/v2/v3 bytes and all historical lineage.

## Evidence pending

- Full `_model_next()` call graph and exact exception/retry behavior.
- Pre-change v3 active verifier and protected-file mismatch set.
- Event Sink tracer RED/GREEN and Model operation behavior matrix.

## Model-call audit

- `_model_next()` is defined once and called once lexically, inside the `run_agent_turn()` while loop after `step += 1`, hook/controller work, and optional `metrics_collector.start_turn(step)`.
- One loop iteration performs at most one actual `_model_next()` call. No Tool, Assistant, recovery, compaction, switching, finalization, or Dashboard path calls it elsewhere.
- Normal `AgentStep` return is a completed Model operation before any downstream interpretation. This includes empty content, thinking `pause_turn`/`max_tokens`, progress content, Tool calls, and final Assistant content.
- Empty response retries (maximum two) append a user nudge and `continue`; recoverable thinking stops (maximum three) append progress/resume messages and `continue`. The next actual call occurs in a new while iteration with an incremented real step.
- Context reactive recovery occurs only in the generic Exception branch when error text indicates prompt overflow. An effective recovery updates messages/context and `continue`s; the failed call remains one failed operation and the retry is a new step/operation.
- ModelSwitcher is tried after Context recovery paths for generic Exception, excluding error text containing `rate`. A successful adapter switch assigns `model` and `continue`s; the next adapter call is a new step/operation.
- `KeyboardInterrupt` is explicitly re-raised. `SystemExit` is not caught by current `Exception` handlers and therefore also propagates through the existing outer `finally`. Batch 3B-2B must emit `interrupted` for both without changing propagation.
- `ConnectionError` and `TimeoutError` are converted to the current normal Assistant fallback, end current metrics when present, set failed outcome, and return messages.
- Other `Exception` values either recover/switch and retry or become the current `Model API error (...)` Assistant fallback. Event payloads must not receive exception type/text even though existing business logs/fallbacks retain them.
- Downstream Tool or Assistant failures cannot retroactively change an already completed Model operation. A max-step fallback after leaving the while loop has no Model operation because no `_model_next()` call occurs.

## Pre-change certification evidence

- Active verifier: v3 matches; 12/12 protected source files match; candidate matches; v1/v2/v3 pins and both historical lineage steps are valid.
- v3 manifest SHA-256: `0722314faf0476e1566657578782a6437d8d218a779ef24bde474351a5b86522`.
- Protected hashes before Batch 3B-2B include Agent Loop `a9980e6d...81d5e`, Run lifecycle `e6cfe820...fc148`, Headless `5d4d4cc8...cd661`, main `23e80ea4...f79ee`, TTY input `386f456e...81189`, and unchanged RunJournal `20f41213...144c1`.

## Model event implementation evidence

- `minicode/run_events.py` is the independent seam: `AgentEventSink.emit(...)`, `emit_event_safely(...)`, and `new_model_operation_id()`; it imports neither Agent Loop, lifecycle, Journal, nor Web.
- The helper passes the original payload object unchanged, treats `None` as no-op, catches only ordinary `Exception`, logs one generic payload-free warning, and deliberately does not swallow `KeyboardInterrupt`/`SystemExit` from a misbehaving test sink.
- RunObservation directly satisfies the Protocol and forwards structured events through the same lifecycle writer with the supplied real step. Disabled/create/start/terminal/append failures remain no-throw/no-op.
- Agent Loop generates `modelop_<32 lowercase hex>` immediately before the sole model call, emits started, and emits exactly one completed or failed with the same ID. The retry matrix proves new IDs and steps for empty responses, Context recovery, and ModelSwitcher.
- Fixed failure kinds are `interrupted`, `network`, `timeout`, and `provider_error`; no exception type/text is placed in event payloads.
- The focused Event Sink, Model event, lifecycle, entrypoint, Agent Loop, and integration matrix is `86 passed, 2 skipped`. Tool/Assistant callbacks and the Gateway one-Run composition remain unchanged.

## Dashboard Model projection evidence

- ReadModel independently whitelists Model events. It validates only `modelop_<32 hex>`, `assistant|tool_calls`, real boolean content presence, bounded non-boolean integer Tool-call count, and fixed failure kinds; it preserves the event envelope's real step.
- Raw payload, Prompt/messages/output/error/provider/usage/cost/duration/model identity are never projected. Invalid IDs/enums/types and unknown fields are dropped; cost/tokens/Tool-call aggregate/errors remain unavailable.
- Coverage is now `lifecycle-model-tool-assistant`, with model/tool/assistant live and usage/Memory/Skills unavailable. “Live” is explicitly code-path instrumentation, not Provider connectivity or streaming.
- The frontend renders Model started/completed/failed, real step, result type, Tool-call count, and fixed failure kind. It never reads `event.payload` or Model prompt/output/usage/duration fields. A failed Model attempt remains one event row and does not control the Run status pill.
- Dashboard/read-model/frontend/installed-wheel expectations pass `112` tests; production JavaScript passes `node --check`.

## v4 certification evidence

- Manifest v4 SHA-256 is `5034b342d68c9a8ef7b450fe2f4bcbda370204f92d3c87f05caf15eec1002e10`; v1 `b543...`, v2 `15df...`, and v3 `0722...` remain byte-identical. The pin reflects the final default-path hardening that avoids operation-ID generation when `event_sink=None`.
- v3→v4 changed files are exactly Agent Loop, Run lifecycle, Headless, main CLI, and TTY input. The only addition is `minicode/run_events.py`; no file is removed.
- v4 protects 13 files. RunJournal, Context compactor, and all Memory retrieval/pipeline/injector/consolidation sources retain their v3 hashes.
- The default verifier reports active v4, all four pins true, all lineage steps exact, candidate match true, and 13/13 current files matching.
- Production baseline certification passes 14 tests; the 108-case semantic-gap certification passes 29 tests with the accepted artifact/projection/per-case fingerprints and side-effect/frozen-state gates unchanged.

## Batch 3B-2B final verification evidence

- Combined lifecycle, Journal, Dashboard, frontend, packaging, Agent Loop, and integration regression: 231 passed, 2 skipped.
- Complete Memory Retrieval matrix: 187 passed. Baseline plus semantic certification: 57 passed; active v4 matches 13/13 files, all four pins, and every lineage edge.
- Complete pytest: 1647 passed, 2 skipped, zero failures in 63.37 seconds; only the three existing unregistered benchmark-marker warnings remain.
- Touched-file Ruff, explicit `py_compile`, full `compileall -q minicode scripts tests`, production `node --check`, and runtime dependency inspection passed. The wheel/isolation/install and installed Gateway/all-read-route/asset/`POST /run` smoke is covered by the passing packaging suite; dependencies remain `[]`.
- Controlled HTTP acceptance through the real Agent Loop created a normal 10-event Tool Run and an 8-event provider-failure/ModelSwitcher-recovery Run. IDs were distinct and correctly paired, steps were 1/2, both final Runs were completed, and seeded input/output/error/Assistant secrets were absent from read APIs.
- Browser acceptance rendered both timelines, all eight main routes, all five Memory subroutes, unavailable usage/Memory/Skill boundaries, and the mock/read-only Dock. Runs error/Retry recovered after server restart; at 1280 px scroll width equaled viewport width; browser development logs were empty.
- Initial browser-fixture cwd mismatch was caused by login-shell behavior inside an isolated HOME. The fixture was discarded and rerun with a non-login shell and a new isolated HOME/workspace; no production source or real user data was affected.

# MiniCode Dashboard Batch 3C-1 Notes

## Production seam audit

- Headless computes `SkillRoutingResult` inside the existing `observe_run()` block. Classic non-TTY and TTY compute it immediately before their existing `observe_run()` blocks because the same result is already needed to rebuild the system prompt. All three can emit one projection after a `RunObservation` exists without rerouting or changing prompt construction. Gateway still delegates to Headless with `source=gateway`, so it must not add a second event or Run.
- `MemoryPipeline.inject()` clears both `_last_injected_ids` and `_last_retrieval_result` before its adaptive cooldown. A cooldown or unavailable pipeline therefore leaves `last_retrieval_result=None`; a real zero-candidate retrieval leaves an actual `MemoryRetrievalResult`. This distinguishes “not executed” from “executed with zero results” without inspecting files or the prompt.
- The final `MemoryRetrievalResult` already carries real candidate/selected/suppressed/rendered tuple lengths, `no_match`, a fixed controller mode, and normalized prompt-token estimate. Missing system message and prompt-injection failure paths replace the final result through `without_rendered(...)`, so the observer can use the final object after the single production `inject()` call.
- No Memory algorithm file needs to change. The minimal protected production delta is expected to remain `agent_loop.py`, `run_events.py`, `headless.py`, `main.py`, and `tui/input_handler.py`; Run lifecycle/Journal and all Memory/Skill algorithms remain unchanged.
- The first TDD slice failed at import as expected, then passed with a deep `run_events.py` projection seam. It validates controlled intent/action/source/mode/reason enums, strict Skill names/directories, finite scores, bounded lists/counts/tokens, explicit truncation, and absence of descriptions, paths, reasons, tools, affinity, Memory IDs/content/query/hash/diagnostics.

## Runtime event wiring evidence

- `emit_skill_routing_safely()` is called exactly once after each existing `RunObservation` becomes available and before `run_agent_turn()`. Headless computes the result inside the Run; classic CLI and TTY retain their earlier prompt-building position and only project the same object inside the Run. Gateway adds no call and therefore still owns one `source=gateway` Run through Headless composition.
- Agent Loop calls `emit_memory_result_safely()` only after the existing `orch.inject_memories(...)` returns and only when an event sink exists. The helper reads the final `last_retrieval_result`; it does not import or call a retriever, pipeline read/inject method, MemoryManager, or prompt parser.
- The real-pipeline tracer records `memory.retrieved` and `memory.rendered` before the first `model.started`, while manager search is called once, the matching content appears once in the system prompt, and retrieval/injection counters remain one. A failing sink produces the exact same returned messages and entry retrieval/injection/success/failure counters as `event_sink=None`.
- Focused event, entrypoint, Agent Loop, and full cybernetic-flow slice: 44 passed.

## Read projection and runtime page evidence

- `DashboardReadModel` recognizes only the three new event types and independently validates fixed schema versions, enums, non-boolean bounded integers, finite Skill scores, safe Skill name/source/directory grammar, and the 20-item display bound. It computes truncation instead of trusting stored input and never returns raw payload.
- Runs coverage is `lifecycle-model-tool-assistant-skill-memory`; Model, Tool, Assistant, Skill, and Memory paths are live, historical coverage is partial, and usage/cost/duration remain unavailable. No lifecycle metric is inferred from event counts.
- `runtimeTraceStore` is independent of Runs, Sessions, persistent Memory, Skills Catalog, and Connections stores. It performs only existing Runs list/detail GETs, uses independent list/detail request IDs plus selected-Run identity guards, never polls, and exposes manual Refresh/Retry.
- Skill Routing, Memory Retrieval, and Memory Injection render loading, loaded, empty, historical/no-event, partial, error, Retry, and manual-refresh states. Historical Runs are not backfilled; missing events are never inferred from Skill directories, Memory files, Prompt, or persistent entry counts.
- Dashboard/read/API/frontend/packaging regression passed 160 tests before the final full run; production JavaScript passes `node --check`.

## v5 certification evidence

- Fixed v5 manifest SHA-256: `70ece17f53ec7963395aadc3be2b104636c2804087928d45c707ee94a5e672ff`. v1 `b543...`, v2 `15df...`, v3 `0722...`, and v4 `5034...` remain byte-identical.
- The exact v4→v5 changed set is Agent Loop, `run_events.py`, Headless, classic main, and TTY input. Added and removed sets are empty. Run lifecycle/Journal, Skill Router, MemoryPipeline/Retrieval/Injector/Manager/consolidation, and Context compactor retain v4 hashes.
- Historical `build_v4_candidate()`/writer now validate and return immutable pinned v4 evidence. Active v5 candidate generation validates every prior pin and lineage edge before accepting the five-file delta.
- Default verification reports active v5, all five pins, exact lineage, candidate equality, and 13/13 current protected hashes. Baseline tests pass 16/16.
- The extended semantic certification passes 29/29 tests over all 108 frozen cases. Accepted artifact `5629...`, behavior projection `b9fa...`, and per-case fingerprint `b73d...` remain unchanged; remote calls, diagnostic side effects, and formal-state changes remain zero.

## Batch 3C-1 final verification evidence

- Full pytest: 1656 passed, 2 skipped, zero failures in 63.44 seconds; only three existing unregistered benchmark-marker warnings remain.
- Touched-file Ruff, explicit `py_compile`, full `compileall -q minicode scripts tests`, production `node --check`, read-only v5 verifier, and nine wheel/isolation/install packaging tests passed. Runtime dependencies remain empty; pyright/mypy are unavailable and are not claimed.
- An isolated HOME/workspace Gateway accepted a real `/run` through Headless and the real Agent Loop. Its ordered event sequence was queued, started, Skill routed, Memory retrieved, Memory rendered, Model started/completed, Assistant completed, Run completed. The real safe facts were Skill 1/1, Memory candidates 1, selected 1, suppressed 0, rendered 1, 48 normalized tokens, standard mode, injected true.
- Seeded Skill description/path and Memory content markers were absent from Runs API and browser DOM. The browser rendered the historical no-event state plus the real Skill Routing/Retrieval/Injection pages, all eight main routes and all five Memory subroutes, localized error/Retry recovery after a server restart, mock/read-only Dock, and unavailable usage/Ops boundaries.
- At 1280 px, document width equaled viewport width; browser warning/error logs were empty. Screenshot: `/tmp/minicode-dashboard-batch3c1-memory-injection.png`. The isolated Gateway listener and browser test tab were closed.

## Encountered issues

- Browser control does not support the requested `networkidle` wait state, so verification used supported `domcontentloaded` plus specific DOM state checks; product code was unaffected.
- Retry recovery was first observed during its legitimate loading state; a bounded follow-up state check confirmed the loaded historical/no-event result with no error before selecting the real Run again.

# MiniCode Dashboard Batch 3C-1.1 Notes

## Pre-fix reproduction and scope lock

- Direct `DashboardReadModel(Path.cwd()).skills()` reproduction returns total 8 with by-source project 7, user 0, compat_project 1, compat_user 0, but `source.status=error` and exactly two `skill_read_failed` diagnostics.
- The two ordinary files exist and were captured without modifying them: project `.DS_Store` size 10244, SHA-256 `560240a5...1b158`, mtime_ns `1783223438916710950`; compat-project `.DS_Store` size 6148, SHA-256 `a03f3791...41b5`, mtime_ns `1782306818727388795`.
- The scanner currently increments `scanned` for every root entry and immediately calls `_validate_source_directory(entry, anchor)`. Therefore every ordinary file both consumes the 10,000-entry discovery budget and becomes a false directory-read diagnostic.
- Existing root-anchored validation resolves strictly, checks containment, requires a directory, bounds source-file size, and protects Skill-file reads. The fix must insert type classification before the counter without changing those validators.
- Safe minimal classification selected for the first tracer: use root-entry metadata (`lstat`) to distinguish ordinary non-directory entries before the counter. Directories continue through `_validate_source_directory`; symlinks also continue through the same validator, retaining the existing escape and non-directory failure behavior. Metadata errors remain localized diagnostics.
- Pre-fix baseline verification is green: v1-v5 manifest SHA values match their fixed pins, active v5 candidate matches, all lineage edges match, and all 13 protected sources match.

## RED-GREEN and real-workspace result

- Public tracer with ordinary files across all four roots failed pre-fix exactly at `source.status == live`; after the metadata gate it passes with four valid Skills, no diagnostics, no ordinary filename/content in JSON, byte/mtime equality, and identical filesystem tree.
- A physical 10,050-file regression (without lowering or monkeypatching the production 10,000-entry limit) still discovers the valid directory sorted after those files, returns one Skill, no cursor, and no `discovery_limited`.
- Exact project plus compat-project `.DS_Store` regression returns two valid Skills with by-source 1/0/1/0, live status, and no diagnostic.
- Combined safety regression keeps valid partial data while malformed UTF-8, unterminated frontmatter, invalid Skill name, and a child directory symlink escaping the root each produce `skill_read_failed`; external/malformed secret text is absent.
- A public metadata-error regression makes one root entry `lstat()` raise and confirms localized path/error-free diagnostic while another valid Skill remains visible.
- A candidate-directory enumeration failure is also localized without raw exception/path leakage while an independent valid Skill remains visible.
- The complete Skill Catalog suite is green: 30 passed.
- Real workspace post-fix result: total 8, project 7, compat_project 1, user/compat_user 0, `source.status=live`, diagnostics empty. Both `.DS_Store` SHA-256 and mtime_ns exactly match pre-fix evidence and both files still exist.

## Final verification and browser evidence

- Required focused suites: Skill Catalog 30 passed; Dashboard Web 52 passed; packaging/wheel/isolated installation 9 passed.
- Installed-wheel Gateway fixture includes project and compat-project `.DS_Store`, project README, two valid Skills, and `/run`; `/api/v1/skills` is live with total 2, correct 1/0/1/0 source counts, zero diagnostics, no ordinary content/path leak, and all other read APIs plus `/run` remain green.
- Static gates: touched Ruff passed, explicit `py_compile` passed, full `compileall -q minicode scripts tests` passed, unchanged production `app.js` passed `node --check`. Runtime dependencies remain empty; pyright and mypy are unavailable.
- Complete pytest: 1662 passed, 2 skipped, zero failures in 64.07 seconds; only the three pre-existing unregistered benchmark-marker warnings remain.
- Final v5 verifier is read-only green: candidate match, all five manifest pins, every lineage edge, and 13/13 protected production files match. Raw v1-v5 manifest SHA values are unchanged; no v6 exists.
- Isolated browser fixture contains two project directory Skills, one compat-project Skill, both `.DS_Store` files, an ordinary README, and an independent RunJournal `skill.routed` event. Catalog is `read-only · live`, total 3, all three cards appear, source/directory filters return the expected two project cards, and no ordinary filenames/content or diagnostic appears.
- Routing renders `project/runtime-route` from RunJournal rather than Catalog. All eight main routes and all five Memory subroutes rendered. At 1280 px document width equals viewport width; console warning/error list is empty; Dock remains mock/read-only.
- Final screenshot: `/tmp/minicode-dashboard-batch3c11-skills-catalog-live.png` (1280×900). Browser test tab and port 18766 Gateway were closed.

# MiniCode Dashboard Batch 4A Notes

## Scope lock

- Canonical source is the existing `AgentStep.usage`; provider request/response behavior and adapter usage semantics are observation inputs, not modification targets.
- The production path is `_model_next()` → safe `model.completed`/`model.failed` projection → existing RunJournal → bounded DashboardReadModel aggregation → Runs/Overview/Ops.
- Persist no model/provider identity, prompt/messages/output, raw provider usage, request identifiers, credentials, pricing, or wall-clock latency boundaries.
- Cost remains `unavailable/null`; Batch 4A does not consume CostTracker or default pricing.

## Phase 1 audit and pre-change evidence

- `ModelUsage` already exists only in `minicode/types.py` with four nullable token buckets and `provider|estimated|unavailable`; both OpenAI and Anthropic streaming/non-streaming paths already attach it to the returned `AgentStep`. No Adapter or type change is required.
- The single production invocation seam is the `_model_next()` call inside `run_agent_turn()`. ModelSwitcher recovery uses `continue`, so every actual retry naturally re-enters the seam and receives a distinct observer-local operation ID.
- Current Model events preserve business control flow but contain no usage or duration. `event_sink=None` already suppresses operation-ID creation; Batch 4A must preserve this and also suppress all clock/projection work.
- `RunObservation.emit()` and `RunJournal.append_event()` already provide optional, best-effort, same-Run persistence. RunJournal owns storage and generic payload sanitization; Batch 4A must not change its lifecycle/writer ownership.
- Dashboard event semantics are strictly whitelisted in `_run_event_details()`. Runs metrics, Snapshot usage, and Ops are currently unavailable. HTTP already has strict query parsing, UTF-8 JSON, `no-store`, structured API 404, and startup-bound workspace isolation.
- Retained aggregation can stay entirely in DashboardReadModel by paging the public `RunJournal.list_runs()` and `list_events()` interfaces under new explicit budgets; no TUI or CostTracker state is needed.
- The frontend already has request-ID stale-response protection for all real stores. Ops needs the same independent idle/loading/loaded/empty/partial/error store and manual refresh, without polling.
- Pre-change focused observation/ReadModel/web/wheel/baseline matrix: 120 passed. Active v5 verifier matches all five pins, every lineage edge, candidate equality, and 13/13 files.
- Pre-change protected hashes include: types `0300f0b1...d366b`, OpenAI adapter `2c4b9235...66848`, Anthropic adapter `6936dc85...98398`, RunJournal `20f41213...44c1`, lifecycle `1a8103e0...cdf9`, Memory `2706a3e6...1108`, MemoryPipeline `a71062f8...35a1`, retrieval `33b27c4e...7376`, injector `059d2812...21ee`, Skills `8df261e8...aaf`.
- Historical manifest pins captured unchanged: v1 `b5434d...b417`, v2 `15df83...1bab`, v3 `072231...6522`, v4 `5034b3...e10`, v5 `70ece17f...72ff`.
- The semantic evaluator consumes `ACTIVE_PRODUCTION_RETRIEVAL_HASHES` and `verify_active_baseline()`, so advancing that single active baseline to v6 is sufficient; duration/performance observations remain excluded from its deterministic behavior projection.

## Phase 2 canonical event observation

- `project_model_usage()` is the deep projection module interface: it emits exactly source plus four nullable camelCase buckets, accepts only `provider|estimated|unavailable`, rejects bool/negative/>1B values, preserves explicit zero, and degrades missing/illegal/hostile values to a fixed unavailable object without leaking exception text.
- `project_model_duration_ms()` accepts only two finite non-bool monotonic readings, returns rounded non-negative integer milliseconds, and rejects negative or greater-than-24-hour observations.
- Agent Loop reads the monotonic clock immediately before and immediately after the one existing `_model_next()` invocation. The end reading occurs before usage projection or event emission, so duration means whole adapter invocation elapsed time including adapter retry/backoff, not Provider server latency.
- `model.completed` preserves its four prior fields and adds canonical `usage` plus `durationMs` when the clock is valid. `model.failed` adds only `durationMs` and never usage.
- Network, timeout, provider error, KeyboardInterrupt, SystemExit, context recovery, and ModelSwitcher retain existing business behavior. Every actual retry re-enters the loop with a new operation ID and its own duration.
- When `event_sink=None`, no observer operation ID, monotonic read, or usage projection occurs. Clock, usage projection, and sink failures remain no-throw and do not alter messages, exception identity, or model call count.
- Phase 2 event/Agent Loop/adapter/runtime matrix: 60 passed; dedicated projector/model-event slice: 28 passed; touched Ruff passed.

## Phase 3 read model and Ops API

- One bounded `_ModelObservationAggregate` pairs valid `model.started` and terminal events by operation ID. Duplicate, unpaired, malformed usage/duration, corrupt Run reads, and Run/event budget exhaustion are localized to deduplicated diagnostics without contaminating valid calls.
- Run detail scans the full bounded Run independently from the requested display page. Tokens are live only when every completed call is provider-reported, partial for mixed/estimated/partly unavailable data, and unavailable for historical/no canonical data. Duration uses completed and failed terminal observations and applies the same live/partial/unavailable distinction.
- Snapshot Overview and `/api/v1/ops` share the same retained-RunJournal scan limits: 100 Runs and 1,000 events per Run. Ops returns Provider and Estimated buckets, a combined provenance-aware aggregate, duration totals/average, explicit historical partial coverage, and `cost={status: unavailable, value: null}`.
- `/api/v1/ops` rejects all query parameters, uses `no-store`, returns structured redacted failures, and is included in the all-read-route secret scan. Backend Dashboard slice: 117 passed.

## Phase 4 production frontend

- `opsStore` is independent and implements idle/loading/loaded/empty/partial/error plus request-ID stale response protection and manual Refresh/Retry. There is no interval fetch, SSE, or write control.
- Overview renders retained input/output/cache buckets, Model duration, provenance/call counts, and Cost unavailable. Run detail renders safe Provider/Estimated/Unavailable terminal summaries and structured token/duration metrics without coercing objects to `[object Object]`.
- Ops renders retained Run counts, Provider/Estimated/combined buckets, completed/failed/unavailable calls, total/average observed duration, scan coverage, diagnostics, and explicit unavailable Cost. The mock/read-only Dock and deferred mock data remain isolated.
- Production JavaScript passes `node --check`; formal prototype resources remain untouched.

## Phase 5 v6 and wheel evidence

- Active production baseline is `memory-retrieval-production-v6`. Exact v5→v6 changed files are only `minicode/agent_loop.py` (`model_usage_observer`) and `minicode/run_events.py` (`model_usage_projection`); added/removed sets are empty.
- v6 manifest SHA-256 is `623366c6d895d057ef03fc7e719d9d2c3dfdd6e4e1f394b355dc6441daaae89b`. Historical v1-v5 pins remain byte-identical. Default verification reports all six pins, every lineage edge, candidate equality, and 13/13 current protected hashes.
- Historical v5 builder/writer now validate and return immutable pinned evidence; only explicit `--write-v6` can create the fixed v6 target after validating v1-v5.
- Semantic certification remains unchanged across all 108 cases. Baseline plus semantic suites: 48 passed. Installed-wheel Gateway `/run`, Snapshot, Runs detail, Ops, System, static assets, and all prior read APIs pass with canonical Provider/Estimated usage, duration, Cost unavailable, and no runtime dependencies; packaging suite: 9 passed.

## Phase 6 final regression and browser evidence

- Full pytest completed with 1690 passed and 2 skipped in 67.02 seconds; the only three warnings are the repository's existing unregistered benchmark markers. Focused Dashboard/backend/frontend was 117 passed, baseline plus semantic certification was 48 passed, and wheel/isolated-install packaging was 9 passed.
- Explicit `py_compile`, full `compileall -q minicode scripts tests`, touched-file Ruff, and production `node --check` all passed. Runtime dependencies remain empty.
- The isolated Gateway used the real `/run` path for Provider, Estimated, Unavailable, and recovery observations, plus one historical Run without canonical usage. Snapshot and Ops aggregated 310 input, 62 output, 16 cache-read, 0 cache-creation, 388 known total tokens, and 500 ms observed duration without inventing Cost.
- The browser exercised all eight main routes and all five Memory subroutes. Runs detail safely rendered Provider/Unavailable and duration values without `[object Object]`; a deliberately failed first Ops read rendered a redacted error and recovered through Retry.
- At 1280x900 there was no horizontal overflow. Browser warning/error logs were empty, mock/read-only boundaries remained visible, and no fixture secret entered the DOM. Final Ops screenshot: `/tmp/minicode-dashboard-batch4a-ops-live.png`.
- The isolated Gateway listener, temporary browser tab, and viewport override were closed after acceptance.

# MiniCode Batch 4A.1 Work Chain Disabled Hotfix Notes

## Scope lock

- Production behavior change is limited to neutral initialization of optional Work Chain controllers in `minicode/agent_loop.py`.
- `enable_work_chain=True`, canonical usage/duration events, Dashboard/API contracts, Cost unavailability, Memory/Skill/Session/RunJournal behavior, and v1-v6 artifacts are frozen.
- The final v6→v7 protected delta must be exactly `minicode/agent_loop.py`, with no added or removed protected files.

## Phase 1 reproduction and audit

- No ContextManager: the minimal public call invokes the Model exactly once, then raises `UnboundLocalError: cannot access local variable 'context_compactor' where it is not associated with a value` during final statistics.
- With `ContextManager(model="test")`: the same public call invokes the Model zero times, then raises `UnboundLocalError: cannot access local variable 'context_cybernetics' where it is not associated with a value` during pre-request context handling.
- AST and reference audit found exactly three genuine Work Chain branch locals that are read outside the branch without a prior neutral binding: `context_compactor`, `context_cybernetics`, and `cost_control`. Other branch-assigned names are either initialized before the branch or are read only after an assignment in the same guarded block.
- The active v6 verifier is green before the repair: candidate matches, all six manifest pins match, every lineage edge matches, and all 13 protected files match.
- Frozen v1-v6 SHA-256 values are unchanged: v1 `b5434d98...b417`, v2 `15df83ef...1bab`, v3 `0722314f...6522`, v4 `5034b342...e10`, v5 `70ece17f...72ff`, v6 `623366c6...89b`. No v7 file existed at the audit point.

## Disabled path implementation and regression

- Production change is exactly three declarations before `if enable_work_chain`: `context_compactor`, `context_cybernetics`, and `cost_control` are each initialized to typed `None`. Their prior declarations inside the enabled branch were removed; all enabled assignments and construction order remain unchanged.
- The new disabled-path suite passes 15 tests covering no ContextManager, real ContextManager ownership, controlled basic auto-compact fallback, six direct constructor tripwires, canonical Provider usage and 375 ms duration, no-sink zero observation, failing-sink equivalence, network/timeout/provider fallback, KeyboardInterrupt/SystemExit identity, Tool/callback/permission behavior, and no MemoryManager access.
- Existing enabled Agent/Model/Run/Skill/Memory observation slice passes 93 tests. After the source fix but before v7, the v6 verifier reports exactly one mismatch (`minicode/agent_loop.py`); every other protected file still matches.

## v7 and semantic certification

- Active baseline is `memory-retrieval-production-v7`, parent v6, with exact changed set `{minicode/agent_loop.py}`, empty added/removed sets, and reason code `work_chain_disabled_initialization`.
- v7 manifest SHA-256 is `120bec4ee33cbbee5d5d056024b96e3e331c1b3101cc6dbe36beaec8fd17ebf4`. v1-v6 hashes remain byte-identical.
- Default verification is read-only and green: all seven pins, every lineage edge, deterministic candidate equality, and 13/13 current protected sources match. `--print-v7` hashes to the same v7 manifest; `--write-v7` owns only the fixed target, while the historical v6 writer no longer rewrites.
- Baseline plus full 108-case semantic evaluator tests pass 51/51. Accepted artifact `5629...fdd3b`, full behavior projection `b9fa...bd60`, and per-case fingerprint `b73d...8667` remain unchanged; remote calls and formal-state/diagnostic side effects remain zero.

## Wheel, runtime, Gateway, and browser acceptance

- Installed-wheel packaging remains 9/9 green. Inside the isolated installed target, real `run_agent_turn(enable_work_chain=False)` succeeds with no ContextManager, with a real ContextManager, with Provider usage/duration observation, and with an always-failing sink; constructor tripwires remain untouched. A real default-enabled call also succeeds before the existing Gateway/API/static smoke runs.
- Source runtime scenarios A-E all pass. Scenario C persisted the exact order `run.queued`, `run.started`, `model.started`, `model.completed`, `assistant.completed`, `run.completed`; canonical source is Provider and controlled duration is 200 ms. Scenario D matches no-sink output/call count, and scenario E completes through the unchanged enabled path.
- The isolated Gateway's real `/run` composition returned `Batch 4A.1 browser runtime ok`. Ops was live with one Provider call, 120 input, 24 output, 8 cache-read, valid 0 ms elapsed, and `cost={status: unavailable, value: null}`; Skills Catalog was live with one controlled project Skill.
- Browser regression opened all eight main routes and all five Memory subroutes. The selected real Run showed Provider usage/duration plus `skill.routed`, `memory.retrieved`, and `memory.rendered`; retrieval/injection pages retained the selected Run. Overview and Ops showed canonical usage/duration and unavailable Cost. At 1280 px every route had no horizontal overflow, no load error or object coercion leak, Dock remained mock/read-only, and console warning/error count was zero.
- The browser viewport was restored, the temporary tab and port 18768 Gateway were closed, and all temporary scripts, Skills, journal records, and directories were removed.

## Final verification and review

- Focused Agent/Event/Dashboard/wheel regression collected and passed 123 tests. Final full pytest passes 1708, skips 2, and fails 0 in 67.53 seconds; only the three pre-existing unregistered benchmark-marker warnings remain.
- Touched-file Ruff, explicit `py_compile`, full `compileall -q minicode scripts tests`, production `node --check`, path-free failure-envelope tests, secret scan, and zero-runtime-dependency check pass. `pyright` and `mypy` are not installed and are not claimed.
- Final read-only v7 verification reports all seven pins, all lineage edges, candidate equality, and 13/13 protected hashes. Review found no blocking or non-blocking defect: the production delta remains only the three neutral declarations in `minicode/agent_loop.py`; `run_events.py`, Dashboard, Pricing/Cost, Memory, Skill, Session, RunJournal, lifecycle, adapters, and historical v1-v6 files are unchanged.

# MiniCode Dashboard Batch 4B-1 Notes

## Scope lock

- Authorized production facts are immutable Catalog entries, deterministic per-success CostQuote, safe `model.costed`, and a strict Run-detail projection.
- Overview/Runs summary/Ops Cost aggregation, frontend amount display, historical backfill, pricing management, Provider billing reconciliation, Tool/MCP Cost, and Batch 4B-2 are excluded.
- v1-v7 manifests, 108-case Memory artifacts, Model request/usage behavior, Session/Memory/Skill algorithms, and zero runtime dependencies are frozen.

## Legacy audit and production evidence

- `cost_tracker.py` remains a TUI-era approximate float table with a `default` fallback, and `model_registry.py` contains a separate incomplete float price view. Neither is authoritative enough for Dashboard facts; Batch 4B-1 does not modify them and the canonical path imports neither table.
- The actual pricing seam is the existing single `_model_next()` call in `run_agent_turn()`. Context recovery and ModelSwitcher both return to that seam, so the successful call sees the current adapter and receives its own operation ID.
- Production Catalog evidence was retrieved on 2026-07-17 from first-party OpenAI model pages. GPT-4o is USD 2.50 input, 1.25 cached input, and 10.00 output per one million tokens; GPT-4o mini is USD 0.15, 0.075, and 0.60 respectively.
- Anthropic's first-party pricing distinguishes cache-write durations and other modifiers not preserved by the current canonical usage contract. Anthropic, OpenRouter, custom endpoints, dynamic routing, and unresolved identities therefore remain unpriced instead of being guessed.
- The v7 pre-change verifier and related 1708-test baseline were green. Historical v1-v7 manifest hashes were captured before changes and remain fixed.

## Pricing deep module and arithmetic

- `minicode/pricing.py` owns frozen `ModelPrice`, `PricingCatalog`, and `CostQuote` value objects plus the small `quote_model_cost()` and `project_model_cost_event()` interfaces. It is independent from Web, RunJournal storage, legacy CostTracker, and third-party packages.
- Catalog `minicode-pricing-2026-07-17-v1`, version 1, currency USD, contains only exact GPT-4o and GPT-4o-mini canonical keys and documented exact aliases. There is no fuzzy/prefix/case-fold/default resolution.
- Direct `OpenAIModelAdapter` calls are priceable only when the effective endpoint is the official OpenAI API and the call is not OpenRouter-routed. A small explicit `catalog_model_key` seam supports controlled exact identities; unresolved/custom raw strings are never placed in the unavailable quote or event.
- OpenAI prompt input includes cache-read tokens, so normal input is `inputTokens - cacheReadTokens` and cached input is priced exactly once. Cache creation is explicitly not applicable for the supported entries. Unsupported semantics, invalid cache/input relationships, missing priced buckets, nonzero unpriced buckets, bool/negative/>1B tokens, and overflow fail closed.
- Rates and all intermediate calculations use `Decimal`. Each of input/output/cache-read/cache-creation is independently converted to integer nano-USD using `ROUND_HALF_EVEN`; the persisted total is the exact sum of those four bounded integers.

## Event, failure, and ReadModel boundaries

- A successful observed call emits `model.started`, `model.completed`, then `model.costed` with the same `operationId`. Failed calls emit only `model.started`, `model.failed`. A ModelSwitcher recovery gets a new ID and is priced from the actual fallback adapter.
- Canonical usage is projected once and shared by completed-event and quote construction. With no sink, no pricing resolver, Catalog lookup, Decimal work, observer clock, or operation ID is invoked.
- Pricing/resolver/arithmetic failures become a fixed `pricing_failed` unavailable payload; sink/RunJournal failures are independently isolated and never replace the model result, alter calls/retries, or change KeyboardInterrupt/SystemExit identity.
- RunJournal's closed event set now accepts `model.costed`. Run Detail whitelists only cost version, operation ID, fixed status/quality/currency/catalog, safe priced key, bounded integer amount/components, or a fixed unavailable reason. Invalid types, bool amounts, and component/total mismatches downgrade to a path/model/exception-free `pricing_failed` view.
- Snapshot Overview, Runs summaries, Ops, and the production frontend still expose Cost as `{"status":"unavailable","value":null}`. No aggregation, total card, currency formatting, historical backfill, or frontend quote logic was added.

## v8 and semantic certification

- Active baseline is `memory-retrieval-production-v8`, parent v7. Exact protected changes are `minicode/agent_loop.py` and `minicode/run_journal.py`; exact protected addition is `minicode/pricing.py`; removed set is empty. Reason code is `canonical_model_cost_observation`.
- v8 manifest SHA-256 is `13a70abaed1091d17bc137fcffab336349ab6d22cf7f503133bf6efd1cb37726`. Historical v1-v7 manifest files and pins remain byte-identical, and the active verifier matches all 14 protected files.
- The accepted 108-case artifact remains `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`; behavior projection remains `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`; per-case fingerprint remains `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.

## Verification and runtime evidence

- Focused Pricing/Agent/ReadModel, work-chain, Dashboard, RunJournal, packaging, baseline, and semantic suites are green. The final complete suite passed 1742 tests with 2 skipped and zero failures in 65.74 seconds; only the three pre-existing benchmark-marker warnings remained. The final added invariants cover Catalog container immutability, non-Decimal fail-closed behavior, independent completed/costed writes, and ReadModel component reconciliation.
- Touched-file Ruff, explicit `py_compile`, full `compileall -q minicode scripts tests`, and unchanged production `app.js` `node --check` passed. Static sensitive-data scanning found no production secret or machine path; dependencies remain `[]`.
- The built wheel contains `minicode/pricing.py` and Dashboard static resources. Isolated installation, resource loading, real installed Gateway `/run`, all existing read routes, `/health`, `/api/v1/health`, and static assets passed.
- A real source Gateway run persisted one priced GPT-4o sequence with 120 input, 24 output, and 8 cache-read tokens: 280000 input + 240000 output + 10000 cache-read + 0 cache-creation = 530000 nano-USD. A controlled unknown model persisted only `unavailable/model_unpriced`; its raw secret-like identity was absent from the journal/API scan. Skill Routing and Memory Retrieval/Injection events remained present, and aggregate Cost remained unavailable.
- The in-app browser blocked local loopback navigation with `ERR_BLOCKED_BY_CLIENT` and explicitly prohibited a workaround. Consequently no fresh interactive 1280×900/console pass is claimed. Browser-oriented route/frontend tests, HTTP smoke, JavaScript syntax, static assets, and runtime API checks are green; the temporary Batch 4B-1 listener and all known wheel/browser/semantic fixtures were closed or removed.

## Batch 4B-2 stable seam

- Batch 4B-2 can consume only validated `model.costed` RunJournal events through the existing bounded DashboardReadModel scan, grouping by `operationId` without re-reading adapter state or recomputing quotes.
- `costVersion`, `catalogId`, `catalogModelKey`, `quality`, integer `amountNanoUsd`, and reconciling component integers are the stable fact contract. Aggregate coverage must preserve priced versus unavailable counts and provider versus estimated quality; it must not silently coerce historical/unpriced runs to zero.
- Deferred work remains Overview/Runs/Ops aggregation and presentation, coverage/partial-state semantics, currency formatting from integer nano-USD, historical coverage copy, and dedicated Batch 4B-2 browser acceptance. Catalog mutation, provider invoice reconciliation, Tool/MCP Cost, write APIs, polling/SSE/WebSocket, and backfill remain outside that seam.

# MiniCode Dashboard Batch 4B-2 Notes

## Scope lock

- Monetary aggregation may consume only persisted same-Run Model events; it must never import/replay Pricing Catalog logic, inspect adapters/runtime names, or reinterpret old facts using a current rate.
- `minicode/agent_loop.py`, `minicode/pricing.py`, and `minicode/run_journal.py` are frozen unless a separately proven severe defect requires a new production lineage. Dashboard-only changes should leave active baseline v8 intact.
- Amount arithmetic remains bounded Python integers. Aggregate JSON uses decimal nano-USD strings; existing strict Run timeline Cost event fields retain their integer compatibility contract.
- Overview, Runs, and Ops must share one reconciliation/aggregation implementation and use complete/partial/unavailable consistently. Unknown, failed, missing, invalid, orphan, duplicate, conflict, quality-mismatched, and scan-limited observations are never silently treated as zero.
- Frontend work extends the confirmed Waku three-column layout and existing stores only. No pricing table, token arithmetic, Catalog fetch, new API, polling, chart, write control, or Dock coupling is authorized.

## Aggregation and UI implementation evidence

- `minicode/web/cost_aggregation.py` is the sole reconciliation module. Its public surface is `CostAggregate`, `aggregate_run_cost`, `merge_cost_aggregates`, `project_cost_metric`, `project_run_cost_summary`, `project_cost_breakdown`, and `project_cost_event_detail`; it imports no production Pricing, adapter, registry, or legacy tracker module.
- Same-Run state proceeds only through started → completed → costed. Failed attempts never receive an amount; orphan/order errors, invalid events, duplicate/conflicting observations, missing events, and quality mismatches are excluded or counted with fixed low-cardinality diagnostics.
- Python sums bounded `int` nano-USD and components. Aggregate APIs serialize every monetary value as a canonical decimal string, while the existing timeline projection remains integer-compatible. `cost-format.js` accepts only canonical decimal strings and formats with `BigInt`; zero, sub-cent, >USD, and >safe-integer values have Node tests.
- Overview and Ops share one retained scan; Runs scans only the returned page and each Run detail scans the bounded full Run independently of its visible event cursor. A single Run read failure remains a limited unavailable item and does not abort its healthy siblings.
- Frontend formal views consume only API Cost fields. Overview, Runs list/detail, and Ops distinguish complete/partial/unavailable, never render unavailable as zero, retain stale-request checks and Retry, and add no fetch interval. The existing one-second timer updates display metadata only.

## Final certification and browser acceptance

- `run_journal.py` audit evidence proves the v7→v8 delta is only the closed `EVENT_TYPES` admission of `model.costed`; removing that one allowlist line reproduces the frozen v7 hash. Explicit tests confirm unknown event types, unsafe paths, NaN, oversized values, and malformed Cost payloads are rejected without a write, while redaction remains active.
- The active v8 verifier reports candidate equality, all v1–v8 manifest pins and lineage checks intact, and 14/14 protected files matching. `minicode/agent_loop.py`, `minicode/pricing.py`, and `minicode/run_journal.py` were not modified by Batch 4B-2.
- The 108-case artifacts remain exactly: accepted artifact `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, behavior projection `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`, and per-case fingerprint `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
- Final full pytest is 1769 passed, 2 skipped, 0 failed in 67.86 seconds, with only the three existing benchmark-marker warnings. Focused modified-product regression is 268 passed; Cost aggregation is 19 passed; baseline/semantic certification is 57 passed; wheel/isolated-install is 9 passed.
- Ruff on touched Python, explicit `py_compile`, `compileall -q minicode scripts tests`, `node --check` for `cost-format.js` and `app.js`, production static sensitive-data scanning, dependency inspection, wheel contents, isolated import/resource loading, installed Gateway routes, and source HTTP smoke all passed.
- A real isolated Gateway `/run` generated one GPT-4o priced observation and one unresolved unavailable observation. Detail reported complete 530000 nano-USD for the former and unavailable/null for the latter; Snapshot and Ops reported partial 530000 with one priced and one unavailable, without re-pricing or exposing the unresolved identity.
- Interactive browser acceptance used an isolated seven-Run fixture: complete priced, unavailable, provider/estimated mixed, failed-attempt partial, historical missing, duplicate, and canonical zero. Runs list/detail, `model.costed` timeline, Overview, and Ops agreed on exact amounts; zero remained distinct from unavailable.
- Ops intentionally returned a first-read failure, showed a redacted error with one Retry action, then recovered to 530650 observed nano-USD: provider component 530450, estimated component 200, priced 6/8 completed, unavailable 1, missing 1, failed attempts 1, and fixed quality/catalog/model/source/reason breakdowns.
- All eight main routes and five Memory subroutes rendered at 1280x900 with no horizontal overflow. Browser console warning/error logs were empty; no fixture secret or `[object Object]` entered the DOM; forbidden billing copy was absent; the right Dock remained visibly mock/read-only.
- The browser viewport override and tab were finalized, the temporary Gateway listener was stopped, and the isolated fixture directory was removed. No Git repository or commit was created.

# MiniCode Dashboard Batch 5A Notes

## Scope lock

- Consume only persisted `tool.started`, `tool.finished`, `model.failed`, and Run lifecycle facts. Do not add events, execute Tools, infer Tool duration, or expose Tool payloads/error text.
- Keep `minicode/agent_loop.py`, `minicode/run_lifecycle.py`, `minicode/run_events.py`, `minicode/run_journal.py`, `minicode/pricing.py`, Headless/CLI/TUI, Memory, Session, and Skill algorithms frozen.
- Tool-with-no-events is unavailable because no instrumentation-version marker proves a zero. Failure zero may be complete only for a fully scanned recorded Run with trustworthy lifecycle/Model/Tool observations.
- Tool error, Model attempt failure, Run terminal failure, interruption, and cancellation remain separate categories; no ambiguous `totalErrors` is authorized.
- Retained Run/Event scans remain bounded at 100 Runs, 1000 events per Run, page size 100, and 20 diagnostics; Tool-name breakdown will be bounded at 50.

## Phase 1 baseline and contract audit

- Related pre-change regression: 238 passed. Full pre-change baseline: 1769 passed, 2 skipped, 0 failed in 67.76 seconds; only the three existing benchmark-marker warnings remain.
- Active v8 verification is read-only and green: candidate matches, all v1–v8 manifest integrity checks pass, and all 14 protected files match.
- `RunObservation.tool_started()` emits a normalized Tool name and a fresh `toolop_<32 lowercase hex>` only after the append succeeds; `tool_finished()` consumes the same-name FIFO queue and emits either a paired finish with that ID or a valid unpaired finish without an ID.
- The producer persists no Tool input/output or duration. Existing Run Detail projection whitelists only name, operation ID, outcome, and paired; it already drops seeded payload/path/command/error secrets.
- `_run_observation_aggregates()` is the correct seam: it reads one Run in pages of 100 up to 1000 events and currently returns Model + Cost. Retained Overview/Ops and Runs list/detail already reuse that scan.
- Snapshot compatibility aliases `overview.usage.toolCalls` and `errors`, plus Run Detail `metrics.toolCalls` and `metrics.errors`, are currently unavailable placeholders. Ops has no Tool/Failure structure yet.

## Aggregation, ReadModel, and UI implementation evidence

- `minicode/web/tool_aggregation.py` owns the immutable `ToolAggregate` / `FailureAggregate` boundary plus per-Run aggregation, bounded merge, metric, Runs-summary, and breakdown projections. It is standard-library-only and has no Tool registry/executor, Journal writer, Pricing, or Agent dependency.
- Tool pairing accepts only a same-Run `toolop_<32 lowercase hex>` start followed by one matching-name paired finish. Legal unpaired finishes remain observed completed callbacks without IDs; dangling, orphan, duplicate, conflict, malformed, limited, and read-failed states remain explicit and safe.
- Tool-with-no-events projects unavailable/null. Failure complete zero is allowed only when a bounded recorded Run has valid lifecycle/Model/Tool observations. Tool errors, Model attempt failures, terminal Run failures, interruptions, and cancellations remain separate; affected Runs deduplicate only Tool/Model/Run failure categories.
- `_run_observation_aggregates()` now returns Model, Cost, Tool, and Failure results from one bounded RunJournal event read. Retained Overview/Ops reuse it, Runs scans only the returned page, and Detail metrics use the full bounded scan independently of the visible Timeline cursor.
- Snapshot adds structured `usage.tools` and `usage.failures` while compatibility aliases remain null. Runs items add compact summaries, Detail replaces placeholders with canonical metrics, and Ops adds bounded Tool/Failure summaries and breakdowns under schema v1.
- The formal Waku UI consumes only API projections. Overview, Runs list/detail, Ops, and System now render complete/partial/unavailable observations; unavailable Tool values remain em dashes, failure categories stay separate, all dynamic data uses `esc()`, and no polling, SSE, write control, Tool operation-ID view, payload view, or duration inference was added.
- Frontend RED→GREEN passed 59 Dashboard Web tests; backend focused Tool/Cost/usage/Runs/ReadModel tests passed 77. Touched-file Ruff and `node --check` are green.
- The wheel smoke now includes `tool_aggregation.py`, runs an installed Gateway `/run` with a paired Tool success and legal unpaired Tool error, and proves Snapshot/Runs/Detail/Ops agreement plus payload-secret and operation-ID exclusion. The isolated build/install/Gateway test passed.

## Final certification and browser acceptance

- Code review found and corrected two presentation omissions: Tool-name rows now include completed counts, and Failure category codes render as Tool errors / Model attempt failures / Run failures / Interruptions / Cancellations.
- The first browser Overview exposed the pre-existing schema-v1 absolute workspace path. Batch 5A security forbids machine paths, so `workspace.path` remains as a compatibility key with `null`, while the UI displays the stable workspace ID and `absolute path hidden`; API/DOM regression tests cover this boundary.
- Final full pytest passed 1793 tests with 2 skipped and zero failures in 67.77 seconds. Only the three pre-existing benchmark-marker warnings remain. Production-focused regression passed 234, final Dashboard/read-model/packaging regression passed 106, packaging passed 9, and v8/108-case certification passed 57.
- Ruff on touched Python, explicit `py_compile`, full `compileall -q minicode scripts tests`, both production `node --check` calls, static sensitive-data scans, dependency/import checks, and no-Git scope checks passed. Runtime dependencies remain empty.
- Active baseline remains `memory-retrieval-production-v8`; candidate matches, all 14 protected files match, and v1-v8 manifest integrity is true. Accepted artifact, behavior projection, and per-case fingerprints remain `5629d6...fdd3b`, `b9fabf0...1bbd60`, and `b73da4...8667` respectively.
- Isolated browser acceptance covered 12 Runs: paired success, paired error, same-name persisted FIFO IDs, unpaired finish, dangling start, duplicate, conflict, ModelSwitcher failure/recovery, Run failed, interrupted, no-Tool historical state, and cancelled. Unsafe Tool/provider payload fields and a malformed Tool operation ID remained absent from API, diagnostics, and DOM.
- Overview rendered 8 observed Tool calls and 4 affected Runs. Runs list/detail preserved unavailable versus zero and displayed strict Tool/Failure metrics without Tool operation IDs. Ops first failed with a fixed message, then Retry recovered to 8 Tool observations, 2 Tool errors, 1 Model failure, 1 Run failure, 1 interruption, and 1 cancellation with separate categories.
- All eight main routes and all five Memory subroutes rendered at 1280x900 with no horizontal overflow. Nav/main/Dock rectangles did not overlap; page console warning/error logs were empty; no fixture secret, machine path, `[object Object]`, ambiguous total error, SSE, or automatic fetch interval was present. The Dock stayed mock/read-only.
- Browser viewport was reset and tab finalization was the final browser action. The temporary Gateway was stopped and its isolated directory removed. No Git repository or commit was created.

# MiniCode Dashboard Batch 5B-1 Notes

## Pre-change freeze

- Related Context/WorkingMemory/Agent/Journal/Dashboard regression passed 301 tests.
- Full pre-change baseline passed 1793 tests with 2 skipped and only the three existing benchmark marker warnings.
- Active v8 candidate is read-only green. The frozen v8 manifest SHA-256 is `13a70abaed1091d17bc137fcffab336349ab6d22cf7f503133bf6efd1cb37726`; v1-v8 manifest file hashes were captured before changes.

## Actual Context and WorkingMemory call graph

- With `enable_work_chain=True` and a `ContextManager`, Agent Loop creates `ContextCompactor` and `ContextCyberneticsOrchestrator`; the pre-request path calls `run_cycle()`, which may call `ContextCompactor.process_request()`. Agent Loop replaces `current_messages` only when the returned `CompactionResult.effective` is true.
- The direct `ContextCompactor.process_request()` branch exists only when no cybernetic orchestrator is present. The `ContextManager.should_auto_compact()` / `compact_messages()` fallback is reachable when the work chain is disabled; it must be observed only when the returned message list actually changes.
- `ContextCompactor.process_request()` may change message content through Tool-result persistence, time-based microcompact, or Auto Compact. Its returned aggregate `CompactionResult` is the only existing normalized result seam; it reports effectiveness and token savings, although Tool-budget savings are internally accumulated from saved bytes under the legacy `tokens_freed` name.
- Model overflow handling emits the existing Model failure first, then calls either `ContextCyberneticsOrchestrator.try_reactive_recover()` or direct `ContextCompactor.reactive_recover()`. Effective recovery replaces `current_messages` and retries the Model; ineffective recovery falls through to ModelSwitcher/final failure.
- `ContextCyberneticsOrchestrator.try_reactive_recover()` delegates directly to `ContextCompactor.reactive_recover()`. The underlying reactive engine may return an effective result, an ineffective result, or `None`; business exceptions/control-flow interrupts are not observation failures and must not be replaced.
- The legacy predictive block discards its recovery return and is effectively shadowed by the always-present `CyberneticOrchestrator` branch. `CyberneticOrchestrator.step_start()` only logs predictive recommendations. Neither offers a reliable production event seam in 5B-1.
- Both Agent Loop feedback forced-compaction sites call missing `ContextCompactor.compact_messages()` and catch ordinary exceptions. This is an existing out-of-scope seam defect; it must get a reproduction test but no false event and no broad Context repair.
- `ContextManager.get_stats()` is not pure: it populates `_token_cache`. Observation must not call it when no sink exists; the fallback may reuse the already-computed pre-compaction stats and the compactor's own post-state/count facts without inventing another algorithm.
- Production `minicode/` has no call to `WorkingMemoryTracker.get_protected_content()`, so WorkingMemory is not actually consumed by compaction. A divergent stale `py-src/` copy has such wiring, but it is not the active requested production package and will not be modified.
- `protect_context()` writes to a module-level tracker after a real final Assistant result. The tracker is shared by Runs in one Python process, but separate TUI/Headless/Gateway processes can have separate instances. Existing `get_stats()` clears expired entries and is unsuitable for observation.

## Implementation and certification

- `run_events.py` owns the `ctxop_<32 lowercase hex>` identity, fixed Context/recovery enums, bounded content-free projectors, and best-effort emit adapters. No-sink paths skip IDs, projection, and WorkingMemory snapshots.
- Effective pre-request cybernetic/direct compaction and changed ContextManager fallback output emit `context.compacted`. Overflow recovery emits `recovery.started` before business work, then an effective compaction and `recovery.completed` on success, or `not_recovered` on a normal ineffective return. Business exceptions and control-flow interrupts remain unchanged and intentionally leave a dangling start.
- `WorkingMemorySnapshot` is frozen and slotted. `snapshot(now=...)` copies the current entry sequence, filters expiry without mutation, and projects only counts and limits. Agent Loop observes it only after the existing final `protect_context()` succeeds.
- RunJournal's only v9 admission change is `working_memory.observed`; its pre-existing Context/recovery event names remain closed. Run Detail rejects malformed versions, IDs, enums, booleans-as-counts, inconsistent message counts, invalid limits, and all extra sensitive fields.
- The Waku Timeline shows only bounded summaries for Context, Recovery, and WorkingMemory. Overview/Ops do not aggregate them. Runs and Memory Lifecycle state `partial`, `process-local`, and cross-Run aggregation unavailable until Batch 5B-2.
- Baseline v9 protects 15 production files and records exactly three changed plus one newly protected file under `context_working_memory_observation`. Its SHA-256 is `3444072607489ec4cc2405b8fb09fe9bcb122f9427f4b94d25aa66b9aa52d4d0`; all historical pins remain green.
- Final verification: 303 focused tests; 1839 passed / 2 skipped full regression; Ruff, `py_compile`, `compileall`, `node --check`, wheel/install, installed Gateway, source HTTP, v9, and 108-case fingerprints green.
- Browser acceptance used a content-seeded isolated Run at 1280×900. All eight routes and five Memory subroutes rendered without overflow or panel overlap; the Timeline displayed ordered Recovery/Context/WorkingMemory rows; console warning/error count was zero; Context IDs, injected secrets, machine paths, and `[object Object]` did not enter the DOM. All temporary browser/runtime resources were removed.
# MiniCode Dashboard Batch 5C-1A.2 Certification Integrity Fix Notes

## Pre-change evidence and authoritative recovery source

- Accepted-gold path before this repair: `artifacts/memory-retrieval-semantic-gap-baseline.json`; current SHA-256 `e5418c27f5824a3d651b4d9ab4438877e42967278657dff4216e6df2fb9a9d55`, mtime_ns `1784351885973530923`, size `3039474`. This is the evaluator-generated file and is not accepted as gold.
- The locally retained pre-5C archive `/Users/zhourunbo/code/coding agent/MiniCode-Python-main.zip` contains `MiniCode-Python-main/artifacts/memory-retrieval-semantic-gap-baseline.json`; streaming those original bytes through SHA-256 yields the authoritative accepted digest `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b` (archive member size `3033592`). This is an existing local authoritative backup, not a regenerated approximation.
- Pre-change production manifest SHA-256 values are: v1 `b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417`; v2 `15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab`; v3 `0722314faf0476e1566657578782a6437d8d218a779ef24bde474351a5b86522`; v4 `5034b342d68c9a8ef7b450fe2f4bcbda370204f92d3c87f05caf15eec1002e10`; v5 `70ece17f53ec7963395aadc3be2b104636c2804087928d45c707ee94a5e672ff`; v6 `623366c6d895d057ef03fc7e719d9d2c3dfdd6e4e1f394b355dc6441daaae89b`; v7 `120bec4ee33cbbee5d5d056024b96e3e331c1b3101cc6dbe36beaec8fd17ebf4`; v8 `13a70abaed1091d17bc137fcffab336349ab6d22cf7f503133bf6efd1cb37726`; v9 `3444072607489ec4cc2405b8fb09fe9bcb122f9427f4b94d25aa66b9aa52d4d0`; v10 `050b6b8787f8061e59f167af588acbd3a1268a4f4859b813bdfc5edc69cf1b87`.
- Pre-change v10 protects 18 files and declares only `minicode/mcp.py`, `minicode/mcp_observation.py`, and `minicode/tooling.py` as additions. The existing singleton `minicode/mcp_event_contract.py` is used by RunJournal and Dashboard ReadModel but is absent from v10 protection.
- Direct root cause: the official evaluator CLI defaults `--output` to the accepted-gold path and `write_reports()` writes it. Both `ACCEPTED_V1_ARTIFACT_SHA256` and Phase 3B `PHASE3A_BASELINE_SHA256` were changed to the non-authoritative `c275...` value.
- Exact pre-change command order: `python scripts/generate_memory_retrieval_production_baseline.py` passed with active v10, candidate match, 18/18 files, and all v1-v10 integrity booleans true. `python scripts/evaluate_memory_retrieval_semantic_gap.py` reported 108 cases, 37 confirmed gaps, evaluation passed, gate passed, and zero remote calls, but overwrote the accepted path to SHA `d2bd319fe84afdf9f5481b3e3c3e228d0c0781266afeafe3989b45cc2a9fc9e7`, mtime_ns `1784354126957443112`, size `3039422`. The following focused pytest run was `60 passed, 1 failed`; the failure was the accepted-artifact SHA check (`d2bd...` actual versus `c275...` pin).

## Final certification evidence

- The accepted gold was restored byte-for-byte from the local archive. Final SHA-256 is `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`; final mtime_ns is `1784135857000000000`. The final official evaluator run left both values unchanged before/after.
- The default generated JSON is now `artifacts/memory-retrieval-semantic-gap-evaluation.json`. Explicit attempts to use the accepted-gold path as `--output` are rejected before evaluation and leave bytes/mtime unchanged.
- Accepted and generated behavior projection SHA-256 are both `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`; accepted and generated deterministic per-case fingerprints are both `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`. The generated report has 108 cases, zero remote calls, all integrity gates true, and `evaluation_passed=true`.
- Both semantic certification and the Phase 3B hybrid loader pin the authoritative `5629...` artifact. No dataset, Retrieval algorithm, MCP runtime behavior, Agent Loop, RunJournal, Dashboard API/UI, Session, Memory, TUI, or dependency behavior changed.
- v1–v9 manifest bytes remain at their captured pins. v10 alone is re-signed at SHA-256 `bc94fe753ba0a30a5b74f9e3d242d9ede4395244fbdebb8f0d1e9992d992dbdb`, protects 19/19 files, matches its deterministic candidate, and adds `minicode/mcp_event_contract.py` to the existing MCP added-file set. Exact contract tampering reports only that path, sets active/candidate matches false, and does not rewrite v10.
- Final required order: first `python -m pytest -q` was `1891 passed, 2 skipped, 3 existing benchmark-marker warnings`; baseline verifier was fully green at 19/19; official evaluator was 108 cases / 37 confirmed gaps / zero remote calls / passed; second `python -m pytest -q` was again `1891 passed, 2 skipped, 3 existing warnings`.
- MCP/Journal/Dashboard/wheel focused regression passed 131 tests after granting required localhost bind permission. The installed-wheel smoke separately passed and now asserts that `minicode/mcp_event_contract.py` is packaged; installed Gateway Snapshot, Runs, Run Detail, Ops, Sessions, Memory, Skills, Connections, System, and `/run` behavior remained green.
- Ruff on every touched Python file, explicit `py_compile`, full `compileall -q minicode scripts tests`, both production `node --check` commands, secret/absolute-path scans, manifest JSON parsing, dependency inspection, and final code review passed. Runtime dependencies remain `[]`; the workspace still has no Git metadata, and no repository/commit was created.
- Batch 5C-1B was not implemented. Connections remains configuration-only and MCP runtime facts retain their existing run-scoped historical semantics.

# MiniCode Dashboard Batch 5C-1B Historical MCP Runtime Aggregation Notes

## Initial baseline and audited seams

- Before Batch 5C-1B changes, full `python -m pytest -q` passed `1891 passed, 2 skipped` in 80.61 seconds; the only warnings are the three existing unregistered benchmark markers.
- The production verifier reports active `memory-retrieval-production-v10`, candidate match true, 19/19 current protected files matching, exact v10 lineage, and v1–v10 manifest integrity all true. The frozen v10 manifest SHA remains `bc94fe753ba0a30a5b74f9e3d242d9ede4395244fbdebb8f0d1e9992d992dbdb`.
- Accepted semantic gold `artifacts/memory-retrieval-semantic-gap-baseline.json` is SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b` with initial mtime `1784135857`. Behavior projection and per-case pins remain `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60` and `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
- `RunJournal.__init__`, `list_runs()`, and `list_events()` are read-only when storage is absent; only run creation/append/transition/retention/index paths write. The Connections scan can use public paging without modifying the protected Journal.
- Existing Connections performs user/project effective config merge inside `_mcp_catalog()` and returns only redacted server summaries. Association must therefore retain the original validated server name internally, compute the shared `mcp_server_key(workspace, name)`, and remove every internal field before API projection.
- Existing retained aggregation limits are 100 Runs and 1000 events per Run, with public event pages capped at 100. Batch 5C-1B will use the same explicit budgets in an independent Connections-only Web module, not Ops or a frontend Runs store.
- The HTTP route and `no-store` handling already satisfy the transport contract. The frontend has an independent request-ID-protected Connections store, manual refresh, retry state, HTML escaping, and no polling/SSE; only its MCP renderer and contract checks need additive historical fields.
- No v10-protected source needs modification. Planned production changes are limited to a new `minicode/web/mcp_runtime_aggregation.py`, `minicode/web/read_model.py`, and existing packaged frontend assets.

## Implemented read boundary and projection

- `minicode/web/mcp_runtime_aggregation.py` is a standard-library-only, read-side deep module. It scans at most 100 Runs and 1000 events per Run in pages of 100, revalidates every candidate with `normalize_mcp_runtime_payload()`, and associates only through shared `mcp_server_key(workspace, server_name)`.
- The deterministic last-observed order is `(timestamp, run_id, sequence)`. Valid unmatched keys are deduplicated into a count only. Wrong-workspace Runs/events, invalid timestamps/sequences, closed-schema violations, unknown fields, bool-as-int values, invalid outcomes, and invalid failure kinds do not enter facts.
- Config and Journal failures are independent. A global Journal failure produces a safe runtime error while preserving configuration; one broken Run/event produces bounded partial diagnostics and does not block other valid observations. Connections reads do not create storage, write files, start a subprocess, or initialize an MCP client.
- `_mcp_catalog()` retains the original validated effective name in a private response-local field only. `connections()` strips it before the API, adds each server's historical projection, and keeps `liveMcpCount=null`, `liveStatus=unavailable`, and current runtime unavailable.
- The current RunJournal list API has no workspace filter. The hard bounded Journal page is therefore read first and every non-current-workspace record/event is rejected before aggregation; observation counts are current-workspace scanned-window facts, while `retainedRuns` is the safely available Journal total.

## UI, packaging, and final evidence

- Connections → MCP now renders separate Current configuration, Current MCP status, and Retained Run history facts. Historical request success/failure never uses a green live/online claim. Disabled remains disabled. The page displays scan coverage, unmatched count, manual refresh, empty/partial/error/Retry states, and uses the existing request-id stale-response guard and `esc()` boundaries without polling/SSE.
- The wheel includes `minicode/web/mcp_runtime_aggregation.py`. Its isolated install fixture uses a real effective `installed-server` key and proves installed Gateway Connections association without exposing that key.
- Final focused ReadModel/HTTP/frontend/wheel matrix: 129 passed. Final full regression: 1902 passed, 2 skipped, with only the three existing benchmark marker warnings. Ruff, `py_compile`, full `compileall`, and both `node --check` commands passed; runtime dependencies remain empty.
- v10 verifier remains green at 19/19 protected sources with all v1-v10 integrity flags true. The 108-case evaluator passed with 37 confirmed gaps and zero remote calls. Accepted gold remains SHA `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, mtime `1784135857`, size `3033592`.
- Isolated browser acceptance used real retained events for success, disabled connection failure, unobserved config, and unmatched removed config. All eight main routes and five Memory subroutes rendered at 1280×900 without horizontal overflow; manual Connections refresh recovered, console warning/error logs were empty, and no absolute path, server key, `[object Object]`, or current online/healthy claim appeared. The temporary viewport, tab, listener, HOME/workspace, config, and Journal were removed.

# MiniCode Dashboard Batch 5C-2A Process-local MCP Current State Notes

## Phase 1 audit in progress

- Scope is contract/registry core plus optional production observation and Gateway composition only. Connections remains the Batch 5C-1B historical/config view with current state unavailable until 5C-2B.
- Required truth vocabulary is process-local/current process only. Retained RunJournal MCP facts are a separate historical source and will never enter the registry.
- Pre-change full regression passed `1902 passed, 2 skipped` in 120.63 seconds with only the three existing benchmark-marker warnings. Active v10 is green at 19/19 files; its manifest SHA-256 is `bc94fe753ba0a30a5b74f9e3d242d9ede4395244fbdebb8f0d1e9992d992dbdb`.
- Accepted semantic gold remains SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, mtime seconds `1784135857`; behavior/per-case pins remain `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60` and `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
- `run_gateway()` owns a `ThreadingHTTPServer`; every `/run` invokes a separate Headless ToolRegistry while all request threads can share one server-owned current-state registry. Standalone Headless and classic/TUI composition remain unobserved by default and cannot be claimed by the Gateway.
- The actual MCP creation path eagerly calls tools/resources/prompts discovery. Batch 5C-2A wraps those existing calls without adding or delaying requests. Public close owns unregister; protocol-candidate cleanup must retain one registration across retries.
- The `task` tool creates nested same-process ToolRegistries. To keep the Gateway process registry complete, the optional registry must travel through ToolRegistry/ToolContext, and the nested registry must be disposed in a `finally` block.
- The complete ownership graph, visibility matrix, lifecycle rules, and 5C-2B seam are recorded in `docs/minicode-dashboard-batch-5c-2a.md` before production edits.

## Implemented contract and composition

- `mcp_current_state.py` owns a schema/state v1 frozen snapshot contract and a bounded registry (256 instances, 100 response servers, 20 diagnostics). Snapshot probes only ready instances, outside the lock; revision checks prevent stale probe writes, and fixed state precedence plus update sequence make aggregation deterministic.
- Optional client observation reuses the shared server key/failure/protocol contracts. None performs no identity/registry work. Failing observers including KeyboardInterrupt/SystemExit subclasses are isolated. Internal candidate cleanup retains ownership; final close unregisters.
- Gateway owns one registry; concurrent `/run` calls share it. Headless/ToolRegistry/factory/Task only pass the optional dependency. Nested Task cleanup is now explicit even when model setup fails. Standalone Headless/classic/TUI are not represented.
- Connections, Dashboard GET, RunJournal events, frontend assets, Agent Loop, Memory, Session, and TUI do not consume current state in this batch. A forbidden-snapshot HTTP test proves the current Connections payload remains unavailable.
- v11 protects 23 files with exact changed set `headless.py`, `mcp.py`, `tooling.py` and added-protection set `gateway.py`, `mcp_current_state.py`, `tools/__init__.py`, `tools/task.py`. Final manifest SHA is `c5d12d47e25db4ebd566f066420d398f7b04a53b518a407003784d8261371c71`; v1-v10 pins are unchanged.

## Final Batch 5C-2A certification

- Required focused order passed: contract/registry `22`, MCP/ToolRegistry/Headless/Gateway `59`, and Dashboard Connections/Packaging `90` tests.
- Both restarted final full runs passed `1948 passed, 2 skipped` in 82.44s and 82.52s. The only warnings are the three existing unregistered benchmark markers.
- Between the full runs, the read-only v11 verifier reported candidate match, 23/23 current files, exact 3-changed/4-added lineage, and all v1-v11 manifest integrity flags true. Controlled current-state and MCP-wiring tamper tests report only the modified path without rewriting v11.
- The official evaluator reported 108 cases, 37 confirmed gaps, 0 remote calls, evaluation passed, and Phase 3B gate passed. Accepted gold SHA/mtime/size remained `5629d6...fdd3b` / `1784135857` / `3033592` before and after.
- Ruff, explicit `py_compile`, full `compileall -q minicode scripts tests`, both production `node --check` calls, runtime/static sensitive scans, and dependency inspection (`[]`) passed.
- The wheel includes `mcp_current_state.py`; its isolated install proves strict snapshot normalization, installed `StdioMcpClient` ready→close lifecycle, server-owned registry propagation through `/run`, health and all existing read-only API behavior. Source tests use the real fake MCP subprocess for start, fallback, request, death/recovery, and cleanup.
- Browser compatibility (not current-state UI acceptance) checked Overview and Connections at 1280×720: three columns did not overlap, body width was 1280, Connections remained `historical / current unavailable`, online/healthy and object-coercion text were absent, and console warning/error count was zero. Browser tabs and temporary Gateway were cleaned; no fake MCP process remained.

# MiniCode Dashboard Batch 5C-2B MCP Current Projection Notes

## Starting evidence

- After localhost permission and a fresh task-start formal guard, the pre-change full suite passed `1948 passed, 2 skipped`; active v11 matched 23/23 with every v1-v11 pin true.
- The accepted semantic gold was SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, mtime ns `1784135857000000000`, and size `3033592`. The initial evaluator then passed 108 cases with zero remote calls.
- The original `/tmp` formal-stage guard described an earlier 875-file tree. The evaluator proved the current tree was unchanged during evaluation but correctly rejected the stale stage comparison. Only that temporary guard was recaptured; `~/.mini-code` was not written.

## Implemented seams

- `mcp_current_projection.py` is the sole snapshot normalization/association/precision module. Frozen nested records contain no raw server name or key. The injected loader is called at most once, and unmatched keys do not affect configured aggregates.
- `DashboardReadModel` accepts one optional zero-argument loader. Only `connections()` calls it; Overview and every other page remain current-registry unaware. Config, current, and historical failures compose independently into a 200 response.
- `run_gateway()` constructs one Registry and shares its identity with both POST `/run` and the loader closure. Handler fallback has no loader and remains explicitly unavailable without creating another registry.
- API schema/mode remain v1/read-only. Current aggregates are exact only for complete configuration plus a nonlimited valid snapshot; otherwise all three and `byState` are null while safe matched cards remain.
- The Waku Connections page keeps configuration/current/history in three columns, adds separate current/historical coverage, and uses manual Refresh/Retry with the existing stale-response guard. No heartbeat, process control, polling, SSE, WebSocket, or global claim was added.

## Certification in progress

- The v12 manifest protects the same 23 files and declares only `minicode/gateway.py` changed with reason `mcp_current_state_projection`. Its SHA-256 is `a8fba6ed9134b465167525f4b8c81de2369363ad0527f6368527de0369bd05a7`; v1-v11 pins remain unchanged.
- Projection/ReadModel/MCP/semantic/baseline/wheel focused tests, touched Ruff, and production JavaScript syntax have passed. Final full/static/browser repetitions and resource cleanup remain before closure.

## Final Batch 5C-2B certification

- Final self-review made v11 reconstruction/writing immutable once its pinned parent exists, added a hard 2,000-config projection input budget, and strengthened the frontend current-state contract to validate aggregate precision, live/non-live shapes, per-server state relations, source relations, and exact failure/protocol enums.
- Current coverage now explicitly renders Gateway-process scope, cross-process unavailable, heartbeat false, configured-set completeness, unmatched suppression, limited state, exact-or-unavailable aggregates, and the required not-registered/snapshot-limited wording.
- Focused final evidence: 84 projection/current/Gateway/HTTP tests, 73 existing MCP tests, 199 Dashboard/frontend/packaging tests, and 9 isolated wheel tests passed. The final frontend/wheel repeat passed 69 tests after UI contract hardening.
- Both final full regressions passed `1970 passed, 2 skipped, 3 warnings` in 122.94s and 122.93s. The warnings are the three existing unregistered benchmark markers.
- Touched Python passed Ruff and `py_compile`; full `compileall -q minicode scripts tests` and both production `node --check` commands passed. A broader read-only Ruff audit reported 88 pre-existing diagnostics outside this Batch's modified files; none was changed or hidden.
- Active v12 matches 23/23 with exact changed set `minicode/gateway.py`; all v1-v12 pins are true. The 108-case evaluator passed with 37 confirmed gaps and zero remote calls. Accepted gold remained SHA `5629d6...fdd3b`, mtime ns `1784135857000000000`, size `3033592`.
- Browser acceptance at 1280×900 covered empty, two concurrent ready instances, release to not_registered/0, starting, failed categories, current/history disagreement, disabled+ready, limited, fail-once Retry, all eight main routes, and five Memory routes. Console warning/error count was zero and no horizontal overflow/key/path/secret/object/global claim appeared. The persisted final screenshot is `artifacts/minicode-dashboard-batch-5c-2b-connections.jpg`; listener, tab, viewport override, fixture process, and temporary data were cleaned.
- Installed-wheel coverage proves the packaged Client ready→close lifecycle and the packaged Connections exact-empty HTTP projection. A final attempt to combine installed active-ready and HTTP projection in one extra smoke was blocked before execution by the environment's external execution-usage limit; the unexecuted test-only edit was reverted. Source Gateway concurrent ready projection remains covered by automated tests and browser acceptance.

# MiniCode Dashboard Batch 7A Live Refresh Foundation Notes

## Contract and scope

- `GET /api/v1/changes` is a content-free invalidation hint, never a data authority. Existing REST interfaces must be reread after a revision change.
- Resources are `runs`, `sessions`, `turns`, `memory`, `skills`, and `connections`; revisions are deterministic opaque equality markers and `generatedAt` is excluded.
- Cross-process discovery must come from actual persisted filesystem state plus an injected process-local MCP registry adapter; Gateway-local counters alone are insufficient.
- One frontend loop owns visibility, bounded polling cadence, backoff, abort/generation, and affected-store invalidation. No render function or route may create a timer.
- Batch 6 Turn/Session/Run authorities and stale guards remain unchanged. No automatic resend, duplicate cancel, streaming, push, watcher thread, write control, or Batch 7B behavior is allowed.

## Audit evidence

- Pending source and baseline audit.

---

# MiniCode Dashboard Batch 6B-2B.1 Cancellation Boundary Hardening Notes

## Untouched v16 baseline

- Full pytest with loopback permission: `2202 passed, 2 skipped, 3 warnings in 118.09s`; warnings are the existing unregistered benchmark markers.
- Active v16: deterministic candidate true, current protected sources 33/33, exact v16 lineage, and every v1-v16 manifest integrity pin true.
- Official evaluator: 108 cases, 37 confirmed gaps, zero remote calls, phase3b gate and evaluation both true.
- Accepted gold before/after evaluator: SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime ns `1784135857000000000`.
- `compileall -q minicode scripts tests`, explicit target py_compile, and every formal production JavaScript `node --check` passed. Dependencies are `[]`.
- Repository-wide Ruff reports 686 pre-existing findings concentrated in historical/build/py-src/ts-src trees. This task will use scoped Ruff as its mutation gate and preserve those unrelated files.

## Source audit before RED

- `ConversationTurnService.turn()` claims accepted and then calls `mark_running()` inside a broad Store-error adapter. If Cancel persists `cancel_requested` in between, `mark_running()` raises `TurnStoreError`; the adapter releases the claim and returns `ConversationTurnFailed`, leaving reconciliation to a later status lookup.
- The same authority inversion can occur when a runtime/session/model/tool error is selected before the outer failure handler but Cancel persists before `_record_failure()`: strict `mark_failed()` rejects `cancel_requested`, and the adapter again turns the public result into generic failure while leaving cancel_requested durable.
- The deep seam is the Store: one typed start decision should atomically choose running versus immediate cancelled, and one typed failure decision should atomically choose real failure versus a previously accepted cancellation. Conversation should never inspect exception text or reread to guess.
- Frontend `checkActiveTurnStatus()` already handles cancel_requested/committing and has operation-generation plus active-Turn guards. Only `chatFeedback()` withholds the manual button from those phases; the fix should extend that single eligibility set and keep all timer prohibitions intact.

## Deterministic RED and typed GREEN

- A `StartGateStore` Event seam held the original request between durable `accepted` and `mark_running()`. Cancel returned `cancellationAccepted=true`, Runtime count stayed zero, and the pre-fix original request deterministically produced `(ConversationTurnFailed, cancel_requested)` instead of `(ConversationTurnCancelled, cancelled)`.
- `TurnStartDecision(record, execution_started)` now makes the start boundary atomic. `accepted` starts execution; `cancel_requested` is converted to `cancelled` under the Store lock and returns a typed non-start decision.
- `TurnFailureDecision(record, failure_recorded)` performs the same authority selection for normal exception paths. A durable pre-commit cancel becomes `cancelled` and cannot be overwritten by `failed`; without cancellation, the original failure behavior remains unchanged.
- Conversation consumes only typed decisions. It does not match Store exception strings or perform a racy status reread to infer cancellation.
- Deterministic coverage includes Runtime factory, Session creation, Model/Tool/runtime failure, cancel-first versus commit-first ordering, terminal immutability, repeated Cancel, HTTP `turn_cancelled`, and zero Runtime/Session/Run side effects at the accepted boundary.

## Frontend recovery and browser evidence

- `chatFeedback()` now offers the existing one-shot `检查状态` action for `cancel_requested` and `committing` as well as the previous recovery phases. No timer, polling, resend, SSE, WebSocket, or background execution was added.
- Node behavioral extraction proves cancel-requested→cancelled and committing→completed recovery and confirms stale responses cannot overwrite a newer Turn or Session.
- At 1280×900 an isolated real Gateway verified normal completion, accepted-boundary cancellation, manual cancel-requested recovery, commit-wins manual recovery, transport loss, process restart, and abandoned-running→interrupted reconciliation. The three columns measured 208/682/380 px, viewport and document width were both 1280 px, and horizontal overflow was false.
- The page console returned no warnings/errors. DOM safety checks found no `Bearer`, `/Users/`, `/private/`, fixture system prompt, `[object Object]`, or machine path. The restart draft remained available and no request was automatically resent.

## Final certification

- Focused product/baseline matrices passed; the broad compatibility matrix passed `315` tests. Installed-wheel build/install/Gateway smoke passed `9` tests, including Chat turn/cancel/status, Session, linked Run, restart reconciliation, health, and packaged assets.
- Official evaluator passed 108 cases with 37 confirmed gaps, zero remote calls, and `evaluation_passed=true`. Accepted gold remained SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime ns `1784135857000000000`.
- Both authoritative full suites passed: `2218 passed, 2 skipped, 3 warnings` in 118.89s and 119.05s. Scoped Ruff, py_compile, full compileall, and both formal production JavaScript checks passed. Dependencies remain `[]`; the read-only repository-wide Ruff count remains 686 pre-existing findings.
- v17 manifest SHA-256 is `2ac1d7185488dd1008407e4711fc3777213dcc1cd405e104f44bf6ca20206857`. Parent is v16; changed files are exactly `minicode/conversation.py`, `minicode/conversation_turn_store.py`, and `minicode/web/static/assets/app.js`; added/removed sets are empty; every v1-v17 pin and all 33 current hashes pass.

---

# MiniCode Dashboard Batch 6B-2B Cooperative Cancellation Notes

## Baseline and RED evidence

- The pre-edit suite first reproduced the sandbox's localhost denial; the identical
  permitted run passed `2144 passed, 2 skipped, 3 warnings in 106.69s`.
- Active v15 matched its deterministic candidate and all 30 protected files;
  every v1-v15 manifest integrity pin was true. Accepted semantic gold was
  `5629d6...fdd3b`, 3,033,592 bytes, mtime ns
  `1784135857000000000`; dependencies were `[]`.
- Deliberate REDs exposed 5 missing Store transitions, 2 missing token-module
  imports, 6 Agent token/checkpoint gaps, and the expected Conversation, strict
  HTTP, and frontend state-machine failures. No production edit preceded those
  failing contracts.

## Implemented authority boundaries

- `turn_cancellation.py` owns only one process-local Event token per live Turn.
  The durable Turn Store owns `cancel_requested`, `committing`, and `cancelled`.
- `begin_commit()` is the atomic race gate. A persisted cancel request refuses
  commit; a running Turn atomically enters committing and then defeats late
  cancellation. Session markers still decide restart completion.
- Optional Agent/Runtime tokens add safe checks around Model, Tool, concurrent
  batch, retry/recovery, and final-return boundaries. The `None` path is an exact
  no-op for Headless, TUI, classic CLI, and `/run`.
- A cancelled Turn saves no Assistant or new Session state. RunJournal records
  best-effort interrupted / `execution_cancelled`, but is never authoritative.
- Strict `POST /api/v1/chat/turns/{turnId}/cancel` accepts only `{}` and returns a
  fixed allowlist. Frontend operation generations prevent late POST/status/cancel
  responses from overwriting newer durable truth. There is no polling or resend.

## Certification evidence

- Focused final matrices passed: token/Store 20, Agent 44, Conversation 40,
  cancellation 47, Chat HTTP 54, compatibility 157, Dashboard/read model 194,
  wheel 9, semantic/baseline contract 46.
- Scoped Ruff, py_compile, compileall, and production JavaScript checks passed.
  The repository-wide read-only Ruff audit still reports 82 unrelated existing
  findings.
- v16 protects 33 files, matches candidate/current hashes, and preserves every
  v1-v16 pin. SHA is `80fa4db12cb43f904a0d89cf0d32df7bd389fda1001c55b6447d7d1a5355decb`.
- Official semantic evaluation remains 108 cases / 37 gaps / zero remote calls /
  pass. Projection and per-case fingerprints remain `b9fabf...1bbd60` and
  `b73da4...8667`; accepted gold SHA/size/mtime are unchanged.
- Isolated browser acceptance passed normal, Model/Tool cancellation, duplicate
  disablement, cancel-wins, commit-wins, restart reconciliation, completed marker
  recovery, completed-unavailable, explicit resend with a fresh Turn ID, XSS,
  eight main routes, five Memory routes, no layout overlap/overflow, and zero
  warning/error logs. The Dock says
  `synchronous · recoverable · cancellable · no live updates`.
- Evaluator-after final full suite passed `2202 passed, 2 skipped, 3` existing
  benchmark-marker warnings in `118.19s`. Temporary-resource cleanup is recorded
  after the listener/filesystem audit: the Browser viewport was reset and tabs
  finalized, the Gateway stopped, port 18765 had no listener, and every
  `minicode-6b2b*` task-owned temporary path was removed.
# MiniCode Dashboard Batch 7A.1 Working Notes

## Phase 1 — immutable v18 starting baseline

- The 759-line Batch 7A.1 contract and required Batch 7A/change-feed/Gateway/HTTP/frontend/persistence/certification seams were inspected before production edits. No `AGENTS.md` is present in or above this workspace.
- Authoritative pre-edit full suite with an isolated real-home guard: `2252 passed, 2 skipped, 3 warnings in 121.26s`; the three warnings remain the existing benchmark markers.
- Active v18 verifier: `candidateMatches=true`, `currentFiles.matches=true`, 35/35 protected files, exact five-changed/two-added v17→v18 lineage, and every v1–v18 `manifestIntegrity` flag true.
- v18 manifest SHA-256 is `515d3cacd96365bc09bfb608df59ff1bfcc4b0c10cff1d1e4e114cb8ef6ecee5`.
- Official semantic evaluator passed 108 cases with 37 confirmed gaps, Phase 3B true, zero remote calls, and `evaluation_passed=true`.
- Accepted semantic gold remains SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime_ns `1784135857000000000`.
- Scoped Ruff passed. Repository-wide Ruff remains exactly 686 pre-existing findings (448 F401, 73 F541, 57 F841, 46 F821, 19 E402, 18 E741, 12 E712, 7 F811, 4 E702, 2 E731).
- Explicit py_compile, full `compileall -q minicode scripts tests`, and every formal JavaScript `node --check` passed. Runtime dependencies remain `[]`.
- Baseline wheel succeeded with `pip wheel --no-deps --no-build-isolation`; the environment does not provide the `build` module and isolated pip build dependency resolution cannot reach an index, so the repository's existing no-build-isolation packaging path is the authoritative offline wheel path.
- Authority map remains unchanged: Change Feed owns bounded content-free revision discovery; RunJournal/Session/Turn/Memory/Skill/MCP persistence remains authoritative; the Event Stream may compare only `status + revision` and emit invalidation/reset/liveness transport facts.

## TDD progress

- Pending first Event Stream RED.

---
# Batch 7B baseline (2026-07-20)

- Required frontend, SSE/Change Feed/Gateway sources, focused tests, Batch 7A /
  7A.1 docs, v19 baseline implementation, and official semantic evaluator were
  read before production edits.
- Untouched full suite: `2296 passed, 2 skipped, 3 warnings in 133.60s` under
  isolated `HOME=/tmp/minicode-batch7b-baseline-home`.
- Active production freeze: `memory-retrieval-production-v19`, 36/36 current
  files, candidate equality true, all v1-v19 pins/lineages true.
- v19 manifest SHA-256:
  `9c48c5c0f02f48c49a31411292b1d65b1e52de4667c2048477343ff64eaa82c6`.
- Accepted semantic gold remains SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime 1784135857 seconds. Runtime dependencies are `[]`.
- Pre-edit protected backend hashes: Gateway `1277b5...e5e7`, HTTP
  `b7a065...530d`, Change Feed `743a75...3e1d`, Event Stream
  `b95430...3e8`. Formal app.js is `360f2a...55d`.
- Existing deep seams: `refreshChangedResources(resourceNames)` is the sole
  REST-store invalidation dispatcher; `createLiveRefreshController()` is a
  dependency-injected polling adapter with AbortController, generation,
  visibility, backoff, and one-in-flight guards. Batch 7B can coordinate these
  rather than introduce another store authority.

---

# MiniCode Dashboard Batch 8C-1.1 Working Notes

## Phase 1 — immutable v26 starting baseline

- The complete 528-line Batch 8C-1.1 contract was read before source/test edits. It limits this batch to genuine no-write Memory Approval snapshot/revision/GET hardening; formal frontend, Permission/File Review, and Batch 8C-2 are excluded.
- Pre-edit full suite with real loopback permission: `2500 passed, 2 skipped, 3 warnings in 166.82s`; the warnings are the three existing unregistered benchmark markers.
- v26 manifest SHA-256 is `b44abf36befb98723b26036530296f8675a0d92ae59884956767b352445ed936` (6,328 bytes). Earlier manifest bytes/pins are the immutable parent evidence.
- Accepted semantic gold `artifacts/memory-retrieval-semantic-gap-baseline.json` is SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime_ns `1784135857000000000`.
- Formal frontend pre-edit hashes: index `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`; app.js `1508700d7d75d99f6a5c166172c89f761e81100bc6d89f6b2873731c1e747ccb`; styles.css `092dd3279f613f802a050276db833d386c30663e6277fb5152597d966149d3e8`; cost-format.js `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`.
- This workspace intentionally has no `.git`; `git status --short` returned `fatal: not a git repository`. No Git repository was initialized and no commit will be created.

## Audited original read/write call graph

- `MemoryApprovalAuthority.snapshot()` calls `_manager()`, which validates candidate symlinks, constructs ordinary `MemoryManager(project_root=workspace)`, validates its resolved scope roots, and calls `pending_entries()`.
- `MemoryManager.__init__()` constructs `MemoryStoreCoordinator(MINI_CODE_DIR)` and unconditionally enters `transaction()`. `_open_lock_file()` creates `MINI_CODE_DIR` and a private persistent `memory-store.lock`, so an empty snapshot is already a writer before parsing any Memory.
- Under that transaction, `_load_all()` runs `_load_approval_audit()`, `_load_scope()`, and `_auto_recover_scope()` for all scopes. `_load_scope()` persists in-memory legacy safety/approval migration via `_save_approval_audit()` and `_save_scope()`; invalid structured JSON invokes `_recover_entries()`, which writes `.json.bak`, then may save recovered state; Markdown fallback parses and calls `_save_scope()`.
- `_apply_loaded_entry_safety()` mutates safety/approval/hash/actor/timestamps and appends migration audit records. `_auto_recover_scope()` invokes `_recover_scope()` for invalid/duplicate/empty entries; recovery removes/fixes entries and calls `_save_scope()`.
- `_save_scope()`/`_save_approval_audit()` ensure scope directories, then `_atomic_write()` creates same-directory temporary files and replaces `memory.json`, `MEMORY.md`, or `approval_audit.json`.
- `revision()` delegates to `snapshot()`, so it has the same side effects. The real GET delegates to the authority and therefore also has them.
- `decide()` must retain the write loader: ordinary manager construction plus `coordinated_write(tuple(MemoryScope), commit)` gives process RLock → POSIX flock → reload/stale authority validation → typed mutation/audit/atomic save. Its public item/revision recomputation is the stale fence and must remain authoritative.
- `DashboardReadModel._read_memory_scope_for_page()` is an existing no-save parser with bounded, configured-root file validation and in-memory safety hardening, but it skips invalid entries and does not implement the exact write-loader compatibility migration. It is useful design evidence, not safe to call directly as the Approval authority without tightening failure semantics and avoiding a core→web dependency.

## Selected deep-module seam

- Keep the external `MemoryApprovalAuthority` interface unchanged. Add one private read-only loader inside the authority module that owns paths, bounded file reads, strict validation, deterministic in-memory legacy interpretation, duplicate detection, audit validation, fallback parsing, and fail-closed symlink handling.
- The loader will return only typed `MemoryEntry` values; it will not construct `MemoryStoreCoordinator`/`MemoryManager`, acquire a lock, create a directory, recover, migrate durably, save, delete, or own any persistence method.
- `snapshot()` and `revision()` use this read module. `decide()` continues to use the existing write manager and reuses `_public_item()`/`_review_revision()` so GET and POST share projection and fencing semantics rather than duplicating them in HTTP.

## Batch 8C-1.1 final implementation and certification

- The production delta is exactly `minicode/memory_approval.py`. Its private read seam performs bounded no-follow directory-relative reads, validates directory identity and regular files, parses current JSON or legacy Markdown without persistence, applies deterministic typed compatibility interpretation in memory, validates approval hashes/audit shape, rejects duplicate IDs globally, and fails closed with `memory_approval_unavailable`.
- Empty snapshot/revision/real GET create no MiniCode root or lock. Current and legacy sources preserve tree entries and every file SHA/size/mtime_ns. Malformed JSON/entry/audit, duplicate IDs, hash mismatch, symlinked roots/files, directories, and FIFOs neither recover nor write. FIFO reads cannot block because the descriptor is opened nonblocking and then rejected as non-regular.
- GET review revisions are accepted by the existing POST decision loader for missing-policy, pre-approval, and Markdown fallback records. Stale content remains 409; idempotent and opposite-terminal behavior is unchanged. A held writer flock does not block the read seam: it sees the complete old file before atomic replacement and the new revision after release.
- Focused finals: Memory policy/Retrieval/Injection/Pipeline/curator/reflection `305 passed`; approval authority/HTTP/cross-process `66 passed`; Gateway/Chat/Permission/Session/SSE compatibility `365 passed`; v27 baseline `139 passed`; packaging/wheel `9 passed`.
- v27 manifest SHA-256 is `18ad99488f7a73e71bbe30011d9c86a8de6ab077b5d1be8790718c6ffac14013`; parent v26 remains `b44abf36befb98723b26036530296f8675a0d92ae59884956767b352445ed936`. Exact changed set is `minicode/memory_approval.py`, with no additions/removals and 50 protected files. Default verifier reports candidate/current matches and every v1-v27 integrity pin true.
- Official evaluator passed 108 cases / 37 gaps / Phase 3B true / zero remote calls. Accepted gold remained SHA `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime_ns `1784135857000000000` before and after.
- Final counted full suites passed `2525 passed, 2 skipped, 3 warnings` in 171.02s and 169.03s. Scoped Ruff, targeted py_compile, full compileall, and all formal JavaScript syntax checks passed; pyright/mypy are unavailable. Runtime dependencies remain `[]`.
- An explicit 917,295-byte wheel build was inspected, isolated-installed outside the source tree, and smoke-tested with an isolated HOME. The approval module and Dashboard assets were present, empty snapshot/revision created no `.mini-code`, and all task-owned wheel/venv/HOME temporary resources were deleted.
- Formal frontend hashes remained exactly the four Phase 1 values. No new browser visual result is claimed because frontend changes were prohibited. Permission/File Review and its reject-only Tool approval defect remain a separate task.

# MiniCode Dashboard Batch 8A-2.2 Working Notes

## Phase 1 baseline and producer audit

- Untouched production full suite: `2525 passed, 2 skipped, 3 warnings in 169.57s`; warnings are the existing benchmark markers.
- Active v27 verifier reports `matches=true`, `candidateMatches=true`, current 50/50, exact v26→v27 Memory Approval delta, and every v1-v27 integrity flag true. Manifest SHA is `18ad99488f7a73e71bbe30011d9c86a8de6ab077b5d1be8790718c6ffac14013`.
- Accepted semantic gold is SHA `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime_ns `1784135857000000000`. Frontend hashes are index `43432f...ae0b`, app.js `150870...ccb`, styles `092dd3...d3e8`, cost formatter `194e6b...2916`; runtime dependencies are `[]`.
- All three real Tools resolve the input through `resolve_tool_path()` and then call the shared `apply_reviewed_file_change(context, original_input, resolved_target, content)`. The old producer built headers from `original_input`; Broker independently resolved `targetPath` but could only redact literal workspace/HOME strings inside the completed diff.
- Deterministic v27 RED: real absolute write/edit/patch each produced `reviewable=false`, `choices=[deny_once]`; the relative control passed. A macOS `/var` alias resolving to the same `/private/var` target stayed `reviewable=true` while leaking the complete `/var/...` header, proving both deny-only and disclosure variants.
- The selected deep seam keeps the caller interface stable and derives the label inside `file_review.py` from resolved `context.cwd` and target. It validates a nonempty normalized relative POSIX label and rejects absolute, dot-segment, `file://`, and Unicode control/format/surrogate categories with a fixed path-free error. Diff body bytes remain untouched for Broker redaction/truncation.
- A first combined Tool/Permission/HTTP matrix passed 76 non-network tests; all remaining 14 setup errors plus one failure were the sandbox denying real loopback `socket.bind()`. The unchanged HTTP matrix is rerun with localhost permission.
# Batch 8A-2.2 completion

- Real Tool RED/GREEN coverage fixed absolute and alias Diff labels at the
  shared file-review boundary and proved producer-only normalization was not
  enough for absolute/control/private-key body classification.
- v28 is active at SHA
  `75c71d1d740b35f530965d7f797f4bbe3ceafb019129be3ee4d73d9256b453e5`;
  v27 and accepted semantic gold remain byte-identical, 50/50 current sources
  match, and v1-v28 integrity is true.
- Final complete suites pass twice at 2,572 passed / 2 skipped / 3 existing
  warnings. Official semantic evaluation passes 108 cases / 37 gaps / remote
  0. Wheel, scoped static checks, and all focused compatibility matrices pass.
- Isolated 1280×900 in-app browser acceptance covered real safe Allow, Deny,
  sensitive deny-only, Cancel, restart recovery, eight main routes, five Memory
  routes, no overflow/overlap, zero console warning/error, and no local-path,
  secret, or object-string disclosure. All temporary resources were removed.
- Batch 8C-2 remains untouched and is now the next eligible batch.

# MiniCode Dashboard Batch 8A-2.2.1 Working Notes

- Untouched v28 certification recovered after one documented Phase 2B timing
  flake: `2572 passed, 2 skipped, 3 warnings`. v28 SHA was
  `75c71d1d740b35f530965d7f797f4bbe3ceafb019129be3ee4d73d9256b453e5`;
  accepted gold and four frontend hashes matched the Batch 8A-2.2 record.
- The initial RED was 18/18: splitline and format/zero-width inputs remained
  reviewable, and the surrogate path could not safely serialize. The extended
  matrix caught explicit-range U+2065 beyond Unicode category `Cf`.
- Production changed only the shared file-review producer and Permission
  projector. The producer classifies `before` and `after` before transformation;
  the projector independently classifies raw review values before LF-only
  parsing. Both return the same fixed content-free marker.
- Final file-review/Tool/Broker/HTTP/lifecycle matrix: 238 passed. Baseline and
  evaluator tests: 181 passed. Installed-wheel real Gateway hardening smoke:
  passed. Scoped Ruff, py_compile, compileall, and production JavaScript syntax
  passed; pyright/mypy were unavailable.
- First and evaluator-after full suites each passed `2773 passed, 2 skipped, 3
  warnings`; the warnings are only the existing benchmark markers. The official
  evaluator passed 108/108 cases, 37 confirmed gaps, Phase 3B true, and zero
  remote calls.
- v29 SHA is
  `e43777832841629549d180e039d40ac54209c5f15a3581e9bdf09b308592d4d1`,
  parent v28, exact two-file change, no add/remove, 50/50 current matches, and
  v1–v29 integrity true. Gold SHA/size/mtime and frontend bytes are unchanged.
- Browser acceptance at 1280×900 used a real isolated Gateway/broker/
  PermissionManager/write_file. Safe content wrote exactly once. VT, NEL,
  U+2028/U+2029, bidi, zero-width, BOM, Cancel, restart, and fresh-after-restart
  wrote nothing. Old approval Allow after restart returned 404
  `permission_not_found`. All 8+5 routes, layout, console and DOM leak checks
  passed.
- Batch 8C-2 was not entered. Next authorized work is Memory Approval Store +
  UI.

# MiniCode Dashboard Batch 9D-1B Agent Observatory Working Notes

## Frozen v34 starting evidence (2026-07-24)

- Active verifier: `memory-retrieval-production-v34`; protected files `56/56`,
  current and candidate both match, and all `34/34` manifest integrity pins
  pass.
- Accepted semantic gold is unchanged: SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime_ns `1784135857000000000`.
- Formal v34 frontend hashes before this batch: `index.html`
  `d00d29b0df3cd2f284a524edef6ad7f5a22e541aa2c9a2740ddc1ea907b01afa`;
  `styles.css`
  `59eb5cab22b6a705ce2fee135635552b3acbc5d39f72d661e774d8c2a8ed1ed4`;
  `app.js`
  `5082899135487a2722830d365df8107119788ab3745ad01bc783840c80b3b91f`;
  `cost-format.js`
  `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`.
- v34 manifest SHA-256 is
  `3136e096a97192de5078882523106f5179cb20a3e9885c050fd187038f815cbb`.
  Runtime dependencies remain `[]`. This workspace has no Git repository, so
  no commit/status operation is available and no repository will be created.

## Audited production seams

- The user selected A / Agent Observatory. Formal scope is Batch 9D-1B:
  Overview, Runs, Sessions and all existing Memory subroutes. Skills,
  Connections, Ops and System remain 9D-1C.
- v34 Shell landmark IDs, all eight hash routes/count hooks, Chat Dock,
  Permission, deletion, Memory approval, Session selection and responsive
  resizer identities are frozen. The refactor must be passive around them.
- Snapshot is authoritative for workspace inventory and aggregated usage, but
  intentionally contains no Run event list. Runs list/detail already exposes
  bounded, redacted canonical events through `/api/v1/runs` and
  `/api/v1/runs/{id}`.
- Runs-page filter state cannot truthfully drive Overview. A separate volatile
  `observatoryStore` will read the existing list/detail contracts, select the
  newest running Run or otherwise the newest retained Run, and render honest
  loading/empty/partial/error states. It adds no backend, persistence,
  EventSource, timer or poller.
- Existing `runs` SSE invalidation currently refreshes Runs only while that
  route is visible. On Overview it will refresh the Observatory projection;
  elsewhere it will continue invalidating the next Runs read. REST remains
  authoritative.
- Existing `runEventSummary`, metric formatters, status pills and escaped text
  are sufficient to compose the Activity trace and Signals without exposing
  Prompt, messages, tool input/output, payloads or absolute paths.
- Runs and Sessions already use master/detail authority seams; 9D-1B can
  preserve their handlers while changing visual hierarchy. Memory has six
  subroutes and must wrap every loading/error/content branch without changing
  approval, deletion, runtime trace or lifecycle behavior.

## Implemented presentation and browser findings

- The formal Shell now follows Direction A: graphite rail, warm editorial main
  surface, cool Chat Dock, route-specific editorial header, eight local
  navigation glyphs and the unchanged semantic/action IDs.
- Overview composes a real workspace instrumentation band, newest active-or-
  retained Run focus, six latest redacted Activity events, aggregate Signals
  and five-item Recent Work ledger. Empty, loading, partial and error states
  never invent fallback facts.
- Runs, Sessions and every Memory subroute share `core-page` Observatory
  composition while keeping their existing master/detail, filters, deletion,
  approval and runtime trace handlers.
- `#memory` now normalizes its null subroute to Overview. `loadOps()` refreshes
  both direct Ops and Memory/Lifecycle consumers; no HTTP or Store contract
  changed.
- Browser fixes from the first pass: Observatory stacks at 900 px to avoid
  internal overflow with a visible rail, and the nav reopen button is hidden
  while a narrow full-width Dock overlay is open.
- Real Browser measurements: 1920 px = nav 216 / main 1304 / Dock 388 with a
  675/489 Observatory grid; 1280 px retains all three columns; 768 px has zero
  document/main/view overflow after the 900 px stack; 375 px has zero overflow,
  single-column band/grid, both reopen controls, and a 375×812 Dock whose
  composer bottom is exactly 812.
- All eight primary routes and all six Memory routes loaded without residual
  loading states or page-console warnings/errors. One Statsig timeout came
  from the Codex Browser host telemetry, not the Dashboard page; `dev.logs()`
  for the page remained empty.

# MiniCode Dashboard Batch 8D-1 Working Notes

## Scope and invariants

- Add only `ConversationDeletionAuthority` and `ProjectMemoryDeletionAuthority`, four strict loopback HTTP routes, any minimal writer/fence coordination required to make them truthful, tests, packaging, docs, and v31 certification.
- Conversation deletion removes the target Session last, after only its linked terminal Turns and terminal Runs. Any active/committing/writer-owned work blocks before deletion.
- Project Memory deletion is current-Workspace Project scope only and removes the target entry, its approval-audit records, Project backlinks, and rebuilt Project indexes while preserving User/Local and unrelated Project data.
- GET previews are fully read-only. POST is one explicit revision-bound action; no destructive automatic retry, frontend work, new EventSource, polling, database, queue, or runtime dependency is allowed.
- Strict response shapes are content-free and path-free; raw exceptions, lock owners, content hashes, titles, messages, prompts, Run events, Tool payloads, and credentials never cross the authority or HTTP interface.

# MiniCode Dashboard Batch 8D-2 Working Notes

## Untouched v31 baseline (2026-07-23)

- Pre-edit full suite: `2845 passed, 2 skipped, 3 warnings in 187.05s`; the
  warnings are the three existing unregistered benchmark markers.
- Active verifier: `memory-retrieval-production-v31`, `matches=true`,
  `candidateMatches=true`, current protected files `54/54`, and v1-v31
  manifest integrity all true.
- v31 manifest SHA-256:
  `d0ea9a10ccd45d6f8e7807f92acfc38afce801f22e8be0967897653aed82fbae`.
- Accepted semantic gold: SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime_ns `1784135857000000000`.
- Formal frontend hashes before edits: index
  `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`;
  app.js
  `3673a3e0d34f718611cea826afe5bdb4cbb8fbfd8711498721fe17cac9e03b80`;
  styles.css
  `a825a19437f1b532195ce6c9785313c08054f8c5830103c0a30474d9ba029d75`;
  cost-format.js
  `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`.
- Runtime dependencies are exactly `[]`. The workspace has no Git metadata and
  no repository will be initialized.
- The certified v31 backend is the sole deletion authority. Batch 8D-2 will
  consume its four routes and will not alter its semantics or source bytes.

## Phase 1 evidence in progress

- Untouched full suite with real loopback permission: `2788 passed, 2 skipped, 3 warnings in 176.87s`; warnings are only the three existing unregistered benchmark markers.
- Active parent is v30: verifier `matches=true`, `candidateMatches=true`, current protected files `50/50`, every v1–v30 integrity pin true. Manifest SHA-256 is `55654b2b979812440514686b44c5bf09b5a0527a59709d37907ffb7ffd9c5edd`.
- Accepted semantic gold before edits: SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, size `3033592`, mtime_ns `1784135857000000000`.
- Formal frontend before edits: index `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`; app.js `3673a3e0d34f718611cea826afe5bdb4cbb8fbfd8711498721fe17cac9e03b80`; styles.css `a825a19437f1b532195ce6c9785313c08054f8c5830103c0a30474d9ba029d75`; cost-format.js `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`.
- Runtime dependencies are exactly `[]`. The workspace has no `.git`; no repository will be initialized.

## Audited storage and writer facts

- Session identity is `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`. `save_session()` orders `_SESSION_STORAGE_LOCK → session_store_transaction(flock) → disk revision check → atomic base/delta write → index replace`; a full base increments `persistence_generation` and only then cleans deltas. Existing `delete_session()` uses the same two locks but removes base/deltas before rewriting the index and treats a missing base as immediately absent, so it cannot by itself reconcile index/delta orphans.
- `ConversationTurnStore` stores one bounded JSON record per `turn_<32hex>` under `dashboard/workspaces/<workspaceId>/turns`. Terminal statuses are exactly `completed/failed/interrupted/cancelled`; non-terminal are `accepted/running/cancel_requested/committing`. It has a process-local root RLock and atomic replace, but no cross-process store flock or deletion interface. `attach_session()` is the first durable point associating a Turn with a Session.
- `RunJournal` stores one directory per `run_<32hex>`, with atomic metadata, append-only events and an exclusive `.writer.lock`. Terminal statuses are the existing `completed/failed/interrupted/cancelled`; `queued/running` are non-terminal. The writer lock is removed only on terminal transition. Retention has a safe terminal-directory delete seam, but there is no single-Run/session delete interface and the index lock is only best-effort.
- Real Chat ordering is claim accepted → running → load/create Session → attach Session to Turn → create linked queued Run/writer → attach Run → execute → committing → Session save → Turn completed → Run terminal on observation exit. Therefore a deletion fence must be honored at Session save, Turn association and linked Run creation; a scan-only preflight followed by raw unlink is racy.
- Memory durable writers already order `MemoryStoreCoordinator process RLock → memory-store flock → reload/revision validation → mutation → atomic scope/audit writes`. Existing `delete_entry()` removes only one entry and saves the scope; it does not remove approval-audit records or Project backlinks. Memory indexes are in-memory and rebuilt by `MemoryFile.delete_entry()` before `memory.json`/`MEMORY.md` are replaced.
- The read-only `MemoryApprovalAuthority` already provides bounded no-follow Project/User/Local entry parsing without constructing `MemoryManager`, but its read projection intentionally discards audit details. The deletion preview can reuse its audited entry parser internally and add a bounded audit-only projection; POST must revalidate inside the existing coordinated Project writer.
- Existing HTTP write adapters already provide strict duplicate-key JSON parsing, 1 KiB length caps, MIME/charset checks, same-origin loopback fencing and fixed safe errors. `MiniCodeWebHandler.do_GET()` currently sends `/api/v1/sessions/*` to Session detail, so the deletion GET routes must be matched before that generic branch. Gateway remains the POST composition point.
- Change Feed already observes Session base/delta/index, Turn files, Run metadata/events/index and Project `memory.json`/`MEMORY.md`. It does not observe `approval_audit.json`; orphan-audit-only Project cleanup therefore requires adding that one content-free stat fact. No EventSource/schema change is needed.

## Selected deep modules and lock order

- A content-free deletion ledger/coordination seam will own workspace-scoped conversation fences and bounded receipts. Writers acquire its coordination guard before entering their existing write seam, check the persistent fence, then release after the association/save/create fact is durable.
- Conversation deletion order: coordination guard + fence creation/reuse → authoritative rescan/revision check → reject active Turn/Run/writer → delete terminal Turns → delete terminal Runs → delete Session representations last → verify all absent → bounded receipt → clear fence.
- Project Memory deletion stays inside the existing Memory RLock/flock transaction: authoritative reload/revision check → remove backlinks and target from the same Project in-memory file → rebuild indexes → atomically replace Project memory representations → remove target audit records and save audit → verify/retry orphan residue. User and Local scopes are never mutation inputs.

## Batch 8D-2 final implementation and certification

- `app.js` now owns two independent volatile deletion stores, exact/bounded
  Conversation and Project Memory preview/result validators, one shared
  accessible dialog, fresh-preview/one-shot-POST flow, fixed safe errors,
  stale/partial/lost-response semantics, tombstones and generation fencing.
- Conversation completion fences Session/Run/detail publications, clears only
  the matching stored selection, switches a matching Dock continuation to new
  mode, preserves the draft, and GET-reconciles Sessions/Runs/snapshot/Turn
  authority. Project completion fences Memory/Approval publications, cancels
  stale Approval identity without deciding, preserves filters, and
  GET-reconciles Memory/Approvals/snapshot.
- Existing sessions/memory SSE invalidations can only request a preview GET.
  There is still one EventSource and no new polling, timer, transport or POST
  retry path.
- CSS adds only low-saturation Waku management cards/dialogs, responsive
  wrapping/stacking, visible focus and reduced motion. Final review aligned the
  narrow footer's visual order with DOM/Tab order and added an executable
  focus-loop test.
- Isolated Browser acceptance at 1280x900 and 700x900 exercised eight main
  routes, six Memory routes, ready/busy/partial, both real deletions,
  cross-page convergence, Dock/draft preservation, Esc/focus return and safe
  disclosure. Console warning/error count was zero and no horizontal overflow,
  private path, raw body or object string was found.
- Dropped-response/network behavior could not be safely synthesized by the
  in-app Browser; deterministic real-`app.js` tests prove lost response, stale,
  partial and SSE no-auto-POST behavior. This is not reported as a browser
  simulation.
- Broad focused matrix: 574 passed. Final deletion/Web/v32/semantic focused
  rerun: 267 passed. Static Ruff, py_compile, compileall and all formal
  JavaScript syntax checks passed; pyright/mypy were unavailable.
- Final wheel SHA was
  `b7e5ccd3304d552fc9c2d9d38d93bd92090877b84baf57fde8c737371b0ae838`.
  Its archive contains the four formal assets and no prototype/task fixture.
  An isolated install served exact app/CSS hashes and completed both real
  deletion routes with adjacent/unlinked/User/Local preservation.
- v32 manifest SHA is
  `9680f6f4bb61d3489a98fd63cff01d99f6a5af2c98891befbfb6c513fc023fb1`.
  Exact v31->v32 delta is app.js and styles.css only, no add/remove; v31 remains
  byte-identical and v1-v32/current/candidate are all true.
- Official evaluator passed 108 cases, 37 gaps, Phase 3B true and remote calls
  0. Gold SHA/size/mtime_ns and dependencies remain unchanged.
- Final complete suites on the frozen production state passed `2855 passed, 2
  skipped, 3 existing warnings` in 188.35s and 188.25s, with the official
  evaluator between them.
- One evaluator-after full attempt hit the repository's frozen Phase 2B timing
  gate once; the isolated test passed immediately and the repeated full suite
  passed without changing a threshold or unrelated source.

---
---

# Persistent Memory and Skill Routing Audit Notes

## Scope and working-tree guardrail

- Review-only task; no production changes are authorized.
- Existing uncommitted files at audit start:
  `.mini-code-memory/MEMORY.md`,
  `.mini-code-memory/approval_audit.json`,
  `.mini-code-memory/memory.json`,
  `.mini-code-memory/pipeline_state.json`,
  `minicode/cybernetic_orchestrator.py`.
- Evidence must distinguish committed behavior from those in-progress edits.

## Evaluation model

A genuine self-evolution loop should expose:

1. observation with stable provenance;
2. candidate extraction with explicit scope and confidence;
3. validation against actual task outcomes;
4. controlled promotion/versioning;
5. context-sensitive routing with abstention;
6. downstream measurement and counterfactual comparison;
7. decay, conflict resolution, rollback, and deletion propagation.

Accumulation without outcome attribution is persistence, not self-evolution.

## Persistent-memory lifecycle findings

- The production loop is connected: `agent_loop.py` wires `MemoryPipeline`,
  injects canonical retrieval results at task start, writes a reflection at
  task end, and applies outcome feedback only to actually rendered IDs.
- Automatic reflections are deliberately
  `USER_REVIEW_REQUIRED`; therefore a candidate cannot become injectable
  without human approval. This is a sound safety boundary but creates a hard
  throughput bottleneck when candidate precision is low.
- The current project store has one reflection entry, now rejected, with zero
  injections and zero outcome-feedback observations. Thus the live project
  currently has no active persistent-memory learning loop.
- The one rejected candidate contains two near-duplicate transient
  `web_search` failure claims plus truncated retry-system text. The rule value
  gate treats any specific single-trace `error_pattern` as a reusable durable
  signal even while adding the limitation that recurrence is unestablished.
  This sends first-occurrence operational noise to the user's review queue.
- Canonical retrieval weights lexical evidence at `0.72` and learned
  usefulness at `0.005`. Moving usefulness from -1 to +1 changes the final
  score by at most `0.01`; outcome feedback therefore has almost no ranking
  authority.
- `MemoryPipeline.maintain()` calls `curator.on_task_complete()`, but the
  orchestrator invokes `maintain()` from `step_end`. A nominal
  “every 10 tasks” curator cadence is therefore every 10 agent steps and can
  mutate memory mid-task.
- The repository contains 53 `advanced_memory.json` entries in legacy/session
  files, but no production code reads `advanced_memory.json`. The active
  `MemoryManager` reads `memory.json`; these artifacts are an orphan memory
  namespace, not usable agent knowledge.
- Reflection/retrieval evaluation is unusually extensive and explicit about
  its limits: the Phase 2B datasets are synthetic; the semantic-gap report
  records 37 confirmed lexical/cross-language gaps; the hybrid adjudication
  rejects the attempted semantic gate for production.

## Skill-routing and evolution findings

- The user's exact Chinese audit request parses as `unknown/unknown` with
  confidence `0.05`. The router ignores intent confidence and selects five
  skills anyway, including TDD, pytest debugging, and safe refactoring.
- The negative control “给我讲个笑话” produces almost the same routed list.
  With unknown intent, `_relevant_capabilities` returns all available registry
  domains/scopes; `_score_text` then finds those terms in Skill metadata. The
  router therefore treats system capability availability as task evidence.
- Chinese keyword extraction does not segment Chinese text, so whole clauses
  become keywords. Chinese review/memory/skill-routing patterns are absent.
  English manifests cannot match these clause tokens.
- The fallback path deliberately returns every discovered skill and ignores
  `top_k`; this increases prompt noise precisely when confidence is lowest.
- Routing is uncalibrated: no minimum score, top-1/top-2 margin, explicit
  abstention reason, or negative evidence. `ParsedIntent.confidence` and Skill
  `examples` are both ignored.
- Routing changes only the prompt's shortlist. The model still decides whether
  to call `load_skill`; there is no `skill.loaded` observation or per-skill
  outcome attribution. `skill.routed` records candidates, not actual Skill
  use, so successful/failed runs cannot train the Skill router.
- `FeedbackController.recommend_skill_update` only sets
  `tool_scheduler._pending_skill_update`; no code consumes that flag.
  `suggest_memory_persistence` calls `context_compactor._tool_budget.flush()`,
  but `ToolResultBudgetManager` has no `flush` method and the exception is
  swallowed. Both advertised positive-feedback actuators are no-ops.
- `propose_skill` is a static placement/frontmatter advisor. It does not mine
  repeated successful traces, create a candidate version, run a shadow
  evaluation, or promote/rollback a Skill.
- Skill content has no durable version, approval status, outcome metrics, or
  rollback pointer. Local discovery precedence exists, but self-authored
  evolution governance does not.
- The adjacent `SmartRouter` learns model-level aggregate statistics, not Skill
  routing. Its learner is not consulted by `route_and_switch`; outcome writes
  are batched by ten with no shutdown flush, and its cached model scores are
  not invalidated. The current uncommitted `feedback_path` change adds a
  destination but does not close any of those gaps.

## Validation

- Focused existing suite: `147 passed in 1.22s` across Skill router/discovery/
  proposal, Memory E2E/integration/regressions, orchestrator, and feedback
  controller tests.
- Passing tests confirm component mechanics but also encode two weak policies:
  fallback must return all Skills, and an ambiguous task may route from generic
  available-capability overlap.
- Production-equivalent routing probe after capability registration:
  - exact user query: `unknown/unknown`, confidence `0.05`; selected
    `minicode-study`, TDD, pytest debugging, safe refactor, README authoring;
  - “审查持久化记忆和技能路由”: `unknown/unknown`, confidence `0.0`; nearly
    the same list;
  - “给我讲个笑话”: `unknown/unknown`, confidence `0.0`; nearly the same
    list.
- Current project durable state: one rejected reflection, zero active entries,
  zero injections, and zero feedback observations.
- Outcome authority is inconsistent:
  `turn_outcome` marks any non-progress final text as success; `TaskState` and
  model-router feedback mark any turn with a tool error as failure; Memory
  feedback uses `turn_outcome`. The same Run can therefore reinforce Memory,
  penalize model routing, and be stored as a failed Task.
- Memory feedback applies the same whole-turn label to every rendered memory;
  it cannot distinguish helpful, unused, or harmful entries. This is correlated
  credit assignment, not causal learning.

## Synthesis

The system currently provides safe persistent context, deterministic
retrieval, static Skill recommendation, and rich observability. It does not yet
provide measurable self-evolution because the positive-feedback actuators are
not connected, Skill use is not observed, outcomes are inconsistent, and
learned Memory utility has negligible decision authority.

---

# Persistent Memory and Skill Routing P0 Repair Notes

## Implemented control-loop corrections

- Skill routing now abstains on unknown/no-evidence input, uses empty fallback,
  consumes bilingual intent aliases and Skill examples, and prevents
  capability/tool/directory/source compatibility from creating relevance.
- ASCII evidence uses token boundaries, so partial words such as `audit` in
  `auditing` cannot route an unrelated sibling Skill.
- Successful `load_skill` calls emit privacy-safe `skill.loaded` observations
  with stable identity and content digest through Run Journal and Dashboard.
- One canonical task outcome now feeds TaskState, auditor, Memory, model
  routing, and pattern feedback; recovered Tool errors no longer turn a
  successful task into a downstream failure.
- Curator maintenance runs once at task finalization. A lone unverified
  transient error is rejected before durable Memory review; verified recovery
  or independent same-task recurrence remains eligible.
- Fake memory-flush and queued-Skill-update actuators were replaced with honest
  observation-only logging.
- SmartRouter feedback is project-scoped under `.mini-code`, flushed at task
  end, invalidates cached scores, and survives restart without cross-project
  task-text or model-stat contamination. Learned reranking is constrained to
  two or more candidates in the same static tier, with at least three
  observations per candidate on the same coarse task profile.

## Verification

- Production-equivalent routing:
  - exact Chinese audit request -> `review/analyze`, confidence `1.0`, three
    directly evidenced Skills;
  - Chinese negative control -> `unknown/unknown`, empty selection, abstain.
- Focused regression: `341 passed in 39.26s`.
- Complete regression:
  `3340 passed, 2 skipped, 3 existing warnings in 194.98s`.
- Existing user changes in `.mini-code-memory/*` and the SmartRouter storage
  persistence intent were preserved; the feedback file itself is now isolated
  per project.

## Residual work

- Skill-level outcome attribution, version ledger, holdout, shadow/canary and
  rollback remain P1/P2.
- Memory usefulness remains whole-turn and weakly weighted.
- Cross-task transient-error recurrence needs a TTL observation buffer.
- Legacy `advanced_memory.json` stores remain orphaned.

---

# Skill Usage Outcome Attribution P1 Notes

## Phase 1 seam trace

- `run_agent_turn` owns the canonical final outcome and constructs every
  `ToolContext` through `_execute_single_tool`, including serial and concurrent
  paths.
- `load_skill` is the only truthful boundary for actual Skill use; routing
  remains candidate observation only.
- The same `RunObservation` is already passed as the event sink from Headless
  and Dashboard Chat through `AgentTurnRuntime.execute`.
- A task-scoped tracker passed through `ToolContext` avoids globals,
  cross-task leakage, and querying the Run Journal during execution.
- The final contract will be one bounded `skill.attributed` event with:
  version, `task_correlation`, canonical outcome fields, unique loaded-Skill
  identities/digests, total count, and truncation state.
- Paths, Skill content, task text, prompts, and model response text are not
  admissible fields.

## Phase 2–3 implementation evidence

- One shared `SkillUsageTracker` is created per `run_agent_turn` only when an
  event sink exists and is propagated through serial/concurrent ToolContext
  construction.
- `load_skill` records only after a successful real load. Identity is
  `(qualifiedName, source, directory, contentDigest)`.
- Repeating the same load twice emits two truthful `skill.loaded` events but
  one deduplicated task attribution.
- A recovered Tool error produces task success plus separate
  `hadToolErrors=true`, `errorsRecovered=true`, and `toolErrorCount=1`.
- Attribution emission is optional observation; tracker or sink failures
  cannot change Tool or Agent results.

## Phase 4 implementation evidence

- `skill.attributed` is accepted by `RunJournal` and is persisted in the same
  Run as the real `skill.loaded` events.
- The Dashboard read model validates the complete versioned contract before
  returning a strict whitelist. Injected task text, model responses, Skill
  content, and local paths are not projected.
- The generic Run event list renders actual Skill loads and task-correlated
  outcomes. The dedicated Skill Routing panel remains routing-only so that
  routed, loaded, and outcome-correlated do not collapse into one claim.
- Focused regression covering runtime observation, RunJournal, Dashboard read
  model/HTTP, agent event sequences, and entrypoint lifecycle:
  `177 passed in 33.79s`.
- A sandboxed run failed only because localhost socket binding was denied;
  rerunning the identical suite with local-socket permission passed.

## Phase 5 final verification

- Contract review tightened canonical consistency:
  `goalAchieved == (outcomeStatus == success)` and
  `errorsRecovered == (hadToolErrors and goalAchieved)`.
- The new private `ToolContext` field was appended after existing fields so
  prior positional construction remains source-compatible.
- Persisted lifecycle order is verified as all `skill.loaded` events before
  the single `skill.attributed` event, followed by `run.completed`.
- Focused suite: `177 passed in 33.79s`; post-review core suite:
  `82 passed in 0.64s`; persisted-order probe: `1 passed in 0.10s`.
- Complete suite:
  `3348 passed, 2 skipped, 3 pre-existing warnings in 194.87s`.
- `ruff`, `compileall`, `node --check`, and `git diff --check` passed.
- Residual boundary: whole-task, possibly multi-Skill correlation is not causal
  effectiveness. Cross-Run aggregation, comparable cohorts, verification/user
  signals, a Skill version ledger, shadow/canary, promotion, and rollback are
  deliberately not actuated in P1.

---

# Cross-Run Skill Evidence Ledger P2A Notes

## Scope

- Build the next safe evaluation layer from RunJournal facts.
- Emit canonical outcomes for unloaded tasks so treatment/control semantics do
  not diverge.
- Compare only single-Skill treatment Runs against zero-Skill controls inside
  the same `intentType/actionType` profile.
- Keep all decisions shadow-only; no live routing or promotion mutation.

## Phase 1 seam findings

- `RunJournal.list_runs()` and `list_events()` both page at at most 100 items;
  the ledger must own pagination and enforce an overall scan bound.
- Run lifecycle status cannot serve as task outcome: a normally returned
  max-step failure can still produce lifecycle `run.completed`.
- P1 attribution supplies canonical outcomes only for tasks that loaded a
  Skill, so a separate `task.outcome` event is required for valid no-Skill
  controls.
- `skill.routed@v1` identifies name/source/directory but not content digest.
  Production discovery reads each Skill once, then deliberately returns a
  content-free summary. It can calculate and retain only a SHA-256 before
  discarding the body, so routing needs no second file read and exposes no
  content. New complete observations can be v2; historical v1 stays readable
  but cannot support exact-version controls.
- Stronger comparable control: same v2-routed Skill digest, same coarse
  intent/action, and zero loaded Skills. Single-Skill treatment must match the
  routed digest. Multi-Skill loads and direct/non-routed loads are excluded.
- The deep-module interface should accept a RunJournal dependency and return
  one immutable/JSON-safe snapshot; scan/join/statistics remain implementation
  details.

## Phase 2–3 implementation evidence

- Every observed `run_agent_turn` now emits one `task.outcome@v1`, even when no
  Skill was loaded. The contract includes canonical status, goal achievement,
  nullable learning success, Tool-error count, and recovered-error state.
- Production discovery carries a SHA-256 derived during the existing Skill
  read, while continuing to strip the body from summaries. Routing emits
  `skill.routed@v2` when the safely projected candidates are complete.
  Incomplete legacy/fixture projections remain v1.
- `SkillEvidenceLedger(journal).snapshot()` is the sole external interface.
  It owns Run/event pagination, strict event validation, profile/digest joins,
  cohort statistics, exclusion accounting, and output bounds.
- Treatment requires exactly one unique loaded Skill, a matching v2 routed
  digest, one consistent attribution, and a binary canonical outcome.
- Control requires the exact routed digest/profile, no loaded event, no
  attribution, and the same binary canonical outcome contract.
- Multi-Skill, direct/mismatched loads, legacy routing, malformed/missing
  outcomes, non-binary outcomes, non-completed Runs, and overlarge event scans
  are excluded with bounded reason counts.
- Wilson 95% intervals gate `positive_signal`/`negative_signal`; fewer than
  five treatment or five control Runs is `insufficient_evidence`. Delta is
  `null` when either cohort is empty.
- Public results always set `mode=shadow` and `promotionEligible=false`.
- Real RunJournal paging probe crossed the 100-Run page seam and retained all
  105 treatment/control records.

## Phase 4 implementation evidence

- The existing read-only Skills endpoint now includes an independently
  failure-isolated evidence wrapper. Skill discovery remains available if the
  RunJournal evidence scan fails.
- Dashboard evidence exposes only the bounded aggregate: scan/eligibility
  counts, exclusion counts, Skill identity/digest, coarse profile, cohort
  outcomes, Wilson intervals, delta, sample gate, and shadow status.
- Run titles, task text, prompts, model responses, Skill bodies, paths, event
  IDs, and Run IDs are never returned by the ledger.
- The frontend validates the evidence contract before rendering and labels the
  panel `shadow only`, `task correlation, not causal proof`, and
  `promotion locked`.
- Run details now strictly project `task.outcome@v1` and both historical
  `skill.routed@v1` and digest-bearing `skill.routed@v2`.

## Phase 5 final verification and review

- A production-equivalent probe creates a real project Skill, discovers and
  routes it, executes five real `load_skill` treatments plus five no-load
  controls, persists their Runs, and derives one positive shadow signal. It
  also proves that task titles and Skill content do not enter the snapshot.
- Review made event order part of eligibility:
  `skill.routed → skill.loaded → task.outcome → skill.attributed`. Corrupt or
  impossible orderings fail closed as inconsistent Skill use.
- Failed `load_skill` attempts are never controls. Runs with Journal read
  diagnostics, per-Run read failures, or more than 500 events are excluded;
  incomplete evidence is surfaced as `partial`, not `live`.
- Scans are capped at the newest 200 retained Runs, 500 events per Run, and
  100 evaluation rows. Truncation is explicit.
- Paged RunJournal regression covers 105 Runs; production discovery/routing
  regression protects the content-free summary → v2 digest seam.
- Focused P2A/Dashboard/Gateway regression passed 237 tests before the final
  review hardening. The final complete suite passed
  `3365 passed, 2 skipped, 3 pre-existing warnings in 195.21s`.
- `ruff`, `compileall`, `node --check`, and `git diff --check` passed.

## P2A boundary

- The ledger measures task-level observational correlation under an exact
  Skill digest and coarse intent/action profile. Routing and model choice are
  not randomized, so selection bias and unobserved task difficulty remain.
- The outcome does not yet include independent verification, user acceptance
  or correction, cost, or latency gates.
- There is still no first-class Skill version record with parent, status,
  evaluation provenance, canary state, or rollback target.
- Consequently every public result remains `mode=shadow` and
  `promotionEligible=false`; no consumer feeds it back into live routing.

---

# Skill Version and Promotion Gate Ledger P2B Notes

## Phase 1 seam findings

- `propose_skill` is intentionally proposal-only. Actual Skill creation/editing
  still happens through ordinary approved file writes, so there is no truthful
  automatic draft provenance or promotion actuator to reuse.
- Production Skill discovery already reads each body and returns a content-free
  summary with SHA-256. A version observer can consume those summaries without
  reopening files or persisting content/path/task text.
- `model.completed` and `model.failed` carry bounded per-call duration;
  `model.costed` carries reconciliable priced/unavailable observations linked
  by `operationId`. These are truthful cost/latency sources for eligible P2A
  Runs.
- There is no RunJournal event for independent task verification or explicit
  post-task user acceptance/correction. Tool success, assistant success, Memory
  feedback, permission approval, and reflection verification are not valid
  substitutes: they measure different facts or are not linked to the public
  Run evidence seam.
- Verification and user gates must therefore remain `unavailable` in P2B until
  dedicated observations exist. Missing gates fail closed; task success cannot
  synthesize them.
- The narrow deep-module interface is:
  `SkillVersionLedger.observe_catalog(skills)` for atomic immutable version
  observation and `snapshot(catalog, evidence)` for read-only lineage/gates.
  Storage validation, version IDs, parent selection, bounds, atomic replacement
  and gate policy stay behind that interface.
- Persist only immutable version facts under project `.mini-code`; evaluation
  remains derived from the current bounded P2A snapshot so stale success cannot
  become durable promotion authority.
- Runtime catalog construction is the truthful write seam. Dashboard remains
  read-only and may pass its independently discovered catalog only to mark
  which persisted digest is currently visible.
- Gate policy for this slice:
  - outcome passes only with at least one positive sample-gated profile and no
    negative/inconclusive sample-gated profile;
  - cost/latency require complete treatment/control observation coverage and
    no mean regression, compared without floating-point division;
  - verification and user gates are unavailable;
  - all versions remain observed/shadow with promotion and rollback execution
    locked.

## Phase 2 implementation evidence

- `SkillVersionLedger.observe_catalog(skills)` records only qualified name,
  source, directory, digest, deterministic version ID, parent, observed status,
  first-observed timestamp, and an honestly empty `createdFromRuns`.
- Version IDs are deterministic hashes of the public identity/digest. A newly
  observed digest links only to the latest prior version of the same
  qualified/source/directory identity; cross-Skill parent linkage is rejected.
- The project store is atomic, owner-only `0600`, bounded to 1,000 versions and
  2 MiB, and coordinated by a process lock plus POSIX file lock.
- Malformed, oversized, symlinked, duplicate, cyclic/out-of-order, or
  cross-Skill history is rejected. Runtime-safe observation swallows the error
  but never overwrites the untrusted bytes.
- `create_default_tool_registry` is the observation seam. It already owns real
  Skill discovery and now records the content-free catalog without changing
  Tool construction when observation storage is unavailable.
- `snapshot(catalog, evidence)` is read-only. It marks a persisted digest as
  currently visible only from the caller-provided catalog; absence does not
  mutate or retire historical versions.

## Phase 3 implementation evidence

- Eligible P2A experiences now reconcile exact per-Run economics from bounded
  canonical Model events. Cost requires every completed operation to have one
  valid priced `model.costed` record and no failed attempt; totals remain
  JavaScript-safe decimal strings.
- Latency requires every started Model operation to have one terminal event
  with a valid bounded `durationMs`. A valid unpriced Cost observation makes
  cost unavailable without erasing independently valid latency.
- Cohorts expose observed Run count, exact total, and explicit complete
  coverage for cost and latency. Missing/malformed/incomplete observations
  never become zero cost or zero latency.
- Version gates consume only strict P2A snapshots for the exact digest.
  Sample-gated positive outcome with no negative/inconclusive profile passes
  the outcome gate; any sampled negative/inconclusive profile fails it.
- Mean cost and latency comparisons use integer cross-multiplication rather
  than floating-point division. Any observed regression fails; incomplete
  coverage is unavailable.
- Verification and user gates are always unavailable in P2B because their
  dedicated observations do not exist. Consequently
  `allRequiredGatesPassed=false`, `promotionCandidate=false`, and
  `promotionLocked=true` even when outcome/cost/latency pass.

## Phase 4 implementation evidence

- The Skills endpoint now returns a separate `versionLedger` wrapper. Catalog,
  P2A evidence, and version storage fail independently; corrupt version history
  does not hide live Skill summaries or shadow evidence.
- Dashboard uses its existing bounded Skill read to compute the same content
  digest and mark persisted versions `catalogCurrent`; the read path never
  observes or writes a version.
- The frontend validates ledger/version IDs, digest, parent/rollback identity,
  observed status, current flag, exact five-gate order/status, and the locked
  promotion invariants before rendering.
- Version cards expose content-free lineage, outcome/verification/user/cost/
  latency gate reasons, observed profile count, current/historical state, and
  explicit promotion/rollback locks. No mutation controls exist.

## Phase 5 review and final verification

- Review tightened “immutable lineage” from “parent is an earlier version of
  the same Skill” to “parent is exactly the immediately preceding observed
  version of that Skill.” Dropped parents, skipped parents, cross-Skill links,
  duplicate IDs, and out-of-order parents all make the complete store
  unavailable and are never repaired by overwriting history.
- The project state root and version file reject symbolic links and non-
  directory/non-regular targets. Reads use `lstat`, `O_NOFOLLOW` where
  available, `fstat`, and device/inode agreement before JSON parsing. A broken
  storage symlink is preserved rather than silently replaced; a symlinked
  `.mini-code` root cannot redirect version writes outside the Workspace.
- Gate input now accepts only canonical `IntentType`/`ActionType` values and
  verifies that `sampleGatePassed` agrees with the five-treatment/five-control
  minimum.
- Economics review separated Model lifecycle integrity from Cost integrity. A
  malformed Cost observation makes Cost unavailable but does not erase a
  fully paired, bounded Model duration; malformed lifecycle observations still
  fail both channels closed.
- Runtime construction remains available when version observation fails.
  Dashboard reads never create or change the ledger and independently isolate
  catalog, P2A evidence, and version-history failures.
- Focused storage/evidence/read-model/Tool regression:
  `115 passed in 2.14s`.
- Dashboard HTTP and installed-wheel packaging regression:
  `80 passed in 41.37s`.
- Complete suite:
  `3381 passed, 2 skipped, 3 pre-existing benchmark-marker warnings in
  196.42s`.
- `python -m compileall`, `node --check`, `git diff --check`, and Ruff over
  `skill_versions.py`, `skill_evidence.py`, the Tool registry integration, and
  the Dashboard read model all passed.
- Functional Reliability Audit 1A scanned 185 capabilities and retained its
  seven known baseline failures: archive Workspace escape/budgets, read-file
  truthfulness, utility validator metadata/conformance, raw Tool-error
  redaction, and ordinary conversational fact intake. P2B does not change
  those oracles or claim they are resolved.

## P2B boundary and next safe slice

- Outcome/cost/latency can now be evaluated for an immutable Skill digest, but
  verification and explicit user acceptance/correction have no canonical
  RunJournal observation and therefore stay `unavailable`.
- No version can become a promotion candidate while either required signal is
  unavailable. There is no Skill file mutation, replay executor, holdout
  assignment, canary traffic, promotion, or rollback actuator.
- The next safe self-evolution slice is P2C: add content-free independent
  verification and post-task user signal events, preserve correction/negative
  evidence, and evaluate them in replay/shadow mode. Canary/promotion should
  remain locked until those observations have production coverage.

---

# Independent Verification and User Signal Evidence P2C Notes

## Phase 1 seam findings

- `_execute_single_tool` is the first shared point after a Tool has returned,
  but a generic `ToolResult.ok` cannot distinguish a real verifier process from
  validation failure, permission denial, Tool crash, or an unrelated command.
  Therefore the Agent loop must never synthesize verification from Tool name,
  output text, or success alone.
- Trusted built-in verifiers can attach a strict content-free marker only after
  their actual subprocess returns. `test_runner` is an explicit test verifier;
  `run_command` may attach a marker only for a directly executed allowlisted
  verifier/build/lint/typecheck invocation, never for a shell snippet,
  background process, or wrapper such as `echo`.
- The canonical event shape can remain four closed fields:
  `verificationVersion`, `kind`, `outcome`, and `source`. It contains no
  command, arguments, path, stdout/stderr, prompt, secret, or user text.
- The existing Run event stream is the correct same-task verification seam:
  the marker is projected immediately after the real Tool result and therefore
  precedes the canonical `task.outcome`.
- A completed Run releases its sole event writer and correctly rejects later
  events. Explicit user feedback is necessarily post-terminal, so reopening
  the event stream would violate its ownership/append-only contract.
- The narrow post-terminal seam is one immutable content-free
  `user_signal.json` sidecar inside the Run directory. RunJournal owns strict
  validation, atomic owner-only storage, idempotence, conflict rejection,
  symlink/size checks, and reads. Run retention and Session deletion already
  remove the complete Run directory, so feedback follows existing lifecycle
  and privacy boundaries automatically.
- Dashboard Chat has a durable completed Turn record linked to exactly one Run.
  `ConversationTurnService` can accept `accept|correct|reject` only after
  authoritative completion and write through RunJournal. Silence, a later
  message, and ordinary Session continuation do not call this seam.
- Headless/TUI tasks retain the programmatic RunJournal feedback seam but have
  no honest explicit user-action UI in this slice. Their user gate stays
  unavailable rather than treating absence as acceptance.

## Phase 2 trusted verification evidence

- `verification_observation.py` owns one exact content-free event contract:
  `verificationVersion`, `kind`, `outcome`, and `source`. Unsupported fields,
  sources, kinds, outcomes, wrappers, shell control operators, and background
  commands are rejected.
- Only the built-in `test_runner` and recognized direct foreground verifier
  commands executed by `run_command` may attach a `ToolResult.verification`
  marker after a real subprocess result. Validation, permission, setup, and
  ordinary Tool success paths never attach one.
- Agent projection binds marker provenance to the actual trusted Tool name, so
  a custom Tool cannot claim `test_runner` or `run_command` as its source.
- RunJournal independently revalidates the payload and records
  `task.verified` before the canonical `task.outcome`. No command, output,
  prompt, path, user text, or secret enters the event.

## Phase 3 explicit user-signal evidence

- RunJournal owns one immutable completed-Run sidecar containing only schema,
  `accept|correct|reject`, the fixed source `explicit_user_action`, and a
  timestamp. Same-value retries are idempotent; a different later value is a
  conflict rather than silent history rewriting.
- The sidecar is bounded, owner-only, atomic, no-follow, device/inode checked,
  and rejects symlink/special-file replacement. Feedback, Session deletion,
  and retention share a Run mutation lock.
- Session-linked feedback also participates in the conversation deletion
  fence. A deletion in progress rejects the late write, while retention or
  deletion cannot remove a Run under an active feedback mutation.
- `ConversationTurnService.record_feedback` accepts feedback only for an
  authoritative completed Turn with the exact durable Run and committed
  Session result. The HTTP route accepts only an exact one-field JSON body and
  exposes fixed safe conflict/unavailable errors.
- Dashboard shows three explicit actions only for the selected Session's
  completed Turn with a real Run. Generation and identity checks discard stale
  responses. Starting another Turn or selecting another Session hides the
  controls; silence and subsequent messages do nothing.

## Phase 4 aggregation and locked version gates

- P2A evidence consumes only strict `task.verified` events between routing and
  outcome. Any observed verification failure is negative; complete passed
  coverage is positive; missing, malformed, or out-of-order evidence remains
  unavailable without erasing outcome, cost, or latency.
- The immutable user-signal sidecar is joined independently. Cohorts expose
  observed/pass/fail and accept/correct/reject counts plus explicit coverage
  completeness for both treatment and control.
- Gate policy v2 validates exact count arithmetic and coverage consistency.
  Treatment verification fails on any failed Run and passes only with complete
  passed coverage. The user gate fails on any correction/rejection and passes
  only when every treatment Run is explicitly accepted.
- When outcome, verification, user, cost, and latency all pass, a version may
  become a `promotionCandidate` in the shadow ledger. `promotionLocked` remains
  true, mode remains shadow, and the Dashboard exposes no promotion, mutation,
  traffic, or rollback action.

## Phase 5 review and verification

- Security review added provenance binding against custom-Tool spoofing,
  arithmetic rejection for fabricated signal counts, deletion-fence tests,
  lock-preservation tests, safe storage race mapping, and selected-Session UI
  scoping.
- Focused runtime/storage/evidence regression: `124 passed`.
- Focused Dashboard/Gateway/packaging regression: `268 passed` after updating
  isolated Node harness dependencies for the new feedback helpers.
- Complete suite: `3418 passed, 2 skipped, 3 pre-existing benchmark-marker
  warnings in 203.05s`.
- Ruff over every modified P2C Python module/test, `python -m compileall`,
  `node --check`, and `git diff --check` all passed.
- Functional Reliability Audit 1A now scans 186 capabilities, including the
  feedback route, and retains exactly the seven known baseline findings:
  `SEC-002`, `SEC-004`, `TOOL-001`, `TOOL-002`, `TOOL-003`, `SEC-005`, and
  `MEM-001`. Its non-zero exit is expected while those baseline issues remain.

## P2C boundary and next safe slice

- Verification coverage currently comes only from the trusted built-in test
  runner and recognized direct `run_command` test/build/lint/typecheck
  commands. Other verification systems remain unavailable rather than being
  inferred.
- The Dashboard/Gateway has an explicit feedback UI. Headless/TUI use has only
  the programmatic RunJournal seam, so its user gate generally stays
  unavailable. The immutable one-shot signal also intentionally cannot model
  a later change of mind.
- Explicit feedback is self-selected and treatment/control cohorts are
  observational, not randomized. Passing gates is stronger evidence, not a
  causal proof that the Skill caused improvement.
- Version provenance still has an honestly empty `createdFromRuns`; no code
  generates a revised Skill body, executes a replay candidate, assigns a
  holdout, routes canary traffic, promotes, or rolls back.
- The next safe slice is P2D: a deterministic offline replay/holdout evaluator
  that produces a proposed immutable Skill artifact and comparison report,
  while keeping filesystem mutation, live routing, promotion, and rollback
  actuators locked.

---

# Same-Turn Verification-Corroborated Memory Feedback Notes

## Why

The persistent-memory/skill-routing review found Memory's own feedback loop
was the weakest link even after P2C gave Skills real verification/user-signal
gates: every rendered memory in a turn got the same coarse whole-turn
success/failure label, and the retrieval weight for that label was only
`0.005` — deliberately not safe to amplify, since the credit assignment
behind it was still confounded. P2C's `task.verified` marker is exactly the
kind of independent, content-free ground truth that review asked for; this
slice reuses it for Memory instead of only for Skills.

## What this slice is (and is not)

Only the synchronous, same-turn channel: verification markers observed
during the current turn (via the already-existing `VERIFICATION_EVENT_TYPE`
emission in `_execute_single_tool`) are tallied by a new `VerificationTracker`
and reduced to one same-turn corroboration signal
(`verification_corroboration`: any failure → negative, complete passed
coverage → positive, no observation → `None`, mirroring how
`skill_versions.py`'s own verification gate already treats the same
three-way outcome). That signal drives a second, separately-counted
`Memory.record_corroborated_feedback`, never blended into the original
`record_feedback` counters.

The async explicit user accept/correct/reject channel is intentionally out of
scope here: it arrives after turn end, once the in-process
`_last_injected_ids` this turn used are gone, and would need a new durable
run_id → rendered-memory-id mapping (the existing `memory.rendered` event is
deliberately count-only — no entry IDs — to keep the Journal content-free).
That is the natural next slice.

## Ranking

`memory_retrieval.py` adds a `corroborated_score` term at weight `0.05`
(10x the naive `usefulness_score` weight), confidence-scaled by
`min(1, corroborated_samples / 3)` so a single anecdote cannot dominate.
Verified regression: corroboration alone cannot activate an otherwise
lexically-unrelated memory (same invariant already proven for the naive
usefulness/recency terms), a single sample is discounted to 1/3 weight, and
a fully-sampled corroborated entry outranks an otherwise-tied uncorroborated
peer.

## Errors encountered

- New ranking tests were first added into `tests/test_memory_retrieval_
  phase2a.py`, which is itself a pinned/frozen asset checked by
  `test_phase2a_pin_cascade_has_exact_hardening_changed_set`. Editing it
  surfaced as an unexpected extra entry in that cascade's changed-file set.
  Reverted via `git checkout` and moved the new tests to a standalone
  `tests/test_memory_corroborated_feedback.py`.
- Full suite showed one failure unrelated to this change:
  `test_dashboard_assets_load_from_an_installed_wheel` expects
  `baidu=response_too_large`, but got `baidu=redirect_blocked` in this
  sandbox. Confirmed via a temporary (reverted) debug print plus a direct
  `socket.getaddrinfo` check that this sandbox's outbound DNS resolves
  `www.baidu.com`/`www.bing.com` into the `198.18.0.0/15` RFC 2544 benchmark
  range, which Python's `ipaddress.is_private` flags as private — the app's
  own SSRF destination guard (`network_safety.py`) correctly raises
  `destination_blocked` before the test's mocked oversized-response layer is
  ever reached. Sandbox network-virtualization artifact, not a regression.

## Verification

- Focused: `tests/test_memory_retrieval_phase2a.py tests/test_memory_
  regressions.py tests/test_memory_acceptance_audit.py tests/test_agent_
  flow.py tests/test_runtime_observation_events.py tests/test_agent_model_
  events.py tests/test_memory_corroborated_feedback.py
  tests/test_memory_retrieval_phase2a_evaluator.py tests/test_memory_
  retrieval_phase2b_evaluator.py` — all green.
- Full suite: `3431 passed, 2 skipped, 1 failed` (the pre-existing
  sandbox-only network failure above).
- Ruff, `python -m compileall`, and `git diff --check` pass on every touched
  file.

## Next safe slice

Wire the explicit user accept/correct/reject signal into Memory too: persist
a Run-owned, content-free `run_id → rendered memory entry ID` sidecar at turn
end (mirroring `user_signal.json`'s atomicity/symlink-safety properties), then
have `ConversationTurnService.record_feedback` (or its caller) resolve that
mapping and call `Memory.record_corroborated_feedback` once the user signal
lands — giving Memory the same two-channel (verification + user) evidence
Skills already have from P2C.

---

# Explicit User-Signal-Corroborated Memory Feedback Notes

## Why

The previous slice gave Memory a same-turn verification channel but
explicitly deferred the async half: explicit user accept/correct/reject
arrives after the turn's runtime (and its in-process `_last_injected_ids`)
is already gone. This closes that gap using the same
`Memory.record_corroborated_feedback` sink the verification slice built, so
Memory finally has the same two independent evidence channels — verification
and explicit user signal — that Skills got from P2C.

## The missing link: where do rendered IDs live long enough?

`user_signal.json` is deliberately post-terminal and immutable. The rendered
Memory IDs, by contrast, are known mid-turn (same moment the existing
count-only `memory.rendered` event fires) and need to survive until an HTTP
request arrives, possibly much later, in a context that no longer holds the
turn's `MemoryPipeline` instance. The fix: a new Run-owned sidecar,
`memory_rendered.json`, written once while the Run is still `running`
(guarded like `append_event`, not like `user_signal.json`), read back later by
`ConversationTurnService.record_feedback` — which itself only needs a
*fresh* `MemoryManager(project_root=workspace)`, since Memory storage is
already file-backed and instance-independent (the same reason every test
helper in this codebase freely constructs a new `MemoryManager` to read back
what another instance wrote).

## Threading the ID through without polluting the public event log

`memory.rendered` is intentionally count-only — no entry IDs — to keep the
Journal event stream itself content-free of internal identity data.  Rather
than adding IDs to that public event or inventing a second sink parameter
threaded through `run_agent_turn`'s already-large signature, the simplest fit
was a **duck-typed extra seam on the existing `AgentEventSink`**:
`emit_memory_result_safely` now does `getattr(sink, "record_rendered_memory_
ids", None)` after its two normal `emit_event_safely` calls. Production's
`RunObservation` implements it (forwarding through `_BestEffortLifecycle` to
the new `RunJournal` method); every test double that doesn't implement it is
silently skipped. No changes to `agent_loop.py`, `run_agent_turn`'s
signature, or the `AgentEventSink` Protocol's required shape were needed.

## Avoiding double-counting on repeated feedback

`record_user_signal` is already idempotent (same signal twice → same
result, no re-write). Naively calling `record_corroborated_feedback`
every time `record_feedback` succeeds would double-increment Memory's
counters on a idempotent replay. Fixed by reading `get_user_signal` *before*
calling `record_user_signal`, and only applying corroboration when nothing
existed beforehand. This is intentionally outside the lock
`record_user_signal` takes internally, so two truly concurrent first
submissions for the same Run could both see "nothing recorded yet" and both
apply corroboration — an accepted, documented, low-severity race (soft
ranking signal, not safety-critical), not eliminated here to avoid changing
`record_user_signal`'s established return contract.

## Errors encountered

- The write-once sidecar's first `FileExistsError` handler silently treated
  *any* pre-existing target as a benign retry — including a planted symlink.
  A dedicated symlink-safety test (mirroring the existing `user_signal.json`
  one) caught this. Fixed by reading the existing target back through the
  same hardened `_read_rendered_memory_ids` path on conflict, only no-opping
  when its content is identical; a symlink or mismatch now raises
  `RunJournalStorageError` same as it would on the read path.
- Assumed a terminal Run would reject a rendered-ID write with
  `RunJournalTransitionError` (mirroring `append_event`'s structure). Actual
  behavior is `RunJournalOwnershipError`, because `transition()` releases the
  writer mutex in the same step it marks a Run terminal — so the terminal-
  status branch in both `append_event` and the new method is effectively
  unreachable through the public API. Corrected the test rather than the
  code, since this matches pre-existing, already-shipped behavior.

## Verification

- Focused: `tests/test_run_journal.py tests/test_run_lifecycle.py tests/
  test_runtime_observation_events.py tests/test_agent_flow.py tests/
  test_memory_regressions.py tests/test_memory_acceptance_audit.py tests/
  test_memory_corroborated_feedback.py tests/test_dashboard_chat_http.py
  tests/test_conversation_cancellation.py` — 200 passed.
- Ruff, `python -m compileall`, and `git diff --check` pass on every touched
  file.
- Full suite: `3443 passed, 2 skipped, 1 failed` — the same one
  pre-existing sandbox-only network failure from the previous slice
  (`test_dashboard_assets_load_from_an_installed_wheel`), unrelated to this
  change.

## Next safe slice

Memory and Skills now share both corroboration channels. Remaining, not
attempted here: promoting either signal from a soft ranking nudge to any
kind of automatic gate/promotion (still explicitly out of scope, matching
P2C's own locked-promotion stance), and closing the accepted TOCTOU race
above if concurrent duplicate feedback submission turns out to matter in
practice.

---

# Memory Corroborated Feedback Observability Notes

## Why

Both corroboration channels (verification, user signal) were fully wired
into ranking with zero way to actually see them working. Rather than build
more automation on unverified plumbing, this slice makes the existing signal
visible on the Memory Dashboard page and proves — with a real, not
simulated, run of the production code — that the numbers a user would see
are the same numbers actually driving retrieval ranking.

## What and where

- `read_model.py`'s Memory page item gains the three corroborated fields
  next to the existing `usefulnessScore`; its strict validator gains them in
  the same finite/non-negative checks already applied to every other
  numeric/counter field on the entry — no new validation philosophy, just
  extending the existing one.
- `app.js`'s `memoryRows()` shows ` · verified N✓ M✗ (score)` only when
  `corroboratedSuccessCount + corroboratedFailureCount > 0`. This mirrors the
  earlier design choice (in the ranking formula itself) that zero
  corroborated samples must be a complete no-op — now true for the display
  too, not just the score.

## How "does it compute correctly" was actually checked

Three independent proofs, from strongest to weakest:

1. **Full production round-trip script** (most convincing): real
   `MemoryManager.add_entry`, real `record_corroborated_feedback` calls (2
   accept, 1 reject), a **fresh** `MemoryManager` instance re-reading from
   disk (exactly what the async user-signal path in conversation.py actually
   does), then both `CanonicalMemoryRetriever.retrieve` (ranking) and
   `DashboardReadModel.memory()` (display) read back the identical
   `corroborated_success_count=2`, `corroborated_failure_count=1`,
   `corroborated_usefulness_score≈0.3333`. All four independent code paths
   (write, reload, rank, display) agreed exactly.
2. **Live browser check**: started the real `python -m minicode.gateway`
   (not a mock/stub) against an isolated demo workspace via a temporary
   launch config + wrapper script (both reverted afterward), seeded a
   `memory.json` with one corroborated and one uncorroborated entry directly
   (bypassing the agent loop, since driving a real chat turn through a mock
   model to trigger verification would have been far more setup for the same
   visual proof), and confirmed in a screenshot: `usefulness 1 · verified 2✓
   1✗ (0.33)` for the corroborated entry, and no `verified` fragment at all
   for the other. No console errors.
3. **Targeted unit tests**: happy path (nonzero and zero corroboration) and
   a dedicated rejection test (negative count, non-finite score) added to
   `test_dashboard_page_read_model.py`.

## Verification

- Focused: `test_dashboard_page_read_model.py test_dashboard_web.py
  test_dashboard_catalog_read_model.py test_dashboard_runs_read_model.py
  test_dashboard_chat_stream_frontend.py
  test_dashboard_permission_frontend.py` — 207 passed.
- Ruff, `compileall`, `node --check`, and `git diff --check` all pass.
- Full suite: `3444 passed, 2 skipped, 1 failed` — the same one
  pre-existing sandbox-only network failure, unrelated to this change.

## Next safe slice

No further automatic gate/promotion work planned for Memory or Skills until
there's real usage data behind these now-visible channels. If concurrent
duplicate user-signal submission for the same Run ever turns out to matter
in practice, that's the one previously-accepted, documented race left to
close.

---

# Legacy `advanced_memory.json` Cleanup Notes

## Why

The very first persistent-memory/Skill-routing review flagged this and it
was never closed: two `advanced_memory.json` files sitting in
`.mini-code-memory-local/` and `.mini-code-session-memory/`, from a schema
(`type`/`priority`/`confidence`/`dependencies`/`context_hash`/a `"session"`
scope) that doesn't match the current `MemoryEntry` at all — i.e. leftover
from a module that no longer exists. Zero production code reads the
filename `advanced_memory.json`; the real `MemoryManager` only ever touches
`memory.json`.

## Confirming it was actually safe to delete

The one thing that gave real pause: `scripts/memory_retrieval_evaluator.
py`'s `snapshot_formal_memory()` hashes exactly these live directories
(`project_root / ".mini-code-memory-local"`,
`project_root / ".mini-code-session-memory"`), and a checked-in artifact
(`artifacts/memory-retrieval-baseline.json`) even has historical hashes
labeled `local/advanced_memory.json` / `session/advanced_memory.json`.
Traced both uses before touching anything:

- `snapshot_formal_memory` is only ever compared **before vs. after the same
  test run** (`test_arm_execution_does_not_modify_formal_memory`) — proving
  the Phase2A/2B evaluator doesn't mutate legacy memory stores while it
  runs, not asserting a fixed historical hash. Fewer files before AND after
  is still equal.
- The baseline artifact's `local/advanced_memory.json` entries describe a
  **different evaluator run's own patched temporary root** (its own
  `formal_memory_access_mode` field says so explicitly), not these real
  repo-root directories. Its own pinned whole-file hash (in
  `memory_retrieval_phase2b_evaluator.py`) only depends on that JSON file's
  own bytes, which this cleanup never touches.
- `test_formal_memory_contamination_audit.py` builds its own isolated
  `home` fixture and never reads these real paths at all.

Ran `test_memory_retrieval_evaluator.py`, `test_formal_memory_contamination_
audit.py`, `test_memory_retrieval_phase2a_evaluator.py`, and
`test_memory_retrieval_phase2b_evaluator.py` (114 tests) both before and
after deleting the files — identical pass results.

## What changed

- Deleted the two orphaned files (both untracked/`.gitignore`d, so this
  doesn't even show up as a git change).
- `docs/CODE_WIKI.md` §5.10 no longer describes `advanced_memory.json` as
  the real storage file or `.mini-code-session-memory/` as a "session
  memory" scope; it now matches the real three-scope `memory.json` layout
  and correctly attributes `.mini-code-session-memory/` to reflection-replay
  capture (`reflection_replay.py`).

## Separate discovery, not acted on

While checking this doc section, noticed `docs/CODE_WIKI.md` has ~41 other
broken `file:///d:/Desktop/minicode/py-src/...` links (looks like it was
authored/exported from a Windows machine with absolute local paths as
"links"), and — more notably — this repo has a second, fully git-tracked
225-file copy of the entire project at `py-src/` alongside `minicode/`.
Neither is related to the Memory system specifically, both are out of scope
for this cleanup, and the `py-src/` duplicate in particular is large enough
that it deserves its own explicit decision rather than being swept in here.
Flagged to the user; not touched.

## Verification

Full suite before and after: `3444 passed, 2 skipped, 1 failed` both times
(the same pre-existing sandbox-only network failure, confirmed via a full
rerun after the cleanup). Ruff/compileall not needed — no Python logic
changed, only a Markdown doc and two deleted data files.

---

# Intent Parser False-Positive Fixes Notes

## Why

Adding 3 new project Skills and probing routing with realistic + negative
task descriptions surfaced a real bug: "What is the weather like today"
came back `explain/read` at confidence `1.0`, when it should abstain. Traced
to `_EXPLAIN_PATTERNS` — every sibling pattern group requires the trigger
verb to be followed by real context (a code/file/domain noun); EXPLAIN's
`(?:explain|describe|tell|what is|how to|how does)` required nothing.

## The three bugs, in the order found

1. `_EXPLAIN_PATTERNS` (English + Chinese) — bare verb, no context
   requirement. Fixed by requiring a nearby code/project noun or filename.
2. `_CONFIGURE_PATTERNS` — identical shape of bug, same fix.
3. `_adjust_confidence` — added its entity/keyword-count bonuses even when
   `base` (the actual pattern-match score) was `0`, so a fully unmatched
   message could still report confidence `0.05`. Fixed to short-circuit to
   `0.0` when there's no real match to boost.

## The self-correction worth remembering

First pass at a *related* gap (a skill's own example text coincidentally
containing a common word like "tell", scoring as a keyword match even
though overall intent was UNKNOWN) went for the blunt fix: gate the whole
keyword-scoring loop in `skill_router.py` on `intent_type != UNKNOWN`. Only
caught because the exact probe suite built for THIS fix also included
"Rename the taskkit package..." — a message with no dedicated REFACTOR
pattern (intent stays `unknown`) that had always routed correctly via the
literal keyword "rename" alone. The blunt gate silently deleted that
legitimate path. The actual fix belonged one layer down: add the specific
trigger verbs that now require pattern context (`tell`, `describe`,
`explain`, `configure`, `setup`, `install`, `init`, `initialize`) to
`_extract_keywords`'s stopword list, so *those specific* words stop leaking
out as free-floating keywords — without touching how any other keyword
(like "rename") contributes to routing. Lesson: when closing a keyword-
collision gap, fix the specific leaking terms, not the general keyword
channel — the channel carries real signal for cases with no dedicated
regex pattern at all.

## Verification

- New `tests/test_intent_parser.py` (first dedicated test file for this
  module) — 22 positive/negative cases across English and Chinese for both
  patterns and the confidence-floor fix.
- New `test_skill_router.py::test_unrelated_small_talk_does_not_route_via_
  coincidental_keyword_overlap` locks in the "tell"/example-text collision
  fix at the routing level; re-verified "rename" still routes correctly
  after the revert-and-refix.
- Focused regression across every file that calls `parse_intent`/
  `IntentParser` (skill router, skill evidence ledger, feedforward
  controller, packaging, run-entrypoint lifecycle) — all pass.
- Full suite: `3468 passed, 2 skipped, 1 failed` — the same pre-existing
  sandbox-only network failure, unrelated.
- Ruff, `compileall`, `git diff --check` pass.

---

# Persistent Memory Completeness Re-review Notes — 2026-07-30

## Verdict

The explicit-memory happy path is mature, but the production persistence
lifecycle is not complete. Review only; no runtime source was changed.

## Reproduced P1 gaps

1. A long-lived `MemoryManager` can render an entry once after another manager
   has rejected it. Retrieval copies the in-memory active entries before the
   later usage-recording mutation refreshes disk revision.
2. A symlinked `.mini-code-memory` root is followed by direct
   `MemoryManager.add_entry()`, producing `memory.json`, `MEMORY.md`, and
   `approval_audit.json` outside the Workspace. ApprovalAuthority validates
   these paths, but the main writer does not.
3. The functional audit still reports stable, environment-independent P1
   `MEM-001`: ordinary conversational facts do not become reviewable durable
   candidates or cross Session boundaries.
4. `MemoryFile._enforce_limits()` silently drops the oldest entry. A one-entry
   limit probe retained the second entry but left one audit record referring to
   the evicted first entry. Public Manager delete/clear paths likewise do not
   provide one all-scope audited forgetting transaction.

## Verification

- Focused persistence/approval/retrieval/deletion tests: `145 passed`.
- Broad Memory-related run: `709 passed, 24 failed` only because the sandbox
  denied loopback socket bind. Rerunning the two affected HTTP files with
  loopback permission gave `42 passed`; therefore all 733 unique tests are
  behaviorally green in a capable environment.
- `run_functional_audit.py --category memory`: exit 1; 3 pass, 1 partial,
  1 fail; sole issue `MEM-001`.
- Detailed review:
  `docs/persistent-memory-completeness-review-2026-07-30.md`.
