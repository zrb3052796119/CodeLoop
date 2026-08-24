"""Cross-platform Node.js harness for JavaScript contract tests."""

from __future__ import annotations

import subprocess
from pathlib import Path


_STDIN_BOOTSTRAP = "eval(require('node:fs').readFileSync(0, 'utf8'))"


def run_node(
    source: str,
    *arguments: str | Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute JavaScript from stdin without putting its body on the command line."""
    return subprocess.run(
        ["node", "-e", _STDIN_BOOTSTRAP, *(str(value) for value in arguments)],
        input=source,
        check=check,
        capture_output=True,
        encoding="utf-8",
    )
