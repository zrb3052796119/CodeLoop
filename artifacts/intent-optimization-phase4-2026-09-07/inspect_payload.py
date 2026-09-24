"""Read-only: enumerate possible semantic inputs without a provider or network."""
import hashlib,json,sys
from dataclasses import asdict
from pathlib import Path
source=Path(sys.argv[1]).resolve();sys.path.insert(0,str(source))
from minicode.intent_parser import IntentParser,IntentContext
w=Path(__file__).resolve().parent;r=w.parents[1]
class Recorder:
    def __init__(self):self.requests=[]
    def infer(self,request,**kwargs):self.requests.append(asdict(request));return ()
files=[w/'holdout.json'] if len(sys.argv)>3 else [r/'artifacts/intent-evaluation-2026-09-07/routine_cases.json',r/'artifacts/intent-evaluation-2026-09-07/independent_cases.json',w.parent/'intent-phase1/holdout.json',w.parent/'intent-phase2/holdout.json',w.parent/'intent-phase3/holdout.json',w/'holdout.json']
rows=[]
for path in files:
 for case in json.loads(path.read_text()):
  c=case.get('context',{});a=Recorder();IntentParser(semantic_adapter=a).parse(case['text'],context=IntentContext.from_messages(c.get('recent_turns',[]),active_task=c.get('active_task','')))
  rows.append({'id':case['id'],'requests':a.requests})
output={'source':str(source),'dataset_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},'source_hashes':{str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (source/'minicode').rglob('*.py')},'case_count':len(rows),'eligible_request_count':sum(len(x['requests']) for x in rows),'rows':rows}
Path(sys.argv[2]).write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
print(output['case_count'],'cases;',output['eligible_request_count'],'potential semantic requests')
