"""Replay all review rounds without overwriting their historical artifacts."""
from __future__ import annotations

import ast
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REVIEW = HERE.parent
SCRIPTS = [
    "evidence_probe.py", "synthesis_probe.py",
    "implementation-review/identity_probe.py", "implementation-review/quote_probe.py",
    "second-fix-review/identity_probe.py", "second-fix-review/grounding_probe.py",
    "second-fix-review/episode_probe.py",
    "objective-contract-review/consumer_probe.py", "objective-contract-review/parser_probe.py",
    "objective-contract-review/validator_episode_probe.py",
    "three-state-review/parser_probe.py", "three-state-review/consumer_probe.py",
    "three-state-review/multi_recovery_scope_probe.py",
]


def main() -> None:
    reports = {}
    for index, relative in enumerate(SCRIPTS):
        source = REVIEW / relative
        destination = HERE / "probe-results" / relative.removesuffix(".py")
        destination.mkdir(parents=True, exist_ok=True)
        name = f"recovery_fix_probe_{index}"
        spec = importlib.util.spec_from_file_location(name, source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        # Resolve original fixture imports first, then redirect output paths.
        module.__file__ = str(destination / source.name)
        for field in ("HERE", "REVIEW"):
            if getattr(module, field, None) == source.parent:
                setattr(module, field, destination)
        tree = ast.parse(source.read_text())
        entry = next(node for node in tree.body if isinstance(node, ast.If)
                     and "__name__" in ast.unparse(node.test))
        output = io.StringIO()
        with redirect_stdout(output):
            exec(compile(ast.Module(body=entry.body, type_ignores=[]), str(source), "exec"), module.__dict__)
        report = json.loads(output.getvalue())
        (destination / "stdout.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        reports[relative] = report
    (HERE / "all-probe-results.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n")
    print(f"Replayed {len(reports)} probe groups; results saved in {HERE / 'all-probe-results.json'}")


if __name__ == "__main__":
    main()
