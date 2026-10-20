"""Host pytest reachability evidence; never a proof of business semantics.

This module is copied into the host's private pytest plugin. It has no project
imports, and captures only candidate Python code and the explicit test target.
"""
from __future__ import annotations

import ast
import dis
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import sys
import threading
import types


def _digest(value):
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def code_digest(code):
    """Bind executable code, not a mutable function name or co_filename."""
    def constant(value):
        if isinstance(value, types.CodeType):
            return ['code', code_digest(value)]
        if isinstance(value, frozenset):
            return ['frozenset', sorted(constant(v) for v in value)]
        return [type(value).__name__, repr(value)]
    return _digest([code.co_code.hex(), code.co_argcount, code.co_posonlyargcount,
        code.co_kwonlyargcount, code.co_flags, code.co_names, code.co_varnames,
        code.co_freevars, code.co_cellvars, code.co_firstlineno, code.co_qualname,
        code.co_linetable.hex(), code.co_exceptiontable.hex(), code.co_stacksize,
        [constant(v) for v in code.co_consts]])


def assertion_index(source, path):
    """Exact syntactic assertion identity; runtime observation is separate."""
    tree = ast.parse(source)
    digest = _digest(source.encode())
    aliases = {a.asname or a.name for n in tree.body if isinstance(n, ast.Import)
               for a in n.names if a.name == 'pytest'}
    imports = {a.asname or a.name for n in tree.body if isinstance(n, ast.ImportFrom)
               and n.module == 'pytest' for a in n.names if a.name in ('raises', 'warns')}
    result = []
    for node in ast.walk(tree):
        kind = 'assert' if isinstance(node, ast.Assert) else None
        if isinstance(node, ast.Call):
            fn = node.func
            if (isinstance(fn, ast.Name) and fn.id in imports or
                isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                and fn.value.id in aliases and fn.attr in ('raises', 'warns')):
                kind = 'exception_check'
        if kind is None:
            continue
        row = {'path': path, 'sourceDigest': digest, 'startLine': node.lineno,
            'endLine': node.end_lineno, 'startColumn': node.col_offset,
            'endColumn': node.end_col_offset, 'kind': kind}
        row['assertionId'] = 'assert_' + _digest(row)[7:31]
        result.append(row)
    return sorted(result, key=lambda r: (r['startLine'], r['startColumn']))


