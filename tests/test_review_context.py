"""Review selection follows frozen changed hunks without widening read scope."""

import ast
import difflib
import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from minicode.review_context import MAX_REVIEW_CONTEXT_CHARS, build_review_context
from minicode.workflow_workspace import _frozen_diff_document


def save(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def frozen_diff(name, before, after):
    patch = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                       fromfile=f"a/{name}", tofile=f"b/{name}")).encode()
    return _frozen_diff_document(patch=patch, base_digest="sha256:" + "0" * 64,
                                 patch_digest="sha256:" + hashlib.sha256(patch).hexdigest())


def visible_lines(packet, name):
    section = next(part for part in packet.split("\nFILE ")[1:] if part.startswith(name + " |"))
    return {int(match[1]) for line in section.splitlines() if (match := re.match(r"^(\d+) \| ", line))}


def test_late_change_supplies_whole_function_initialization_consumers_tests_and_api(tmp_path):
    before = ("from api import publish\n\ndef work(values):\n"
              "    accumulated = []\n" + "".join(f"    scratch_{i} = {i}\n" for i in range(350))
              + "    accumulated.extend(values[:1])\n"
              "    publish(accumulated)\n    return accumulated\n")
    after = before.replace("values[:1]", "values")
    path = save(tmp_path, "app.py", after)
    save(tmp_path, "api.py", "raise RuntimeError('DO_NOT_EXECUTE_OR_INCLUDE')\n\ndef publish(values):\n    return values\n")
    save(tmp_path, "tests/test_app.py", "from app import work\n\ndef test_values():\n    assert work([1, 2]) == [1, 2]\n")
    packet = build_review_context(tmp_path, ["app.py"], [("pytest", "tests/test_app.py::test_values")],
                                  "unrelated keyword survey must not decide source selection", None,
                                  frozen_diff("app.py", before, after))
    definition = ast.parse(after).body[1]
    assert set(range(definition.lineno, definition.end_lineno + 1)) <= visible_lines(packet, "app.py")
    assert "accumulated = []" in packet and "publish(accumulated)" in packet
    assert "assert work([1, 2]) == [1, 2]" in packet
    assert "def publish(values):" in packet
    assert "DO_NOT_EXECUTE_OR_INCLUDE" not in packet
    assert hashlib.sha256(path.read_bytes()).hexdigest() in packet
    assert path.read_text() == after
    assert len(packet) <= MAX_REVIEW_CONTEXT_CHARS


def test_smallest_changed_method_is_selected_not_unrelated_methods(tmp_path):
    before = ("LIMIT = 1\n\ndef helper(value):\n    return value\n\n"
              "class Worker:\n    def unrelated(self):\n        return 'UNRELATED_SECRET'\n\n"
              "    def run(self, value):\n        initial = helper(value[:LIMIT])\n        return initial[:1]\n")
    after = before.replace("initial[:1]", "initial")
    save(tmp_path, "worker.py", after)
    packet = build_review_context(tmp_path, ["worker.py"], [], "", None, frozen_diff("worker.py", before, after))
    assert "def run(self, value):" in packet and "initial = helper(value[:LIMIT])" in packet
    assert "class Worker:" in packet and "LIMIT = 1" in packet and "def helper(value):" in packet
    assert "UNRELATED_SECRET" not in packet
    assert "OMITTED lines" in packet


def test_oversized_function_explicitly_omits_gaps_but_keeps_initialization_change_and_tail(tmp_path):
    before = ("def process(values):\n    initialized = list(values)\n"
              + "".join(f"    unrelated_{i} = '{'x' * 90}'\n" for i in range(1800))
              + "    selected = initialized[:1]\n    consumed = list(selected)\n    return consumed\n")
    after = before.replace("selected = initialized[:1]", "selected = initialized")
    save(tmp_path, "large.py", after)
    packet = build_review_context(tmp_path, ["large.py"], [], "", None, frozen_diff("large.py", before, after))
    assert "def process(values):" in packet and "initialized = list(values)" in packet
    assert "selected = initialized" in packet and "return consumed" in packet
    assert "OMITTED lines" in packet and "normal read_file" in packet
    assert "OMITTED complete changed function(s): process" in packet
    assert len(packet) <= MAX_REVIEW_CONTEXT_CHARS


@pytest.mark.parametrize("mode", ["empty", "incomplete", "wrong-digest", "wrong-source"])
def test_missing_or_unusable_diff_never_claims_complete_source_evidence(tmp_path, mode):
    before, after = "def work():\n    return 1\n", "def work():\n    return 2\n"
    save(tmp_path, "app.py", before if mode == "wrong-source" else after)
    diff = frozen_diff("app.py", before, after)
    if mode == "empty":
        diff = frozen_diff("app.py", after, after)
    elif mode == "incomplete":
        diff["complete"] = False
    elif mode == "wrong-digest":
        diff["patchDigest"] = "sha256:" + "1" * 64
    packet = build_review_context(tmp_path, ["app.py"], [], "", None, diff)
    assert "OMITTED" in packet or "NO_CHANGED_HUNKS" in packet
    assert "return 1" not in packet and "return 2" not in packet
    if mode == "wrong-source":
        assert "postimage differs" in packet


