# MiniCode Reliability 1B-1A: HTTP Request Safety

## Status

**Passed and certified.** Reliability 1B-1A closes SEC-001 and the
`http_request` response portion of SEC-004. The remaining `web_fetch`,
`web_search`, archive, Tool-truthfulness and ordinary-fact Memory findings stay
open and were not connected to this boundary.

## Original SEC-001 reproduction

The unchanged full-profile `http_request` Tool sent a real POST to an isolated
random loopback fixture with `ToolContext.permissions=None`. The Tool returned
success, the fixture received exactly one `/mutation` request, and there were
zero pending approvals.

## Original call graph

```text
Agent -> ToolRegistry -> http_request validator
      -> urllib Request -> urlopen -> unbounded read -> output truncation
```

## Target call graph

```text
Agent -> ToolRegistry -> http_request adapter
      -> immutable normalize + budgets
      -> destination resolve/classify
      -> safe network review
      -> one-operation Permission decision
      -> fingerprint/destination revalidation
      -> final cancellation checkpoint
      -> pinned standard-library transport
      -> bounded stream reader
      -> safe response/error projection
```

## HTTP method policy

| Method | Public HTTPS | Public HTTP | Body | Redirect |
| --- | --- | --- | --- | --- |
| GET | destination validation, no approval | allowed only without sensitive headers | no | manual, bounded, every hop revalidated |
| HEAD | destination validation, no approval | allowed only without sensitive headers | no | manual, bounded, every hop revalidated |
| OPTIONS | fresh approval every call | blocked as cleartext | no | never followed |
| POST | fresh approval every call | blocked as cleartext | yes | never followed |
| PUT | fresh approval every call | blocked as cleartext | yes | never followed |
| PATCH | fresh approval every call | blocked as cleartext | yes | never followed |
| DELETE | fresh approval every call | blocked as cleartext | yes | never followed |

Method input is normalized to uppercase only after strict string validation.
Every other method and every non-string value is `invalid_request`.

## Destination policy

| Destination | Decision |
| --- | --- |
| Public HTTPS host resolving only to global IPs | allowed under method policy |
| Public HTTP host resolving only to global IPs | GET/HEAD only, no sensitive headers |
| IPv4/IPv6 loopback | hard blocked |
| RFC1918/private/unique-local | hard blocked |
| Link-local | hard blocked |
| Multicast/reserved/unspecified/non-global | hard blocked |
| IPv4-mapped IPv6 with non-global mapped address | hard blocked |
| Hostname resolving to any unsafe address | hard blocked |
| URL userinfo | hard blocked |
| Non-HTTP(S) scheme | hard blocked |
| Missing/invalid host or invalid port | hard blocked |
| Redirect target | normalize, resolve and classify again before any connection |

An ordinary permission Allow never overrides destination safety. The transport
connects to the revalidated resolved address while preserving the original
hostname for HTTP Host and TLS SNI/certificate verification.

## Network Permission schema

The internal request uses `schemaVersion=1`, `kind=network` and an exact review
object containing only:

- `reviewVersion`
- normalized `method`
- `scheme`
- ASCII `hostname`
- explicit/default `port`
- query-free, bounded `pathSummary`
- `hasBody`
- `hasSensitiveHeaders`
- content-free `requestFingerprint`

The broker adds `reviewable` and choices. A malformed, incomplete, truncated or
unsafe review is deny-only. Request body, query, fragment, header values, DNS
addresses, URL userinfo, credentials, Tool input/output, exceptions and local
paths are never included.

## Fingerprint and approval semantics

The fingerprint hashes a canonical structure containing the normalized method,
scheme, IDNA hostname, effective port, full request target, body digest and
ordered header-name/value digests. It exposes no request content.

`PermissionManager` returns an operation-local authorization binding only after
Allow. The adapter compares that binding to the immutable request immediately
before the final checkpoint. There is no network allow cache and every OPTIONS
or mutation operation prompts again.

## Budgets

Initial fixed limits:

| Resource | Budget |
| --- | ---: |
| URL | 4,096 UTF-8 bytes |
| Request headers | 32 |
| Header name | 128 bytes |
| Header value | 4,096 bytes |
| Aggregate header bytes | 16 KiB |
| Encoded request body | 64 KiB |
| Timeout | 0.1–30 seconds |
| Redirects | 3 |
| Response body | 1 MiB |
| Read chunk | 64 KiB |
| Safe rendered Tool output | 15,000 characters |

Booleans are rejected for numeric inputs. Timeout must be finite and positive.
One monotonic deadline covers DNS, connect, redirect and response reads.
Content-Length is checked before streaming and every body, including error
responses, is read with an explicit bounded chunk size.

## Stable error vocabulary

The adapter uses low-cardinality codes:

- `invalid_request`
- `unsupported_scheme`
- `destination_blocked`
- `permission_required`
- `permission_denied`
- `permission_cancelled`
- `permission_expired`
- `request_cancelled`
- `request_body_too_large`
- `response_too_large`
- `timeout`
- `dns_error`
- `tls_error`
- `redirect_blocked`
- `redirect_not_allowed`
- `http_error`
- `network_unavailable`
- `request_failed`
- `unsupported_response_type`

