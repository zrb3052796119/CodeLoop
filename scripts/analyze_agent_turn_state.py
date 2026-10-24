"""Measure nested-function capture without importing or running the agent loop.

The responsibility labels are an explicit review hypothesis, not inferred
ownership. This inventory is advisory; it is not a CI or evaluation gate.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import json
import symtable
from collections import defaultdict
from pathlib import Path

RESPONSIBILITIES = {
    "reconcile_task_ledger": "context",
    "observable_message_count": "telemetry",
    "raise_if_deadline_exceeded": "budget",
    "enforce_skill_load_before_final": "tools",
    "enforce_discovery_before_final": "tools",
    "enforce_subagent_completion_before_final": "lifecycle",
    "observe_workflow_delivery": "lifecycle",
    "committed_delivery_remaining": "lifecycle",
    "finish_workflow_delivery": "lifecycle",
    "restore_committed_child_receipts": "lifecycle",
    "interrupted_parent_message": "lifecycle",
    "_revalidate_model_visible_memory": "context",
    "observe_implementation_tool_result": "tools",
    "submission_correction_progress": "tools",
    "progress_view_paths": "context",
    "implementation_request_messages": "context",
    "implementation_view_active": "context",
    "configured_output_limit": "budget",
    "completion_input_estimate": "budget",
    "_attempt_max_steps_soft_landing": "completion",
    "finish_bounded_completion": "completion",
    "observe_batch_feedback": "tools",
    "flush_write_feedback_batch": "tools",
    "deliver_batch_feedback": "tools",
    "supersede_completion_feedback": "tools",
    "enforce_implementation_completion_before_final": "completion",
    "closing_failed": "completion",
    "retain_migration_checkpoint": "completion",
    "phase_input_estimate": "budget",
    "_predictive_compaction": "context",
}


def _tables(table, ancestors=()):
    yield table, ancestors
    for child in table.get_children():
        yield from _tables(child, (*ancestors, table))


def _target_root(node):
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _owned_nodes(function):
    """Walk a function's body, excluding nested lexical scopes."""
    def walk(node):
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Lambda, ast.ListComp, ast.SetComp,
                             ast.DictComp, ast.GeneratorExp)):
            return
        for child in ast.iter_child_nodes(node):
            yield from walk(child)
    for statement in function.body:
        yield from walk(statement)


