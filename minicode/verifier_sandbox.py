"""Host-owned process sandbox capabilities for deterministic verification."""

from __future__ import annotations

from functools import lru_cache
from collections.abc import Mapping
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile


_VERIFIER_COMMAND_NAMES = (
    "cargo",
    "cmake",
    "dotnet",
    "go",
    "make",
    "mypy",
    "npm",
    "pyright",
    "pytest",
    "python",
    "python3",
    "ruff",
)


class DarwinSeatbeltVerifierSandbox:
    """Wrap one check in a deny-by-default macOS Seatbelt profile."""

    def __init__(self, executable: str = "/usr/bin/sandbox-exec") -> None:
        resolved = Path(executable).resolve(strict=True)
        if not resolved.is_absolute() or not os.access(resolved, os.X_OK):
            raise ValueError("sandbox-exec capability is unavailable")
        self._executable = str(resolved)

    @staticmethod
    def _literal(path: str) -> str:
        # Seatbelt accepts JSON-compatible quoted string literals.
        return json.dumps(str(Path(path).resolve(strict=True)))

    def wrap_command(
        self,
        *,
        argv: tuple[str, ...],
        cwd: str,
        verifier_home: str,
        extra_read_roots: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        if not argv or not Path(argv[0]).is_absolute():
            raise ValueError("sandbox argv must start with an absolute executable")
        cwd_literal = self._literal(cwd)
        home_literal = self._literal(verifier_home)
        # Toolchains legitimately stat their own ancestors (pytest's
        # collection tree walk probes each parent of the invocation
        # directory).  Allow metadata-only reads on those ancestor
        # directories themselves — never their subtrees — so such probes
        # succeed without exposing any file contents outside the workspace
        # and the verifier home. Explicitly registered toolchains also need
        # metadata on their own ancestors when the clone is elsewhere.
        ancestor_literals = " ".join(
            dict.fromkeys(
                f"(literal {self._literal(str(ancestor))})"
                for base in (Path(cwd), Path(verifier_home), *(Path(root) for root in extra_read_roots))
                for ancestor in base.resolve(strict=True).parents
                if ancestor.parent != ancestor
            )
        )
        # Host-user-registered toolchains (env channel) live outside the
        # system roots — typically a conda/venv prefix under $HOME.  Their
        # prefix must stay readable or the very binary the user explicitly
        # trusted cannot even load its interpreter and libraries.
        extra_root_literals = " ".join(
            dict.fromkeys(
                f"(subpath {self._literal(root)})"
                for root in extra_read_roots
                if str(Path(root).resolve(strict=True)) not in {str(Path(cwd).resolve(strict=True)), str(Path(verifier_home).resolve(strict=True))}
            )
        )
        profile = "".join(
            (
                "(version 1)",
                "(deny default)",
                # A repository check may execute its primary toolchain but
                # cannot fork descendants. This makes Popen ownership and
                # kill-on-timeout complete for the default backend.
                "(allow process-exec)",
                "(allow signal (target self))",
                "(allow sysctl-read)",
                (
                    "(allow file-read-metadata " + ancestor_literals + ")"
                    if ancestor_literals
                    else ""
                ),
                "(allow file-read* (subpath \"/System\")",
                " (subpath \"/usr\") (subpath \"/Library\")",
                # Process startup on current macOS reads the filesystem root
                # (dyld/platform init); without the literal the wrapped check
                # aborts (SIGABRT) before reaching the sandboxed process.
                " (literal \"/\")",
                " (literal \"/dev/null\") (literal \"/dev/urandom\")",
                " (subpath ",
                cwd_literal,
                ") (subpath ",
                home_literal,
                ")",
                (" " + extra_root_literals) if extra_root_literals else "",
                ")",
                "(allow file-write* (subpath ",
                cwd_literal,
                ") (subpath ",
                home_literal,
                # Toolchain logging defaults open /dev/null for writing; the
                # device discards everything, so allowing it cannot leak or
                # persist state.
                ") (literal \"/dev/null\"))",
                "(deny network*)",
            )
        )
        return (self._executable, "-p", profile, "--", *argv)

    @staticmethod
    def infrastructure_failure(*, returncode: int, stderr: str) -> bool:
        """Distinguish Seatbelt launcher failure from a check assertion."""
        return returncode == 71 or stderr.lstrip().startswith("sandbox-exec:")


def _root_owned_nonwritable_executable(path: Path) -> bool:
    """Accept automatic POSIX defaults only from root-owned immutable paths."""
    if os.name == "nt":  # pragma: no cover - Windows has a separate host policy
        return True
    current = path
    while True:
        try:
            metadata = current.stat()
        except OSError:
            return False
        if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return False
        if current.parent == current:
            return True
        current = current.parent


def default_verifier_trusted_executables() -> dict[str, str]:
    """Resolve a small host PATH, never the caller's repository/user PATH."""
    trusted_path = os.pathsep.join(
        entry
        for entry in os.defpath.split(os.pathsep)
        if entry and Path(entry).is_absolute()
    )
    capabilities: dict[str, str] = {}
    for name in _VERIFIER_COMMAND_NAMES:
        candidate = shutil.which(name, path=trusted_path)
        if candidate is None:
            continue
        try:
            resolved = Path(candidate).resolve(strict=True)
        except OSError:
            continue
        if (
            resolved.is_file()
            and os.access(resolved, os.X_OK)
            and _root_owned_nonwritable_executable(resolved)
        ):
            capabilities[name] = str(resolved)
    return capabilities


@lru_cache(maxsize=1)
def default_verifier_sandbox_backend() -> DarwinSeatbeltVerifierSandbox | None:
    """Probe real read/write/network denial; absence remains fail-closed."""
    if platform.system() != "Darwin":
        return None
    try:
        backend = DarwinSeatbeltVerifierSandbox()
        python = Path("/usr/bin/python3").resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="minicode-sandbox-probe-") as root:
            # Probe paths must be canonical: Seatbelt literals resolve
            # symlinks (macOS /var -> /private/var) and deny the rest.
            root_path = Path(root).resolve()
            workspace = root_path / "workspace"
            home = root_path / "home"
            outside = root_path / "outside"
            workspace.mkdir()
            home.mkdir()
            outside.mkdir()
            (workspace / "inside.txt").write_text("inside", encoding="utf-8")
            (outside / "secret.txt").write_text("secret", encoding="utf-8")
            script = (
                "import pathlib,socket,sys;"
                "cwd=pathlib.Path(sys.argv[1]);out=pathlib.Path(sys.argv[2]);"
                "assert (cwd/'inside.txt').read_text()=='inside';"
                "(cwd/'write.txt').write_text('ok');"
                "blocked=0;"
                "\ntry:(out/'secret.txt').read_text()"
                "\nexcept PermissionError:blocked+=1"
                "\ntry:(out/'write.txt').write_text('bad')"
                "\nexcept PermissionError:blocked+=1"
                "\ntry:s=socket.socket();s.bind(('127.0.0.1',0))"
                "\nexcept PermissionError:blocked+=1"
                "\nassert blocked==3"
            )
            wrapped = backend.wrap_command(
                argv=(str(python), "-c", script, str(workspace), str(outside)),
                cwd=str(workspace),
                verifier_home=str(home),
            )
            probe = subprocess.run(
                wrapped,
                cwd=str(workspace),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={"HOME": str(home), "PATH": os.defpath},
                check=False,
                timeout=3,
            )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return backend if probe.returncode == 0 else None


