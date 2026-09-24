"""Load only the original scoring function, without running the old runner."""
import ast
from pathlib import Path


def load_original_score():
    path = Path(__file__).resolve().parents[2] / "artifacts/intent-evaluation-2026-09-07/run_evaluation.py"
    parsed = ast.parse(path.read_text(), filename=str(path))
    functions = [node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "score"]
    assert len(functions) == 1
    module = ast.Module(body=functions, type_ignores=[])
    namespace = {}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["score"]