def analyze(source: str, *, filename: str, function_name: str) -> dict:
    tree = ast.parse(source, filename=filename)
    function = next(node for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == function_name)
    module_table = symtable.symtable(source, filename, "exec")
    function_table = next(table for table in module_table.get_children()
                          if table.get_name() == function_name
                          and table.get_lineno() == function.lineno)
    table_index = {(table.get_name(), table.get_lineno()): (table, ancestors)
                   for table, ancestors in _tables(function_table)}
    module_bindings = {symbol.get_name() for symbol in module_table.get_symbols()
                       if symbol.is_assigned() or symbol.is_imported()
                       or symbol.is_namespace()}
    builtin_names = set(vars(builtins)) - module_bindings
    function_locals = {symbol.get_name() for symbol in function_table.get_symbols()
                       if symbol.is_local()}
    closures = []
    state_users = defaultdict(set)
    state_groups = defaultdict(set)
    state_rebindings = defaultdict(set)
    state_mutations = defaultdict(set)
    for node in sorted(ast.walk(function), key=lambda n: getattr(n, "lineno", 0)):
        if node is function or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        table, ancestors = table_index[(node.name, node.lineno)]
        free = sorted(table.get_frees())
        owners = {}
        for name in free:
            for ancestor in reversed(ancestors):
                if name in ancestor.get_identifiers() and ancestor.lookup(name).is_local():
                    owners[name] = f"{ancestor.get_name()}:{ancestor.get_lineno()}"
                    break
            else:
                owners[name] = "unresolved"
        globals_used = sorted(symbol.get_name() for symbol in table.get_symbols()
                              if symbol.is_global() and symbol.is_referenced())
        rebound = sorted(symbol.get_name() for symbol in table.get_symbols()
                         if symbol.is_free() and symbol.is_assigned())
        mutations = set()
        for statement in _owned_nodes(node):
            targets = []
            if isinstance(statement, (ast.Assign, ast.Delete)):
                targets = statement.targets
            elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
                targets = [statement.target]
            for target in targets:
                for part in ast.walk(target):
                    if isinstance(part, (ast.Attribute, ast.Subscript)) and isinstance(part.ctx, (ast.Store, ast.Del)):
                        name = _target_root(part)
                        if name in free:
                            mutations.add(name)
        parent_state = sorted(name for name in free
                              if owners[name] == f"{function_name}:{function.lineno}")
        group = RESPONSIBILITIES.get(node.name, "unclassified")
        identity = f"{node.name}:{node.lineno}"
        for name in parent_state:
            state_users[name].add(identity)
            state_groups[name].add(group)
            if name in rebound:
                state_rebindings[name].add(identity)
            if name in mutations:
                state_mutations[name].add(identity)
        closures.append({
            "name": node.name, "line": node.lineno, "end_line": node.end_lineno,
            "physical_lines": node.end_lineno - node.lineno + 1,
            "responsibility": group,
            "enclosing_function": ancestors[-1].get_name(),
            "free_variables": free, "free_variable_owners": owners,
            "turn_captures": parent_state,
            "parameters": sorted(table.get_parameters()),
            "locals_excluding_parameters": sorted(set(table.get_locals()) - set(table.get_parameters())),
            "global_references": sorted(set(globals_used) - builtin_names),
            "builtin_references": sorted(set(globals_used) & builtin_names),
            "nonlocal_rebindings": rebound,
            "captured_attribute_or_item_writes": sorted(mutations),
        })
    bindings = [{"name": name, "closures": sorted(state_users[name]),
                 "responsibilities": sorted(state_groups[name]),
                 "nonlocal_rebindings": sorted(state_rebindings[name]),
                 "attribute_or_item_writes": sorted(state_mutations[name])}
                for name in sorted(state_users)]
    denominator = len(bindings)
    shared_closures = sum(len(item["closures"]) > 1 for item in bindings)
    shared_groups = sum(len(item["responsibilities"]) > 1 for item in bindings)
    return {
        "source": filename, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "function": function_name, "line": function.lineno, "end_line": function.end_lineno,
        "physical_lines": function.end_lineno - function.lineno + 1,
        "parameters": sorted(function_table.get_parameters()),
        "turn_local_bindings": len(function_locals),
        "immediate_body_functions": sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                        for node in function.body),
        "direct_nested_functions": sum(item["enclosing_function"] == function_name for item in closures),
        "all_nested_functions": len(closures),
        "captured_turn_bindings": denominator,
        "bindings_shared_by_closures": shared_closures,
        "bindings_shared_by_responsibilities": shared_groups,
        "shared_closure_ratio": shared_closures / denominator if denominator else 0,
        "shared_responsibility_ratio": shared_groups / denominator if denominator else 0,
        "unclassified_functions": [item["name"] for item in closures if item["responsibility"] == "unclassified"],
        "closures": closures, "bindings": bindings,
    }