class ExecutionCollector:
    """A deduplicated per-case call tree and precise probe opcode observations."""
    def __init__(self, root, test_paths):
        self.root = Path(root).resolve()
        self.test_paths = {str(Path(p.split('::')[0]).resolve()) for p in test_paths}
        self.cases = []
        self.current = None
        self.files, self.codes, self.opcodes = {}, {}, {}
        self.frames, self.thread_parents = {}, {}
        self._previous = None
        self._monitor = None
        self._monitored_codes = {}

    def _file(self, filename):
        if filename in self.files:
            return self.files[filename]
        result = None
        path = Path(filename)
        if (path.is_absolute() and str(path).startswith(str(self.root) + '/')
                and path.suffix == '.py' and not path.is_symlink()
                and path.resolve() == path):
            relative = path.relative_to(self.root)
            if not set(relative.parts) & {'.git', '.venv', 'venv', 'site-packages', '__pycache__'}:
                try:
                    raw = path.read_bytes()
                    source = raw.decode('utf-8')
                    tree = compile(source, str(path), 'exec', dont_inherit=True)
                    digests = set()
                    def walk(code):
                        digests.add(code_digest(code))
                        for item in code.co_consts:
                            if isinstance(item, types.CodeType):
                                walk(item)
                    walk(tree)
                    if str(path) in self.test_paths:
                        # Pytest rewrites assertion code before executing it.
                        # Authenticate that transformation, not arbitrary code
                        # compiled with a spoofed filename/line number.
                        from _pytest.assertion.rewrite import rewrite_asserts
                        rewritten = ast.parse(source, filename=str(path))
                        rewrite_asserts(rewritten, raw, str(path))
                        walk(compile(rewritten, str(path), 'exec', dont_inherit=True))
                    result = (relative.as_posix(), _digest(raw), digests,
                        assertion_index(source, relative.as_posix()) if str(path) in self.test_paths else [])
                except (OSError, UnicodeError, SyntaxError, ValueError):
                    self._limitation('source_identity_unavailable')
        self.files[filename] = result
        return result

    def _identity(self, code):
        cached = self.codes.get(id(code))
        if cached is not None and cached[0] is code:
            return cached[1]
        data = self._file(code.co_filename)
        identity = None
        if data is not None:
            digest = code_digest(code)
            identity = {'path': data[0], 'sourceDigest': data[1], 'qualname': code.co_qualname,
                'firstLine': code.co_firstlineno, 'codeDigest': digest,
                'identityVerified': digest in data[2]}
        # CodeType equality ignores its filename. Retain the actual object so
        # equal bytecode from a different candidate file cannot inherit identity.
        self.codes[id(code)] = (code, identity)
        return identity

    def _limitation(self, reason):
        if self.current is not None:
            self.current['complete'] = False
            if reason not in self.current['limitations']:
                self.current['limitations'].append(reason)

    def _parent(self, frame):
        parent = frame.f_back
        while parent is not None:
            entry = self.frames.get(id(parent))
            if entry is not None and entry[0] is parent:
                return entry[1]
            parent = parent.f_back
        return self.thread_parents.get(id(threading.current_thread()))

    def _profile(self, frame, event, arg):
        if self.current is None:
            return
        if event == 'call':
            # Thread roots have no Python f_back to their creating consumer.
            if frame.f_code.co_name == 'start' and frame.f_code.co_filename == threading.__file__:
                thread = frame.f_locals.get('self')
                if isinstance(thread, threading.Thread):
                    self.thread_parents[id(thread)] = self._parent(frame)
            identity = self._identity(frame.f_code)
            if identity is None:
                return
            if (self._monitor is not None and frame.f_code.co_filename in self.test_paths
                    and id(frame.f_code) not in self._monitored_codes):
                sys.monitoring.set_local_events(self._monitor, frame.f_code, sys.monitoring.events.INSTRUCTION)
                self._monitored_codes[id(frame.f_code)] = frame.f_code
            existing = self.frames.get(id(frame))
            if existing is not None and existing[0] is frame:
                return  # Coroutine/generator resume retains its original ancestry.
            parent = self._parent(frame)
            call_id = 'call_' + _digest([parent, identity])[7:31]
            calls = self.current['_calls']
            if call_id not in calls:
                if len(calls) >= 4096:
                    self._limitation('call_report_capacity')
                    return
                calls[call_id] = {'callId': call_id, 'parentCallId': parent, **identity, 'count': 0}
            calls[call_id]['count'] += 1
            self.frames[id(frame)] = (frame, call_id)
        elif event == 'return':
            entry = self.frames.get(id(frame))
            if entry is None or entry[0] is not frame:
                return
            flags = frame.f_code.co_flags
            if flags & (inspect.CO_COROUTINE | inspect.CO_GENERATOR | inspect.CO_ASYNC_GENERATOR):
                opcode = frame.f_code.co_code[frame.f_lasti] if frame.f_lasti >= 0 else 0
                if dis.opname[opcode] in ('YIELD_VALUE', 'YIELD_FROM'):
                    return
            self.frames.pop(id(frame), None)

    def _positions(self, code):
        cached = self.opcodes.get(id(code))
        if cached is None or cached[0] is not code:
            data = self._file(code.co_filename)
            result = {}
            if data and data[3]:
                for instruction in dis.get_instructions(code):
                    position = instruction.positions
                    if position.lineno is None or position.col_offset is None:
                        continue
                    # Control/return bookkeeping inherits source locations but is
                    # not evaluation of an assertion expression.
                    if instruction.opname in ('RESUME', 'RETURN_VALUE', 'RETURN_CONST', 'NOP', 'CACHE',
                            'JUMP_FORWARD', 'JUMP_BACKWARD', 'JUMP_BACKWARD_NO_INTERRUPT'):
                        continue
                    for row in data[3]:
                        if ((row['startLine'], row['startColumn']) <= (position.lineno, position.col_offset)
                                and (position.end_lineno, position.end_col_offset) <= (row['endLine'], row['endColumn'])):
                            result.setdefault(instruction.offset, []).append(row)
            self.opcodes[id(code)] = (code, result)
        return self.opcodes[id(code)][1]

    def _trace(self, frame, event, arg):
        if self.current is None:
            return None
        if frame.f_code.co_filename not in self.test_paths:
            return None
        if event == 'call':
            frame.f_trace_opcodes = True
            frame.f_trace_lines = False
        if event in ('opcode', 'exception'):
            self._observe(frame.f_code, frame.f_lasti, event == 'exception')
        return self._trace

    def _observe(self, code, offset, raised=False):
        if self.current is None or code.co_filename not in self.test_paths:
            return
        identity = self._identity(code)
        if identity is None or not identity['identityVerified']:
            return
        for row in self._positions(code).get(offset, ()):
            assertions = self.current['_assertions']
            item = assertions.setdefault(row['assertionId'], {**row, 'raised': False})
            if raised:
                item['raised'] = True

    def _instruction(self, code, offset):
        self._observe(code, offset)

    def _raise(self, code, offset, error):
        if not isinstance(error, (StopIteration, StopAsyncIteration)):
            self._observe(code, offset, True)

    def start(self, node_id):
        if self.current is not None:
            self._limitation('overlapping_test_execution')
            return
        self.current = {'nodeId': node_id, 'complete': True, 'limitations': [], '_calls': {}, '_assertions': {}}
        self.frames, self.thread_parents = {}, {}
        self._previous = (sys.getprofile(), sys.gettrace(), threading.getprofile(), threading.gettrace())
        if any(self._previous):
            self._limitation('preexisting_trace_hook')
        self._initial_threads = {id(t) for t in threading.enumerate()}
        if len(self._initial_threads) > 1:
            self._limitation('preexisting_threads_not_traced')
        threading.setprofile(self._profile)
        sys.setprofile(self._profile)
        if hasattr(sys, 'monitoring'):
            self._monitor = next((i for i in range(5, -1, -1) if sys.monitoring.get_tool(i) is None), None)
            if self._monitor is None:
                self._limitation('instruction_monitor_unavailable')
            else:
                sys.monitoring.use_tool_id(self._monitor, 'minicode-pytest-evidence')
                sys.monitoring.register_callback(self._monitor, sys.monitoring.events.INSTRUCTION, self._instruction)
                sys.monitoring.register_callback(self._monitor, sys.monitoring.events.RAISE, self._raise)
                sys.monitoring.set_events(self._monitor, sys.monitoring.events.RAISE)
        else:
            threading.settrace(self._trace)
            sys.settrace(self._trace)

    def stop(self):
        if self.current is None:
            return
        if (sys.getprofile() != self._profile or
                (not hasattr(sys, 'monitoring') and sys.gettrace() != self._trace)):
            self._limitation('trace_hook_replaced')
        if any(id(t) not in self._initial_threads for t in threading.enumerate()):
            self._limitation('thread_outlived_case')
        profile, trace, thread_profile, thread_trace = self._previous
        sys.setprofile(profile)
        sys.settrace(trace)
        threading.setprofile(thread_profile)
        threading.settrace(thread_trace)
        if self._monitor is not None:
            for code in self._monitored_codes.values():
                sys.monitoring.set_local_events(self._monitor, code, 0)
            sys.monitoring.set_events(self._monitor, 0)
            sys.monitoring.register_callback(self._monitor, sys.monitoring.events.INSTRUCTION, None)
            sys.monitoring.register_callback(self._monitor, sys.monitoring.events.RAISE, None)
            sys.monitoring.free_tool_id(self._monitor)
            self._monitor = None
            self._monitored_codes.clear()
        case = self.current
        self.current = None
        case['calls'] = sorted(case.pop('_calls').values(), key=lambda row: row['callId'])
        case['assertions'] = sorted(case.pop('_assertions').values(), key=lambda row: row['assertionId'])
        self.cases.append(case)
        self.frames, self.thread_parents = {}, {}

    def report(self, junit_digest):
        """Return the complete collected original, independent of display size.

        Collection limitations remain on their cases. Transport uses bounded
        shards below; unrelated later calls must not erase an earlier witness.
        """
        cases = [dict(case) for case in self.cases]
        body = {'version': 1, 'junitDigest': junit_digest, 'cases': cases}
        return {**body, 'digest': _digest(body)}


