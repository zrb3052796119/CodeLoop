"""Real host pytest traces, unrelated inventory code, no model responses."""
from copy import deepcopy
import sys

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.permissions import PermissionManager
from minicode.review_probe_execution import run_isolated_review_probe
from minicode.reviewer_verification import ReviewerProbe, ReviewerProbeLedger
from minicode.workflow_workspace import WorkflowWorkspaceTransaction


PRODUCT = '''class Store:
    def save(self, count):
        return count

def release(store, count, amount):
    return store.save(count + amount)

def normalize(count):
    return count
'''


def execute(tmp_path, monkeypatch, code, product=PRODUCT):
    (tmp_path / 'stock.py').write_text(product)
    (tmp_path / 'test_public.py').write_text('def test_public(): assert True\n')
    monkeypatch.setattr(PermissionManager, 'ensure_command', lambda *_a, **_k: None)
    class Sandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv
    verifier = DeterministicVerifier(sandbox_backend=Sandbox(), explicit_trusted_executables={'python': sys.executable})
    probe = ReviewerProbe.create(criterion_ids=['stock-release'], scenario='Release through the public stock API and Store.save', code=code)
    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        frozen = transaction.freeze(allowed_paths=('stock.py', 'test_public.py'), paths_relative_to_cwd=True)
        receipt = run_isolated_review_probe(probe=probe, frozen=frozen,
            check=VerificationCheck('public', 'tests', ('python', '-m', 'pytest', '-q', 'test_public.py'), 20),
            test_path='test_public.py', verifier=verifier, read_roots=('.',), forbidden_paths=('.git', '.env'),
            run_kwargs={'workflow_id': 'wf_' + 'a'*32, 'task_id': 'task_stock', 'execution_assignment_id': 'asg_stock',
                'patch_digest': frozen.patch_digest, 'permissions': PermissionManager(str(tmp_path), prompt=None), 'permission_cwd': str(tmp_path)})
        receipt = ReviewerProbeLedger().record(probe, frozen.patch_digest, receipt)
    assert receipt['status'] == 'passed', receipt
    return receipt


def report(receipt):
    result = receipt['testReport'].get('executionReport')
    assert result is not None, 'A passing case report is not proof that the cited assertion/consumer executed'
    assert all(c['complete'] for c in result['cases']), result
    return result


def test_uncalled_assertion_does_not_gain_execution_evidence(tmp_path, monkeypatch):
    receipt = execute(tmp_path, monkeypatch, 'from stock import normalize\ndef uncalled():\n    assert False\ndef test_stock():\n    assert normalize(2) == 2\n')
    case = report(receipt)['cases'][0]
    assert [a['startLine'] for a in case['assertions']] == [5]
    assert all(a['startLine'] != 3 for a in case['assertions'])


def test_helper_cannot_claim_public_consumer_call_chain(tmp_path, monkeypatch):
    receipt = execute(tmp_path, monkeypatch, 'from stock import normalize\ndef test_stock():\n    assert normalize(2) == 2\n')
    calls = report(receipt)['cases'][0]['calls']
    assert any(c['qualname'] == 'normalize' and c['identityVerified'] for c in calls)
    assert not any(c['qualname'] in ('release', 'Store.save') for c in calls)


def test_real_consumer_chain_is_bound_to_actual_parent_and_code(tmp_path, monkeypatch):
    receipt = execute(tmp_path, monkeypatch, 'from stock import release, Store\ndef test_stock():\n    assert release(Store(), 2, 3) == 5\n')
    case = report(receipt)['cases'][0]
    calls = {c['callId']: c for c in case['calls']}
    save = next(c for c in calls.values() if c['qualname'] == 'Store.save')
    assert save['identityVerified'] and calls[save['parentCallId']]['qualname'] == 'release'
    assert save['path'] == 'stock.py' and save['sourceDigest'].startswith('sha256:')
    assert save['codeDigest'].startswith('sha256:')
    assert case['assertions'][0]['startLine'] == 3


def test_same_name_filename_and_line_fake_cannot_be_real_consumer(tmp_path, monkeypatch):
    code = '''import stock
from stock import Store, release
def test_stock():
    namespace = {}
    exec(compile("class Store:\\n    def save(self, count):\\n        return 999\\n", stock.__file__, "exec"), namespace)
    Store.save = namespace['Store'].save
    assert release(Store(), 2, 3) == 999
'''
    receipt = execute(tmp_path, monkeypatch, code)
    calls = report(receipt)['cases'][0]['calls']
    save = next(c for c in calls if c['qualname'] == 'Store.save')
    assert save['firstLine'] == 2 and not save['identityVerified']


def test_async_resume_and_thread_consumer_keep_real_parent(tmp_path, monkeypatch):
    product = PRODUCT + '''\nasync def async_release(store, count, amount):
    import asyncio
    await asyncio.sleep(0)
    return store.save(count + amount)

def threaded_release(store, count, amount):
    import threading
    result = []
    worker = threading.Thread(target=lambda: result.append(store.save(count + amount)))
    worker.start()
    worker.join()
    return result[0]
'''
    code = '''import asyncio
from stock import async_release, threaded_release, Store
def test_stock():
    assert asyncio.run(async_release(Store(), 2, 3)) == 5
    assert threaded_release(Store(), 2, 3) == 5
'''
    receipt = execute(tmp_path, monkeypatch, code, product)
    case = report(receipt)['cases'][0]
    calls = {c['callId']: c for c in case['calls']}
    saves = [c for c in calls.values() if c['qualname'] == 'Store.save']
    def ancestors(call):
        result = []
        while call['parentCallId']:
            call = calls[call['parentCallId']]
            result.append(call['qualname'])
        return result
    assert len(saves) == 2
    assert any('async_release' in ancestors(c) for c in saves)
    assert any('threaded_release' in ancestors(c) for c in saves)


