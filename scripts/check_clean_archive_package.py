"""Build and import the package from the exact committed Git archive."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_REQUIRED_IMPORTS = (
    "minicode.main",
    "minicode.gateway",
    "minicode.cron_runner",
    "minicode.headless",
    "minicode.agent_loop",
    "minicode.memory",
    "minicode.memory_hybrid",
    "minicode.memory_hybrid_runtime",
    "minicode.intent_semantic_calibration_cli",
)
_REQUIRED_WHEEL_FILES = (
    "minicode/web/static/index.html",
    "minicode/web/static/assets/app.js",
    "minicode/web/static/assets/styles.css",
)


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def check_clean_archive(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return a bounded report for an isolated wheel built from ``HEAD``."""

    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="minicode-release-archive-") as raw_temp:
        temp = Path(raw_temp)
        archive_path = temp / "source.tar"
        archive = subprocess.run(
            ["git", "-C", str(root), "archive", "--format=tar", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if archive.returncode != 0:
            return {
                "schemaVersion": 1,
                "passed": False,
                "stage": "git_archive",
                "detail": archive.stderr.decode("utf-8", errors="replace")[-1000:],
            }
        archive_path.write_bytes(archive.stdout)
        source = temp / "source"
        source.mkdir()
        with tarfile.open(archive_path, mode="r:") as bundle:
            # The archive is produced directly by the local Git repository; it
            # contains source files only. Use the hardened extraction filter on
            # Python versions that provide it while retaining 3.11 support.
            if sys.version_info >= (3, 12):
                bundle.extractall(source, filter="data")
            else:
                bundle.extractall(source)

        wheel_dir = temp / "wheel"
        wheel_dir.mkdir()
        built = _run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(wheel_dir),
                ".",
            ],
            cwd=source,
        )
        if built.returncode != 0:
            return {
                "schemaVersion": 1,
                "passed": False,
                "stage": "wheel_build",
                "detail": built.stderr[-2000:],
            }
        wheels = sorted(wheel_dir.glob("*.whl"))
        if len(wheels) != 1:
            return {
                "schemaVersion": 1,
                "passed": False,
                "stage": "wheel_discovery",
                "detail": f"expected one wheel, found {len(wheels)}",
            }
        with zipfile.ZipFile(wheels[0]) as bundle:
            packaged = set(bundle.namelist())
        missing_assets = sorted(set(_REQUIRED_WHEEL_FILES) - packaged)
        if missing_assets:
            return {
                "schemaVersion": 1,
                "passed": False,
                "stage": "wheel_contents",
                "missing": missing_assets,
            }

        installed = temp / "installed"
        installed.mkdir()
        install = _run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--target",
                str(installed),
                str(wheels[0]),
            ],
            cwd=temp,
        )
        if install.returncode != 0:
            return {
                "schemaVersion": 1,
                "passed": False,
                "stage": "wheel_install",
                "detail": install.stderr[-2000:],
            }

        # -I ignores PYTHONPATH. Select the wheel before the first import and
        # reject any module supplied by an ambient/editable installation.
        smoke = "\n".join(
            [
                "import importlib, sys",
                "from pathlib import Path",
                f"installed = Path({str(installed)!r}).resolve()",
                "sys.path.insert(0, str(installed))",
                f"modules = {list(_REQUIRED_IMPORTS)!r}",
                "for name in modules:",
                "    module = importlib.import_module(name)",
                "    origin = Path(module.__file__).resolve()",
                "    assert origin.is_relative_to(installed), f'{name}: outside installed wheel: {origin}'",
                "from minicode.agent_runtime import final_response_text",
                "assert final_response_text([{'role':'user','content':'x'}, {'role':'tool_result','content':'committed'}]) == 'committed'",
            ]
        )
        env = os.environ.copy()
        env.update(
            {
                "PYTHONNOUSERSITE": "1",
                "HOME": str(temp / "home"),
                "USERPROFILE": str(temp / "home"),
                "MINI_CODE_MODEL_MODE": "mock",
            }
        )
        (temp / "home").mkdir()
        imported = _run(
            [sys.executable, "-I", "-c", smoke],
            cwd=temp,
            env=env,
        )
        if imported.returncode != 0:
            return {
                "schemaVersion": 1,
                "passed": False,
                "stage": "installed_imports",
                "detail": imported.stderr[-2000:],
            }
        return {
            "schemaVersion": 1,
            "passed": True,
            "stage": "complete",
            "wheel": wheels[0].name,
            "importCount": len(_REQUIRED_IMPORTS),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify an isolated wheel built from the committed Git archive."
    )
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)
    try:
        report = check_clean_archive(args.root)
    except (OSError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as error:
        report = {
            "schemaVersion": 1,
            "passed": False,
            "stage": "check_failed",
            "errorType": type(error).__name__,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