_EXECUTION_CHUNK_BYTES = 64 * 1024


def _execution_chunk_name(case_path: Path, index: int) -> str:
    # The host chooses case_path. The manifest supplies no filesystem paths.
    return case_path.name + '.execution-' + str(index) + '.jsonpart'


def _reject_symlinks(path: Path) -> None:
    """Windows has no ``O_NOFOLLOW``. Refuse a symlink anywhere on the path."""
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise OSError("refusing to follow a symlink")


def _write_execution_bytes(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    elif os.name == "nt":
        _reject_symlinks(path)
    else:
        raise OSError("safe execution evidence I/O unavailable")
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            count = os.write(descriptor, view)
            if count <= 0:
                raise OSError('short execution evidence write')
            view = view[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_execution_report(report: dict, case_path: Path) -> dict:
    """Persist the full original outside the bounded case/JUnit sidecar.

    This module is copied into the host's private pytest plugin. Only a fully
    written, hashed set of shards is published in that plugin's manifest.
    """
    chunk_count, buffer, total = 0, bytearray(), 0
    digest = hashlib.sha256()

    def flush() -> None:
        nonlocal total, chunk_count
        data = bytes(buffer)
        path = case_path.parent / _execution_chunk_name(case_path, chunk_count)
        _write_execution_bytes(path, data)
        chunk_count += 1
        digest.update(data)
        total += len(data)
        buffer.clear()

    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    for text in encoder.iterencode(report):
        data = text.encode('utf-8')
        offset = 0
        while offset < len(data):
            count = min(_EXECUTION_CHUNK_BYTES - len(buffer), len(data) - offset)
            buffer.extend(data[offset:offset + count])
            offset += count
            if len(buffer) == _EXECUTION_CHUNK_BYTES:
                flush()
    if buffer:
        flush()
    manifest = {'storageVersion': 1, 'junitDigest': report['junitDigest'],
            'executionDigest': report['digest'], 'byteCount': total,
            'digest': 'sha256:' + digest.hexdigest(), 'chunkCount': chunk_count}
    _write_execution_bytes(case_path.with_name(case_path.name + '.execution.json'),
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                   allow_nan=False).encode('utf-8'))
    return manifest


def _read_execution_chunks_by_path(manifest: dict, case_path: Path) -> dict | None:
    """Read execution shards without ``dir_fd``. Windows cannot open a directory."""
    parent = case_path.parent
    parent_info = parent.lstat()
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        return None
    payload = bytearray()
    for index in range(manifest['chunkCount']):
        name = _execution_chunk_name(case_path, index)
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            return None
        path = parent / name
        expected_size = min(_EXECUTION_CHUNK_BYTES, manifest['byteCount'] - len(payload))
        metadata = path.lstat()
        if (stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1 or metadata.st_size != expected_size):
            return None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size != expected_size
                    or (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino)):
                return None
            data = bytearray()
            while len(data) <= expected_size:
                block = os.read(descriptor, expected_size + 1 - len(data))
                if not block:
                    break
                data.extend(block)
            after = os.fstat(descriptor)
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            if (tuple(getattr(before, field) for field in fields)
                    != tuple(getattr(after, field) for field in fields)
                    or len(data) != expected_size):
                return None
            payload.extend(data)
        finally:
            os.close(descriptor)
    if _digest(bytes(payload)) != manifest['digest']:
        return None
    report = json.loads(payload)
    if (not isinstance(report, dict) or report.get('junitDigest') != manifest['junitDigest']
            or report.get('digest') != manifest['executionDigest']
            or json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                          allow_nan=False).encode('utf-8') != payload):
        return None
    return report


