import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from minicode.intent_parser import IntentParser
cases=['这个函数能否帮我重构一下？','这个函数可否请你帮我重构一下？','这个配置是不是可以帮我修改一下？','该函数能否帮我改成异步？','这个脚本是否可以请你修改一下？','这个命令能否请你执行一下？','Can this file please be updated?','Could this function please be refactored?','Would this script please be updated to support JSON?']
rows=[dict(text=t,actual=IntentParser().parse(t).to_detailed_dict()) for t in cases]
Path(sys.argv[2]).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
for r in rows:print(r['text'],r['actual']['intent_type'],[(o['action_type'],o['polarity']) for o in r['actual']['operations']])
