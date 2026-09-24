"""Compare real versions under identical, already recorded model proposals."""
from __future__ import annotations
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

WORK = Path(__file__).resolve().parent
SOURCE = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(SOURCE))
from minicode.intent_parser import (
    ActionType, IntentContext, IntentParser, IntentPolarity,
    IntentType, SemanticIntentProposal,
)
from summarize import runs

class FixedAdapter:
    def __init__(self, proposals):
        self.proposals = proposals
        self.requests = []
    def infer(self, request, **kwargs):
        self.requests.append(asdict(request))
        return tuple(self.proposals)

def canonical(parsed):
    detail = parsed.to_detailed_dict()
    detail.pop('timestamp', None)
    return detail

rows = []
for dataset, mode, before_dir, after_dir in runs():
    if mode != 'live' or (len(sys.argv) > 3 and dataset != sys.argv[3]):
        continue
    before = json.loads((before_dir/'results.json').read_text())
    after = json.loads((after_dir/'results.json').read_text())
    for old, new in zip(before, after, strict=True):
        assert old['case'] == new['case']
        case = old['case']
        ctx = case.get('context', {})
        context = IntentContext.from_messages(ctx.get('recent_turns', []), active_task=ctx.get('active_task', ''))
        modes = {}
        for name, recorded in [('empty', None), ('baseline_accepted', old), ('final_accepted', new)]:
            proposals = []
            if recorded and recorded['actual']:
                for operation in recorded['actual']['operations']:
                    if any(code.startswith('semantic:') for code in operation['evidence_codes']):
                        proposals.append(SemanticIntentProposal(
                            intent_type=IntentType(operation['intent_type']),
                            action_type=ActionType(operation['action_type']),
                            polarity=IntentPolarity(operation['polarity']),
                            confidence=operation['confidence'],
                            clause='fixed recorded proposal; original request owns scope',
                        ))
            adapter = FixedAdapter(proposals)
            parsed = IntentParser(semantic_adapter=adapter).parse(case['text'], context=context)
            modes[name] = {'proposals': [asdict(p) for p in proposals],
                           'requests': adapter.requests, 'actual': canonical(parsed),
                           'read_only': parsed.is_read_only()}
        rows.append({'dataset': dataset, 'id': case['id'], 'modes': modes})
output = {'source': str(SOURCE), 'source_hashes': {
    str(p.relative_to(SOURCE)): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in (SOURCE/'minicode').rglob('*.py')}, 'rows': rows}
Path(sys.argv[2]).write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n')
print('Replayed', len(rows), 'cases x 3 fixed proposal sets')
