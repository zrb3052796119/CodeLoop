# MiniCode Reliability 1B-1A.1: Bounded DNS Resolver

## Status

Implementation and certification complete.
DNS-001 is the P1 resource-exhaustion defect closed by this batch. It was found
after Reliability 1B-1A certification and is not retroactively presented as an
Audit 1A discovery.

## Original linear-growth RED

The original `_resolve_with_deadline()` started one daemon thread per request.
A controlled `getaddrinfo()` blocker produced this exact result:

- live threads before: 1;
- timed-out calls: 25/25;
- bottom resolver entries: 25;
- live threads after: 26;
- retained resolver threads: 25.

The first production-facing test failed at `assert entered <= 4` because the
actual value was 25. Releasing the test blocker reduced its retained threads to
zero.

## Call graphs

Original:

```text
validate_destination
  -> _resolve_with_deadline
    -> new daemon Thread for this request
      -> socket.getaddrinfo (possibly uninterruptible)
  -> caller waits only until deadline
  -> timed-out thread remains
```

Current:

```text
validate_destination
  -> one process-local BoundedResolver
    -> fixed pending deque
    -> fixed daemon workers
      -> socket.getaddrinfo
  -> caller's original monotonic deadline
  -> NetworkSafetyError with low-cardinality code
```

`http_utils.py` did not change. The existing pinned HTTP/TLS transport,
Permission, redirect, response and output interfaces are unchanged.

## Capacity contract

| Resource | Fixed limit |
| --- | ---: |
| Worker threads | 4 |
| Queued work items | 8 |
| Maximum outstanding | 12 |

Workers are created once, lazily, and are never replaced after a caller
timeout. Thread names contain only `minicode-dns-resolver-<ordinal>`.
The queue is a fixed-capacity `deque`; there is no unbounded `Queue`, Future
submission, executor or cache.

When capacity is full, submission fails closed immediately with
`resolver_busy`. The Tool projection is fixed:
`error[resolver_busy]: The DNS resolver is temporarily busy.` It contains no
hostname, port, query, answer or exception.

## Deadline and abandonment

Queue wait and bottom DNS execution share the caller's original monotonic
deadline. A queued item that expires is removed before execution and can never
perform late DNS work. An active caller that expires is marked abandoned, but
its worker remains active and continues to occupy its fixed worker slot until
the real `getaddrinfo()` returns.

When that bottom call returns, an abandoned result is discarded, the slot is
released and no hostname, answer or raw exception is retained. The resolver
does not grant a fresh timeout after queue wait.

## Close and process exit

`close()` is idempotent and non-blocking:

- it stops new submission;
- queued waiters receive `network_unavailable` and are removed;
- active waiters are awakened with the same safe error;
- active bottom resolver calls retain their worker slots until they return;
- it never joins an uninterruptible worker.

Workers are daemon threads, so an uninterruptible platform resolver cannot keep
the process alive. A real child process exited successfully while its snapshot
still reported one active blocked worker. The same proof runs from the
installed wheel with a three-second outer safety budget.

## Recovery and concurrency

After a blocked call is released, `active_count` returns to zero and a later
public IPv6 answer succeeds on the same worker set. No executor or replacement
pool is created.

Concurrent tests prove:

- active and queued counts never exceed their limits;
- each caller retains an independent deadline;
- a timed-out slow call does not change a simultaneous fast result;
- close/submit and completion/timeout races terminate without deadlock;
- no counter becomes negative and no slot is released twice.

## Exception and privacy contract

`socket.gaierror`, `OSError` and unexpected `RuntimeError` all become
`dns_error`. The same worker subsequently serves a successful call. Neither
`ResolverError`, `NetworkSafetyError`, snapshots, thread names nor audit
evidence include raw exception text or target content.

`ResolverSnapshot` contains only:

- `worker_limit`;
- `queue_limit`;
- `active_count`;
- `queued_count`;
- `accepting`;
- `closed`.

## TDD slice order

