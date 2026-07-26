# MiniCode Reliability 1B-1C — Built-in Web Search Provider Chain

## Certification status

Reliability 1B-1C is implemented, certified and closed. The built-in
core-profile, read-only
`web_search` Tool now uses a fixed, bounded Baidu→DuckDuckGo HTML provider
chain. It truthfully separates successful results, explicit empty pages,
challenge pages, unrecognized response structure, HTTP status classes and
network failures without echoing the query or raw diagnostics.

The original final-certification blocker was removed by Reliability 1B-1C.1:
default Phase 2A acceptance is now deterministic/advisory while real P95 and
the unchanged 5.0 ms gate remain available through explicit strict
enforcement. The hardening changed no web-search or other `minicode/`
production bytes.

This batch did not change archive Tools, Memory, Agent Loop, Session,
RunJournal, MCP, Dashboard behavior, Permission semantics or runtime
dependencies, and did not enter Reliability 1B-2.

## Before and after

Before:

```text
ToolRegistry
  -> web_search
     -> one hard-coded DuckDuckGo URL
     -> urllib.request.urlopen(timeout=15)
     -> unbounded response.read()
     -> one whole-document regex
     -> empty list for empty/challenge/markup drift
     -> query/raw exception in failures
```

After:

```text
ToolRegistry
  -> web_search strict validator
     -> fixed provider order/config validation
     -> one 15 s monotonic deadline
     -> search_provider("baidu", deadline <= now + 6 s)
        -> normalize_http_request
        -> execute_safe_get_response
           -> validate_destination + shared BoundedResolver
           -> IP-pinned HTTP/TLS transport
           -> per-hop redirect revalidation
           -> bounded response read
        -> BaiduHtmlParser -> immutable SearchProviderOutcome
     -> if needed and time remains:
        search_provider("duckduckgo", deadline <= now + 6 s)
        -> same safe transport -> DuckDuckGoHtmlParser
     -> bounded success projection or low-cardinality failure
```

The same `web_search_tool` object remains registered exactly once in the core
Tool list and is used by TUI, Headless, Gateway `/run` and Dashboard Chat
through the existing `ToolRegistry`/Agent runtime composition.

## Provider deep module

`minicode/tools/search_providers.py` owns the provider boundary independently
of `ToolDefinition`:

- frozen `SearchResult`;
- frozen invariant-checked `SearchProviderOutcome`;
- closed `SearchProviderStatus`;
- `SearchProvider` protocol;
- fixed provider endpoint construction;
- provider configuration;
- separate streaming parsers;
- safe textual result-URL projection.

An outcome cannot use an empty tuple ambiguously: `success` requires results,
and every non-success status requires none. The closed statuses are
`success`, `no_results`, `timeout`, `dns_error`, `network_unavailable`,
`forbidden`, `rate_limited`, `server_error`, `challenge`,
`response_unrecognized`, `response_too_large`, `redirect_blocked` and
`tls_error`.

## Provider order, configuration and fallback

The default order is exactly `baidu,duckduckgo`.
`MINI_CODE_WEB_SEARCH_PROVIDERS` may select either single provider or either
order of those two. Empty values, surrounding whitespace, empty members,
uppercase/unknown names, duplicates or more than two members fail closed as
`provider_config_invalid` before any transport call. Arbitrary URLs, hosts,
proxy settings, commands and credentials are not configurable.

Providers run serially, once each, with no retry, sleep or backoff:

1. Any non-empty valid result set succeeds immediately, even if it contains
   fewer than the requested maximum; the next provider is not called.
2. Explicit `no_results`, network/status failure, challenge or unrecognized
   response may fall through once while the shared deadline remains.
3. Two explicit empty results produce `no_results`.
4. Empty plus any failure produces `search_incomplete`.
5. All failures produce `search_unavailable`.
6. Exhausting the total deadline produces `search_timeout` and prevents the
   next provider from starting.

The total call budget is 15 seconds. Each provider receives at most six
seconds and never a refreshed total budget. DNS queueing, connect, redirect,
read and parse are charged against the same monotonic deadline.

