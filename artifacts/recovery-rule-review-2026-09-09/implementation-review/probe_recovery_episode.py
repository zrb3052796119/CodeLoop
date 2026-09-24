import importlib.util
import json
from dataclasses import asdict
from pathlib import Path

spec = importlib.util.spec_from_file_location('fixture', 'tests/test_recovery_lesson.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.recovery_lesson import extract_recovery_episodes, episode_to_task_evidence, RecoveryLessonAgent
from minicode.reflection_llm import parse_llm_candidate, ReflectionLLMConfig
from minicode.reflection_synthesis import ReflectionClaimValidator, ReflectionValueGate
cfg = ReflectionLLMConfig(mode='llm_shadow')

# An unrelated successful command intervenes before the real retry.
trace = f._timeout_retry_trace()
trace[3:3] = f._call(5, 'unrelated', 'run_command', ok=True, tool_input={'command':'git','args':['status']})
ev = TraceEvidenceExtractor().extract(f.TASK, trace)
ep = extract_recovery_episodes(ev)[0]
print('unrelated_followup:', json.dumps({'failed':ep.failed_calls[0].invocation,'selected_followup':ep.followup_calls[0].invocation,'real_retry':ev.tool_calls[-1].invocation}))

# An explicit recovery linked by the shared red-green proof loses that proof in projection.
trace = f._fix_verified_trace()
trace.insert(5, {'event_id':'e12','type':'recovery','action':'Insert expiry validation before token lookup','related_error_call_ids':['c1']})
trace[-2]['output_summary'] = '1 passed'
ev = TraceEvidenceExtractor().extract(f.TASK, trace)
ep = extract_recovery_episodes(ev)[0]
sub = episode_to_task_evidence(ep, ev)
claim = f._abstract_recovery_claim(sub)
claim['evidence_quotes'] = ['Expired token was accepted']
candidate = parse_llm_candidate(f._scripted_response(ev.outcome, [claim]),f.TASK,ev,cfg)
pv = ReflectionClaimValidator().validate(candidate, ev)
sv = ReflectionClaimValidator().validate(candidate, sub)
print('red_green_projection:',json.dumps({'episode_status':ep.causal_status,'parent_checks':[(v.call_id,v.result) for v in ev.verification],'sub_checks':[(v.call_id,v.result) for v in sub.verification],'parent_valid':len(pv.valid_claims),'sub_valid':len(sv.valid_claims),'sub_issues':[i.code for i in sv.issues]}))

# Candidate observations can pass validation but are discarded by the old whole-task value gate.
ev = TraceEvidenceExtractor().extract(f.TASK, f._timeout_retry_trace())
ep = extract_recovery_episodes(ev)[0]
sub = episode_to_task_evidence(ep, ev)
err = sub.errors[0]
claim = {'claim_type':'warning','semantic_key':'report_timeout_observation','statement':'The report command timed out with a one-second budget; its later invocation with a 120-second budget succeeded, without establishing the cause.','evidence_ids':list(err.source_event_ids),'epistemic_status':'inferred','applies_when':'When executing this report command under a short timeout.','limitations':['Only one observation; the timeout increase is not a verified causal fix.'],'verification_ids':[],'related_error_ids':[err.error_id],'related_recovery_ids':[],'evidence_quotes':['Command timed out after 1 seconds']}
candidate = parse_llm_candidate(f._scripted_response(ev.outcome, [claim]),f.TASK,sub,cfg)
validation = ReflectionClaimValidator().validate(candidate,sub)
value = ReflectionValueGate().evaluate(candidate,validation,sub)
client = f._QueueClient([f._scripted_response(ev.outcome, [claim])])
r = RecoveryLessonAgent(client,cfg).attempt_lessons(f.TASK,ev)
print('candidate_observation:',json.dumps({'valid_claims':len(validation.valid_claims),'value_accepted':value.accepted,'reason_codes':value.reason_codes,'lessons':len(r.lessons),'persistable_outcome':len(r.outcomes[0].persistable_claims),'outcome':r.to_dict()['outcomes'][0]}))

# A successfully accepted lesson is not copied into the per-episode outcome.
ev = TraceEvidenceExtractor().extract(f.TASK,f._fix_verified_trace())
sub = episode_to_task_evidence(extract_recovery_episodes(ev)[0],ev)
r = RecoveryLessonAgent(f._QueueClient([f._scripted_response(ev.outcome,[f._abstract_recovery_claim(sub)])]),cfg).attempt_lessons(f.TASK,ev)
print('outcome_accepted_claims:',json.dumps({'lessons':len(r.lessons),'outcome_claim_count':r.outcomes[0].claim_count,'outcome_persistable_claims':len(r.outcomes[0].persistable_claims)}))

# Independent episode responses restart the generated claim-id counter.
import copy
first = f._fix_verified_trace()[:-1]
second = copy.deepcopy(f._fix_verified_trace())
for event in second:
    event['event_id'] = 'second-' + event['event_id']
    if 'call_id' in event:
        event['call_id'] = 'second-' + event['call_id']
    if 'input' in event:
        event['input'] = json.loads(json.dumps(event['input']).replace('auth.py','billing.py'))
    for key in ['files','files_changed','files_read']:
        if key in event:
            event[key] = [p.replace('auth.py','billing.py') for p in event[key]]
ev = TraceEvidenceExtractor().extract(f.TASK, first + second)
responses = []
for index, ep in enumerate(extract_recovery_episodes(ev)):
    sub = episode_to_task_evidence(ep,ev)
    claim = f._abstract_recovery_claim(sub)
    claim['semantic_key'] += f'_case_{index}'
    claim['evidence_quotes'] = ['Expired token was accepted']
    responses.append(f._scripted_response(ev.outcome,[claim]))
r = RecoveryLessonAgent(f._QueueClient(responses),cfg).attempt_lessons(f.TASK,ev)
print('episode_claim_ids:',json.dumps({'episodes':len(r.episodes),'lessons':len(r.lessons),'ids':[c.claim_id for c in r.lessons],'semantic_keys':[c.semantic_key for c in r.lessons],'pipeline_valid_by_id_count':len({c.claim_id:c for c in r.lessons})}))

# A quote of an invocation present in the model envelope is still rejected.
ev = TraceEvidenceExtractor().extract(f.TASK,f._timeout_retry_trace())
sub = episode_to_task_evidence(extract_recovery_episodes(ev)[0],ev)
err = sub.errors[0]
claim = {'claim_type':'warning','semantic_key':'timeout_invocation_quote','statement':'The report invocation used a 120-second timeout after an earlier timeout.','evidence_ids':list(err.source_event_ids),'epistemic_status':'inferred','applies_when':'When executing this report command under a short timeout.','limitations':['One observation.'],'verification_ids':[],'related_error_ids':[err.error_id],'related_recovery_ids':[],'evidence_quotes':[sub.tool_calls[-1].invocation]}
try:
    parse_llm_candidate(f._scripted_response(ev.outcome,[claim]),f.TASK,sub,cfg)
except Exception as exc:
    print('invocation_quote:',json.dumps({'real_invocation':sub.tool_calls[-1].invocation,'rejection':str(exc)}))

# Budget decline must not be reported as a real provider call.
from minicode.agent_budget import AgentTurnBudget
client = f._QueueClient([])
r = RecoveryLessonAgent(client,cfg).attempt_lessons(f.TASK,ev,agent_budget=AgentTurnBudget(max_model_calls=1))
print('budget_call_count:',json.dumps({'client_calls':client.calls,'reported_llm_called':r.llm_called,'failure_code':r.outcomes[0].failure_code}))
