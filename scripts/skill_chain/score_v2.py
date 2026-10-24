"""Offline derived scores; original runs and frozen cases are never rewritten.

Protocol compliance is observable tool sequencing, not semantic correctness.
Only an attributed agent/human review can supply a semantic verdict; it is not a gold label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DISCOVERY_TOOLS = frozenset({'load_skill', 'browse_skill_directory', 'search_skills'})
OBSERVATION_SCOPE = (
    'Successful read_file calls whose input or FILE/PATH output header identifies '
    'a SKILL.md under a skills directory; exact complete Skill-body matches in '
    'any ordinary recorded tool output. This does not prove absence of fragmented, '
    'encoded or unrecorded body exposure. Listing paths or browsing descriptions '
    'alone is not treated as reading a Skill body.'
)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def normalized_path(value: object) -> str:
    text = posixpath.normpath(str(value).strip().strip('"\''))
    # macOS records may use either spelling for the same temporary directory.
    return text.removeprefix('/private') if text.startswith('/private/var/') else text


def relative_path(value: object, workspace: str) -> str:
    path = normalized_path(value)
    root = normalized_path(workspace)
    return path[len(root) + 1:] if path.startswith(root + '/') else path


def is_skill_path(value: object) -> bool:
    parts = normalized_path(value).split('/')
    return len(parts) >= 3 and parts[-1].lower() == 'skill.md' and 'skills' in parts[:-2]


def body_bypasses(
    run: Mapping[str, Any], skill_bodies: Mapping[str, str]
) -> list[dict[str, Any]]:
    observations = []
    for index, execution in enumerate(run.get('executions', []), 1):
        name = execution.get('name')
        if name in DISCOVERY_TOOLS:
            continue
        output = str(execution.get('output', ''))
        reasons = []
        paths = []
        if name == 'read_file' and execution.get('ok'):
            arguments = execution.get('input') or {}
            if isinstance(arguments, dict) and is_skill_path(arguments.get('path', '')):
                paths.append(str(arguments['path']))
            # Read only the leading canonical header, not prose in the body.
            header = re.match(r'(?i)^(?:FILE|PATH):[ \t]*(.+?)(?:\r?\n|$)', output)
            if header and is_skill_path(header[1]):
                paths.append(header[1])
            if paths:
                reasons.append('successful_skill_file_read')
        body_matches = sorted(
            name for name, body in skill_bodies.items() if body and body in output
        )
        if body_matches:
            reasons.append('complete_skill_body_in_ordinary_output')
        if reasons:
            observations.append({
                'execution_index': index,
                'ordinal': execution.get('ordinal'),
                'model_index': execution.get('model_index'),
                'tool': name,
                'reasons': reasons,
                'paths': sorted(set(paths)),
                'body_matches': body_matches,
                'output_sha256': digest(output),
            })
    return observations


def _reviewed_outcome(review: Mapping[str, Any] | None) -> tuple[str, bool | None]:
    if not review or review.get('status') in {None, 'unknown', 'manual_required'}:
        return 'manual_required', None
    if review.get('status') not in {'pass', 'fail'}:
        raise ValueError('Review status must be pass, fail, or unknown')
    if review.get('reviewer_type') not in {'agent', 'human'}:
        raise ValueError('reviewer_type must accurately identify agent or human')
    if not all(review.get(key) for key in ('reviewer', 'reason', 'evidence')):
        raise ValueError('An attributed verdict requires reviewer, reason, and evidence')
    if not isinstance(review['evidence'], list) or not all(
        isinstance(item, str) and item.strip() for item in review['evidence']
    ):
        raise ValueError('Review evidence must be a nonempty list of references')
    return 'attributed_semantic_review', review['status'] == 'pass'


def derive_score(
    run: Mapping[str, Any], expected: Mapping[str, Any], *,
    skill_bodies: Mapping[str, str] | None = None,
    body_corpus_complete: bool = False,
    semantic_review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score only recorded facts; missing semantic review never becomes success."""
    bodies = dict(skill_bodies or {})
    for load in run.get('loads', []):
        if load.get('body'):
            bodies.setdefault(str(load.get('qualified_name')), str(load['body']))
    bypasses = body_bypasses(run, bodies)
    calls = run.get('model_calls', [])
    infrastructure_error = bool(run.get('infrastructure_error') or any(
        call.get('error_type') and not call.get('completed') for call in calls
    ))
    unclassified = run.get('stop_reason') == 'exception' and not run.get('budget_exhausted')
    valid = not bool(run.get('fake') or run.get('timeout') or infrastructure_error
                     or unclassified or run.get('stop_reason') == 'interrupted')
    loaded = [load for load in run.get('loads', [])
              if load.get('ok') and load.get('body_integrity_ok')]
    names = {str(load['qualified_name']) for load in loaded}
    allowed = set(expected['allowed_skills'])
    completed_calls = {call.get('index'): call for call in calls if call.get('completed')}

    def handoffs(load: Mapping[str, Any]) -> list[int]:
        return [index for index in load.get('confirmed_handoff_requests', [])
                if index in completed_calls
                and load.get('body_sha256') in completed_calls[index].get('body_digests_present', [])]

    stages = []
    previous_evidence = 0
    final = str(run.get('final', ''))
    for spec in expected['stages']:
        load = next((item for item in loaded if item['qualified_name'] in spec['skills']), None)
        confirmed = handoffs(load) if load else []
        read = next((item for item in run.get('executions', [])
                     if item.get('name') == 'read_file' and item.get('ok')
                     and relative_path((item.get('input') or {}).get('path', ''), str(run.get('workspace', ''))) == spec['evidence']
                     and load and item['model_index'] > load['model_index']
                     and any(index <= item['model_index'] for index in confirmed)), None)
        stages.append({
            'load_hit': load is not None,
            'confirmed_handoff': bool(confirmed),
            'evidence_after_handoff': read is not None,
            'phase_order': bool(load and load['model_index'] > previous_evidence),
            # A label/token check is only a formatting/protocol observation.
            'report_markers_present': spec['report_label'].lower() in final.lower()
            and all(str(token).lower() in final.lower() for token in spec['required_tokens']),
        })
        previous_evidence = read['model_index'] if read else 10**9
    result_constraints = bool(run.get('completed') and run.get('fixture_unchanged')
                              and not any(str(token).lower() in final.lower()
                                          for token in expected.get('forbidden_output_tokens', [])))
    negative = bool(expected['negative'])
    false_trigger = bool(names or bypasses) if negative else None
    negative_protocol_clean = bool(valid and result_constraints and not false_trigger) if negative else None
    if negative and not body_corpus_complete and not false_trigger:
        negative_protocol_clean = None
    protocol = (negative_protocol_clean if negative else
                bool(valid and result_constraints and stages and not (names - allowed)
                     and all(all(stage.values()) for stage in stages)))
    outcome_status, semantic_correct = _reviewed_outcome(semantic_review)
    task_success = (False if protocol is False or semantic_correct is False else
                    True if protocol is True and semantic_correct is True else None)
    return {
        'schema': 'skill-chain-derived-score/v2',
        'valid_live': valid, 'negative': negative,
        'protocol_compliance': protocol,
        'semantic_correct': semantic_correct,
        'semantic_review_is_gold': False,
        'outcome_status': outcome_status,
        'task_success': task_success,
        'negative_protocol_clean': negative_protocol_clean,
        'negative_clean': task_success if negative else None,
        'negative_false_trigger': false_trigger,
        'successful_loader_names': sorted(names),
        'wrong_extra_loads': sorted(names - allowed),
        'ordinary_body_exposures': bypasses,
        'body_corpus_complete': body_corpus_complete,
        'observation_scope': OBSERVATION_SCOPE,
        'stages': stages,
        'attributed_semantic_review': dict(semantic_review) if semantic_review else None,
    }


