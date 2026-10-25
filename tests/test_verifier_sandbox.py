"""Host capability selection and Darwin sandbox wrapper contracts."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import tempfile

import pytest

from minicode.verifier_sandbox import (
    DarwinSeatbeltVerifierSandbox,
    resolve_verifier_host_capabilities,
)


def test_darwin_wrapper_is_absolute_deny_by_default_and_network_denied(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "sandbox-exec"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    cwd = tmp_path / "workspace"
    home = tmp_path / "home"
    cwd.mkdir()
    home.mkdir()
    executable = Path("/usr/bin/true").resolve(strict=True)
    backend = DarwinSeatbeltVerifierSandbox(str(launcher))

    wrapped = backend.wrap_command(
        argv=(str(executable),),
        cwd=str(cwd),
        verifier_home=str(home),
    )

    assert Path(wrapped[0]).is_absolute()
    assert wrapped[1] == "-p"
    profile = wrapped[2]
    assert "(deny default)" in profile
    assert "(deny network*)" in profile
    assert str(cwd) in profile
    assert str(home) in profile
    assert wrapped[-1] == str(executable)
    # Toolchains stat their ancestors during collection (pytest walks each
    # parent of the invocation directory); the profile must allow
    # metadata-only reads on the ancestor directories themselves while
    # keeping their subtrees unreadable.
    assert "(allow file-read-metadata" in profile
    assert f'(literal "{tmp_path}")' in profile
    assert f'(subpath "{tmp_path}")' not in profile


def test_darwin_wrapper_classifies_launcher_failure_as_infrastructure() -> None:
    assert DarwinSeatbeltVerifierSandbox.infrastructure_failure(
        returncode=71,
        stderr="sandbox-exec: sandbox_apply: Operation not permitted",
    )
    assert not DarwinSeatbeltVerifierSandbox.infrastructure_failure(
        returncode=1,
        stderr="assertion failed",
    )


def test_explicit_runtime_capabilities_override_platform_defaults(
    monkeypatch,
) -> None:
    import minicode.verifier_sandbox as module

    default_backend = object()
    explicit_backend = object()
    monkeypatch.setattr(
        module,
        "default_verifier_sandbox_backend",
        lambda: default_backend,
    )
    monkeypatch.setattr(
        module,
        "default_verifier_trusted_executables",
        lambda: {"python3": "/usr/bin/python3"},
    )

    backend, trusted = resolve_verifier_host_capabilities(
        {
            "_verifierSandboxBackend": explicit_backend,
            "_verifierTrustedExecutables": {
                "python3": "/opt/host/python3",
                "ruff": "/opt/host/ruff",
            },
        }
    )

    assert backend is explicit_backend
    assert trusted == {
        "python3": "/opt/host/python3",
        "ruff": "/opt/host/ruff",
    }


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS Seatbelt")
def test_registered_toolchain_can_resolve_ancestors_without_reading_their_contents(tmp_path):
    # The verification clone need not share the toolchain's directory tree.
    # --copies keeps executable resolution inside this isolated toolchain.
    toolchain_parent = tmp_path / "separate-host" / "toolchains"
    prefix = toolchain_parent / "python"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", "--copies", str(prefix)], check=True)
    executable = prefix / "bin" / "python3"
    assert executable.is_file() and not executable.is_symlink()
    sentinel = toolchain_parent / "private.txt"
    sentinel.write_text("host contents must remain unreadable")
    with tempfile.TemporaryDirectory(prefix="minicode-sandbox-separate-") as separate:
        workspace = Path(separate).resolve() / "workspace"
        home = Path(separate).resolve() / "home"
        workspace.mkdir()
        home.mkdir()
        code = (
            "from pathlib import Path\nimport socket\n"
            f"parent = Path({str(toolchain_parent)!r})\n"
            "assert parent.stat().st_mode\n"
            f"sentinel = Path({str(sentinel)!r})\n"
            "try:\n    sentinel.read_text()\n"
            "except PermissionError:\n    pass\n"
            "else:\n    raise AssertionError('ancestor contents exposed')\n"
            f"target = Path({str(prefix / 'forbidden-write')!r})\n"
            "try:\n    target.write_text('forbidden')\n"
            "except PermissionError:\n    pass\n"
            "else:\n    raise AssertionError('toolchain write allowed')\n"
            "try:\n    sock = socket.socket(); sock.bind(('127.0.0.1', 0))\n"
            "except PermissionError:\n    pass\n"
            "else:\n    sock.close(); raise AssertionError('network allowed')\n"
            "print('registered-toolchain-isolated')\n"
        )
        command = DarwinSeatbeltVerifierSandbox().wrap_command(
            argv=(str(executable), "-c", code), cwd=str(workspace),
            verifier_home=str(home), extra_read_roots=(str(prefix),))
        result = subprocess.run(command, cwd=workspace, capture_output=True, text=True,
            env={"HOME": str(home), "PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "registered-toolchain-isolated"
    assert sentinel.read_text() == "host contents must remain unreadable"
    assert not (prefix / "forbidden-write").exists()
