#!/usr/bin/env python3
"""Create a permission-hardened current-state backup of formal memory files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SNAPSHOT_KIND = "current_post_contamination_state"
FORMAL_RELATIVE_PATHS = (
    Path("memory/memory.json"),
    Path("memory/MEMORY.md"),
    Path("memory/approval_audit.json"),
    Path("sessions_index.json"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class SnapshotResult:
    backup_dir: Path
    manifest_path: Path
    file_count: int
    all_hashes_match: bool


def create_snapshot(
    *,
    real_home: Path,
    backup_root: Path | None = None,
    timestamp: str | None = None,
) -> SnapshotResult:
    """Copy existing formal files byte-for-byte without importing MiniCode."""
    home = Path(real_home).expanduser().resolve()
    mini_code_dir = home / ".mini-code"
    snapshot_label = timestamp or datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    root = Path(backup_root or mini_code_dir / "recovery-backups").expanduser().resolve()
    backup_dir = root / f"memory-test-isolation-{snapshot_label}"
    backup_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(backup_dir, 0o700)

    captured_at = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, object]] = []
    for relative in FORMAL_RELATIVE_PATHS:
        source = mini_code_dir / relative
        if not source.is_file():
            continue
        backup = backup_dir / source.name
        shutil.copyfile(source, backup)
        os.chmod(backup, 0o600)
        source_stat = source.stat()
        records.append(
            {
                "source_path": str(source),
                "backup_filename": backup.name,
                "source_hash": _sha256(source),
                "backup_hash": _sha256(backup),
                "size": source_stat.st_size,
                "mtime_ns": source_stat.st_mtime_ns,
                "snapshot_timestamp": captured_at,
                "snapshot_kind": SNAPSHOT_KIND,
            }
        )

    manifest = {
        "snapshot_kind": SNAPSHOT_KIND,
        "snapshot_timestamp": captured_at,
        "files": records,
    }
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(manifest_path, 0o600)
    return SnapshotResult(
        backup_dir=backup_dir,
        manifest_path=manifest_path,
        file_count=len(records),
        all_hashes_match=all(
            record["source_hash"] == record["backup_hash"] for record in records
        ),
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-home", type=Path, default=Path.home())
    parser.add_argument("--backup-root", type=Path, default=None)
    parser.add_argument("--timestamp", default=None)
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    result = create_snapshot(
        real_home=args.real_home,
        backup_root=args.backup_root,
        timestamp=args.timestamp,
    )
    print(
        json.dumps(
            {
                "backup_dir": str(result.backup_dir),
                "file_count": result.file_count,
                "all_hashes_match": result.all_hashes_match,
                "snapshot_kind": SNAPSHOT_KIND,
            },
            sort_keys=True,
        )
    )
    return 0 if result.all_hashes_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
