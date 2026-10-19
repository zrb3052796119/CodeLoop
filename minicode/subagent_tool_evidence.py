"""Bounded untrusted child diagnostics; only the parent writes its journal."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import threading

_NAME = 'tool-evidence.jsonl'
_MAX_BYTES = 4 * 1024 * 1024
_MAX_RECORD = 64 * 1024
_MAX_RECORDS = 512
_FIELDS = {'tool_name', 'phase', 'tool_input', 'output', 'is_error', 'invocation_id'}
_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}')


def open_transport_directory(path: Path) -> int | None:
    """Unsupported platforms lose optional diagnostics, never weaken no-follow."""
    if (os.name != 'posix' or any(not hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK'))
            or os.open not in getattr(os, 'supports_dir_fd', ())):
        return None
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None


@contextmanager
def tool_evidence_writer(directory: Path):
    from minicode.failure_evidence import redact_diagnostic_text, sanitize_diagnostic_value
    root = open_transport_directory(directory)
    descriptor = None
    try:
        if root is not None:
            try:
                descriptor = os.open(_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     0o600, dir_fd=root)
            except OSError:
                pass
        lock = threading.Lock()
        used = count = 0
        def record(*, tool_name, phase, tool_input=None, output='', is_error=False, invocation_id):
            nonlocal used, count
            if descriptor is None:
                return
            value = {'tool_name': str(tool_name)[:120], 'phase': phase,
                     'tool_input': sanitize_diagnostic_value(tool_input) if phase == 'started' else None,
                     'output': redact_diagnostic_text(output if isinstance(output, str) else '[NON-TEXT OUTPUT OMITTED]', max_chars=16000),
                     'is_error': bool(is_error), 'invocation_id': invocation_id}
            raw = (json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode()
            if len(raw) > _MAX_RECORD:
                value['tool_input'] = {'preview': redact_diagnostic_text(json.dumps(value['tool_input']), max_chars=8000), 'truncated': True}
                raw = (json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode()
            with lock:
                if len(raw) > _MAX_RECORD or used + len(raw) > _MAX_BYTES or count >= _MAX_RECORDS:
                    return
                used += len(raw)
                count += 1
                if os.write(descriptor, raw) != len(raw):
                    raise OSError('short child diagnostic write')
                os.fsync(descriptor)
        yield record
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if root is not None:
            os.close(root)


def replay_tool_evidence(directory: int | None, sink) -> None:
    """Treat spool content as data: fixed file, bounded reads, closed records."""
    if directory is None or not callable(sink):
        return
    descriptor = None
    try:
        descriptor = os.open(_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > _MAX_BYTES:
            return
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            remaining = _MAX_BYTES
            for _ in range(_MAX_RECORDS):
                raw = stream.readline(min(remaining + 1, _MAX_RECORD + 1))
                if not raw:
                    break
                remaining -= len(raw)
                if remaining < 0 or len(raw) > _MAX_RECORD or not raw.endswith(b'\n'):
                    break  # A partial write after SIGKILL cannot become evidence.
                try:
                    record = json.loads(raw)
                    if (not isinstance(record, dict) or set(record) != _FIELDS
                            or record['phase'] not in {'started', 'finished'}
                            or not isinstance(record['tool_name'], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.:-]{0,119}', record['tool_name'])
                            or not isinstance(record['invocation_id'], str) or not _ID.fullmatch(record['invocation_id'])
                            or not isinstance(record['output'], str) or len(record['output']) > 16000
                            or type(record['is_error']) is not bool):
                        continue
                    # The parent sink re-redacts and assigns its own child ID,
                    # timestamp and destination; no child path is accepted.
                    from minicode.failure_evidence import redact_diagnostic_text
                    record['tool_name'] = redact_diagnostic_text(record['tool_name'], max_chars=120)
                    sink(**record)
                except Exception:  # Untrusted records and a broken optional sink cannot change the terminal.
                    continue
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)