Errors never include the full URL, query, host credential, header value, body,
raw exception, traceback, Workspace or HOME.

## RED→GREEN order

1. Unapproved POST zero side effect.
2. Deny zero side effect.
3. Allow once exact execution and fingerprint binding.
4. Cancel/timeout/close/prompt-unavailable zero side effect.
5. Deterministic final checkpoint race.
6. Destination classification table.
7. Redirect policy.
8. Request budgets.
9. Response budgets.
10. TUI/Gateway/Dashboard review.
11. RunJournal/output/DOM/log redaction.
12. Installed wheel.

## Explicitly outside this batch

`web_fetch`, `web_search`, archive tools, file truthfulness, utility schemas,
ordinary-fact Memory, Agent Loop business flow, Session, MCP, pricing, accepted
semantic gold, Phase 2B thresholds and Dashboard visual design are unchanged.

## Final checkpoint and zero-side-effect evidence

The mutation adapter revalidates the destination after approval, verifies the
opaque request fingerprint, and calls
`PermissionManager.ensure_operation_active()` immediately before constructing
and sending the request. A deterministic Event-based race test pauses after
Allow, cancels the Turn, resumes the checkpoint, and proves the fixture receives
zero requests.

The real loopback and pinned-transport fixtures prove:

- no approval, Deny, Cancel, expiry, broker close/restart and unavailable prompt
  all send zero HTTP requests;
- one Allow sends exactly one request;
- a second identical operation creates a new approval;
- approval cannot be replayed for a different method, destination or content;
- destination re-resolution after approval blocks DNS changes before transport.

## TUI, Gateway, Dashboard and RunJournal

`PermissionManager.ensure_network()` accepts only the exact versioned safe
review and never caches a network decision. The process-local Gateway broker
projects `kind=network`, supports only Allow once/Deny once, and makes unsafe
or incomplete reviews deny-only. The formal Dashboard validates the same union,
escapes every rendered field, hides sensitive values and brackets public IPv6
for display. Permission RunJournal events remain content-free: kind, Tool name,
operation identity, reviewability and terminal decision only.

Browser acceptance used the formal packaged assets with the real broker API.
At 1280×900 the three-column layout had no overflow; the unsafe review exposed
only Deny, while the valid POST review exposed Allow once and Deny with only the
safe fields. At 700×900 the existing responsive Session dock collapsed and
reopened correctly. Console warning/error logs were empty, and DOM checks found
no query, fixture secret, absolute path or `[object Object]`.

## Wheel and audit evidence

The isolated-wheel smoke runs from a non-source cwd and proves full-profile Tool
registration, safe GET, mutation Deny with zero send, Allow once, response
overflow rejection, Gateway Permission API behavior and packaged formal assets.
Runtime dependencies remain `[]`.

The final Functional Audit contains 185 capabilities and 10 still-open issues.
`tool.http_request` is `pass` for deterministic, installed, safety and
truthfulness evidence; live external network is deliberately `blocked` in the
final deterministic audit. SEC-001 is absent/closed. SEC-004 is reassigned to
`tool.web_fetch` because only the HTTP Request response path was closed here.

## Production baseline and semantic evidence

- active baseline: `memory-retrieval-production-v36`
- parent: `memory-retrieval-production-v35`
- manifest SHA-256:
  `7d576aed1594c58e96d3125c28e2556ffab7bb60ccdd43c97b462201456a678a`
- protected files: 58/58, candidate/current match
- exact delta: five changed, two added, zero removed
- historical integrity: v1–v36 all true
- official evaluator: 108 cases, 37 confirmed gaps, Phase 3B true,
  zero remote calls
- accepted gold:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns `1784135857000000000`, unchanged

The two final complete suites each passed 3,042 tests with two skips and only
the three existing benchmark-marker warnings.

## Verification summary

- HTTP safety: 69 passed
- Permission/TUI/Dashboard: 113 passed
- Gateway/Chat/Cancel: 150 passed
- Tooling/RunJournal: 65 passed
- Functional Audit contract: 4 passed
- packaging: 9 passed
- isolated installed-wheel smoke: 1 passed
- v34/v35/v36/baseline/semantic certification: 215 passed
- scoped Ruff, `py_compile`, full `compileall`, `node --check` for both formal
  scripts and the scoped dangerous-pattern scan: passed
- pyright, mypy and pip-audit: not installed, therefore not executed

## Remaining findings and Reliability 1B-1B seam

SEC-002, SEC-003, WEB-001, WEB-002, MEM-001 and TOOL-001/002/003 remain open.
SEC-004 remains open only for `web_fetch` wire bytes and archive aggregate
decompression. SEC-005 also remains an audit finding outside this batch.

Reliability 1B-1B may reuse the stable, standard-library primitives in
`minicode.tools.network_safety`—immutable request normalization, destination
classification and pinning, monotonic deadlines, bounded response reads and
safe error projection—but must add its own RED tests and explicit wiring.
This batch deliberately did not import that module from `web_fetch` or
`web_search`.
