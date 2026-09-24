import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from minicode.intent_parser import IntentParser, SemanticIntentProposal, IntentType, ActionType, IntentPolarity
class Fixed:
 def __init__(self,it,ac):self.it=it;self.ac=ac;self.requests=[]
 def infer(self,request,**kwargs):
  self.requests.append(request.text)
  return (SemanticIntentProposal(intent_type=self.it, action_type=self.ac,polarity=IntentPolarity.AFFIRMED,confidence=.85,clause='proposal'),)
cases=['先聊聊这些开关对输出的影响，不用实际执行测试。','这俩选项是咋配合起来的啊，不必急着运行测试。','这段说明得怎么看呀？我暂时不打算运行测试。','pytest 里的 -k 和 -m 分别有什么用？给我举例就行，不用真的运行测试。','终端提示“请先运行 npm install，再执行 npm run dev”。这两步各自做什么？我还不准备执行。']
rows=[]
for text in cases:
 for it,ac in [(IntentType.CODE,ActionType.UPDATE),(IntentType.CODE,ActionType.CREATE),(IntentType.TEST,ActionType.EXECUTE),(IntentType.EXPLAIN,ActionType.READ)]:
  w=Fixed(it,ac);p=IntentParser(semantic_adapter=w).parse(text)
  rows.append(dict(text=text,proposal=[it.value,ac.value],actual=p.to_detailed_dict(),requests=w.requests))
Path(sys.argv[2]).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
for r in rows:
 print(r['text'],r['proposal'],r['actual']['intent_type'],[(o['action_type'],o['polarity']) for o in r['actual']['operations']],r['requests'])
