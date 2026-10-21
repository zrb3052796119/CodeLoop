"""Passive full-turn observations. Imports no production code until invocation."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass, replace
import hashlib
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from unittest.mock import patch


def sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def json_sha(value: object) -> str:
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    temporary.replace(path)


def error_category(exc: BaseException) -> str:
    name = type(exc).__name__
    if 'Deadline' in name or isinstance(exc, TimeoutError):
        return 'timeout'
    if 'Budget' in name or str(exc) == 'evaluation_output_token_budget_exhausted':
        return 'budget'
    if name in {'HTTPError', 'URLError', 'ConnectionError'}:
        return 'transport'
    if isinstance(exc, KeyboardInterrupt) or 'Cancel' in name:
        return 'cancelled'
    return 'unclassified'


def _sizes(value: object) -> dict[str, int]:
    encoded = json.dumps(value, ensure_ascii=True)
    return {'chars': len(encoded), 'bytes': len(encoded.encode('utf-8'))}


def _numeric_usage(value: object) -> object:
    """Keep numeric accounting and nested counter labels, never response prose."""
    if isinstance(value, dict):
        return {str(k): _numeric_usage(v) for k, v in value.items()
                if isinstance(v, (dict, int, float)) and not isinstance(v, bool)}
    return value


class _ObservedResponse:
    def __init__(self, response, record: dict, observer: 'RunRecorder'):
        self.response = response
        self.record = record
        self.observer = observer
        self.chunks: list[bytes] = []
        self.finished = False
        self.response_bytes = 0
        self.record['status'] = getattr(response, 'status', getattr(response, 'code', None))

    def __getattr__(self, name):
        return getattr(self.response, name)

    def _observe(self, data):
        if isinstance(data, bytes):
            self.response_bytes += len(data)
            if self.response_bytes <= 20 * 1024 * 1024:
                self.chunks.append(data)
            else:
                self.record['response_capture_overflow'] = True
            if not data:
                self._finish()
        return data

    def read(self, *args, **kwargs):
        try:
            data = self._observe(self.response.read(*args, **kwargs))
            if not args or args[0] is None or args[0] < 0:
                self._finish()
            return data
        except BaseException as exc:
            self.record.update(error_type=type(exc).__name__, error_category=error_category(exc))
            self._finish()
            raise

    def read1(self, *args, **kwargs):
        try:
            return self._observe(self.response.read1(*args, **kwargs))
        except BaseException as exc:
            self.record.update(error_type=type(exc).__name__, error_category=error_category(exc))
            self._finish()
            raise

    def _finish(self):
        if self.finished:
            return
        self.finished = True
        self.record['elapsed_ms'] = round((time.monotonic() - self.record.pop('_started')) * 1000, 3)
        self.record['response_bytes'] = self.response_bytes
        self.record['response_received'] = bool(self.response_bytes)
        try:
            body = json.loads(b''.join(self.chunks))
            if isinstance(body, dict):
                usage = body.get('usage')
                self.record['usage'] = _numeric_usage(usage) if isinstance(usage, dict) else None
                self.record['response_model'] = body.get('model') if isinstance(body.get('model'), str) else None
        except (ValueError, UnicodeDecodeError):
            self.record['usage'] = None
            self.record['usage_missing_reason'] = 'response_not_complete_json'
        self.chunks.clear()
        self.observer.save()

    def close(self):
        try:
            self.response.close()
        finally:
            self._finish()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


class RunRecorder:
    def __init__(self, result: dict, run_dir: Path, *, live: bool):
        self.result = result
        self.run_dir = run_dir
        self.live = live
        self.phase = 'setup'
        self.active_model_call: int | None = None
        self.lock = threading.RLock()

    def save(self):
        with self.lock:
            # Runtime/headers are never attached to result. Private timers are omitted.
            public = json.loads(json.dumps(self.result, default=str))
            for request in public.get('provider_requests', []):
                request.pop('_started', None)
            write_json(self.run_dir / 'run.json', public)

    def emit(self, event_type, *, step=None, payload=None):
        with self.lock:
            self.result['events'].append({'type': event_type, 'phase': self.phase, 'step': step, 'payload': dict(payload or {})})

    @contextmanager
    def timed_phase(self, phase: str):
        previous, self.phase = self.phase, phase
        started = time.monotonic()
        try:
            yield
        finally:
            self.result['phase_timings_ms'][phase] = round((time.monotonic() - started) * 1000, 3)
            self.phase = previous
            self.save()

    @contextmanager
    def observe_http(self):
        original = urllib.request.OpenerDirector._open
        observer = self

        def observed(opener, request, data=None):
            raw = request.data or b''
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                payload = None
            url = urlsplit(request.full_url)
            kind = 'embedding' if '/embeddings' in url.path else 'chat' if '/chat/completions' in url.path else 'other'
            with observer.lock:
                index = len(observer.result['provider_requests']) + 1
                record = {
                    'index': index, 'phase': observer.phase, 'model_index': observer.active_model_call,
                    'kind': kind, 'endpoint_host': url.hostname, 'endpoint_path': url.path,
                    'request_bytes': len(raw), 'request_sha256': sha(raw),
                    'request_payload_sha256': json_sha(payload),
                    'request_file': f'wire-requests/{index:03d}.json',
                    'usage': None, 'response_received': False, '_started': time.monotonic(),
                    'body_digests_present': [],
                }
                if isinstance(payload, dict):
                    record.update(model=payload.get('model'), tools_json=_sizes(payload.get('tools', [])),
                                  messages_json=_sizes(payload.get('messages', [])),
                                  tool_count=len(payload.get('tools', [])), stream=bool(payload.get('stream')))
                    if kind == 'embedding':
                        inputs = payload.get('input', [])
                        record['embedding_input_count'] = len(inputs) if isinstance(inputs, list) else 1
                        record['embedding_input_chars'] = sum(len(value) for value in inputs if isinstance(value, str)) if isinstance(inputs, list) else len(str(inputs))
                    for load in observer.result['loads']:
                        # JSON escaping is avoided by comparing the recursively decoded message content.
                        contents = [m.get('content', '') for m in payload.get('messages', []) if isinstance(m, dict)]
                        if load['ok'] and load['body_integrity_ok'] and load['body'] and any(
                            load['body'] in content for content in contents if isinstance(content, str)
                        ):
                            record['body_digests_present'].append(load['body_sha256'])
                            load.setdefault('wire_request_attempts_with_body', []).append(index)
                observer.result['provider_requests'].append(record)
                # Provider credentials live only in HTTP headers, which are never observed.
                # Refuse unexpected body-level credentials instead of persisting them.
                if isinstance(payload, dict) and any(k.lower() in {'api_key', 'apikey', 'authorization', 'password', 'secret'} for k in payload):
                    record.update(error_type='UnsafeRequestBody', error_category='harness_configuration')
                    observer.save()
                    raise RuntimeError('request_body_contains_credentials')
                write_json(observer.run_dir / record['request_file'], payload)
                observer.save()
            if not observer.live:
                record.update(error_type='OfflineNetworkDenied', error_category='harness_configuration')
                record['elapsed_ms'] = 0
                record.pop('_started', None)
                observer.save()
                raise urllib.error.URLError('offline_network_denied')
            try:
                return _ObservedResponse(original(opener, request, data), record, observer)
            except BaseException as exc:
                record.update(error_type=type(exc).__name__, error_category=error_category(exc),
                              elapsed_ms=round((time.monotonic() - record.pop('_started', time.monotonic())) * 1000, 3))
                observer.save()
                raise

        with patch.object(urllib.request.OpenerDirector, '_open', observed):
            yield

    def instrument_registry(self, registry, workspace: Path, allowlist: set[str]):
        from minicode.tooling import ToolRegistry
        selected = []
        for definition in registry.list():
            if definition.name not in allowlist:
                continue
            original, name = definition.run, definition.name

            def observed(data, context, *, original=original, name=name):
                started = time.monotonic()
                output = original(data, context)
                text = str(output.output)
                with self.lock:
                    record = {'ordinal': len(self.result['executions']) + 1,
                              'model_index': len(self.result['model_calls']), 'name': name,
                              'input': data, 'ok': bool(output.ok), 'output': text,
                              'output_sha256': sha(text), 'elapsed_ms': round((time.monotonic() - started) * 1000, 3)}
                    self.result['executions'].append(record)
                    if name == 'load_skill':
                        body, qualified, integrity = '', str(data.get('name', '')), False
                        header, separator, loaded_body = text.partition('\n\n')
                        if output.ok and separator:
                            source_path = next((line[6:] for line in header.splitlines() if line.startswith('PATH: ')), '')
                            path = Path(source_path).resolve()
                            root = workspace / '.mini-code' / 'skills'
                            if path.is_relative_to(root) and path.is_file():
                                body = path.read_text(encoding='utf-8')
                                qualified = str(path.parent.relative_to(root))
                                integrity = body == loaded_body
                        self.result['loads'].append({
                            'model_index': record['model_index'], 'requested_name': data.get('name'),
                            'qualified_name': qualified, 'ok': bool(output.ok), 'body': body,
                            'body_integrity_ok': integrity, 'body_sha256': sha(body),
                            'output_sha256': sha(text), 'request_attempts_with_body': [],
                            'confirmed_handoff_requests': [], 'wire_request_attempts_with_body': [],
                        })
                    self.save()
                return output
            selected.append(replace(definition, run=observed))
        return ToolRegistry(selected, skills=registry.get_skills())

    def cost_summary(self) -> dict:
        requests = self.result['provider_requests']
        model_calls = self.result['model_calls']
        counters = {'input_tokens': 0, 'output_tokens': 0, 'cache_read_tokens': 0, 'cache_creation_tokens': 0}
        for call in model_calls:
            usage = call.get('usage') or {}
            if usage.get('source') == 'provider':
                for key in counters:
                    value = usage.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        counters[key] += value
        missing = [r['index'] for r in requests if not isinstance(r.get('usage'), dict)]
        provider_totals = {}
        for kind in ('chat', 'embedding', 'other'):
            selected = [request for request in requests if request['kind'] == kind]
            totals = {'attempts': len(selected), 'usage_missing': 0,
                      'observed_input_tokens': 0, 'observed_output_tokens': 0,
                      'observed_cache_read_tokens': 0, 'cache_read_missing': 0}
            for request in selected:
                usage = request.get('usage')
                if not isinstance(usage, dict):
                    totals['usage_missing'] += 1
                    totals['cache_read_missing'] += 1
                    continue
                for target, fields in (
                    ('observed_input_tokens', ('prompt_tokens', 'input_tokens')),
                    ('observed_output_tokens', ('completion_tokens', 'output_tokens')),
                ):
                    value = next((usage[field] for field in fields if isinstance(usage.get(field), int)), None)
                    if value is not None:
                        totals[target] += value
                details = usage.get('prompt_tokens_details') or usage.get('input_tokens_details') or {}
                cached = details.get('cached_tokens', usage.get('prompt_cache_hit_tokens'))
                if isinstance(cached, int):
                    totals['observed_cache_read_tokens'] += cached
                else:
                    totals['cache_read_missing'] += 1
            provider_totals[kind] = totals
        return {'model_call_provider_observed_tokens': counters,
                'provider_token_totals_by_kind': provider_totals,
                'provider_http_attempts': len(requests),
                'embedding_http_attempts': sum(r['kind'] == 'embedding' for r in requests),
                'usage_missing_request_ids': missing,
                'provider_usage_complete': bool(requests) and not missing,
                'monetary_cost': None, 'monetary_cost_status': 'not_computed_no_frozen_matching_price',
                'note': 'Model usage excludes embedding token counters; raw provider usage for every HTTP attempt is retained. Missing usage is unknown, never zero.'}


class RecordingModel:
    def __init__(self, adapter, observer: RunRecorder, *, output_cap: int, per_call_output: int, call_timeout: float):
        self.adapter = adapter
        self.observer = observer
        self.output_cap = output_cap
        self.per_call_output = per_call_output
        self.call_timeout = call_timeout
        self.accounted_output = 0
        self.observed_output = 0

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    def next(self, messages, on_stream_chunk=None, store=None, *, cancellation_token=None, deadline_monotonic=None, **controls):
        from minicode.model_call_control import call_model_next, checkpoint_model_call
        checkpoint_model_call(cancellation_token=cancellation_token, deadline_monotonic=deadline_monotonic)
        remaining = self.output_cap - self.accounted_output
        if remaining <= 0:
            self.observer.result['budget_exhausted'] = 'output_tokens'
            raise RuntimeError('evaluation_output_token_budget_exhausted')
        limit = min(remaining, self.per_call_output, controls.get('max_output_tokens_override') or self.per_call_output)
        controls.update(max_output_tokens_override=limit, on_stream_chunk=on_stream_chunk, store=store)
        started = time.monotonic()
        deadline = min(started + self.call_timeout, deadline_monotonic or float('inf'))
        snapshot = json.loads(json.dumps(messages, ensure_ascii=False, default=str))
        result = self.observer.result
        index = len(result['model_calls']) + 1
        call = {'index': index, 'phase': self.observer.phase, 'completed': False,
                'input_file': f'requests/{index:03d}.json', 'input_sha256': json_sha(snapshot),
                'input_messages_json': _sizes(snapshot), 'body_digests_present': [], 'usage': None,
                'requested_output_limit': limit}
        for load in result['loads']:
            if load['ok'] and load['body_integrity_ok'] and load['body'] and any(load['body'] in str(m.get('content', '')) for m in snapshot):
                call['body_digests_present'].append(load['body_sha256'])
                load['request_attempts_with_body'].append(index)
        result['model_calls'].append(call)
        write_json(self.observer.run_dir / call['input_file'], snapshot)
        self.observer.active_model_call = index
        self.observer.save()
        debit = limit
        try:
            step = call_model_next(self.adapter, messages, optional_kwargs=controls,
                                  cancellation_token=cancellation_token, deadline_monotonic=deadline)
            usage = asdict(step.usage) if is_dataclass(step.usage) else None
            call.update(completed=True, type=step.type, content=step.content, calls=step.calls, usage=usage)
            observed = usage.get('output_tokens') if usage else None
            if usage and usage.get('source') == 'provider' and isinstance(observed, int) and observed >= 0:
                debit = observed
                self.observed_output += observed
                call['output_budget_debit_source'] = 'provider_usage'
            else:
                call['output_budget_debit_source'] = 'request_limit_without_provider_usage'
            wire_bodies = {digest for request in result['provider_requests'] if request.get('model_index') == index
                           for digest in request.get('body_digests_present', [])}
            for load in result['loads']:
                if index in load['request_attempts_with_body'] and (not self.observer.live or load['body_sha256'] in wire_bodies):
                    load['confirmed_handoff_requests'].append(index)
            return step
        except BaseException as exc:
            call.update(error_type=type(exc).__name__, error_category=error_category(exc),
                        timeout=error_category(exc) == 'timeout', output_budget_debit_source='request_limit_after_failed_call')
            raise
        finally:
            self.accounted_output += debit
            call.update(elapsed_ms=round((time.monotonic() - started) * 1000, 3), output_budget_debit=debit,
                        provider_request_ids=[r['index'] for r in result['provider_requests'] if r.get('model_index') == index])
            result['output_budget_accounting'] = {'cap': self.output_cap, 'accounted_output_tokens': self.accounted_output,
                                                  'provider_observed_output_tokens': self.observed_output}
            self.observer.active_model_call = None
            write_json(self.observer.run_dir / f'responses/{index:03d}.json', call)
            self.observer.save()
