# MiniCode Reliability 1B-1C.1 — Phase 2A Certification Hardening

## Outcome

Reliability 1B-1C.1 is certified and Reliability 1B-1C can be closed.
Default Phase 2A pytest and CLI acceptance no longer depend on whether one
real wall-clock sample happens to place canonical P95 above or below 5.0 ms.
The evaluator still measures and reports real latency, and the unchanged
`canonical P95 <= 5.0 ms` gate remains authoritative when strict enforcement
is explicitly requested.

This batch changed no `minicode/` production source, Memory algorithm, fixture,
accepted artifact, semantic gold, runtime dependency, web-search behavior or
performance threshold. It did not create v40 and did not enter Reliability
1B-2.

## Original nondeterminism

The pre-change second Reliability 1B-1C full suite failed with canonical P95
`5.269083 ms` against the fixed `5.0 ms` limit while system CPU was
`84.38% idle`. Two default tests treated that environment-sensitive
observation as an acceptance gate:

- all legacy `performance_gates` had to be true;
- real canonical P95 had to be at most 5 ms.

The CLI used the same legacy gate group as its exit authority. Its defaults
also pointed at the three accepted Phase 2A artifact/doc paths.

`deterministic_phase2a_view()` removed the latency object and per-case
`latency_ms`, but retained the P95-derived legacy gate, `strictPassed` and
timing-derived acceptance. Two otherwise identical reports could therefore
produce different “deterministic” projections solely because one P95 was
5.0 ms and the other was 5.0 ms plus epsilon.

Synthetic RED tests reproduced each contract defect without repeatedly
running a real benchmark or selecting a lucky sample.

## Pure performance policy

`evaluate_phase2a_performance_policy()` is a pure classification seam. It
reads no clock, file, network, environment or global state and accepts only
explicit metrics plus `advisory|strict`.

Deterministic gates:

- average task-start saves at most 2;
- average full-turn saves at most 3.

Wall-clock gate:

- canonical P95 at most 5.0 ms.

The result exposes `enforcementMode`, `deterministicGates`,
`wallClockGates`, `deterministicPassed`, `strictPassed` and
`acceptancePassed`.

- `deterministicPassed` depends only on save-I/O budgets.
- `strictPassed` requires deterministic gates and the real wall-clock gate.
- advisory acceptance uses `deterministicPassed` while retaining a truthful
  false wall-clock observation.
- strict acceptance uses `strictPassed`.

The report then composes that policy with correctness, quality, file
integrity, protected-state immutability and zero network calls. Legacy
`performance_gates` remain as `legacy_observation_only`; they are not default
acceptance authority.

The policy rejects bools, NaN, infinity, negative and non-numeric metrics,
unknown or unhashable modes, and missing required inputs. Boundary tests prove
that 5.0 ms and saves 2/3 pass, 5.0 ms plus epsilon fails strict, and any save
budget excess fails both advisory and strict acceptance.

## Timing-free projection

The deterministic projection now removes or normalizes:

- aggregate and per-case latency;
- the wall-clock legacy gate;
- `wallClockGates`;
- enforcement mode;
- wall-clock-derived `strictPassed`;
- timing-derived policy and report acceptance.

It preserves correctness, quality, deterministic gates, deterministic
acceptance, IDs, scores, candidate/selected/rendered identity, no-match,
budgets, no-network and integrity evidence. Synthetic reports with opposite
wall-clock results project identically, while a deterministic gate change
still produces a different projection.

## CLI and artifact boundary

The default CLI is advisory and writes only generated paths:

- `artifacts/memory-retrieval-phase2a-evaluation.json`;
- `docs/memory-retrieval-phase2a-evaluation.md`;
- `docs/memory-retrieval-phase2a-evaluation-comparison.md`.

`--enforce-wall-clock-performance` selects strict mode using the same
evaluator and the same real measurement. There is no retry, second algorithm,
fake latency or adjusted threshold. Unknown arguments are rejected.

Any output option resolving to one of the three accepted paths is rejected
before evaluation or writing. Tests verify accepted bytes, size and mtime are
unchanged after each rejection.

## One strict benchmark

Exactly one explicit strict run was made with all four outputs under `/tmp`.
The immediately preceding CPU samples were:

- `79.75% idle`, then `85.31% idle`;
- load average `2.37 / 2.61 / 2.41`.

The real result was:

- canonical P50 `1.748625 ms`;
- canonical P95 `2.768958 ms`;
- unchanged 5.0 ms wall-clock gate `true`;
- deterministic acceptance `true`;
- strict result `true`;
- final acceptance `true`;
- remote calls `0`;
- exit code `0`.

The run was not retried. Its temporary directory was removed after evidence
capture.

## Frozen assets and pin cascade

Accepted Phase 2A assets remained byte/size/mtime identical:

| Path | SHA-256 | Size | mtime_ns |
| --- | --- | ---: | ---: |
| `artifacts/memory-retrieval-phase2a.json` | `2f488120e4016d9fafb275cd2b22b7e978ddf8f4039b990aeff1724e00759327` | 2374133 | 1784121750613180196 |
| `docs/memory-retrieval-phase2a.md` | `7414300118d678bbf7d1e1c9eba91c473d11044b83fc19d4ebc7f705d702b09b` | 1935 | 1784121750613367490 |
| `docs/memory-retrieval-phase2a-comparison.md` | `4c148cbe54f4e3d39ed5f2e1726f8ba7ee465b93d9329d7f39d884c0fa66e3fe` | 914 | 1784121750613434490 |

The exact Phase 2A frozen-pin changes, reason
`phase2a_wall_clock_policy_hardening`, were:

| Path | Previous SHA-256 | Current SHA-256 |
| --- | --- | --- |
| `scripts/evaluate_memory_retrieval_phase2a.py` | `6371ea3da21fe40845c588ece56679d451ab087d9acf8fa64aa8691a4fbae1ad` | `24caf504c1b7965cb4ad69e539091a7d741eb4f0a00b9903d1d6a289a48185b5` |
| `scripts/memory_retrieval_phase2a_evaluator.py` | `f0ac492f8ab0d83055cc1e78ada4d38fa249276228e57f3dfc5fd6eacdd3ca3e` | `e65b6ecb59804d7ff5aa04113f6028b64d546c2abf75436175dc40bf39c4a404` |
| `tests/test_memory_retrieval_phase2a_evaluator.py` | `ad4693f597b1dbb754520ee883b36fc78b9d4f9e257f79e0b88a6251dd45b0ae` | `bb8193c5c60b4025f96908251c0af8594764dff66c6c80d48e7e780fb4748759` |

Only those three entries changed in
`PHASE2A_FROZEN_HASHES`. Normalizing those three pin strings makes the current
Phase 2B evaluator byte hash equal its prior hash, proving its change is
pin-only. Its resulting SHA is
`e8c075c3e114c2c5f9c1645e1b53ea365973de883eb3f6a8b2c833ecbef0765d`.

Only that evaluator entry changed in semantic
`PHASE2B_FROZEN_HASHES`; all other Phase 2A and Phase 2B pins remain exactly
unchanged. Normalizing the one semantic pin restores the prior semantic
evaluator SHA, proving the cascade ends there. Controlled temporary-tree
tampering reports exactly the modified evaluator and never rewrites an
accepted artifact.

Accepted Phase 2B artifacts remained byte/size/mtime identical:

| Path | SHA-256 | Size | mtime_ns |
| --- | --- | ---: | ---: |
| `artifacts/memory-retrieval-phase2b.json` | `2d082e1aa50c1461a78ef5e18c56b59533460a140634effb911fd6c5b4bd3996` | 94181 | 1784815255303450427 |
| `artifacts/memory-retrieval-phase2b.schema.json` | `a0a9a8093e9970d1fcd275f9d7670804b8b2ecd67ec468b45c13b5ee3390820a` | 6408 | 1784815045149215200 |
| `docs/memory-retrieval-phase2b-comparison.md` | `6e2649e0345f6ec58433d3863a160e8cceb8e8828253cfec842faf35951113e5` | 547 | 1784815255324455321 |
| `docs/memory-retrieval-phase2b-performance.md` | `3cff028426be913baa06cacbd2eff69b3141f74ff16528d5e44b4f37416a5235` | 789 | 1784815255324553279 |
| `docs/memory-retrieval-phase2b.md` | `9ec83beff0ab5a5c0b2af3fd65e62f37b441a4416e556b98c751032e51027da9` | 1660 | 1784815255314381393 |

## Certification

- Phase 2A directed: `105 passed`.
- Phase 2B/consolidation regression: `56 passed`.
- semantic freeze directed: `34 passed`.
- first complete suite: `3355 passed, 2 skipped, 3 warnings` in 207.13 s.
- official semantic evaluator: 108 cases, 37 confirmed gaps, Phase 3B true,
  zero remote calls and evaluation passed.
- behavior projection:
  `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`.
- per-case fingerprint:
  `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
- second complete suite: `3355 passed, 2 skipped, 3 warnings` in 207.33 s.
- scoped Ruff, `py_compile`, `compileall -q minicode scripts tests` and
  production JavaScript `node --check`: passed.
- pyright, mypy and pip-audit: not installed, therefore not run.
- raw path/secret/traceback scan: generated reports and formal assets clean;
  only the evaluator's protective scanner literals match in source.

Accepted semantic gold remains SHA
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
size `3033592`, mtime_ns `1784135857000000000`.

The v39 verifier remains active with parent v38, manifest SHA
`9bcf038d20aa7c044f4db613626b484e2fa89819929be20b46390bca00a99d6e`,
62/62 production files, candidate/current match and all v1–v39 integrity
flags true. Web-search production hashes remain:

- `http_utils.py`: `d677707fe69f25147fe98ad51f6bd733276191ff05988c3b32d6734db6d1bd84`;
- `search_providers.py`: `e0baa6e1924feb90d422c2d6fb211c69213a8d9d0a18da49543009ffdc4643d5`;
- `web_search.py`: `c2c2912914ef76024dd4e768bba73ea41fce6dbbc758943d6ed310e07ebbb187`.

Functional Audit remains 185 capabilities, 124 pass, 44 partial, 7 fail,
1 unavailable, 6 blocked and 3 not reachable, with exactly `SEC-002`,
`SEC-004`, `TOOL-001`, `TOOL-002`, `TOOL-003`, `SEC-005` and `MEM-001`.
`WEB-001` and `WEB-002` did not return. Dependencies remain `[]`.

Because all 62 production files and formal frontend assets are byte-identical
to certified v39, the prior Reliability 1B-1C isolated wheel and browser
evidence is reused rather than falsely reported as repeated. Packaging tests
were included in both complete suites.

## Stable next interface

The stable certification interface for future work is:

- one evaluator that always records real wall-clock observations;
- one pure policy that classifies deterministic and wall-clock gates;
- advisory default acceptance for correctness/quality/integrity/no-network
  and deterministic budgets;
- explicit strict opt-in for the unchanged real performance threshold;
- timing-free projections containing no wall-clock-derived authority;
- generated outputs separated from immutable accepted history.

Reliability 1B-2 remains separately authorized work.
