"""Fixed-denominator and false-adoption gate counterexamples."""
from copy import deepcopy
import pytest

from scripts import analyze_multi_agent_trigger as analysis
from scripts import audit_multi_agent_trigger as audit


def experiment():
    rows, checked = [], []
    for index, case in enumerate(audit.CASES):
        identity = {'id': case + '-r1-auto', 'case': case, 'repeat': 1, 'arm': 'auto'}
        rows.append({**identity, 'scopePassed': True, 'processCleanup': {'cleanupComplete': True},
                     'usageComplete': True, 'unknownUsageCalls': 0, 'ledgerDamaged': False, 'recoveryEvidenceErrors': [],
                     'success': False, 'oracleSuccess': False, 'regressionTestPassed': False, 'changedSourceFiles': []})
        checked.append({**identity, 'triggerEvidence': {'deliveredTrigger': index < 2, 'launchClassificationComplete': True,
                                                      'launchRequests': int(index < 2), 'ambiguousTaskRequests': 0}})
    manifest = {'protocolVersion': audit.PROTOCOL, 'phase': 'trigger-gen2', 'gatePolicy': deepcopy(audit.GATE_POLICY)}
    evidence = {'valid': True, 'mode': 'final', 'protocolVersion': audit.PROTOCOL, 'expectedTrials': 8, 'auditedTrials': 8, 'trials': checked}
    return manifest, rows, evidence


def test_two_real_deliveries_pass_adoption_even_when_repairs_fail():
    result = analysis.assess(*experiment())
    assert result['passed'] and result['triggerRate'] == .25 and result['realRepairCount'] == 0


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'partial', 'invalid', 'scope', 'unknown', 'cleanup', 'wrong_protocol', 'negative_launch', 'negative_rejected', 'negative_ambiguous', 'negative_unresolved', 'one_trigger', 'self_report', 'missing_audited_cell', 'old_gate'])
def test_each_guard_blocks_without_changing_denominator(mutation):
    manifest, rows, evidence = experiment()
    if mutation == 'missing': rows.pop()
    elif mutation == 'duplicate': rows[-1] = deepcopy(rows[-2])
    elif mutation == 'partial': evidence['mode'] = 'partial'
    elif mutation == 'invalid': evidence['valid'] = False
    elif mutation == 'scope': rows[0]['scopePassed'] = False
    elif mutation == 'unknown': rows[0]['unknownUsageCalls'] = 1; rows[0]['usageComplete'] = False
    elif mutation == 'cleanup': rows[0]['processCleanup']['cleanupComplete'] = False
    elif mutation == 'wrong_protocol': manifest['protocolVersion'] = 'multi-agent-gen2-v1'
    elif mutation in {'negative_launch', 'negative_rejected'}:
        evidence['trials'][-1]['triggerEvidence']['launchRequests'] = 1
        evidence['trials'][-1]['triggerEvidence']['deliveredTrigger'] = mutation == 'negative_launch'
    elif mutation == 'negative_ambiguous': evidence['trials'][-1]['triggerEvidence']['ambiguousTaskRequests'] = 1
    elif mutation == 'negative_unresolved': evidence['trials'][-1]['triggerEvidence']['launchClassificationComplete'] = False
    elif mutation == 'one_trigger': evidence['trials'][0]['triggerEvidence']['deliveredTrigger'] = False
    elif mutation == 'self_report':
        for row, checked in zip(rows, evidence['trials']):
            row['deliveredTrigger'] = True; row['collaborationExecuted'] = True
            checked['triggerEvidence']['deliveredTrigger'] = False
    elif mutation == 'missing_audited_cell': evidence['trials'].pop()
    elif mutation == 'old_gate': manifest['gatePolicy']['triggerRateDenominator'] = 6
    result = analysis.assess(manifest, rows, evidence)
    assert not result['passed']
    assert result['triggerRateDenominator'] == 8


def test_negative_adoption_is_in_numerator_but_fails_zero_misfire():
    manifest, rows, evidence = experiment()
    evidence['trials'][-1]['triggerEvidence'].update(deliveredTrigger=True, launchRequests=1)
    result = analysis.assess(manifest, rows, evidence)
    assert result['deliveredTriggerTrials'] == 3 and result['triggerRate'] == 3 / 8
    assert result['negativeLaunchRequests'] == 1 and not result['passed']


def test_eight_repairs_without_real_child_delivery_cannot_replace_trigger():
    manifest, rows, evidence = experiment()
    for row, checked in zip(rows, evidence['trials']):
        row.update(success=True, oracleSuccess=True, regressionTestPassed=True, changedSourceFiles=['minicode/a.py'])
        checked['triggerEvidence']['deliveredTrigger'] = False
    result = analysis.assess(manifest, rows, evidence)
    assert result['realRepairCount'] == 8 and result['triggerRate'] == 0 and not result['passed']


def test_invalid_audit_never_labels_diagnostic_counts_verified():
    manifest, rows, evidence = experiment()
    evidence['valid'] = False
    for row in rows:
        row.update(success=True, oracleSuccess=True, regressionTestPassed=True, changedSourceFiles=['minicode/a.py'])
    result = analysis.assess(manifest, rows, evidence)
    assert result['deliveredTriggerTrials'] == 2 and result['realRepairCount'] == 8
    assert result['countsVerified'] is False
    assert result['verifiedDeliveredTriggerTrials'] is None and result['verifiedRepairCount'] is None
    assert result['triggerRateDenominator'] == 8 and not result['passed']
