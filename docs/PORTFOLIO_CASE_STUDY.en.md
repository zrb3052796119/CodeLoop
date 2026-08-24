# CodeLoop 3-Minute Case Study: Learning from a Tool Failure

> [中文详细版](./PORTFOLIO_CASE_STUDY.md) ·
> [sanitized attribution projection](../artifacts/persistent-memory-large-study-v3/auth-policy-attribution.json) ·
> [full paired-study report](./2026-08-21--persistent-memory-large-study--r1--robustness-check.md)

## The 3-Minute Version

Here, **warm / Memory** means a new Run receives the relevant approved lesson;
**cold** repeats the same task without it. Paired numbers compare reuse-stage
target Turns; learning and reflecting on the lesson has a separate upfront
cost.

**Problem — 30 seconds.** I extended an existing MiniCode Python runtime with
an evidence-controlled persistent-learning loop. In this controlled case, a
real remote provider operated on a synthetic repository. Fault injection
required the learning Run to first read the nonexistent
`src/auth_policy.py`. The agent recovered to
`backend/src/auth_policy.py` and returned the exact marker
`AUTH-POLICY-731`.

**Mechanism — 50 seconds.** The runtime did not persist an arbitrary model
summary. It paired the failed `read_file`, the corrected `read_file`, and the
successful target content as structured recovery evidence. The recovery policy
classified that corrected read as targeted tool-recovery verification, passed
the lesson through safety/approval checks, and stored a project Memory entry.
No independent test command ran inside the learning Run; an external experiment
oracle checked the marker, tool trace, and no-edit condition after the Run.

**Cross-conversation reuse — 40 seconds.** A new conversation received only:

```text
Read the gateway authentication policy and report its exact policy marker.
Do not edit files.
```

The prompt contained neither path nor marker. The public
[attribution projection](../artifacts/persistent-memory-large-study-v3/auth-policy-attribution.json)
publishes the learned-entry / `renderedEntryIds` join and raw-source hashes.
It is an inspectable curated attestation; a full raw audit still requires the
locally retained sidecars. Retrieval and injection counts were both one. The
warm Run's first repository action read the correct file; a matched cold Run
began with directory exploration.

| Target-turn metric | Memory / warm | Cold |
| --- | ---: | ---: |
| External-oracle success | yes | yes |
| Tool calls | **1** | 4 |
| Model calls | **2** | 5 |
| Cumulative input tokens | **13,358** | 33,065 |

**Scale and boundary — 60 seconds.** The larger study covered 16 synthetic
path-recovery families, three provider blocks, and 48 warm/cold pairs. Tool
calls were 50 vs 240 (-79.2%); task input tokens were 652,911 vs 1,539,738
(-57.6%); success was 48/48 vs 47/48. This supports one bounded claim:
relevant, approved path-recovery lessons reduced repeated discovery in this
controlled task family. The initial error was induced, the remote model version
was not frozen, and the study did not cover complex edits or a noisy Memory
store.

**Engineering value.** This is not only a retrieval hit-rate demo. It connects
write evidence, safety/approval, canonical retrieval, rendered-entry
attribution, and paired external oracles—each with a separate failure surface.

Stop here for a three-minute interview answer. The remainder is an audit index.

## Audit Trail

### Learning Run

```text
run_589e8d07a4934c8e9f2eb668891c1373

read_file("src/auth_policy.py")
  → error[not_found]
  → list_files × 3
  → read_file("backend/src/auth_policy.py")
  → success, marker = AUTH-POLICY-731
```

The experiment fixture deliberately induced the wrong first action so the
failure/recovery pair was deterministic. This is a fault-injection test, not an
estimate of natural model error frequency.

The resulting sanitized identity is:

```text
entry_id: project-1787325545078471000-517600cb
claim_id: claim-000001
source: reflection
safety_status: safe
approval_status: approved
approval_policy: auto_approve_verified
recovery_evidence_kind: tool_recovery
recovery_evidence_scope: targeted
independent_verifier_in_learning_run: false
```