def read_execution_report(manifest: object, case_path: Path) -> dict | None:
    """Read exact private shards before cleanup, restoring the unchanged v1 report.

    Missing or altered storage supplies no execution credit. Case results and
    their original JUnit binding are independently retained by the caller.
    """
    def digest_value(value: object) -> bool:
        return (isinstance(value, str) and len(value) == 71 and value.startswith('sha256:')
                and all(char in '0123456789abcdef' for char in value[7:]))

    try:
        if (not isinstance(manifest, dict) or set(manifest) != {
                'storageVersion', 'junitDigest', 'executionDigest', 'byteCount', 'digest', 'chunkCount'}
                or type(manifest['storageVersion']) is not int or manifest['storageVersion'] != 1
                or type(manifest['byteCount']) is not int or manifest['byteCount'] < 1
                or any(not digest_value(manifest[key]) for key in ('junitDigest', 'executionDigest', 'digest'))
                or type(manifest['chunkCount']) is not int or manifest['chunkCount'] < 1):
            return None
        if manifest['chunkCount'] != (manifest['byteCount'] + _EXECUTION_CHUNK_BYTES - 1) // _EXECUTION_CHUNK_BYTES:
            return None
        if os.name == "nt":
            return _read_execution_chunks_by_path(manifest, case_path)
        directory = os.open(case_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        payload = bytearray()
        try:
            for index in range(manifest['chunkCount']):
                expected_size = min(_EXECUTION_CHUNK_BYTES, manifest['byteCount'] - len(payload))
                descriptor = os.open(_execution_chunk_name(case_path, index),
                    os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory)
                try:
                    before = os.fstat(descriptor)
                    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                            or before.st_size != expected_size):
                        return None
                    data = bytearray()
                    while len(data) <= expected_size:
                        block = os.read(descriptor, expected_size + 1 - len(data))
                        if not block:
                            break
                        data.extend(block)
                    after = os.fstat(descriptor)
                    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
                    if (tuple(getattr(before, field) for field in fields)
                            != tuple(getattr(after, field) for field in fields)
                            or len(data) != expected_size):
                        return None
                    payload.extend(data)
                finally:
                    os.close(descriptor)
        finally:
            os.close(directory)
        if _digest(bytes(payload)) != manifest['digest']:
            return None
        report = json.loads(payload)
        if (not isinstance(report, dict) or report.get('junitDigest') != manifest['junitDigest']
                or report.get('digest') != manifest['executionDigest']
                or json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                              allow_nan=False).encode('utf-8') != payload):
            return None
        return report
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def validate_execution_report(report):
    """Old case-only receipts remain readable but contain no execution credit."""
    if not isinstance(report, dict):
        return False
    value = report.get('executionReport')
    if not isinstance(value, dict) or set(value) != {'version', 'junitDigest', 'cases', 'digest'}:
        return False
    body = {key: value[key] for key in ('version', 'junitDigest', 'cases')}
    try:
        if (value['version'] != 1 or value['junitDigest'] != report['reportDigest']
                or _digest(body) != value['digest'] or not isinstance(value['cases'], list)):
            return False
        expected = {case['nodeId'] for case in report['caseReport']['cases']}
        seen = set()
        for case in value['cases']:
            if (set(case) != {'nodeId', 'complete', 'limitations', 'assertions', 'calls'}
                    or case['nodeId'] in seen or case['nodeId'] not in expected
                    or type(case['complete']) is not bool or not isinstance(case['limitations'], list)
                    or any(not isinstance(x, str) for x in case['limitations'])
                    or case['complete'] == bool(case['limitations'])):
                return False
            seen.add(case['nodeId'])
            calls = {c['callId']: c for c in case['calls']}
            if len(calls) != len(case['calls']):
                return False
            for call in calls.values():
                if (set(call) != {'callId', 'parentCallId', 'path', 'sourceDigest', 'qualname', 'firstLine',
                        'codeDigest', 'identityVerified', 'count'} or type(call['identityVerified']) is not bool
                        or type(call['firstLine']) is not int or call['firstLine'] < 1
                        or type(call['count']) is not int or call['count'] < 1
                        or not all(isinstance(call[k], str) and call[k] for k in ('path', 'sourceDigest', 'qualname', 'codeDigest'))):
                    return False
                identity = {k: call[k] for k in ('path', 'sourceDigest', 'qualname', 'firstLine', 'codeDigest', 'identityVerified')}
                if call['callId'] != 'call_' + _digest([call['parentCallId'], identity])[7:31]:
                    return False
                visited, ancestor = set(), call
                while ancestor['parentCallId'] is not None:
                    if ancestor['parentCallId'] in visited or ancestor['parentCallId'] not in calls:
                        return False
                    visited.add(ancestor['parentCallId'])
                    ancestor = calls[ancestor['parentCallId']]
            assertion_ids = set()
            for assertion in case['assertions']:
                if (set(assertion) != {'assertionId', 'path', 'sourceDigest', 'startLine', 'endLine',
                        'startColumn', 'endColumn', 'kind', 'raised'} or type(assertion['raised']) is not bool
                        or assertion['kind'] not in ('assert', 'exception_check')
                        or any(type(assertion[k]) is not int for k in ('startLine', 'endLine', 'startColumn', 'endColumn'))
                        or not 1 <= assertion['startLine'] <= assertion['endLine']
                        or min(assertion['startColumn'], assertion['endColumn']) < 0):
                    return False
                source = {k: v for k, v in assertion.items() if k not in ('assertionId', 'raised')}
                if (assertion['assertionId'] != 'assert_' + _digest(source)[7:31]
                        or assertion['assertionId'] in assertion_ids):
                    return False
                assertion_ids.add(assertion['assertionId'])
        return seen == expected
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def observed_assertions(report, *, source_digest, start_line, end_line):
    """Return actual non-raising assertions in completed passing test cases."""
    if not validate_execution_report(report):
        return []
    passed = {c['nodeId'] for c in report['caseReport']['cases'] if c['outcome'] == 'passed'}
    return [{'nodeId': c['nodeId'], **a} for c in report['executionReport']['cases']
        if c['complete'] and c['nodeId'] in passed for a in c['assertions']
        if not a['raised'] and a['sourceDigest'] == source_digest
        and start_line <= a['startLine'] and a['endLine'] <= end_line]


def execution_plugin_source(root, targets):
    """Exact host source copied outside the candidate; no candidate imports."""
    return (Path(__file__).read_text(encoding='utf-8') + '\n_execution = ExecutionCollector(' + repr(str(Path(root).resolve()))
        + ', ' + repr([str((Path(root) / p.split('::')[0]).resolve()) for p in targets if p.split('::')[0].endswith('.py')]) + ')\n')
