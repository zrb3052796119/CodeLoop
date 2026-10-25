# Windows portable Memory publication proof

This package documents a pre-existing Windows publication bug. Original Windows
3.11 and 3.12 CI jobs failed the installed-wheel Memory approval smoke with
WinError 32: `_publish_portable` tried to replace a temporary file while its
`mkstemp` descriptor remained open. The publisher bytes are identical at the
initial `88ec62d0`, `9f936942`, and published P0 `ff2f18ea` references. This is not
a Phase 1 context-retention extraction regression.

The production fix adds two statements and two comments: after write/fsync/fstat
and regular-file/size validation, close the descriptor and set it to None; then
perform the existing pathname identity/size/reparse, parent and target checks.
A failed close aborts before replace. POSIX code is unchanged. Portable path
revalidation remains weaker than descriptor-relative containment; post-replace
rejection does not promise rollback or immunity to arbitrary concurrent tampering.

## Evidence

- `before-final-tests.log` and `after-final-tests.log` use exactly the same final
  test bytes and argv. The two fresh/existing-target cases fail on the original
  source, pass on the fix. The sharing seam checks the real captured fd with
  fstat; it does not merely record close calls. It then uses real os.replace.
  Both runs use the final fixture with publisher-local os/tempfile proxies,
  keeping standard-library objects untouched. The baseline overlay records its
  actual imported publisher path and has the exact archived original source.
- `after-portable.log`: 10 cases pass. Extra cases cover close failure, changes
  at the close boundary (same-size inode substitution, size, parent, target),
  and post-replace identity/size/reparse rejection. Reparse metadata is simulated;
  the underlying filesystem operations are real.
- `after-safety-publication.log`: six safety/publication files, 96 passed.
- `after-packaging.log`: real wheel build/install plus packaging checks, 9 passed.
  This ran before the test-only hook isolation; the production and packaging
  test bytes did not change, and the affected six-file safety set was rerun.
- `ruff.log`, `diff-check.log`, and `independent-review.*`: strict E/F lint,
  whitespace check and independent read-only review with no blocking findings.
- `ci/` preserves both complete original job logs and small sanitized failure
  excerpts. `windows-publication-source-proof.json` records the original source
  and raw-log hashes. The empty first download attempts and intermediate test
  log are deliberately excluded. A temporary overlay setup failure (missing
  tests/__init__.py, no tests collected) is excluded from regression evidence;
  the corrected overlay run above collected and failed exactly two cases.
- `final.patch`, `test_memory_authority_io.py`, `publisher-before.py`, and
  `source-and-proof.json` preserve the reviewed change, exact final test and
  baseline bytes. No full source tree, wheel directory, or pycache is included.

Fix validation above ran on macOS. **A native Windows run of the fixed candidate
and its full acceptance suite are still pending.** The original Windows failure
and the local real-fd seam are complementary evidence, not a claim of a native
Windows pass.

## Reproduce

Use an isolated scratch checkout at the `worktreeBase` in source-and-proof.json,
install its development requirements, and copy the archived final test to
`tests/test_memory_authority_io.py`. Its publisher must match beforeSourceSha256.
Run the argv in before-final-tests.json from that checkout with its root as
PYTHONPATH, an isolated HOME, and API/MINI_CODE settings removed (run_checks.py
shows the executed harness). Expect the two sharing tests to fail. Apply only
the production hunk in final.patch; rerun the identical command and expect two
passes. Run the portable selection and related files listed in the run metadata
to verify preserved rejection/cleanup behavior. Native Windows additionally
exercises the installed-wheel smoke without the sharing seam.

Each archive member and the compressed archive has a SHA-256 in manifest.json.
The archive uses deterministic names, modes, timestamps and gzip mtime=0.