def test_diff_paths_imports_and_checks_all_remain_permission_checked(tmp_path):
    before, after = "def work():\n    return None\n", "from denied_api import secret\n\ndef work():\n    return secret()\n"
    save(tmp_path, "app.py", after)
    save(tmp_path, "denied_api.py", "def secret():\n    return 'DENIED_CONTENT'\n")
    save(tmp_path, "tests/test_public.py", "def test_public():\n    assert True\n")
    save(tmp_path, "tests/test_hidden.py", "HIDDEN_ORACLE_CONTENT = 1\n")
    outside = save(tmp_path.parent, f"{tmp_path.name}-outside.py", "OUTSIDE_CONTENT = 1\n")
    (tmp_path / "escape.py").symlink_to(outside)
    (tmp_path / "nul.py").write_bytes(b"NUL_CONTENT = 1\0\n")
    accesses = []

    class Permissions:
        def ensure_path_access(self, path, intent):
            accesses.append((Path(path).name, intent))
            if Path(path).name == "denied_api.py":
                raise PermissionError("forbidden")

    checks = [SimpleNamespace(kind="tests", argv=("pytest", "tests/test_public.py", "escape.py", "nul.py")),
              SimpleNamespace(kind="oracle", argv=("pytest", "tests/test_hidden.py"))]
    packet = build_review_context(tmp_path, ["app.py"], checks, "", Permissions(), frozen_diff("app.py", before, after))
    for token in ("DENIED_CONTENT", "OUTSIDE_CONTENT", "NUL_CONTENT", "HIDDEN_ORACLE_CONTENT"):
        assert token not in packet
    assert "test_hidden.py" not in packet
    assert ("denied_api.py", "read") in accesses
    assert all(name != outside.name and intent == "read" for name, intent in accesses)
    assert "assert True" in packet


@pytest.mark.parametrize("name", ["../outside.py", "/etc/passwd", "other.py", ".private/secret.py", "nul\0.py"])
def test_untrusted_diff_cannot_add_source_read_authority(tmp_path, name):
    save(tmp_path, "other.py", "DIFF_SCOPE_SECRET = 2\n")
    accesses = []

    class Permissions:
        def ensure_path_access(self, path, intent):
            accesses.append(path)

    packet = build_review_context(tmp_path, ["allowed.py"], [], "", Permissions(),
                                  frozen_diff(name, "DIFF_SCOPE_SECRET = 1\n", "DIFF_SCOPE_SECRET = 2\n"))
    assert "DIFF_SCOPE_SECRET" not in packet
    assert not accesses
    assert "OMITTED" in packet


def test_removed_line_hunk_and_new_file_hunk_use_current_bytes(tmp_path):
    before = "def work():\n    value = 1\n    redundant = 2\n    return value\n"
    after = before.replace("    redundant = 2\n", "")
    save(tmp_path, "app.py", after)
    packet = build_review_context(tmp_path, ["app.py"], [], "", None, frozen_diff("app.py", before, after))
    assert "value = 1" in packet and "return value" in packet and "redundant =" not in packet
    save(tmp_path, "new.py", after)
    packet = build_review_context(tmp_path, ["new.py"], [], "", None, frozen_diff("new.py", "", after))
    assert "def work():" in packet and "return value" in packet


def test_many_changed_files_and_long_public_tests_keep_total_packet_bounded(tmp_path):
    patches = []
    paths = []
    for index in range(16):
        name = f"module_{index}.py"
        before = "def work():\n" + "".join(f"    scratch_{i} = {i}\n" for i in range(600)) + "    return 1\n"
        after = before.replace("return 1", "return 2")
        save(tmp_path, name, after)
        patches.append("".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                                   fromfile=f"a/{name}", tofile=f"b/{name}")))
        paths.append(name)
    patch = "".join(patches).encode()
    diff = _frozen_diff_document(patch=patch, base_digest="sha256:" + "0" * 64,
                                 patch_digest="sha256:" + hashlib.sha256(patch).hexdigest())
    packet = build_review_context(tmp_path, paths, [], "", None, diff)
    assert len(packet) <= MAX_REVIEW_CONTEXT_CHARS
    assert "OMITTED: 4 additional source paths" in packet
    assert packet.endswith("=== END REVIEW SOURCE CONTEXT ===\n")
