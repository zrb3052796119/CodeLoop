"""Excluded connectivity/usage probe; never effect-estimate evidence."""
from dataclasses import asdict
import json
from pathlib import Path
import time

from run_three_arm_real import frozen_runtime, resolved_transport_route
from evaluation_transport import EvaluationTransport, TransportConfig
from minicode.model_registry import create_model_adapter
from minicode.tooling import ToolRegistry


def main():
    root = Path('artifacts/three-arm-real-2026-10-13/transport-pilot').resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'requests.sqlite').exists():
        raise RuntimeError('Prior pilot evidence exists; do not overwrite.')
    runtime = frozen_runtime()
    config = TransportConfig(
        ledger_path=str(root / 'requests.sqlite'), expected_model=runtime['model'],
        expected_route_sha256=resolved_transport_route(runtime), deadline_epoch=time.time()+180,
        max_model_calls=2, max_total_tokens=50000, max_output_tokens=512,
        connect_timeout_seconds=30, idle_timeout_seconds=60, request_timeout_seconds=120,
    )
    (root / 'config.json').write_text(json.dumps(asdict(config), indent=2))
    model = create_model_adapter(runtime['model'], tools=ToolRegistry([]), runtime=runtime)
    with EvaluationTransport(config) as transport:
        for _ in range(2):
            model.next([{'role':'user','content':'Reply with exactly READY. Do not use tools.'}])
        snapshot = transport.ledger.snapshot()
    (root / 'receipts.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    print(json.dumps({
        'requests': snapshot['started'], 'usageComplete': snapshot['usageComplete'],
        'cacheUsageComplete': snapshot['cacheUsageComplete'],
        'tokens': snapshot['inputTokensKnown'] + snapshot['outputTokensKnown'],
        'timing': [{k:r.get(k) for k in ['seconds','ttftSeconds','firstByteSeconds','streamDone','usage']} for r in snapshot['requests']],
    }, ensure_ascii=False))

if __name__ == '__main__':
    main()
