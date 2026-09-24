"""Run the audited synthetic evaluation against the explicitly checked GLM route."""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

WORK = Path(__file__).resolve().parent
ROOT = WORK.parents[1]
sys.path.insert(0, str(ROOT))
from minicode.config import load_runtime_config

EXPECTED_URL = 'https://open.bigmodel.cn/api/coding/paas/v4/chat/completions'

def verify_inputs():
    for manifest_name in ['candidate-payload-manifest.json', 'baseline-payload-manifest.json']:
        manifest = json.loads((WORK/manifest_name).read_text())
        source = Path(manifest['source'])
        for name, digest in manifest['dataset_hashes'].items():
            assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest
        for name, digest in manifest['source_hashes'].items():
            assert hashlib.sha256((source/name).read_bytes()).hexdigest() == digest
    runtime = load_runtime_config(ROOT)
    assert runtime['model'] == 'glm-5.3-flash'
    assert runtime['provider'] == 'custom'
    assert runtime['customBaseUrl'] == EXPECTED_URL
    assert runtime['intentSemanticClassifier']['timeoutSeconds'] == 5.0
    assert runtime['intentSemanticClassifier']['minConfidence'] == 0.72

assert (WORK/'payload-audit.md').is_file(), 'Read-only independent payload audit required'
verify_inputs()
# Fixed inputs only. No Agent loop or action execution is used by evaluate.py.
jobs = [
    ('fresh50', 'fresh50-baseline-live', ['--source-root', str(WORK/'baseline')]),
    ('fresh50', 'fresh50-final-live', []),
    ('old50', 'old50-final-live', []),
    ('prior40', 'prior40-final-live', []),
    ('priorfresh40', 'priorfresh40-final-live', []),
    ('priorfresh50', 'priorfresh50-final-live', []),
]
for dataset, name, extra in jobs:
    verify_inputs()
    print('START', name, flush=True)
    with (WORK/(name+'.log')).open('w') as log:
        subprocess.run([sys.executable, str(WORK/'evaluate.py'), '--dataset', dataset,
                        '--output', name, '--live', *extra], cwd=ROOT,
                       env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    result = json.loads((WORK/name/'summary.json').read_text())
    print('DONE', name, result['all_labeled_checks_correct'], '/', result['total'],
          result['semantic_status'], flush=True)