The general Run verification event remained `unverified`; “verified” in the
approval policy refers to the runtime's targeted corrected-tool recovery
classification. The post-Run experiment oracle is separate.

### Reuse Run

```text
run_c40582fd5e9f4ce0b7c1691fe0e1e930
```

The rendered Memory identity was:

```json
{
  "entryIds": ["project-1787325545078471000-517600cb"],
  "schemaVersion": 1
}
```

The raw journal and workspace remain local because they can contain complete
prompts, responses, paths, and project Memory. The committed sanitized artifact
publishes only synthetic fields, the asserted ID join, public manifest/result
hashes, and SHA-256 identities for the retained local sources. Its regression
test joins the public Run IDs and target-turn metrics to frozen data.

### Three-block family result

To avoid selecting one favorable sample, the same `auth-policy` family ran in
three provider blocks:

| Metric | Warm mean | Cold mean | Change |
| --- | ---: | ---: | ---: |
| Success | 3/3 | 3/3 | equal |
| Tool calls | 1.00 | 3.33 | -70.0% |
| Model calls | 2.00 | 3.67 | -45.5% |
| Input tokens | 13,356 | 24,216 | -44.8% |
| Duration | 2.74 s | 4.92 s | -44.4% |
| Direct-first | 3/3 | 0/3 | +3 |

Sources:

- [frozen manifest](../artifacts/persistent-memory-large-study-v3/manifest.json)
- [frozen first-run result](../artifacts/persistent-memory-large-study-v3/full-results-initial.json)
- [target-turn rows](../artifacts/persistent-memory-large-study-v3/analysis-output/turn-level.csv)
- [learning-turn rows](../artifacts/persistent-memory-large-study-v3/analysis-output/learning-turn-level.csv)
- [family summary](../artifacts/persistent-memory-large-study-v3/analysis-output/family-summary.csv)
- [sanitized attribution projection](../artifacts/persistent-memory-large-study-v3/auth-policy-attribution.json)

## Reproduce the Public Analysis

```bash
python -m pytest -q \
  tests/test_portfolio_case_artifact.py \
  tests/test_persistent_memory_large_study.py \
  tests/test_persistent_memory_large_study_analysis.py

python scripts/analyze_persistent_memory_large_study.py \
  --manifest artifacts/persistent-memory-large-study-v3/manifest.json \
  --result artifacts/persistent-memory-large-study-v3/full-results-initial.json \
  --output-dir /tmp/codeloop-memory-analysis
```

The analyzer writes equivalent aggregates into the output report and
`statistics.json`; it does not print the following summary verbatim to stdout:

```text
analyzed 96 target Turns, 48 pairs, 16 families
warm: 48 successes, 1.0417 tools, 2.0417 model calls, 13602.3 input tokens
cold: 47 successes, 5.0000 tools, 4.8125 model calls, 32077.9 input tokens
```

Frozen input SHA-256 values:

```text
manifest.json:             923272933307127ab0a99e45e1e8449f10ee8a121810baf05e71196d195f6e0d
full-results-initial.json: 6cb06e4ce0aca747f837a678b8f678ceb7b5249ba6ae4e078664c55adbaed592
```

## Limitations to State Unprompted

- These are synthetic, read-only path-recovery tasks—not general coding work.
- The learning error was fault injection, not a naturally sampled failure.
- All 16 families share the same high-level recovery mechanism.
- Relevant Memory was guaranteed; noisy-store hard negatives and conflicts were
  outside this study.
- The V3 result did not hash-bind the remote model ID or provider version.
- 48/48 vs 47/48 is descriptive; no non-inferiority margin was preregistered.
- Learning has an upfront cost. The descriptive input-token break-even estimate
  is about 1.93 similar reuses, or 2.03 including reflection.
- The sanitized join is public, but the full raw evidence sidecar is not.