1. Twenty-five timeouts no longer create 25 workers.
2. Fixed pending capacity and 100 fail-closed submissions.
3. Queue wait consumes the original deadline.
4. Active abandoned results are discarded after real completion.
5. Capacity recovers without rebuilding the pool.
6. Close wakes waiters and never joins a blocker.
7. A real blocked child process exits.
8. Concurrent deadlines and close/submit races remain bounded.
9. Resolver exceptions are redacted and workers survive.
10. `validate_destination()` preserves public/unsafe/pinning behavior.
11. `http_request` saturation performs zero transport sends.
12. Installed wheel includes and exercises the resolver boundary.

The initial Slice 1 behavior test was RED then GREEN. The small deep module
necessarily supplied several later invariants together; those later tests were
run one slice at a time and were already GREEN. No artificial regression was
introduced to manufacture failures.

## HTTP, Permission and TLS regression

Existing tests continue to prove safe GET/HEAD, one-operation mutation
Permission, Allow once, Deny/Cancel/Timeout zero-send behavior, post-approval
destination revalidation, request fingerprint binding, bounded redirects and
responses, pinned IP transport, and original TLS hostname/SNI verification.
Resolver saturation returns before `_open_no_redirect()` and therefore sends
zero bytes.

## Installed wheel and Functional Audit

The wheel contains `minicode/tools/bounded_resolver.py`. From a non-source cwd,
the installed smoke covers safe GET, DNS failure, saturation, process exit,
Permission interfaces and Gateway behavior.

Functional Audit continues to report `tool.http_request` as pass with no issue.
Its deterministic evidence records the 4/8/12 capacity, `resolver_busy` and
daemon-exit contract. SEC-001 remains closed. SEC-004 remains open only for
`web_fetch` and archive aggregate decompression.

## Production baseline and semantic gold

- active baseline: `memory-retrieval-production-v37`;
- parent: `memory-retrieval-production-v36`;
- manifest SHA-256:
  `27dda6944d88016ceabcd08960b3b2ef230df7460590d1165b3195ed23adb67b`;
- exact production delta: one changed, one added, zero removed;
- protected files: 59.

The accepted semantic gold remains:

- SHA-256:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`;
- size: 3,033,592 bytes;
- mtime_ns: `1784135857000000000`.

Final certification evidence:

- resolver/HTTP/packaging focused suite: 92 passed;
- Permission/TUI/Dashboard suite: 113 passed;
- Gateway/Chat/Cancel/Turn suite: 153 passed;
- Functional Audit contract: 4 passed;
- production baseline suites: 189 passed;
- semantic evaluator tests: 32 passed;
- official evaluator: 108 cases, 37 confirmed gaps, Phase 3B gate true,
  zero remote calls, evaluation passed;
- complete pytest, run 1: 3062 passed, 2 skipped;
- complete pytest, run 2: 3062 passed, 2 skipped;
- scoped Ruff, `py_compile`, `compileall`, and both JavaScript syntax checks:
  passed.

The three warnings in each complete pytest run are the pre-existing unregistered
`benchmark` marks. pyright, mypy and pip-audit were not installed and therefore
were not reported as executed.

## Explicitly not fixed

SEC-002, SEC-003, WEB-001, WEB-002, MEM-001, TOOL-001, TOOL-002, TOOL-003 and
SEC-005 remain open. SEC-004 remains open for `web_fetch` and archive
decompression. This batch did not wire the resolver into `web_fetch` or
`web_search`, change archive behavior, enter Reliability 1B-1B, alter
Permission/UI contracts, add a runtime dependency, or change the semantic gold
or performance thresholds.

## Stable seam for Reliability 1B-1B

Future Web work may explicitly reuse:

- `BoundedResolver.resolve(hostname, port, deadline=...)`;
- `BoundedResolver.snapshot()`;
- non-blocking `BoundedResolver.close()`;
- `validate_destination(url, deadline=...)`;
- `ValidatedDestination` address pinning;
- `NetworkSafetyError` low-cardinality projection;
- the existing bounded response reader.

Reliability 1B-1B must add its own RED tests before wiring `web_fetch` or
`web_search`; this batch makes no claim that either capability is repaired.