## Shared safe transport and HTTP status

`minicode/tools/http_utils.py` now has one internal bounded response executor
and two compatibility projections:

- existing `execute_safe_http()` / `execute_safe_get()` preserve their prior
  `HTTP >= 400 -> http_error` behavior for `http_request` and `web_fetch`;
- new `execute_safe_get_response()` is GET-only and exposes the bounded final
  status to a search provider.

Both paths preserve destination validation, the one process-local
4-worker/8-queued/12-outstanding resolver, all-public DNS enforcement,
validated IP pinning, original TLS hostname/SNI, explicit redirect handling,
per-target revalidation, connection close, one deadline, 1 MiB wire limit and
at most 64 KiB per read. Search maps 403 and other 4xx to `forbidden`, 429 to
`rate_limited`, 5xx to `server_error`, and keeps transport/status bodies out
of Tool output.

## Provider parsers and response classification

`BaiduHtmlParser` and `DuckDuckGoHtmlParser` are separate
`html.parser.HTMLParser` implementations. They stream through bounded input,
ignore script/style content, decode entities, normalize whitespace, remove
Unicode C0/C1 control characters, cap titles at 300 characters and snippets
at 600, limit candidate retention, deduplicate normalized URLs and return at
most `num_results`.

Each parser distinguishes:

- recognized result markup with at least one safe result;
- provider-specific explicit empty markers;
- challenge/captcha/verification markers;
- changed or unrelated HTML as `response_unrecognized`.

No match is therefore not treated as proof of an empty result. Malformed or
unexpected parser behavior also fails closed as `response_unrecognized`.

## Result URL and output safety

Result URLs are projected as text only; search never resolves or fetches them.
The projection accepts only HTTP/HTTPS, rejects userinfo, controls, invalid
ports, over-4096-byte values, localhost and literal loopback/private/
link-local/unsafe IPs, normalizes scheme/IDNA hostname and removes fragments.
DuckDuckGo `uddg` links are decoded only when the redirect shape is exact.
Valid opaque Baidu `/link...` URLs are retained and visibly labeled as a
provider link.

Input is a closed object containing only `query` and optional `num_results`.
Query validation rejects empty/whitespace-only strings, Unicode control
characters, invalid UTF-8 scalars, more than 512 characters or more than 2048
UTF-8 bytes. `num_results` is a non-boolean integer in `1..10`.

Success contains provider ID, actual result count, numbered bounded title,
safe URL and bounded snippet. Failure contains only a fixed error code,
message, fixed provider IDs and fixed status terms. Query, provider URL,
redirect Location, headers, cookies, DNS data, exception text, response body,
traceback and local paths are never projected.

## Functional Audit

Before Reliability 1B-1C:

- 185 capabilities;
- 123 pass, 44 partial, 8 fail, 1 unavailable, 6 blocked and 3 not reachable;
- 9 issues including `WEB-001` and `WEB-002`;
- `tool.web_search` was fail/partial with an unbounded single provider.

After Reliability 1B-1C:

- 185 capabilities;
- 124 pass, 44 partial, 7 fail, 1 unavailable, 6 blocked and 3 not reachable;
- 7 issues;
- `WEB-001` and `WEB-002` are removed;
- `tool.web_search` deterministic, installed-wheel, safety, truthfulness and
  status are all `pass`, with `issues=[]`;
- live remains honestly `blocked` because optional external-network smoke was
  not run.

The remaining issues are `SEC-002`, `SEC-004`, `TOOL-001`, `TOOL-002`,
`TOOL-003`, `SEC-005` and `MEM-001`. The audit command intentionally exits 1
while these issues remain.

## Baseline, semantic and verification evidence

The active baseline is `memory-retrieval-production-v39`, parent v38.
Manifest SHA-256:
`9bcf038d20aa7c044f4db613626b484e2fa89819929be20b46390bca00a99d6e`.
It protects 62 files; candidate/current match and v1–v39 manifest integrity is
true. The exact v38→v39 production delta is:

