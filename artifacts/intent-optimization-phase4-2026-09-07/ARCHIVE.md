# Offline report storage

`offline-all-baseline.json`, `offline-all-final.json` and `offline-delivery.json` are preserved as compressed original bytes in the [generated-report archive](../../docs/artifact-archive-2026-10-24.md). Their hashes in this directory's existing `manifest.json` are unchanged.

Before using historical scripts that read those reports, run from the repository root:

```sh
python scripts/restore_archived_artifacts.py --restore
```

The command verifies and restores the exact files without Git or network access. Other source snapshots, inputs and reports in this evidence bundle remain in place.
