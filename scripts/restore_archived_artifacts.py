"""Verify or restore archived generated reports without Git or network access."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path("artifacts/archives/governance-2026-10-24/manifest.json")


def _relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("archive paths must be normalized repository-relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("archive paths must be normalized repository-relative paths")
    if not path.parts or path.parts[0] != "artifacts":
        raise ValueError("archived reports must stay under artifacts/")
    return Path(*path.parts)


def _contained(root: Path, relative: Path) -> Path:
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("archive path escapes its root")
    return target


def verified_reports(root: Path, manifest: Path = DEFAULT_MANIFEST) -> list[tuple[Path, bytes]]:
    """Read all originals and validate both the packed and unpacked hashes."""
    payload = json.loads((root / manifest).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("entries"), list):
        raise ValueError("unsupported archive manifest")
    seen = set()
    result = []
    for entry in payload["entries"]:
        path = _relative_path(entry["original_path"])
        if path in seen:
            raise ValueError(f"duplicate original path: {path}")
        seen.add(path)
        archive = _contained(root, _relative_path(entry["archive_path"]))
        packed = archive.read_bytes()
        if len(packed) != entry["archive_bytes"] or hashlib.sha256(packed).hexdigest() != entry["archive_sha256"]:
            raise ValueError(f"compressed archive hash/size mismatch: {archive}")
        expected_size = entry["original_bytes"]
        if type(expected_size) is not int or expected_size < 0:
            raise ValueError("original byte count must be a nonnegative integer")
        with gzip.open(archive, "rb") as stream:
            original = stream.read(expected_size + 1)
        if len(original) != expected_size or hashlib.sha256(original).hexdigest() != entry["original_sha256"]:
            raise ValueError(f"original report hash/size mismatch: {path}")
        result.append((path, original))
    return result


def restore_reports(reports: list[tuple[Path, bytes]], output_root: Path) -> int:
    """Restore exact bytes; preflight all destinations and never overwrite data."""
    output_root = output_root.resolve()
    pending = []
    for relative, original in reports:
        target = _contained(output_root, relative)
        if target.exists():
            if target.is_file() and target.read_bytes() == original:
                continue
            raise FileExistsError(f"refusing to replace existing output: {target}")
        pending.append((relative, original))
    for relative, original in pending:
        target = _contained(output_root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Recheck after creating parents; exclusive creation also refuses a
        # file or symlink that appeared since preflight.
        target = _contained(output_root, relative)
        with target.open("xb") as stream:
            stream.write(original)
    return len(pending)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="repository or exported source root containing the archives")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--restore", action="store_true",
                        help="restore originals; without this flag only verify archives")
    parser.add_argument("--output-root", type=Path,
                        help="restore under this directory; defaults to --root")
    args = parser.parse_args()
    reports = verified_reports(args.root, args.manifest)
    print(f"Verified {len(reports)} reports ({sum(len(raw) for _, raw in reports):,} original bytes).")
    if args.restore:
        output_root = args.output_root or args.root
        count = restore_reports(reports, output_root)
        print(f"Restored {count} missing reports under {output_root.resolve()}.")


if __name__ == "__main__":
    main()