- changed: `minicode/tools/http_utils.py`;
- added to protection: `minicode/tools/search_providers.py`;
- added to protection: `minicode/tools/web_search.py`;
- removed: none.

The v38 manifest remains
`49f3319b06289ef23ab8c2f40bc3da0deaf443cb365f654cd2d1683a42b727f3`.
The official evaluator reports 108 cases, 37 confirmed gaps,
`phase3b_gate=true`, `remote_calls=0` and `evaluation_passed=true`. Accepted
gold remains SHA
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
size 3,033,592 and mtime_ns `1784135857000000000`.

Original Reliability 1B-1C verification results before certification
hardening:

- search/provider focused tests: 159 passed;
- search plus safe-network/Tool/Audit focused matrix: 333 passed;
- resolver/Permission/Agent/Gateway compatibility matrix:
  632 passed, 2 skipped;
- complete baseline/semantic test matrix: 239 passed;
- packaging/isolated wheel tests: 9 passed;
- complete suite after decoded-target fix:
  3314 passed, 2 skipped, 3 existing warnings in 205.85 s;
- second complete suite after decoded-target fix:
  2 failed, 3312 passed, 2 skipped, 3 existing warnings in 208.12 s;
- scoped Ruff, `py_compile`, `compileall -q minicode scripts tests` and formal
  JavaScript `node --check` passed.

The wheel was built without dependencies or build isolation, installed with
`PYTHONNOUSERSITE=1`, imported from a non-source working directory and
verified for exactly-one Tool registration, packaged provider modules,
provider outcomes/fallback/statuses, safe-network blocking, bounded responses,
existing web Tools, Gateway health/run and Dashboard assets. Runtime
dependencies remain `[]`.

The two recorded failures were both in the then-unchanged
`tests/test_memory_retrieval_phase2a_evaluator.py`. The CLI report records
canonical retrieval P95 `5.269083 ms` against the frozen `5.0 ms` threshold;
the other failure observes different boolean performance-gate results from
two consecutive evaluations. A read-only system sample immediately afterward
reported `84.38% CPU idle`. No Memory source, test, threshold, manifest or
gold was changed, and the failing test was not rerun to select a lucky result.
The later raw trailing-control guard passed the final 159/333/632 scoped
matrices, wheel, baseline and static checks. The full suite was deliberately
not rerun after that guard.

Reliability 1B-1C.1 then established synthetic RED evidence, added the pure
Phase 2A deterministic/advisory/strict policy, repaired the timing-free
projection, separated generated output paths from accepted artifacts and
applied the minimal Phase 2A→Phase 2B→semantic pin cascade. Exactly one strict
run measured canonical P50/P95 `1.748625/2.768958 ms`, passed the unchanged
5.0 ms gate and returned 0 with zero remote calls. Final complete suites on
the same source both passed:

- `3355 passed, 2 skipped, 3 warnings` in 207.13 s;
- official semantic evaluator: 108 cases, 37 gaps, Phase 3B true, zero remote
  calls and passed;
- `3355 passed, 2 skipped, 3 warnings` in 207.33 s.

Accepted Phase 2A/2B assets and semantic gold retain their SHA/size/mtime,
v39 remains 62/62 with all v1–v39 integrity flags true, and the Functional
Audit remains 124 pass with exactly the seven non-WEB issues. Full hardening
evidence is in
`docs/minicode-reliability-1b-1c1-phase2a-certification-hardening.md`.

## Live status, cleanup and next boundary

No optional real external-network search smoke was run; deterministic fixtures
and loopback transport tests are the acceptance authority. No claim is made
that public provider markup or reachability is permanently stable.

Test servers, resolver fixtures, Gateway threads, wheel/install directories,
isolated HOME/workspaces and hash-seed directories are temporary/context-owned
and were closed or removed. No task listener was intentionally retained.

The stable interfaces for eventual next-stage work are the immutable provider
outcome contract,
fixed configuration loader, safe result projection and GET-only bounded
status-observing transport seam. Reliability 1B-1C is closed. Archive, Memory
and the other seven audit issues remain for separately authorized work;
Reliability 1B-2 was not entered.
