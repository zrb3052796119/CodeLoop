"""A useful anchor must arrive with bounded same-function value provenance."""

import ast
import re

import minicode.implementation_context as context_module
from minicode.implementation_context import (
    MAX_PACKET_CHARS,
    _read,
    build_implementation_context,
)


def large_source():
    source = "def dispatch(raw, runner, sink, retry):\n"
    source += "".join(f"    filler_{i} = {i}  # unrelated preparation work\n" for i in range(120))
    source += "    x = list(raw)\n"
    source += "".join(f"    filler_{i} = {i}  # unrelated preparation work\n" for i in range(120, 900))
    source += "    if retry:\n        # Recover response from retry continuation.\n        y = runner(x)\n"
    source += "".join(f"    trailing_{i} = {i}  # unrelated cleanup work\n" for i in range(120))
    return source + "    sink(y)\n    return y\n"


def test_primary_block_keeps_input_binding_first_consumer_and_return(tmp_path):
    source = large_source()
    (tmp_path / "dispatch.py").write_text(source)
    packet = build_implementation_context(tmp_path, ["dispatch.py"], [],
                                          "Recover response from retry continuation.")
    assert len(source) > MAX_PACKET_CHARS
    assert "y = runner(x)" in packet, "Retain the primary relevant block."
    assert "x = list(raw)" in packet, "The selected block's local input must not be lost among lexical matches."
    assert "sink(y)" in packet, "Deliver the first consumer of the block's result."
    assert "return y" in packet
    assert len(packet) <= MAX_PACKET_CHARS
    assert "OMITTED lines" in packet


def test_dependencies_use_nearest_local_assignment_and_do_not_cross_nested_scope(tmp_path):
    code = (
        "def other():\n    x = 'OTHER_SCOPE'\n    sink(y)\n\n"
        "def dispatch(raw, runner, sink, retry):\n"
        "    x = 'OUTDATED_BINDING'\n"
        "    x = runner(\n        raw,\n        mode='LATEST_BINDING',\n    )\n"
        "    def nested():\n        x = 'NESTED_SCOPE'\n        sink(y)\n        return y\n"
        "    if retry:\n        y = runner(x)\n"
        "    try:\n        sink(y)\n    except ValueError:\n        pass\n"
        "    return y\n"
    )
    (tmp_path / "dispatch.py").write_text(code)
    source = _read(tmp_path, "dispatch.py", None)
    anchor = next(node for node in ast.walk(source.tree) if isinstance(node, ast.If))
    chunks = context_module._dependency_chunks(source, anchor)
    selected = {line for chunk in chunks for line in chunk}
    text = "\n".join(source.lines[line - 1] for line in sorted(selected))
    assert "LATEST_BINDING" in text
    assert not any(marker in text for marker in ("OTHER_SCOPE", "OUTDATED_BINDING", "NESTED_SCOPE"))
    binding = next(node for node in ast.walk(source.tree)
                   if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                   and node.value.keywords)
    assert set(range(binding.lineno, binding.end_lineno + 1)) <= selected
    consumer = next(node for node in ast.walk(source.tree)
                    if isinstance(node, ast.Expr) and node.lineno > anchor.end_lineno)
    assert consumer.lineno in selected
    assert "return y" in text and "except ValueError" not in text


def test_dependencies_remain_permission_checked_and_cannot_add_paths(tmp_path):
    (tmp_path / "dispatch.py").write_text(large_source())
    (tmp_path / "private.py").write_text("PRIVATE_CONTENT = 1\n")
    accesses = []

    class Denied:
        def ensure_path_access(self, path, intent):
            accesses.append((path, intent))
            raise PermissionError("not authorized")

    packet = build_implementation_context(tmp_path, ["dispatch.py"], [],
                                          "Recover response from retry continuation.", Denied())
    assert len(accesses) == 1 and accesses[0][1] == "read"
    assert "y = runner(x)" not in packet and "PRIVATE_CONTENT" not in packet


def test_oversized_dependency_is_omitted_atomically_without_displacing_primary_block(tmp_path):
    source = large_source().replace("    x = list(raw)\n", "    x = runner(\n" +
                                    "".join(f"        argument_{i}='{'z' * 70}',\n" for i in range(300)) + "    )\n")
    (tmp_path / "dispatch.py").write_text(source)
    packet = build_implementation_context(tmp_path, ["dispatch.py"], [],
                                          "Recover response from retry continuation.")
    assert "y = runner(x)" in packet and "sink(y)" in packet and "return y" in packet
    assert "argument_0=" not in packet and "argument_299=" not in packet
    visible = {int(match[1]) for line in packet.splitlines() if (match := re.match(r"^(\d+) \| ", line))}
    binding = next(node for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and node.value.keywords)
    assert not set(range(binding.lineno, binding.end_lineno + 1)) & visible
    assert len(packet) <= MAX_PACKET_CHARS
