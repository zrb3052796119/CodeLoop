"""Dispatch the 60-trial directory-presel verification batch."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
POSITIVE = ['E01', 'E02', 'E03', 'E04', 'E05', 'E06', 'E07', 'E08']
NEGATIVE = ['N01', 'N02', 'N03', 'N04', 'N05', 'N06', 'N07', 'N08']
CONCURRENCY = 4
REPEATS = 3


def build_schedule() -> list[tuple[str, int]]:
    schedule = []
    for repeat in range(1, REPEATS + 1):
        for case_id in POSITIVE + NEGATIVE:
            schedule.append((case_id, repeat))
    return schedule


def run_one(case_id: str, repeat: int, live: bool, gate: bool = False) -> dict:
    base = 'trials-gate' if gate else 'trials'
    output = HERE / base / f'{case_id}-P-r{repeat}'
    if output.exists():
        return {'case_id': case_id, 'repeat': repeat, 'skipped': 'output_exists'}
    started = time.monotonic()
    command = [sys.executable, str(HERE / 'runner.py'),
               '--runtime-file', str(HERE / 'runtime-private.json'),
               '--case-id', case_id, '--repeat', str(repeat),
               '--output', str(output)]
    if live:
        command.append('--live')
    if gate:
        command.append('--gate')
    completed = subprocess.run(command, capture_output=True, text=True, timeout=900)
    summary = {}
    for line in completed.stdout.strip().splitlines():
        try:
            summary = json.loads(line)
        except ValueError:
            continue
    summary['returncode'] = completed.returncode
    summary['elapsed_seconds'] = round(time.monotonic() - started, 1)
    if completed.returncode not in (0, 2):
        summary['stderr_tail'] = completed.stderr[-2000:]
    return summary


def main() -> None:
    live = '--live' in sys.argv
    gate = '--gate' in sys.argv
    only = [arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--only=')]
    schedule = build_schedule()
    if only:
        schedule = [(c, r) for c, r in schedule if c in only]
    print(f'dispatching {len(schedule)} trials live={live} concurrency={CONCURRENCY}', flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(run_one, case_id, repeat, live, gate): (case_id, repeat)
                   for case_id, repeat in schedule}
        for future in as_completed(futures):
            summary = future.result()
            results.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    suffix = ('gate' if gate else '') + ('live' if live else 'smoke')
    write = HERE / f'dispatch-{suffix}.json'
    write.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    completed = sum(1 for r in results if r.get('completed'))
    print(f'DONE completed={completed}/{len(results)} -> {write}', flush=True)


if __name__ == '__main__':
    main()