def test_caught_assertion_failure_is_not_passing_assertion_evidence(tmp_path, monkeypatch):
    receipt = execute(tmp_path, monkeypatch, 'def test_stock():\n    try:\n        assert False\n    except AssertionError:\n        pass\n    assert True\n')
    assertions = report(receipt)['cases'][0]['assertions']
    assert next(a for a in assertions if a['startLine'] == 3)['raised']
    assert not next(a for a in assertions if a['startLine'] == 6)['raised']


def test_execution_report_tamper_and_legacy_absence_are_distinct(tmp_path, monkeypatch):
    from minicode.pytest_execution_evidence import validate_execution_report
    receipt = execute(tmp_path, monkeypatch, 'def test_stock():\n    assert True\n')
    original = receipt['testReport']
    assert validate_execution_report(original)
    altered = deepcopy(original)
    altered['executionReport']['cases'][0]['assertions'][0]['startLine'] += 1
    assert not validate_execution_report(altered)
    old = deepcopy(original)
    del old['executionReport']
    assert not validate_execution_report(old)


def test_unexecuted_branch_on_same_line_has_no_assertion_credit(tmp_path, monkeypatch):
    code = 'def test_stock():\n    if False: assert False\n    assert True; assert 2 == 2\n'
    receipt = execute(tmp_path, monkeypatch, code)
    assertions = report(receipt)['cases'][0]['assertions']
    assert [a['startLine'] for a in assertions] == [3, 3]
    assert len({a['startColumn'] for a in assertions}) == 2


def test_real_exception_check_and_thread_assertion_are_observed(tmp_path, monkeypatch):
    code = '''import pytest, threading
def check_in_thread():
    assert 2 == 2
def test_stock():
    with pytest.raises(ValueError):
        raise ValueError('expected input rejection')
    thread = threading.Thread(target=check_in_thread)
    thread.start()
    thread.join()
'''
    receipt = execute(tmp_path, monkeypatch, code)
    assertions = report(receipt)['cases'][0]['assertions']
    assert {(a['startLine'], a['kind'], a['raised']) for a in assertions} == {
        (3, 'assert', False), (5, 'exception_check', False)}


def test_fake_code_cannot_supply_uncalled_assertion_location(tmp_path, monkeypatch):
    code = '''def uncalled():
    assert False
def test_stock():
    namespace = {}
    exec(compile("def uncalled():\\n    assert True\\n", __file__, "exec"), namespace)
    namespace['uncalled']()
    assert True
'''
    receipt = execute(tmp_path, monkeypatch, code)
    assert [a['startLine'] for a in report(receipt)['cases'][0]['assertions']] == [7]


def test_overwritten_trace_is_explicitly_incomplete(tmp_path, monkeypatch):
    code = 'import sys\ndef test_stock():\n    assert True\n    sys.setprofile(None)\n'
    receipt = execute(tmp_path, monkeypatch, code)
    case = receipt['testReport']['executionReport']['cases'][0]
    assert not case['complete'] and 'trace_hook_replaced' in case['limitations']
    from minicode.pytest_execution_evidence import observed_assertions
    assert observed_assertions(receipt['testReport'], source_digest=receipt['sourceDigest'], start_line=1, end_line=4) == []


def test_legacy_capacity_incomplete_never_acquires_execution_credit(tmp_path, monkeypatch):
    from minicode.pytest_execution_evidence import _digest, observed_assertions, validate_execution_report
    receipt = execute(tmp_path, monkeypatch, 'from stock import release, Store\ndef test_stock():\n    assert release(Store(), 2, 3) == 5\n')
    original = receipt['testReport']
    limited = deepcopy(report(receipt))
    limited['cases'][0].update(complete=False, calls=[], assertions=[], limitations=['execution_report_capacity'])
    limited['digest'] = _digest({key: limited[key] for key in ('version', 'junitDigest', 'cases')})
    extended = {**original, 'executionReport': limited}
    assert validate_execution_report(extended)
    assert limited['cases'][0]['limitations'] == ['execution_report_capacity']
    assert limited['cases'][0]['calls'] == limited['cases'][0]['assertions'] == []
    assert original['passed'] == extended['passed'] == 1
    assert observed_assertions(extended, source_digest=receipt['sourceDigest'], start_line=1, end_line=3) == []


def test_equal_code_from_distinct_files_keeps_each_actual_source_identity(tmp_path, monkeypatch):
    first = '# first storage\ndef consume():\n    return 1\n'
    second = '# other storage\ndef consume():\n    return 1\n'
    (tmp_path / 'first.py').write_text(first)
    (tmp_path / 'second.py').write_text(second)
    one = compile(first, str(tmp_path / 'first.py'), 'exec').co_consts[0]
    two = compile(second, str(tmp_path / 'second.py'), 'exec').co_consts[0]
    assert one is not two and one == two  # The cache must not use CodeType equality.
    receipt = execute(tmp_path, monkeypatch, 'import first, second\ndef test_stock():\n    assert first.consume() == 1\n    assert second.consume() == 1\n')
    calls = [c for c in report(receipt)['cases'][0]['calls'] if c['qualname'] == 'consume']
    assert {c['path'] for c in calls} == {'first.py', 'second.py'}
    assert len({c['sourceDigest'] for c in calls}) == 2
    assert all(c['identityVerified'] for c in calls)
