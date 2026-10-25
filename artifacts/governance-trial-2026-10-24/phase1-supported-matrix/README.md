# Supported-matrix CI: e6492f9b, with preserved upload failure

Exact source `e6492f9be7357a707bb8f05c722aa2af4eefcc3d`, run `40130139264`, run number/seed 50. Attempt 1 completed all four POSIX pytest processes successfully. Its macOS3.11 job nevertheless failed because actions/upload-artifact could not create the JUnit artifact (ENOTFOUND). The original failure, complete raw log and missing XML remain preserved; no XML was invented.

Exactly one `gh run rerun --failed` request was issued after attempt 1 was terminal. Only macOS3.11 has a new start/runner execution. GitHub attempt 2 lists six new API job IDs: the other five retain their original start/end/runner and success. The mapping is retained explicitly; this is not six new runs. Final run conclusion: **success**.

| Job | Evidence attempt / job | Conclusion | XML outcomes | Seed |
| --- | --- | --- | --- | --- |
| Full runtime / Python 3.12 on ubuntu-latest | 1 / 120979908000 | success | 14952 passed / 0 failed / 0 errors / 19 skipped | 50 |
| Experimental package checks / Python 3.11 on windows-latest | 1 / 120979908081 | success | Experimental package/static/archive checks; no full-suite XML | n/a |
| Full runtime / Python 3.12 on macos-latest | 1 / 120979908130 | success | 14963 passed / 0 failed / 0 errors / 8 skipped | 50 |
| Experimental package checks / Python 3.12 on windows-latest | 1 / 120979908151 | success | Experimental package/static/archive checks; no full-suite XML | n/a |
| Full runtime / Python 3.11 on macos-latest | 2 / 120994485852 | success | 14963 passed / 0 failed / 0 errors / 8 skipped | 50 |
| Full runtime / Python 3.11 on ubuntu-latest | 1 / 120979908190 | success | 14952 passed / 0 failed / 0 errors / 19 skipped | 50 |

Each job records PR head separately from the actual checked-out merge SHA and exact test command. Original attempt1 macOS3.11 reported 14,963 passed/8 skipped in 3,307.63s in raw stdout; its XML was not uploaded and is not treated as verified XML. Counts are not summed across environments or retry attempts.

The retry artifact has its own new artifact ID, creation time bound to the retry upload step (one-second API timestamp rounding allowance, exact raw artifact ID/digest also required), run/head identity, API ZIP digest and downloaded ZIP/XML byte hashes. The original artifact inventory lacked macOS3.11 JUnit; the retry XML remains only under attempt2. The read-only lineage review predates the retry result and is explicitly not a final CI acceptance.

The bundle includes original attempt1 raw logs, job metadata and three available POSIX XML files, its terminal run record and frozen-source index; the one retry request and its actual result; and attempt2 metadata, mapping and retry raw/XML. No old candidate green, full experimental Windows diagnostic, model record, environment, artifact ZIP duplicate or poll history is included. The separately visible Windows diagnostic remains independent of these package-only main jobs.

The frozen attempt1 snapshot index lists 54 locally retained originals; the compact bundle selects necessary evidence and does not claim to contain every poll/ZIP listed in that historical index. The initial read-only observer inventory assumption failed and was corrected from the actual API response without any second CI rerun; the initial full response and final mapping are retained.

Archive: 46 ordinary relative files, 10932547 raw bytes / 1823581 gzip bytes; SHA-256 `284655961023c37c53d075a889a7ac184fd1c45b6a26d66ef0ecc0635d85b61e`. Sorted USTAR headers and gzip mtime0/empty filename. Every member was safely restored and byte-compared; sizes/SHA are in manifest.json. Restoring does not execute code. This artifact records evidence, not a merge/promotion decision.