def read_body_corpus(run_dir: Path, run: Mapping[str, Any]) -> tuple[dict[str, str], bool]:
    """Read only hash-verified retained fixture Skill files; no reconstruction."""
    manifest_path = run_dir / 'fixture-manifest.json'
    if not manifest_path.is_file():
        return {}, False
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    bodies = {}
    complete = True
    workspace = Path(str(run.get('workspace', run_dir / 'workspace'))).resolve()
    for relative, expected_digest in manifest.get('files', {}).items():
        if not is_skill_path(relative):
            continue
        path = (workspace / relative).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            complete = False
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_digest:
            complete = False
            continue
        bodies[relative] = raw.decode('utf-8')
    return bodies, complete and bool(bodies)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--reviews', type=Path, help='Attributed agent/human review JSON keyed by case ID')
    parser.add_argument('--output', type=Path, help='New derived JSON; refuses overwrites')
    args = parser.parse_args()
    cases_path = HERE / 'eval/cases.json'
    raw_cases = cases_path.read_bytes()
    if hashlib.sha256(raw_cases).hexdigest() != (HERE / 'eval/cases.sha256').read_text().split()[0]:
        raise ValueError('Frozen cases digest mismatch')
    cases = {case['id']: case for case in json.loads(raw_cases)['cases']}
    reviews = json.loads(args.reviews.read_text()) if args.reviews else {}
    rows = []
    for run_path in sorted(args.batch.glob('C*/run.json')):
        case_id = run_path.parent.name
        run = json.loads(run_path.read_text(encoding='utf-8'))
        bodies, complete = read_body_corpus(run_path.parent, run)
        rows.append({'id': case_id, 'run_sha256': hashlib.sha256(run_path.read_bytes()).hexdigest(),
                     'score': derive_score(run, cases[case_id]['expected'], skill_bodies=bodies,
                                           body_corpus_complete=complete, semantic_review=reviews.get(case_id))})
    result = {
        'schema': 'skill-chain-derived-batch/v2',
        'scorer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'rubric_sha256': hashlib.sha256((HERE / 'outcome-rubric.md').read_bytes()).hexdigest(),
        'cases_sha256': hashlib.sha256(raw_cases).hexdigest(),
        'counts': {'attempted': len(rows),
                   'valid_live': sum(row['score']['valid_live'] for row in rows),
                   'protocol_pass': sum(row['score']['protocol_compliance'] is True for row in rows),
                   'semantically_reviewed': sum(row['score']['outcome_status'] == 'attributed_semantic_review' for row in rows),
                   'task_success': sum(row['score']['task_success'] is True for row in rows),
                   'task_unknown': sum(row['score']['task_success'] is None for row in rows)},
        'rows': rows,
    }
    output = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf-8') as handle:
            handle.write(output)
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
