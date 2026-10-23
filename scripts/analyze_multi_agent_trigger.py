#!/usr/bin/env python3
"""Fixed-eight adoption gate and descriptive diagnostics, independent of runner."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

try:
    from scripts.audit_multi_agent_trigger import Audit, CASES, NEGATIVE_CASES, PROTOCOL, GATE_POLICY, canonical_digest, legacy
except ModuleNotFoundError:
    from audit_multi_agent_trigger import Audit, CASES, NEGATIVE_CASES, PROTOCOL, GATE_POLICY, canonical_digest, legacy


def assess(manifest: dict, rows: list[dict], audit: dict) -> dict:
    expected = {(case, 1, 'auto') for case in CASES}
    cells = Counter((r.get('case'), r.get('repeat'), r.get('arm')) for r in rows)
    audited = audit.get('trials', [])
    audited_cells = Counter((r.get('case'), r.get('repeat'), r.get('arm')) for r in audited)
    valid_coverage = len(audited) == 8 and set(audited_cells) == expected and all(n == 1 for n in audited_cells.values())
    # Never trust row-level collaboration claims or delete invalid cells from 8.
    delivered = [r for r in audited if r.get('case') in CASES and r.get('triggerEvidence', {}).get('deliveredTrigger') is True]
    delivered_cases = sorted({r['case'] for r in delivered})
    negatives = [r for r in audited if r.get('case') in NEGATIVE_CASES]
    negative_complete = (len(negatives) == 2 and {r['case'] for r in negatives} == set(NEGATIVE_CASES)
                         and all(r.get('triggerEvidence', {}).get('launchClassificationComplete') is True for r in negatives))
    negative_launches = sum(r.get('triggerEvidence', {}).get('launchRequests', 0) for r in negatives)
    repairs = [r for r in rows if r.get('success') is True and r.get('oracleSuccess') is True
               and r.get('regressionTestPassed') is True and r.get('scopePassed') is True and r.get('changedSourceFiles')]
    conditions = {
        'triggerProtocolAndFrozenPolicy': manifest.get('protocolVersion') == PROTOCOL and manifest.get('phase') == 'trigger-gen2' and manifest.get('gatePolicy') == GATE_POLICY,
        'eightTerminalCells': len(rows) == 8 and set(cells) == expected and all(n == 1 for n in cells.values()),
        'validFinalIndependentAudit': audit.get('valid') is True and audit.get('mode') == 'final' and audit.get('protocolVersion') == PROTOCOL and audit.get('expectedTrials') == 8 and audit.get('auditedTrials') == 8 and valid_coverage,
        'cleanScopeUsageAndCleanup': len(rows) == 8 and all(r.get('scopePassed') is True and r.get('processCleanup', {}).get('cleanupComplete') is True
            and r.get('usageComplete') is True and r.get('unknownUsageCalls') == 0 and r.get('ledgerDamaged') is False and r.get('recoveryEvidenceErrors') == [] for r in rows),
        'atLeastTwoDeliveredTriggers': len(delivered_cases) >= 2,
        'negativeLaunchClassificationComplete': negative_complete,
        'zeroNegativeLaunchRequests': len(negatives) == 2 and negative_launches == 0 and all(r.get('triggerEvidence', {}).get('ambiguousTaskRequests') == 0 for r in negatives),
    }
    counts_verified = conditions['validFinalIndependentAudit']
    return {'schemaVersion': 1, 'passed': all(conditions.values()), 'conditions': conditions,
            'countsVerified': counts_verified,
            'verifiedDeliveredTriggerTrials': len(delivered_cases) if counts_verified else None,
            'verifiedRepairCount': len(repairs) if counts_verified else None,
            'triggerRateDenominator': 8, 'deliveredTriggerTrials': len(delivered_cases), 'triggerRate': len(delivered_cases) / 8,
            'deliveredCases': delivered_cases, 'negativeLaunchRequests': negative_launches if negative_complete else None,
            'knownNegativeLaunchRequests': negative_launches, 'realRepairCount': len(repairs),
            'interpretation': 'Selective adoption on this frozen synthetic population only. Invalid/missing cells remain in denominator and block passage. Repair success is separate; no causal before/after or single-agent superiority claim.'}


def request_costs(directory: Path, rows: list[dict]) -> dict:
    roles = defaultdict(lambda: {'requests': 0, 'knownTokens': 0, 'unknownUsageRequests': 0, 'requestSeconds': 0.0, 'missingDurationRequests': 0})
    for row in rows:
        path = directory / 'private' / row['id'] / 'transport-summary.json'
        if not path.exists():
            continue
        for request in json.loads(path.read_text()).get('requests', []):
            role = str(request.get('role') or 'unknown').lower()
            group = 'parentIncludingNativeReviews' if role == 'main' else 'readOnlyChildren' if role in {'plan', 'explore', 'analyst'} else 'executor' if role == 'executor' else 'otherWorkflowRoles'
            value = roles[group]
            value['requests'] += 1
            usage = request.get('usage', {})
            counts = (usage.get('input_tokens'), usage.get('output_tokens'))
            if usage.get('source') == 'provider' and all(type(n) is int and n >= 0 for n in counts):
                value['knownTokens'] += sum(counts)
            else:
                value['unknownUsageRequests'] += 1
            seconds = request.get('seconds')
            if type(seconds) in (int, float) and seconds >= 0:
                value['requestSeconds'] += seconds
            else:
                value['missingDurationRequests'] += 1
    return {'byRoleGroup': dict(sorted(roles.items())), 'interpretation': 'Known tokens are lower bounds when usage is unknown. Request seconds may overlap and are not phase wall time. Native read-only reviews remain in main cost, never counted as task adoption.'}


def analyze(directory: Path) -> dict:
    manifest_bytes = (directory / 'manifest.json').read_bytes()
    result_path = directory / 'results.json'
    results_bytes = result_path.read_bytes() if result_path.exists() else b'{"trials":[]}'
    manifest, results = json.loads(manifest_bytes), json.loads(results_bytes)
    audit = Audit(directory, allow_partial=False).run()
    rows = results.get('trials', [])
    gate = assess(manifest, rows, audit)
    gate.update(manifestSha256=legacy.digest(manifest_bytes), resultsSha256=legacy.digest(results_bytes) if result_path.exists() else None, auditSha256=canonical_digest(audit))
    costs = request_costs(directory, rows)
    for name, value in [('trigger-audit.json', audit), ('trigger-gate.json', gate), ('trigger-request-costs.json', costs)]:
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    by_id = {r['id']: r for r in audit['trials']}
    lines = ['# Phase 2 新八题触发结果', '', '本轮是全新合成任务包，与历史任务不可比；没有同题修前对照，不能识别因果增益或单 Agent 优越性。', '',
             '计数状态：' + ('完整独立审计已验证。' if gate['countsVerified'] else '证据无效或不完整；以下均为待验证记录值，不是已证实送达或修复。'), '',
             '触发门：' + ('通过' if gate['passed'] else '未通过') + f"；执行并送达记录 {gate['deliveredTriggerTrials']}/8（{gate['triggerRate']:.0%}）。",
             f"负例已知启动请求 {gate['knownNegativeLaunchRequests']}；零误触发证据完整：{gate['conditions']['negativeLaunchClassificationComplete']}。修复成功记录 {gate['realRepairCount']}/8，独立报告。", '',
             '| 题目 | 启动请求 | 子代理执行/送达 | 动态提示 | 提示响应工具 | 修复成功 | 请求 | 已知 token | Agent 秒 | 未知用量请求 | 终止诊断 |',
             '| --- | ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |']
    for row in rows:
        trial = by_id.get(row['id'], {})
        trigger, guidance = trial.get('triggerEvidence', {}), trial.get('guidanceEvidence', {})
        lines.append(f"| {row['case']} | {trigger.get('launchRequests', '未知')} | {len(trigger.get('actualChildIds', []))}/{len(trigger.get('deliveredChildIds', []))} | {guidance.get('dynamicHintObserved', '未知')} | {guidance.get('firstHintResponseTools')} | {row.get('success')} | {row.get('modelCalls')} | {row.get('totalTokens')} | {row.get('durationSeconds')} | {row.get('unknownUsageCalls', '未知')} | {','.join(trial.get('diagnostics', {}).get('terminalOutcomes', [])) or row.get('error', '')} |")
    lines += ['', '未满足条件：' + ('、'.join(k for k, v in gate['conditions'].items() if not v) or '无'), '',
              '完整静态/动态呈现与后续选择见 trigger-audit.json。动态提示未呈现不能解释为拒绝采纳；复制到原生只读视图的提示不重复算父提示。提示后选择仅作描述，不作因果归因。', '',
              '角色费用见 trigger-request-costs.json；未知用量保留为未知，失败不删除，不补抽。', '']
    (directory / 'trigger-report.md').write_text('\n'.join(lines))
    return gate


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.input.resolve()), ensure_ascii=False))
