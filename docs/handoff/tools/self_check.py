"""Offline acceptance check for a normally installed CodeLoop distribution.

Run with the target virtual environment's Python. This script never inserts the
source checkout into sys.path and intentionally rejects editable installs.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.metadata
import importlib.resources
import io
import ipaddress
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ENTRYPOINTS = {
    "minicode-py": "minicode.main:main",
    "minicode-gateway": "minicode.gateway:run_gateway",
    "minicode-cron": "minicode.cron_runner:main",
    "minicode-headless": "minicode.headless:main",
    "minicode-calibrate-intent": "minicode.intent_semantic_calibration_cli:main",
}
STATIC_FILES = (
    "static/index.html",
    "static/assets/app.js",
    "static/assets/cost-format.js",
    "static/assets/styles.css",
)


def clean_environment() -> dict[str, str]:
    # Preserve OS identity variables as-is; Path.home is isolated in the worker.
    allowed = {
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "SYSTEMDRIVE",
    }
    environment = {
        name: value for name, value in os.environ.items() if name.upper() in allowed
    }
    environment.update(
        {
            "MINI_CODE_MODEL_MODE": "mock",
            # Runtime config still validates a model and credential in mock mode.
            "MINI_CODE_MODEL": "offline-self-check",
            "MINI_CODE_PROVIDER": "custom",
            "CUSTOM_API_BASE_URL": "http://127.0.0.1:9/v1",
            "CUSTOM_API_KEY": "offline-self-check-placeholder",
            "MINI_CODE_SHOW_GUIDE": "0",
            "MINI_CODE_LOG_LEVEL": "ERROR",
            "MINI_CODE_INTENT_SEMANTIC_ENABLED": "0",
            "MINI_CODE_SKILL_RERANKER_ENABLED": "0",
            "MINI_CODE_MEMORY_HYBRID_ENABLED": "0",
            "MINI_CODE_SUBAGENT_ROUTING_ENABLED": "0",
            "MINI_CODE_REFLECTION_SYNTHESIZER_MODE": "deterministic",
        }
    )
    return environment


def worker() -> int:
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "FAIL",
        "python": platform.python_version(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "checks": [],
        "isolation": {
            "python_isolated_mode": bool(sys.flags.isolated),
            "temporary_project": True,
            "temporary_home_via_Path_home": True,
            "model_environment": "allowlist-reset; synthetic mock model/key with loopback endpoint",
            "network": "non-loopback socket connections and DNS blocked",
            "child_processes": "blocked",
            "verifier_sandbox_probe": "disabled for offline list-only smoke",
        },
        "verification_boundary": "Offline installed-package smoke check with synthetic model configuration only. The startup verifier sandbox probe is explicitly mocked as unavailable. Real API credentials, provider connectivity, model output quality, interactive TTY, browser interaction, gateway delivery, cron execution, command sandbox capability and cross-platform support are not verified.",
    }
    checks: list[dict[str, object]] = []
    report["checks"] = checks
    network_attempts: list[str] = []
    process_attempts: list[str] = []

    def is_loopback(host: object) -> bool:
        if isinstance(host, bytes):
            host = host.decode("ascii", errors="replace")
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
        except ValueError:
            return False

    def audit(event: str, args: tuple[object, ...]) -> None:
        if event in {"socket.connect", "socket.sendto"}:
            address = args[-1]
            if not isinstance(address, tuple) or not is_loopback(address[0]):
                network_attempts.append(event)
                raise PermissionError(
                    "Offline self-check blocked a non-loopback socket operation"
                )
        elif event == "socket.getaddrinfo" and not is_loopback(args[0]):
            network_attempts.append(event)
            raise PermissionError("Offline self-check blocked external DNS resolution")
        elif event in {
            "subprocess.Popen",
            "os.system",
            "os.posix_spawn",
            "os.exec",
            "os.spawn",
        }:
            process_attempts.append(event)
            raise PermissionError("Offline self-check blocked a child process")

    sys.addaudithook(audit)
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    try:
        with tempfile.TemporaryDirectory(prefix="codeloop-self-check-home-") as home:  # noqa: SIM117 - keep home lifetime separate from import patches
            with (
                patch.object(Path, "home", return_value=Path(home)),
                contextlib.redirect_stdout(captured_stdout),
                contextlib.redirect_stderr(captured_stderr),
                contextlib.ExitStack() as local_patches,
            ):
                if sys.version_info < (3, 11):  # noqa: UP036 - launcher must report unsupported interpreters
                    raise RuntimeError("Python 3.11 or newer is required")
                if not sys.flags.isolated:
                    raise RuntimeError("Worker requires python -I")
                checks.append({"name": "python", "status": "PASS", "minimum": "3.11"})

                distribution = importlib.metadata.distribution("minicode-py")
                report["package_version"] = distribution.version
                package = importlib.import_module("minicode")
                package_file = Path(package.__file__ or "").resolve()
                expected = Path(
                    distribution.locate_file("minicode/__init__.py")
                ).resolve()
                if package_file != expected or not any(
                    str(item).replace("\\", "/") == "minicode/__init__.py"
                    for item in distribution.files or ()
                ):
                    raise RuntimeError(
                        "Install a wheel or use pip install . in a fresh virtual environment; editable/source imports are not accepted"
                    )
                report["package_location"] = str(package_file.parent)
                checks.append(
                    {"name": "normal_installed_distribution", "status": "PASS"}
                )
                # List-files does not exercise verifier execution. Prevent this
                # single startup probe from launching a platform child process.
                local_patches.enter_context(
                    patch(
                        "minicode.verifier_sandbox.default_verifier_sandbox_backend",
                        return_value=None,
                    )
                )

                installed_entries = {
                    entry.name: entry.value
                    for entry in distribution.entry_points
                    if entry.group == "console_scripts"
                }
                for name, target in ENTRYPOINTS.items():
                    module_name, attribute = target.split(":", 1)
                    module = importlib.import_module(module_name)
                    if installed_entries.get(name) != target or not callable(
                        getattr(module, attribute, None)
                    ):
                        raise RuntimeError(
                            f"Missing or invalid console entrypoint: {name}"
                        )
                    checks.append(
                        {
                            "name": f"entrypoint:{name}",
                            "status": "PASS",
                            "target": target,
                        }
                    )

                resources = importlib.resources.files("minicode.web")
                for relative in STATIC_FILES:
                    resource = resources.joinpath(relative)
                    if not resource.is_file() or not resource.read_bytes():
                        raise RuntimeError(
                            f"Missing or empty packaged resource: {relative}"
                        )
                    checks.append({"name": f"resource:{relative}", "status": "PASS"})

                config = importlib.import_module("minicode.config")
                if config.MINI_CODE_DIR != Path(home) / ".mini-code":
                    raise RuntimeError(
                        "Global config directory was not isolated before import"
                    )
                if config.load_effective_settings().get("mcpServers"):
                    raise RuntimeError(
                        "Unexpected MCP configuration in isolated self-check"
                    )
                checks.append({"name": "isolated_config_without_mcp", "status": "PASS"})

                marker = Path.cwd() / "CODELOOP_SELF_CHECK_MARKER.txt"
                marker.write_text(
                    "Temporary offline acceptance marker.\n", encoding="utf-8"
                )
                headless = importlib.import_module("minicode.headless")
                response = headless.run_headless("/ls")
                if marker.name not in response:
                    raise RuntimeError(
                        "Mock headless /ls did not return the temporary project marker"
                    )
                checks.append(
                    {
                        "name": "mock_headless_list_files",
                        "status": "PASS",
                        "marker_found": True,
                    }
                )
                if network_attempts or process_attempts:
                    raise RuntimeError(
                        "Smoke check attempted blocked network or child-process access"
                    )
                report["status"] = "PASS"
    except (Exception, SystemExit) as error:  # noqa: BLE001 - report all smoke failures as structured JSON
        report["error"] = f"{type(error).__name__}: {error}"
        report["diagnostic_output"] = (
            captured_stderr.getvalue() + captured_stdout.getvalue()
        )[-4000:]
    report["blocked_network_attempts"] = network_attempts
    report["blocked_process_attempts"] = process_attempts
    report["captured_output_characters"] = len(captured_stdout.getvalue()) + len(
        captured_stderr.getvalue()
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path, help="Also save the JSON report to this path"
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker()
    try:
        with tempfile.TemporaryDirectory(
            prefix="codeloop-self-check-project-"
        ) as project:
            completed = subprocess.run(
                [sys.executable, "-I", str(Path(__file__).resolve()), "--worker"],
                cwd=project,
                env=clean_environment(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
            )
        report = json.loads(completed.stdout)
        if completed.returncode != 0 and report.get("status") == "PASS":
            report["status"] = "FAIL"
            report["error"] = "Worker returned a nonzero exit code"
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        report = {
            "schema_version": 1,
            "status": "FAIL",
            "error": f"{type(error).__name__}: {error}",
        }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
