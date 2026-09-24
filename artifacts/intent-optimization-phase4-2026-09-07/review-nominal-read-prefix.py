import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from minicode.intent_parser import IntentParser, SemanticIntentProposal, IntentType, ActionType, IntentPolarity
class Writer:
 def __init__(self):self.requests=[]
 def infer(self,request,**kwargs):
  self.requests.append(request.text)
  return (SemanticIntentProposal(intent_type=IntentType.CODE, action_type=ActionType.UPDATE,polarity=IntentPolarity.AFFIRMED,confidence=.85,clause='proposal'),)
cases=['说明文字换成英文。','说明文档给它换个更自然的说法。','解释文字帮我润色一下。','分析报告换成中文。','说明按钮换一个颜色。']
rows=[]
for text in cases:
 w=Writer();p=IntentParser(semantic_adapter=w).parse(text)
 rows.append(dict(text=text,actual=p.to_detailed_dict(),requests=w.requests))
Path(sys.argv[2]).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
for r in rows:print(r['text'],r['actual']['intent_type'],[(o['action_type'],o['polarity']) for o in r['actual']['operations']],r['requests'])
