"""Regression for atomic source excerpts and actual API evidence delivery."""
import ast
import re

from minicode.implementation_context import build_implementation_context


def write(root, name, code):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code)


def lines_in(packet, name):
    body = next(section for section in packet.split('\nFILE ')[1:] if section.startswith(name + ' |'))
    return {int(match.group(1)) for line in body.splitlines()
            if (match := re.match(r'^(\d+) \| ', line))}


def test_related_control_flow_survives_other_large_definitions(tmp_path):
    unrelated = 'def background_transport(data):\n'
    unrelated += ''.join(f'    state_{i} = data  # schema protocol findings model typed result task evidence\n' for i in range(130))
    target = 'def finish_child(history, missing, journal, result):\n'
    target += ''.join(f'    setup_{i} = None\n' for i in range(300))
    target += ('    attempts = len(history)\n'
               '    journal.record_attempts(attempts)\n'
               '    # Give one bounded closing reminder for a missing typed result.\n'
               '    if missing:\n'
               '        reminder_history = history\n'
               '        result = invoke_reminder(\n')
    target += ''.join(f'            option_{i}={i},\n' for i in range(65))
    target += ('            messages=reminder_history,\n'
               '        )\n'
               '        history = result.messages\n'
               '    projected = project_result(result)\n'
               '    journal.save(history)\n'
               '    return projected\n')
    source = unrelated + '\n' + target
    write(tmp_path, 'dispatch.py', source)
    packet = build_implementation_context(tmp_path, ['dispatch.py'], [],
        'A bounded closing reminder loses completed history and typed result. '
        'The closing reminder must keep completed history, count attempts, '
        'then project result and save completed history. Keep schema protocol findings compatible.')
    tree = ast.parse(source)
    branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If))
    supplied = lines_in(packet, 'dispatch.py')
    assert set(range(branch.lineno, branch.end_lineno + 1)) <= supplied
    assert 'journal.record_attempts(attempts)' in packet
    assert 'projected = project_result(result)' in packet
    assert 'journal.save(history)' in packet
    assert len(packet) <= 32000


def test_large_imported_function_signature_is_never_cut_mid_parameters(tmp_path):
    signature = 'def run_operation(\n' + ''.join(
        f'    optional_setting_{i}: str = "value-{i}",\n' for i in range(70)) + '):\n'
    write(tmp_path, 'api.py', signature + '    raise RuntimeError("never execute imported code")\n')
    write(tmp_path, 'app.py', 'from api import run_operation\n\ndef start():\n    return run_operation()\n')
    packet = build_implementation_context(tmp_path, ['app.py'], [], 'Fix start run_operation')
    provided = lines_in(packet, 'api.py')
    assert set(range(1, len(signature.splitlines()) + 1)) <= provided
    assert 'never execute imported code' not in packet
    assert len(packet) <= 32000


def test_import_statements_alone_do_not_spend_dependency_budget(tmp_path):
    imports = ''.join(f'from unused_{i} import ignored_{i}\n' for i in range(12))
    for i in range(12):
        write(tmp_path, f'unused_{i}.py', f'def ignored_{i}():\n    return None\n')
    write(tmp_path, 'api.py', 'def run_operation(*, model, messages):\n    return None\n')
    write(tmp_path, 'app.py', imports + 'from api import run_operation\n\ndef start():\n    return run_operation(model=None, messages=[])\n')
    packet = build_implementation_context(tmp_path, ['app.py'], [], 'Fix start run_operation')
    assert 'def run_operation(*, model, messages):' in packet
    assert 'FILE unused_0.py' not in packet


def test_long_straight_line_function_that_fits_is_supplied_whole(tmp_path):
    source = "import json\n\ndef process_record(record):\n"
    source += "".join(f"    scratch_{i} = {i}\n" for i in range(300))
    source += "    record.restore_evidence()\n    return record\n"
    write(tmp_path, "app.py", source)
    packet = build_implementation_context(
        tmp_path, ["app.py"], [], "Fix process_record restore_evidence"
    )
    supplied = lines_in(packet, "app.py")
    definition = ast.parse(source).body[1]
    assert set(range(definition.lineno, definition.end_lineno + 1)) <= supplied
    assert len(packet) <= 32000


def test_oversized_straight_line_function_supplies_whole_related_statements(tmp_path):
    source = "import json\n\ndef process_record(record):\n"
    source += "".join(f"    scratch_{i} = {i}\n" for i in range(1800))
    source += "    completed = record.completed_history\n"
    source += "    evidence = restore_evidence(\n"
    source += "".join(f"        option_{i}={i},\n" for i in range(70))
    source += "        history=completed,\n    )\n"
    source += "    record.save(evidence)\n    return evidence\n"
    write(tmp_path, "app.py", source)
    packet = build_implementation_context(
        tmp_path, ["app.py"], [], "Fix restore_evidence preserving completed_history"
    )
    supplied = lines_in(packet, "app.py")
    definition = ast.parse(source).body[1]
    invocation = next(
        statement for statement in definition.body
        if isinstance(statement, ast.Assign)
        and isinstance(statement.value, ast.Call)
    )
    assert set(range(invocation.lineno, invocation.end_lineno + 1)) <= supplied
    assert "completed = record.completed_history" in packet
    assert "record.save(evidence)" in packet
    assert "1803 |     scratch_1799 = 1799" in packet
    assert len(packet) <= 32000
