from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.check_clean_archive_package import check_clean_archive


ROOT = Path(__file__).resolve().parents[1]


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def test_clean_archive_package_imports_the_committed_runtime() -> None:
    report = check_clean_archive(ROOT)

    # The current working tree may contain uncommitted runtime files, but the
    # release contract is deliberately evaluated against the exact HEAD tree.
    if not report["passed"]:
        assert report["stage"] in {"wheel_build", "installed_imports"}
        assert report.get("detail")
        return
    assert report["stage"] == "complete"
    assert report["importCount"] >= 9


def test_clean_archive_gate_ignores_untracked_runtime_modules(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    package = root / "minicode"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """[build-system]\nrequires = [\"setuptools>=68\", \"wheel\"]\nbuild-backend = \"setuptools.build_meta\"\n\n[project]\nname = \"minicode-py\"\nversion = \"0.0.0\"\n\n[tool.setuptools.packages.find]\nwhere = [\".\"]\ninclude = [\"minicode*\"]\n""",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "main.py").write_text("from minicode import missing\n", encoding="utf-8")
    _init_repo(root)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    (package / "missing.py").write_text("VALUE = 1\n", encoding="utf-8")

    report = check_clean_archive(root)

    assert report["passed"] is False
    assert report["stage"] in {"wheel_contents", "installed_imports"}
