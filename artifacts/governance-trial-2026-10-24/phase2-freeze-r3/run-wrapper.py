"""Execute the published r3 schedule once; never refreeze or replace r2 data."""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

base = Path(__file__).parent
repo = Path('/Users/zhourunbo/code/coding agent/MiniCode-Python-main')
output = base / 'phase2-r3/prepared/execution'
log_path = base / 'phase2-live-r3.log'
meta_path = base / 'phase2-live-r3-run-meta.json'
expected = 'b32718a77f1680cf2d7d465d655a95bdcdcf5c6c745c3d30e1b9e430193d3b01'
registered = 'eaa9645d3a2a39649d1e80c28ae3db39fc3c9bab89f8586e12515378babee5b2'
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

if digest(output / 'manifest.json') != expected:
    raise SystemExit('Published frozen manifest differs')
if any(p.exists() for p in (log_path, meta_path, output / 'trials', output / 'private', output / 'results.json')):
    raise SystemExit('Prior r3 execution evidence exists; inspect explicitly, never rerun')
if subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo, text=True).strip():
    raise SystemExit('Source checkout must be clean at initial launch')
if subprocess.check_output(['git', 'diff', '8b15640ab14316873596e921a7b6dda116320672', 'HEAD', '--', 'minicode', 'scripts', 'tests', 'pyproject.toml'], cwd=repo):
    raise SystemExit('Runtime source changed since preregistration')
assert digest(repo / 'artifacts/governance-trial-2026-10-24/phase2-freeze-r3/manifest.json') == expected
assert digest(repo / 'artifacts/governance-trial-2026-10-24/phase2-preparation-r3/manifest.json') == registered
assert json.loads((output / 'study-design.json').read_text())['portableManifestSha256'] == registered
command = [str(repo / '.venv-acceptance/bin/python'), 'scripts/run_three_arm_real.py', 'run', '--output', str(output)]
meta = dict(command=command, manifestSha256=expected, registrationSha256=registered,
            sourceHead=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
            model='glm-5.3-flash', economicBudgetCap=None, scheduledCells=32,
            sourceBaseline='9f9369421486f018c837f84d21bd727aed6ab326',
            harnessRevision='8b15640ab14316873596e921a7b6dda116320672',
            predecessorManifest='32dacc257169eff53026a3b8e1663ed68b62ceae1adac118589073921d47d332',
            predecessorDisposition='Stopped pilot: 5 completed retained, 1 interrupted unfinalized, 26 unstarted; never pooled with r3',
            started=time.time(), noRefreeze=True, noCellReruns=True)
meta_path.write_text(json.dumps(meta, indent=2) + '\n')
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
try:
    with log_path.open('x') as log:
        result = subprocess.run(command, cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT)
    meta['exitCode'] = result.returncode
finally:
    meta['finished'] = time.time()
    meta['durationSeconds'] = meta['finished'] - meta['started']
    meta_path.write_text(json.dumps(meta, indent=2) + '\n')
print(json.dumps(meta))
raise SystemExit(meta['exitCode'])