def markdown(report: dict) -> str:
    lines = [
        "# Agent Turn 状态归属测量（2026-10-24）", "",
        "本文件由 `scripts/analyze_agent_turn_state.py` 生成；只读取源码，不导入或运行 Agent，不是新增 gate。", "",
        f"源码：`{report['source']}`；SHA-256：`{report['source_sha256']}`。",
        (f"`{report['function']}` 第 {report['line']}–{report['end_line']} 行，"
        f"共 {report['physical_lines']} 物理行、{len(report['parameters'])} 参数；"
        f"函数体直接语句中的函数定义 {report['immediate_body_functions']} 个；"
        f"同一词法层（含控制块内定义）{report['direct_nested_functions']} 个；所有层级共 {report['all_nested_functions']} 个。"), "",
        "## 口径与限制", "",
        ("AST 给出函数边界；Python `symtable` 给出词法作用域，区分函数参数、局部绑定、闭包自由变量、"
        "模块全局和未被模块绑定遮蔽的 builtin。闭包自由变量包含 Python 为更深层闭包转交的捕获；"
        "只把实际解析到 `run_agent_turn` 绑定的名字计入 turn 状态，深层函数自己父闭包的局部值单列在 JSON。"),
        ("这里的“状态”包含回调、对象引用和内部函数绑定，不能仅由变量名判断可变性。"
        "记录 `nonlocal` 重新绑定和捕获对象属性/下标写入；通过方法调用、别名、反射及外部函数发生的修改未作静态推断。"
        "未捕获的主循环局部量、主循环调用顺序和模块全局依赖不进入共享比例；因此这不是全函数耦合率。"),
        ("职责组是脚本中可审阅的人工假设：lifecycle / tools / budget / telemetry / context / completion。"
        "后两组保留上下文投影和完成审查的实际职责，不为凑四个模块而并入其他组。分类变化会改变跨组比例。"), "",
        "## 共享比例与 D1 决策", "",
        (f"- 分母：{report['captured_turn_bindings']} 个被任一命名闭包捕获的唯一 turn 绑定"
        f"（turn 全部局部绑定 {report['turn_local_bindings']} 个）。"),
        (f"- 被至少两个闭包捕获：{report['bindings_shared_by_closures']} / {report['captured_turn_bindings']}"
        f" = {report['shared_closure_ratio']:.2%}。"),
        (f"- 跨至少两个职责组：{report['bindings_shared_by_responsibilities']} / {report['captured_turn_bindings']}"
        f" = {report['shared_responsibility_ratio']:.2%}。"),
        ("- 试行阈值的明确解释：跨职责组共享比例 **>60%** 时，不按这套分组直接搬移闭包；"
        "先画状态所有权及调用顺序。阈值是本次治理的启发式，不是业界标准或质量评分。"),
        ("- 测量结果：**触发 >60% 阈值，直接抽取 no-go**。"
         if report['shared_responsibility_ratio'] > .6 else
         "- 测量结果：**未触发 >60% 阈值**；这只允许进入边界设计，不构成直接重构的安全证明。"),
        ("- 当前决策：D1 测量完成，允许进入边界设计；抽取验收尚未进行。先选择单一职责明确状态所有者、"
        "输入/输出和副作用顺序，逐一验证 cancellation、budget、delivery 与 completion 的契约；本次不改生产循环。"), "",
        "## 闭包矩阵", "",
        "| 函数 / 行 | 职责 | 所有自由变量 | turn 捕获 | 全局 / builtin | nonlocal 重绑 |", 
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for item in report["closures"]:
        lines.append(f"| `{item['name']}` / {item['line']} | {item['responsibility']} | "
                     f"{len(item['free_variables'])} | {len(item['turn_captures'])} | "
                     f"{len(item['global_references'])} / {len(item['builtin_references'])} | "
                     f"{', '.join(item['nonlocal_rebindings']) or '—'} |")
    lines.extend(["", "## Turn 绑定 × 职责矩阵", "",
                  "| 绑定 | 职责组 | 捕获该绑定的闭包 | 重绑 / 属性或下标写入 |",
                  "| --- | --- | --- | --- |"])
    for item in report["bindings"]:
        writes = sorted(set(item["nonlocal_rebindings"] + item["attribute_or_item_writes"]))
        lines.append(f"| `{item['name']}` | {', '.join(item['responsibilities'])} | "
                     f"{', '.join(item['closures'])} | {', '.join(writes) or '—'} |")
    lines.extend(["", "## 复现", "", "```sh",
                  ("python scripts/analyze_agent_turn_state.py "
                  "--markdown docs/agent-turn-state-ownership-2026-10-24.md"), "```", "",
                  "可另用 `--json <输出路径>` 保存完整参数、局部、全局、builtin、自由变量及词法所有者清单。", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("minicode/agent_loop.py"))
    parser.add_argument("--function", default="run_agent_turn")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    report = analyze(args.source.read_text(encoding="utf-8"), filename=args.source.as_posix(),
                     function_name=args.function)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json:
        args.json.write_text(rendered, encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(markdown(report), encoding="utf-8")
    if not args.json and not args.markdown:
        print(rendered, end="")
    else:
        print(f"{report['all_nested_functions']} closures; "
              f"{report['captured_turn_bindings']} captured turn bindings; "
              f"cross-responsibility sharing {report['shared_responsibility_ratio']:.2%}")


if __name__ == "__main__":
    main()
