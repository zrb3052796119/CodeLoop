# PR #3 formal CI evidence — seed 53

[CI run 40151364118](https://github.com/zrb3052796119/CodeLoop/actions/runs/40151364118), attempt 1, completed successfully without reruns. PR head: `fbfed052f628030914d89456fcac75306f7b5d5d`; actual checked-out merge: `5e0b2529ba8f390fe9928ecbfa585c11b64e3445`. All four full runtime jobs used seed **53**.

| Scope | Result |
| --- | --- |
| Linux, Python 3.11 and 3.12 | Each 14,952 passed / 19 skipped / 0 failed / 0 errors |
| macOS, Python 3.11 and 3.12 | Each 14,963 passed / 8 skipped / 0 failed / 0 errors |
| Windows, Python 3.11 and 3.12 | Both experimental package/static/archive checks passed; no full-runtime acceptance |

The repaired semantic-deadline target ran both parameter cases successfully on all four formal runtime environments, with zero target skips. `repaired-target-verification.json` records an XML read, not another test execution. Environment counts and repeats are not summed.

Six raw job logs and metadata, four original JUnit files, terminal run/artifact metadata and the compact verification are retained. API artifact digest, downloaded ZIP SHA, artifact ID/digest in the corresponding raw log, and ZIP member/XML byte equality were checked before packaging. Artifact ZIP duplicates and poll history are omitted; exact artifact IDs and ZIP/XML hashes remain in the summaries and verification.

The 27-file frozen compact allowlist is copied unchanged, plus the final verification, repaired-target read and actual PR merge receipt: **30 members**. PR #3 merged at `2026-10-25T02:44:17Z` into `8c0a9582057cc9d8ccaa86528c4847a19178d28e`. The merge receipt is subsequent history; it does not make seed 53 evidence a post-merge run. Naturally triggered main seed 54 and experimental Windows seed 9 runs remain separately observed, with no conclusion claimed here.

The original main seed 52 macOS failure and fixture repair proof remain preserved in [the fixture evidence](https://github.com/zrb3052796119/CodeLoop/blob/fbfed052f628030914d89456fcac75306f7b5d5d/artifacts/governance-trial-2026-10-24/intent-deadline-fixture-proofs/README.md); this successful run does not replace that red result. [Experimental Windows full diagnostics](https://github.com/zrb3052796119/CodeLoop/actions/runs/40151364058) remain visibly failed and retained separately, outside the approved Linux/macOS formal scope; their large duplicate payload is not included. No production bug or model/productivity benefit is inferred.

Archive: **10,682,724 raw bytes / 1,414,017 gzip bytes**, SHA-256 `839e97db81c4a1688249452e6ffe6c1f994d8f606ba567bd496e12c0b3f48520`. Deterministic sorted USTAR and fixed-time gzip were assembled twice with identical bytes; all 30 ordinary relative members were actually restored and byte-compared with their sources. `manifest.json` supplies every member size/SHA. No source tree, HOME, cache, virtual environment, credential, model transcript or new test execution is included.

To inspect, verify the archive SHA against the manifest, extract only its listed regular relative members into an empty directory, and verify every member size/SHA. Extracting does not execute the included records. Preparation-time absolute paths in metadata are retained for provenance and are not required for reading the evidence.
