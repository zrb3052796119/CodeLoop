# MiniCode Reliability 1B-1B — Web Fetch Safe Transport Boundary

## Certification

Reliability 1B-1B is complete. The built-in core-profile, read-only
`web_fetch` Tool now uses the same bounded DNS, destination-validation and
IP-pinned transport authority as `http_request`. This batch does not change
`web_search`, archive Tools, Permission semantics, Agent Loop, Memory, Session,
RunJournal, MCP or Dashboard frontend behavior.

## Before and after

Before:

```text
ToolRegistry
  -> web_fetch prefix-only URL check
  -> urllib redirect opener
  -> implicit DNS and implicit redirects
  -> unbounded response.read()
  -> render/truncate and raw-detail-prone errors
```

After:

```text
ToolRegistry
  -> web_fetch typed normalization
  -> execute_safe_get
     -> execute_safe_http
        -> validate_destination
           -> shared BoundedResolver (4 workers / 8 queued / 12 outstanding)
        -> validated IP-pinned HTTP/HTTPS connection
        -> explicit redirect loop (max 3)
           -> normalize + validate + pin every target
        -> read_bounded_response
           -> 1 MiB wire limit / at most 64 KiB per read
  -> bounded HTML/JSON/text rendering
  -> content-free low-cardinality errors
```

The shared transport seam is
`execute_safe_http(NormalizedHttpRequest, deadline, destination=None) ->
SafeHttpResponse`. `execute_safe_get()` is the GET-only adapter used by
`web_fetch`. The structured result contains status, safe content type, safe
content encoding and bounded payload bytes; callers do not parse another
Tool's rendered output.

## Input and destination contract

- Only HTTP/HTTPS GET is accepted.
- Input is a closed object with required non-empty `url` and optional strict
  integer `max_chars` in `100..50000`.
- Control characters, overlong URLs, userinfo, invalid ports, unsupported
  schemes, booleans, strings, fractions, NaN/Infinity and extra fields fail
  closed.
- Hostnames use the existing IDNA/case normalization; fragments are removed.
- Initial and redirected destinations use `validate_destination()` and the one
  process-local `BoundedResolver`.
- Every DNS answer must be public. IPv4, IPv6, IPv4-mapped IPv6, loopback,
  private, link-local, multicast, reserved, unspecified, malformed and mixed
  public/private answers are covered.
- DNS errors, timeouts and resolver saturation call no transport.

## Pinning, TLS, redirects and time

Connections use a validated fixed IP. HTTPS retains the normalized hostname
for SNI and certificate verification. The transport performs no second
hostname DNS resolution.

Automatic urllib redirects are disabled. Relative Locations use `urljoin`;
every hop is normalized, resolved, validated and pinned again. Loops, missing
or malformed Location, unsafe targets and a fourth redirect return
`redirect_blocked`. Rejected targets receive zero target sends.

One monotonic deadline represents the complete 30-second operation, including
DNS, connections, redirects and body reads. No hop receives a fresh timeout.

## Response and rendering contract

- Wire bodies are bounded before decode/render at 1 MiB.
- Each `read()` is at most 64 KiB.
- Oversized declared Content-Length fails before a body read.
- Exact-boundary declared and streaming bodies succeed; boundary+1 and
  chunked/no-length oversized bodies fail with `response_too_large`.
- HTTP error bodies are bounded and closed but never displayed.
- Only `text/*`, `application/json` and `application/*+json` are rendered.
- Non-identity Content-Encoding and binary media types fail closed.
- Unknown charset falls back to UTF-8 replacement.
- HTML script/style blocks are removed case-insensitively before entity and
  whitespace normalization.
- `max_chars` applies after rendering. Success reports status, safe media type,
  wire bytes, original rendered character count and truthful truncation state.

Errors use only the fixed network vocabulary and never include a URL,
hostname/IP, query, headers, credentials, DNS answers, reason phrase,
exception text, traceback or local path.

## Functional Audit

Before this batch the matrix had 185 capabilities, 122 pass, 9 fail and 10
issues. The certified matrix has:

- 185 capabilities;
- 123 pass, 44 partial, 8 fail, 1 unavailable, 6 blocked and 3 not reachable;
- 9 issues;
- `SEC-003` removed;
- `SEC-004` retained only for archive decompression byte/member/time budgets;
- `tool.web_fetch` deterministic, installed-wheel, safety, truthfulness and
  final status all `pass`, with an empty issue list.

`WEB-001`, `WEB-002`, `SEC-002`, `SEC-004`, `TOOL-001`, `TOOL-002`,
`TOOL-003`, `SEC-005` and `MEM-001` remain open.

## Packaging and verification

- `tests/test_web_fetch_safety.py`: 78 passed.
- `web_fetch` + `http_request` + bounded resolver: 161 passed.
- Web/HTTP/Resolver/Tool/TUI/Permission/Gateway Chat compatibility: 391 passed.
- Functional Audit contract: 4 passed.
- Baseline v34-v38 and core baseline tests: 196 passed.
- Semantic evaluator tests: 32 passed.
- Packaging and isolated wheel/Gateway smoke: 9 passed.
- Final complete pytest suites: 3147 passed, 2 skipped and the same 3 existing
  benchmark-marker warnings, in 210.38s and 210.29s.
- Scoped Ruff, `py_compile`, `compileall -q minicode scripts tests` and all
  formal JavaScript `node --check` commands passed.
- Runtime dependencies remain `[]`.

The isolated wheel was built with `pip wheel --no-deps
--no-build-isolation`, installed with `PYTHONNOUSERSITE=1`, imported from the
installation target and exercised from a non-source cwd. It verified
core-profile discovery, JSON/HTML/text success, private/mixed/redirect-private
blocking, DNS error/timeout/busy zero transport, oversized response blocking,
the existing `http_request` smoke, Gateway health/run routes and Dashboard
static assets.

No live external-network smoke was run. It is opt-in, volatile and not an
acceptance gate.

## Baseline and semantic evidence

The active baseline is `memory-retrieval-production-v38`, parent v37. Its
manifest SHA-256 is
`49f3319b06289ef23ab8c2f40bc3da0deaf443cb365f654cd2d1683a42b727f3`.
It protects 60 files. The exact v37→v38 delta is:

- changed: `minicode/tools/http_utils.py`;
- added to protection: `minicode/tools/web_fetch.py`;
- removed: none.

`web_fetch.py` is an added protected file because it was not among v37's 59
paths; this is not represented falsely as a changed protected file. Every
v1–v38 pin is valid, candidate/current match and the tamper test reports only
the changed file without rewriting the manifest.

The official evaluator reports 108 cases, 37 confirmed gaps, Phase 3B
gate=true, remote calls=0 and evaluation_passed=true. Accepted gold remains:

- SHA-256:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`;
- size: 3,033,592 bytes;
- mtime_ns: `1784135857000000000`.

## Cleanup and next boundary

All pytest servers, resolver fixtures, Gateway threads, wheel/install
directories, isolated HOME/workspaces and evaluator hash-seed directories are
owned by context managers or temporary directories and were closed/removed.
No task listener remains.

The stable next-stage interface is the typed safe GET seam plus
`NetworkSafetyError` low-cardinality taxonomy. Reliability 1B-1B stops here:
`web_search` provider/fallback work and archive decompression budgets remain
separate future batches.