_USER_TRUSTED_EXECUTABLES_ENV = "MINI_CODE_VERIFIER_TRUSTED_EXECUTABLES"


def user_registered_trusted_executables(
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve host-user-registered trusted executables from the environment.

    ``MINI_CODE_VERIFIER_TRUSTED_EXECUTABLES`` accepts comma-separated
    ``name=/absolute/path`` pairs, e.g.
    ``pytest=/opt/verify-venv/bin/pytest``.  This is a user-owned channel on
    par with the other ``MINI_CODE_*`` knobs: model output can never reach it.
    Every entry must name an existing, executable, non-workspace file; an
    invalid entry is dropped rather than weakening the whole channel.  Such
    explicitly registered executables remain subject to the workspace
    rejection in ``DeterministicVerifier`` but are exempt from the default
    ``$HOME`` untrusted root, which is what locks out conda/venv toolchains.
    """

    source = os.environ if env is None else env
    raw = source.get(_USER_TRUSTED_EXECUTABLES_ENV)
    if not raw or not raw.strip():
        return {}
    registered: dict[str, str] = {}
    for item in raw.split(","):
        name, separator, path = item.partition("=")
        name = name.strip()
        path = path.strip()
        if not separator or not name or "/" in name or "\\" in name:
            continue
        candidate = Path(path)
        if not candidate.is_absolute():
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            continue
        registered[name] = str(resolved)
    return registered


def resolve_verifier_host_capabilities(
    runtime: Mapping[str, object] | None,
) -> tuple[object | None, dict[str, str]]:
    """Apply host-explicit capabilities over safe platform defaults."""
    backend: object | None = default_verifier_sandbox_backend()
    trusted = default_verifier_trusted_executables()
    trusted.update(user_registered_trusted_executables())
    if not isinstance(runtime, Mapping):
        return backend, trusted
    verifier_settings = runtime.get("verifier")
    settings = verifier_settings if isinstance(verifier_settings, Mapping) else {}
    if "_verifierSandboxBackend" in runtime:
        backend = runtime["_verifierSandboxBackend"]
    elif "_sandboxBackend" in settings:
        backend = settings["_sandboxBackend"]
    explicit_trusted = runtime.get("_verifierTrustedExecutables")
    if explicit_trusted is None:
        explicit_trusted = settings.get("_trustedExecutables")
    if isinstance(explicit_trusted, Mapping):
        trusted.update(
            {
                str(name): str(path)
                for name, path in explicit_trusted.items()
                if isinstance(name, str) and isinstance(path, str)
            }
        )
    return backend, trusted


__all__ = [
    "DarwinSeatbeltVerifierSandbox",
    "default_verifier_sandbox_backend",
    "default_verifier_trusted_executables",
    "resolve_verifier_host_capabilities",
    "user_registered_trusted_executables",
]
