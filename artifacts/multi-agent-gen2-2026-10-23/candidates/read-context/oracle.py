"""Hidden gen-2 context-coordinate checks; run only on a delivered copy."""
from pathlib import Path
import json
import sys
import tempfile

workspace = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(workspace))
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.grep_files import grep_files_tool
from minicode.tools.read_file import read_file_tool


def check_context_coordinates():
    errors = []
    checks = 0
    def check(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            errors.append(message)
    with tempfile.TemporaryDirectory(prefix="gen2-context-oracle-") as directory:
        root = Path(directory)
        registry = ToolRegistry([read_file_tool, grep_files_tool])
        context = ToolContext(cwd=str(root))
        for index, separator in enumerate(("\f", "\v", "\x85", "\u2028", "\u2029")):
            text = "first = '甲" + separator + "乙'\nneedle = 42\nlast = '尾😀'\n"
            name = f"source{index}.py"
            (root / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
            search = registry.execute("grep_files", {
                "path": ".", "include": name, "pattern": "needle", "context_lines": 1,
            }, context)
            assert search.ok, search.output
            check(f"{name}:2: needle = 42" in search.output, "Search must report physical LF-normalized source lines: " + search.output)
            check(f"{name}:1:  first = '甲{separator}乙'" in search.output, "Source string data must stay on its physical line: " + name)
            for offset in (len(text), len(text) + 1, len(text) + 12345):
                read = registry.execute("read_file", {"path": name, "offset": offset, "limit": 5}, context)
                assert read.ok, read.output
                check(f"TOTAL_CHARS: {len(text)}\n" in read.output, "EOF total must measure normalized file content, not requested offset: " + read.output)
                check("TRUNCATED: no\n" in read.output, "Read beyond EOF must not request continuation: " + name)
                check(read.output.split("\n", 5)[-1] == "", "A read at or beyond EOF must have an empty body: " + name)
            middle = registry.execute("read_file", {"path": name, "offset": 2, "limit": 7}, context)
            assert middle.ok, middle.output
            check(middle.output.split("\n", 5)[-1] == text[2:9], "Ordinary character window changed: " + name)
    return {"passed": not errors, "checks": checks, "errors": errors}


result = check_context_coordinates()
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(0 if result["passed"] else 1)
