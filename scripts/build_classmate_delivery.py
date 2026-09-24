"""Build an allowlisted working-tree source delivery without personal state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

DEFAULT_DATE = "2026-09-18"
ARCHIVE_ROOT = "CodeLoop"
EXCLUDED_PARTS = {
    "__pycache__",
    "dashboard_prototype",
    ".git",
    ".mini-code",
    ".claude",
    ".codex",
    ".agents",
    ".venv",
    "node_modules",
}
ROOT_README = """# CodeLoop 同学交付版

请先阅读 [交付说明与文档目录](docs/handoff/README.md)。

环境要求：Python 3.11 或更新版本。推荐创建独立虚拟环境后，在此目录运行：

```sh
python -m pip install .
python docs/handoff/tools/self_check.py
```

自检使用离线 mock，不验证真实模型服务。真实使用前请按交付文档配置自己的 API。
此包是当前工作区的运行源码快照，包含未提交变更；准确内容以 DELIVERY-MANIFEST.json 为准。
"""


def safe_files(root: Path, relative_dir: str) -> list[Path]:
    """Walk only the nominated tree; never follow any directory symlink."""
    directory = root / relative_dir
    if not directory.is_dir() or directory.is_symlink():
        return []
    result: list[Path] = []
    for current, directories, filenames in os.walk(directory, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if name not in EXCLUDED_PARTS
            and not name.startswith(".")
            and not (Path(current) / name).is_symlink()
        )
        for name in sorted(filenames):
            path = Path(current) / name
            if stat.S_ISREG(path.lstat().st_mode):
                result.append(path)
    return result


def read_regular(root: Path, path: Path) -> bytes:
    relative = path.relative_to(root)
    for parent in (relative, *relative.parents):
        if (root / parent).is_symlink():
            raise ValueError(f"Refusing symlink: {relative.as_posix()}")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Not a regular file: {relative.as_posix()}")
    return path.read_bytes()


def write_regular(path: Path, content: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Refusing non-regular output: {path}")
    path.write_bytes(content)


def git_head(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def make_archive(root: Path, output: Path, date: str) -> dict[str, object]:
    if root.is_symlink():
        raise ValueError("Source root must not be a symlink")
    for parent in (output, *output.parents):
        if parent.is_symlink():
            raise ValueError(f"Output directory must not traverse symlinks: {parent}")
    output.mkdir(parents=True, exist_ok=True)

    nominated = {root / "pyproject.toml", root / ".env.example"}
    for relative in ("CONTRIBUTIONS.md", "LICENSE", "minicode/cron.example.json"):
        candidate = root / relative
        if candidate.exists() and not candidate.is_symlink():
            nominated.add(candidate)
    nominated.update(
        path for path in safe_files(root, "minicode") if path.suffix == ".py"
    )
    nominated.update(
        path
        for path in safe_files(root, "minicode/web/static")
        if path.suffix in {".html", ".js", ".css"}
    )
    nominated.update(
        path
        for path in safe_files(root, "docs/handoff")
        if path.suffix in {".md", ".py"} or path.name == ".env.example"
    )
    handoff_readme = root / "docs/handoff/README.md"
    if handoff_readme not in nominated:
        raise ValueError("docs/handoff/README.md is required before packaging")

    payloads: dict[str, tuple[bytes, str]] = {}
    for path in sorted(nominated):
        relative = path.relative_to(root).as_posix()
        payloads[f"{ARCHIVE_ROOT}/{relative}"] = (read_regular(root, path), relative)
    payloads[f"{ARCHIVE_ROOT}/README.md"] = (
        ROOT_README.encode("utf-8"),
        "generated: delivery entrypoint",
    )
    project = tomllib.loads(
        payloads[f"{ARCHIVE_ROOT}/pyproject.toml"][0].decode("utf-8")
    )["project"]
    entries = [
        {
            "path": name,
            "source": source,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for name, (content, source) in sorted(payloads.items())
    ]
    manifest = {
        "schema_version": 1,
        "project": "CodeLoop",
        "package_name": project["name"],
        "package_version": project["version"],
        "delivery_date": date,
        "baseline_git_head": git_head(root),
        "snapshot_kind": "working-tree",
        "includes_uncommitted_changes": True,
        "snapshot_note": "Files are read from the working tree, including local changes; baseline_git_head does not identify the delivered content.",
        "file_count": len(entries),
        "files": entries,
        "manifest_scope": "files lists every payload file except DELIVERY-MANIFEST.json itself; paths are ZIP member names.",
        "exclusions": [
            "real .env files",
            ".git",
            "runtime state",
            "symlinks",
            "tests",
            "experiments and generated artifacts",
            "personal skills",
            "dashboard_prototype",
            "caches",
            "unknown files outside the allowlist",
        ],
        "verification_scope": "Integrity and allowlisted packaging only. No claim of real model API or platform compatibility testing.",
    }
    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    archive_path = output / f"CodeLoop-source-{date}.zip"
    if archive_path.is_symlink() or (
        archive_path.exists() and not archive_path.is_file()
    ):
        raise ValueError(f"Refusing non-regular output: {archive_path}")
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, (content, _) in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 18, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
        info = zipfile.ZipInfo(
            f"{ARCHIVE_ROOT}/DELIVERY-MANIFEST.json", date_time=(2026, 9, 18, 0, 0, 0)
        )
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, manifest_data)
    write_regular(output / "manifest.json", manifest_data)
    checksums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in (archive_path, output / "manifest.json")
    )
    write_regular(output / "SHA256SUMS.txt", checksums.encode("utf-8"))
    return {
        "status": "PASS",
        "archive": str(archive_path),
        "manifest": str(output / "manifest.json"),
        "sha256sums": str(output / "SHA256SUMS.txt"),
        "payload_file_count": len(entries),
        "snapshot_kind": "working-tree",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).absolute().parents[1]
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--date", default=DEFAULT_DATE)
    args = parser.parse_args()
    if len(args.date) != 10 or any(
        character not in "0123456789-" for character in args.date
    ):
        parser.error("--date must use YYYY-MM-DD without path separators")
    root = args.source.absolute()
    output = (
        args.output or root / "dist" / f"classmate-delivery-{args.date}"
    ).absolute()
    try:
        result = make_archive(root, output, args.date)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(
            json.dumps({"status": "FAIL", "error": str(error)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
