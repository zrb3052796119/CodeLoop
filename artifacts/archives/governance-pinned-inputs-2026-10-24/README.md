# Archived pinned test and replay inputs

This separate manifest preserves eight complete original JSON inputs. Original
SHA-256 values, accepted failures, source refs and historical evidence manifests
are unchanged. The older four-report archive and its manifest remain unchanged.

Before quality checks, pytest or historical replay, run from the repository root:

```sh
python scripts/restore_archived_artifacts.py --manifest artifacts/archives/governance-pinned-inputs-2026-10-24/manifest.json --restore
```

Omit `--restore` to verify without writing. Add `--output-root /tmp/codeloop-input-export`
to restore elsewhere; consumers with fixed repository-relative paths still need
in-place recovery. The existing helper validates packed and original sizes/hashes,
refuses to overwrite differing files, and needs no Git history, network or model.
Recovered originals are ignored local files. No evaluator supports transparent
gzip input: restoration is an explicit prerequisite, also performed by CI.

See [manifest](manifest.json) and [archive documentation](../../../docs/artifact-archive-2026-10-24.md).
This reduces tracked checkout/source-archive content, not Git history. Running the
tests/replays still requires disk space for the original bytes after restoration.

## Verification evidence

[verification.tar.gz](./verification.tar.gz) retains the clean-export restoration, logical-tree comparison, real consumer probes, quality profiles, focused pytest logs and independent byte review. [verification-manifest.json](./verification-manifest.json) records every original member hash; the compressed archive was restored and compared before commit. The focused run was 145 passed / 1 sandbox cleanup failure; the unchanged failed node passed separately on the host. These runs are not added together or described as a full green suite. Full combined-candidate acceptance is recorded separately.

The verification archive is a plain tar.gz evidence bundle; the JSON restore helper only handles entries in [manifest.json](./manifest.json). Extract verification evidence into a new empty directory with `tar -xzf verification.tar.gz -C /tmp/your-empty-evidence-directory`. Original absolute paths and precommit states remain historical records.
