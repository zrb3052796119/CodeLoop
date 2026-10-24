from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.check_clean_archive_package as archive_checker
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
    assert report["passed"], report
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


@pytest.mark.parametrize("wheel_state", ["complete", "untracked_runtime", "foreign_origin"])
def test_clean_archive_imports_cannot_be_satisfied_by_ambient_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wheel_state: str,
) -> None:
    """Real child imports must prefer and remain inside the installed wheel."""
    root = tmp_path / "repo"
    package = root / "minicode"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68", "wheel"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        '[project]\nname = "minicode-py"\nversion = "0.0.0"\n\n'
        '[tool.setuptools.packages.find]\nwhere = ["."]\ninclude = ["minicode*"]\n\n'
        '[tool.setuptools.package-data]\n'
        'minicode = ["web/static/index.html", "web/static/assets/*"]\n',
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    for module in archive_checker._REQUIRED_IMPORTS:
        (root / (module.replace(".", "/") + ".py")).write_text(
            "SOURCE = 'wheel'\n", encoding="utf-8")
    (package / "agent_runtime.py").write_text(
        "def final_response_text(messages):\n    return messages[-1]['content']\n",
        encoding="utf-8",
    )
    for relative in archive_checker._REQUIRED_WHEEL_FILES:
        asset = root / relative
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text("fixture asset", encoding="utf-8")

    # A complete physical package simulates an ambient editable installation.
    # The subprocess is real; only its initial import search path is supplied.
    ambient = tmp_path / "ambient"
    ambient_package = ambient / "minicode"
    ambient_package.mkdir(parents=True)
    marker = tmp_path / "ambient-imported"
    (ambient_package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ambient')\n",
        encoding="utf-8",
    )
    for module in archive_checker._REQUIRED_IMPORTS:
        (ambient / (module.replace(".", "/") + ".py")).write_text(
            "SOURCE = 'ambient'\n", encoding="utf-8")
    (ambient_package / "agent_runtime.py").write_text(
        (package / "agent_runtime.py").read_text(encoding="utf-8"), encoding="utf-8")

    if wheel_state == "untracked_runtime":
        (package / "main.py").write_text("from minicode import missing\n", encoding="utf-8")
    elif wheel_state == "foreign_origin":
        (package / "memory_hybrid_runtime.py").unlink()
        (package / "__init__.py").write_text(
            f"__path__.append({str(ambient_package)!r})\n", encoding="utf-8")
    _init_repo(root)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    if wheel_state == "untracked_runtime":
        (package / "missing.py").write_text("VALUE = 1\n", encoding="utf-8")

    actual_run = archive_checker._run
    import_processes = []

    def run_with_ambient(argv, **kwargs):
        if "-I" in argv and "-c" in argv:
            argv = list(argv)
            argv[-1] = f"import sys; sys.path.insert(0, {str(ambient)!r})\n" + argv[-1]
            import_processes.append(argv)
        return actual_run(argv, **kwargs)

    monkeypatch.setattr(archive_checker, "_run", run_with_ambient)
    report = check_clean_archive(root)

    assert len(import_processes) == 1
    if wheel_state == "complete":
        assert report["passed"] is True, report
        assert report["stage"] == "complete"
        assert report["importCount"] == 9
        assert not marker.exists(), "ambient package satisfied the wheel import check"
    else:
        assert report["passed"] is False, report
        assert report["stage"] == "installed_imports"
        assert "missing" in report["detail"] if wheel_state == "untracked_runtime" else (
            "minicode.memory_hybrid_runtime" in report["detail"]
            and "outside installed wheel" in report["detail"])
