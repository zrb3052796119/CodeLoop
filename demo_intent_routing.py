"""演示：两句话在生产意图解析器 + 生产路由器里的真实结果。"""

import json

from minicode.intent_parser import parse_intent
from minicode.skill_router import SkillRouter, required_skill_names_for_routing
from minicode.capability_registry import CapabilityRegistry

PROMPTS = [
    "给我讲个笑话",
    "Do not use the pytest-debugging skill; just tell me a joke",
]

fixture = json.load(open("tests/fixtures/agent_quality/skill-routing.json"))
catalog = []
for raw in fixture["catalog"]:
    skill = dict(raw)
    skill.setdefault("qualified_name", skill["name"])
    skill.setdefault("description", "")
    skill.setdefault("source", "project")
    skill.setdefault("path", f"/quality-fixture/{skill['name']}/SKILL.md")
    catalog.append(skill)

router = SkillRouter()
registry = CapabilityRegistry()

for prompt in PROMPTS:
    print("=" * 72)
    print(f"输入: {prompt!r}")
    print("=" * 72)

    parsed = parse_intent(prompt)

    print("\n── ParsedIntent（顶层）────────────────────────────")
    print(f"  intent_type   : {parsed.intent_type.value}")
    print(f"  action_type   : {parsed.action_type.value}")
    print(f"  confidence    : {parsed.confidence}")
    print(f"  keywords      : {parsed.keywords}")
    print(f"  entities      : {parsed.entities}")
    print(f"  decision_reason: {parsed.decision_reason!r}")

    print(f"\n── 子句级 operations（共 {len(parsed.operations)} 条）────")
    for i, op in enumerate(parsed.operations):
        print(f"  [{i}] intent={op.intent_type.value:<10} action={op.action_type.value:<8}"
              f" polarity={op.polarity.value}")
        text = getattr(op, "text", None)
        if text:
            print(f"      子句文本   : {text!r}")
        kw = getattr(op, "keywords", None)
        if kw:
            print(f"      关键词     : {list(kw)[:8]}")
        ev = getattr(op, "evidence_codes", None) or getattr(op, "evidence", None)
        if ev:
            codes = [getattr(e, "code", e) for e in ev]
            print(f"      证据码     : {codes[:8]}")

    affirmed = parsed.affirmed_operations
    negated = parsed.negated_operations
    print(f"\n── 极性汇总: affirmed={len(affirmed)}  negated={len(negated)} ──")
    for op in affirmed:
        print(f"  affirmed: {op.intent_type.value}/{op.action_type.value}")
    for op in negated:
        print(f"  negated : {op.intent_type.value}/{op.action_type.value}")

    print("\n── SkillRouter.route()（生产调用，与 CI 评测同款）────")
    routing = router.route(catalog, parsed, registry, top_k=5)
    selected = [item.qualified_name or item.name for item in routing.selected]
    print(f"  selected      : {selected}")
    print(f"  required      : {required_skill_names_for_routing(routing)}")
    for attr in ("outcome", "decision", "abstained", "reason"):
        val = getattr(routing, attr, None)
        if val is not None:
            print(f"  {attr:<13}: {val}")
    print()
