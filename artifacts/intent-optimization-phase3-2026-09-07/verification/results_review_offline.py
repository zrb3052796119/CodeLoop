from pathlib import Path
import sys,json,hashlib
from dataclasses import asdict
source=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(source))
from minicode.intent_parser import IntentParser,SemanticIntentProposal,IntentPolarity,IntentType,ActionType,IntentContext
p=Path(__file__).parent
baseline=json.loads((p/"fresh50-baseline-live/results.json").read_text())
final=json.loads((p/"fresh50-final-live/results.json").read_text())
class Adapter:
    def __init__(self,proposals):self.requests=[];self.proposals=proposals
    def infer(self,request,**kwargs):
        self.requests.append(asdict(request));return tuple(self.proposals)
def canonical(parsed):
    x=parsed.to_detailed_dict();x.pop("timestamp",None);return x
rows=[]
for b,f in zip(baseline,final):
    assert b["case"]==f["case"]
    case=b["case"];ctx=case.get("context",{})
    context=IntentContext.from_messages(ctx.get("recent_turns",[]),active_task=ctx.get("active_task",""))
    modes={}
    for mode in ["empty","baseline_accepted","final_accepted"]:
        proposals=[]
        if mode!="empty":
            live=b if mode=="baseline_accepted" else f
            for o in live["actual"]["operations"]:
                if any(code.startswith("semantic:") for code in o["evidence_codes"]):
                    proposals.append(SemanticIntentProposal(intent_type=IntentType(o["intent_type"]),action_type=ActionType(o["action_type"]),polarity=IntentPolarity(o["polarity"]),confidence=o["confidence"],clause="fixed accepted candidate for offline boundary comparison"))
        a=Adapter(proposals)
        parsed=IntentParser(semantic_adapter=a).parse(case["text"],context=context)
        modes[mode]=dict(proposals=[asdict(x) for x in proposals],requests=a.requests,actual=canonical(parsed),read_only=parsed.is_read_only())
    rows.append(dict(id=case["id"],modes=modes))
Path(sys.argv[2]).write_text(json.dumps(dict(source=str(source),source_hashes={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (source/"minicode").rglob("*.py")},rows=rows),ensure_ascii=False,indent=2)+"\n")
