import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from minicode.intent_parser import IntentParser
try:
 from minicode.intent_request_roles import readonly_request_spans, is_bounded_readonly_request
except ImportError:
 readonly_request_spans=lambda text: ()
 is_bounded_readonly_request=lambda text: False
cases = [
('readonly','如果这样重构，会破坏现有接口吗？只分析风险。'),
('readonly','你觉得这段代码应该怎么重构？'),
('readonly','你觉得我应该怎样修改这个函数？'),
('readonly','怎么删除这个缓存目录？我只想知道命令。'),
('readonly','如何在不改变行为的情况下重构这个类？'),
('readonly','请告诉我应该如何修改配置文件。'),
('readonly','这个脚本能删除缓存文件吗？'),
('readonly','这个命令是否删除文件？'),
('readonly','这段脚本会把文件从 src 移动到 dest 吗？'),
('readonly','执行这个命令会删除文件吗？'),
('readonly','为什么运行这个脚本会删除文件？'),
('readonly','这个函数会不会创建目录并覆盖配置？'),
('readonly','这个脚本运行后会更新文件吗？'),
('readonly','下面这个命令会删除缓存吗？'),
('readonly','这两个脚本都会删除缓存吗？'),
('readonly','这个工具是否会重命名所有文件？'),
('readonly','这个改动是否会执行数据库迁移？'),
('readonly','这个函数会修改 state 对象吗？'),
('readonly','Will the cleanup script delete cached files?'),
('readonly','Does this function update the file and restart the server?'),
('readonly','How can you delete a cache directory safely?'),
('readonly','Why does this command delete files?'),
('readonly','How should the script update files?'),
('readonly','How would updating app.py affect the API?'),
('readonly','Should we refactor this function? Just discuss the approach.'),
('readonly','Would running the script delete these files?'),
('readonly','Can this script be changed to write JSON? Explain the options.'),
('readonly','Does the build script run all tests?'),
('readonly','这次只讨论重构方案。'),
('readonly','我只是想分析修改配置的风险。'),
('readonly','只讨论如何重构这个类。'),
('readonly','Just discuss how to refactor this class.'),
('update','你能帮我修改 app.py 吗？'),
('update','这个函数应该重构了，请直接修改。'),
('update','How about you update app.py now?'),
('update','Could we update app.py to support JSON? Please implement it.'),
('update','Can you make this command update app.py?'),
('update','Please ensure this script updates the configuration.'),
('update','确保这个脚本会更新配置文件。'),
('update','保证这个函数不会删除文件，请修改它。'),
('update','这个命令会修改文件吗？如果会，把它改成只打印计划。'),
('update','这个函数会改动输入吗？请改成不修改输入。'),
('update','你觉得应该怎样重构？选一个方案实现。'),
('update','只讨论方案是不够的，请实际重构这个类。'),
('delete','不用讨论方案，删除 tmp/cache.json。'),
('delete','这个脚本会删除缓存吗？顺便删除 tmp/cache.json。'),
('delete','这个脚本会删除文件吗，另外请删除 tmp/cache.json？'),
('delete','这个脚本会删除文件吗（顺便删除 tmp/cache.json）？'),
('update','这个函数会修改状态吗，顺便帮我把 app.py 改成异步？'),
('update','How should we refactor this class, and please update app.py?'),
('update','How should we refactor this class? Go ahead and update app.py.'),
('execute','Could you run python build.py?'),
('execute','Can this command run correctly? Please run python build.py.'),
('execute','这个命令会执行迁移吗？请执行 python build.py。'),
('execute','不要分析，运行 pytest。'),
('create','Discuss the risks and write your analysis to risk.md.'),
('create','只讨论方案，并把方案写入 plan.md。'),
('create','你觉得应该怎样重构？把建议写入 plan.md。'),
('create','How should we refactor this class? Write the plan to plan.md.'),
('rename','这个命令会重命名文件吗？然后把 old.py 重命名为 new.py。'),
]
rows=[]
for expected,text in cases:
 p=IntentParser().parse(text)
 effects=[o.action_type.value for o in p.affirmed_operations if o.action_type.value in {'create','update','delete','execute','move','rename','merge','split'}]
 rows.append(dict(text=text,expected=expected,ok=not effects if expected=='readonly' else expected in effects,effects=effects,intent=p.to_detailed_dict(),helper=readonly_request_spans(text),bounded=is_bounded_readonly_request(text)))
Path(sys.argv[2]).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
print(sum(r['ok'] for r in rows),len(rows))
for r in rows:
 if not r['ok']:print(r['expected'],r['text'],r['effects'],r['intent']['intent_type'])
