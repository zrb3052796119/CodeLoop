"""Host capability selection and Darwin sandbox wrapper contracts."""

from __future__ import annotations

from pathlib import Path

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
