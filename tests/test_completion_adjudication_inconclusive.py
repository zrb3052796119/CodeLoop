"""Unresolved test evidence must not authorize resumed product changes."""
import pytest

from minicode.agent_loop import run_agent_turn
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.types import AgentStep
from test_completion_failure_adjudication_resume import seed_failed_checkpoint
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import _call

inventory = chronology.inventory


@pytest.mark.parametrize('native', [True, False], ids=['explicit-uncertainty', 'missing-native-verdict'])
def test_unresolved_adjudication_preserves_checkpoint_without_exposing_writer(inventory, monkeypatch, native):
    case = inventory
    document, saved, _ = seed_failed_checkpoint(case, monkeypatch, product_gap=False)
    continuation = HostImplementationContinuation.from_document(document, spec=case['spec'], messages=saved)
    original = (case['workspace'] / 'stock.py').read_bytes()
    requests = []

    class Uncertain:
        def next(self, messages, **kwargs):
            names = set(kwargs['tools_override'].list_all())
            requests.append(names)
            if 'completion_probe_adjudication_submit' in names:
                if native:
                    return _call('uncertain-evidence', 'completion_probe_adjudication_submit',
                        verdict='inconclusive', reason='The independent review cannot establish the expected behavior.',
                        objectiveEvidence=[], sourceEvidence=[])
                return AgentStep(type='assistant', kind='final', content='This evidence remains unresolved.')
            raise AssertionError('Unconfirmed evidence was sent to the product writer.')

    result = run_agent_turn(model=Uncertain(), messages=saved,
        implementation_continuation=continuation, **case['options'])
    assert len(requests) == 1
    assert 'completion_probe_adjudication_submit' in requests[0]
    assert (case['workspace'] / 'stock.py').read_bytes() == original
    assert result[-1].get('_partial') is True
    final_document, _ = case['checkpoints'][-1]
    assert final_document['executorId'] == document['executorId']
    assert final_document['candidateDigest'] == document['candidateDigest']
    assert final_document['reviewState']['failureAdjudications'][-1]['verdict'] == 'inconclusive'
    feedback = final_document['completionFeedback']['content']
    assert 'Repair the evidenced gaps' not in feedback
    assert 'No product repair is inferred' in feedback
