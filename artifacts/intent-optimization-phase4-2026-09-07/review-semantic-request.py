import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from minicode.intent_parser import IntentParser, SemanticIntentProposal, IntentType, ActionType, IntentPolarity
class Writer:
 def __init__(self):self.requests=[]
 def infer(self,request,**kwargs):
  self.requests.append(request.text)
  return (SemanticIntentProposal(intent_type=IntentType.CODE, action_type=ActionType.UPDATE,polarity=IntentPolarity.AFFIRMED,confidence=.85,clause='proposal'),)
cases=['Can this file please be updated?','Could this function please be refactored?','Would this script please be updated to support JSON?','这个脚本是否可以请你修改一下？','这个函数能否帮我重构一下？','Does this file update the cache?','这个脚本是否会修改缓存？']
rows=[]
for text in cases:
 w=Writer();p=IntentParser(semantic_adapter=w).parse(text)
 rows.append(dict(text=text,actual=p.to_detailed_dict(),requests=w.requests))
Path(sys.argv[2]).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
for r in rows:print(r['text'],r['actual']['intent_type'],[(o['action_type'],o['polarity']) for o in r['actual']['operations']],r['requests'])
