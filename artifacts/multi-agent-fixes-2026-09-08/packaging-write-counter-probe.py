"""Read-only attribution probe; writes only inside its isolated temporary workspace.

Usage: python packaging-write-counter-probe.py /absolute/production/root
The real write_file tool, PermissionManager, and ToolContext come from that root.
The prompt supplies the user's allow-once decision; no agent loop is involved.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


production_root = Path(sys.argv[1]).resolve(strict=True)
sys.dont_write_bytecode = True
sys.path.insert(0, str(production_root))

from minicode.auto_mode import PermissionMode
import minicode.file_review as file_review
import minicode.permissions as permissions_module
import minicode.tooling as tooling_module
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext
from minicode.tools.write_file import write_file_tool


with tempfile.TemporaryDirectory(prefix="packaging-write-counter-") as temp_dir:
    workspace = Path(temp_dir).resolve(strict=True)
    permissions_module.MINI_CODE_PERMISSIONS_PATH = workspace / "permissions.json"
    target = workspace / "installed-absolute-allow.txt"
    content = "installed-absolute-safe\n"
    prompts = []
    path_writes = []
    atomic_replacements = []
    original_path_write_text = Path.write_text
    original_replace = os.replace

    def prompt(request):
        prompts.append(request)
        return {"decision": "allow_once"}

    def count_path_write(self, data, *args, **kwargs):
        if self == target:
            path_writes.append(data)
        return original_path_write_text(self, data, *args, **kwargs)

    def count_replace(source, destination, *args, **kwargs):
        if Path(destination) == target:
            atomic_replacements.append({
                "source_is_same_directory_temp": Path(source).parent == target.parent,
                "source_content": Path(source).read_text(encoding="utf-8"),
                "destination_exactly_matches_counter_target": Path(destination) == target,
            })
        return original_replace(source, destination, *args, **kwargs)

    manager = PermissionManager(str(workspace), prompt=prompt, auto_mode=PermissionMode.DEFAULT)
    Path.write_text = count_path_write
    os.replace = count_replace
    try:
        outcome = write_file_tool.run(
            write_file_tool.validator({"path": str(target), "content": content}),
            ToolContext(cwd=str(workspace), permissions=manager),
        )
    finally:
        Path.write_text = original_path_write_text
        os.replace = original_replace

    report = {
        "production_root": str(production_root),
        "loaded_modules": {
            "file_review": file_review.__file__,
            "tooling": tooling_module.__file__,
            "permissions": permissions_module.__file__,
        },
        "file_review_sha256": hashlib.sha256(Path(file_review.__file__).read_bytes()).hexdigest(),
        "ok": outcome.ok,
        "output": outcome.output,
        "prompt_count": len(prompts),
        "prompt_kinds": [item.get("kind") for item in prompts],
        "target_content": target.read_text(encoding="utf-8"),
        "target_content_correct": target.read_text(encoding="utf-8") == content,
        "path_write_text_calls": path_writes,
        "smoke_counter_assertion_would_pass": path_writes == [content],
        "atomic_replacements": atomic_replacements,
    }
    print(json.dumps(report, indent=2))
