"""Decide when the parent should delegate a coding turn.

A second agent earns its cost when the change spans more than one source
file, when one file is a multi-step redesign that a separate reviewer can
check, or when the user asked for that split. A read-only question stays
with the parent even if it names several files or mentions editing. A
one-function fix stays with the parent too. Routing that is off, and a
host-owned implementation spec, stay with the parent: there is no separate
child model to hand the work to, or the parent loop already owns the review.

The cues are lexical, in English and Simplified Chinese. They are not a parse
of the task. By default they are a hint in the parent prompt: the parent
chooses whether to delegate. ``delegationWriteGate`` (default off) is what
blocks parent writes until a workflow call returns.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_PATH_ATOM = r"[\w.\-\u4e00-\u9fff]{1,80}"
_CODE_PATH = re.compile(
    r"(?:^|(?<=[\s'\"`（(「『【，,：:、]))"
    r"((?:" + _PATH_ATOM + r"/){0,8}" + _PATH_ATOM +
    r"\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|c|cc|cpp|h|hpp))"
    r"(?![\w\u4e00-\u9fff])"
)
_EN_CHANGE = (
    r"implement|fix|add|change|update|modify|write|refactor|create|edit|"
    r"redesign|rewrite|overhaul|re-architect|rearchitect"
)
_ZH_CHANGE = r"实现|修复|修改|改一下|更新|重构|重写|新增|创建"
_CHANGE = re.compile(
    rf"(?:\b(?:{_EN_CHANGE})\b|{_ZH_CHANGE})",
    re.IGNORECASE,
)
_REDESIGN = re.compile(
    r"(?:\b(?:redesign|rewrite|overhaul|re-architect|rearchitect)\b|重写|重构|重设计)",
    re.IGNORECASE,
)
# A second behavior, not the redesign verb alone. "Rewrite hold()" stays solo.
_REDESIGN_SCOPE = re.compile(
    r"(?:\b(?:then|invariants?|edge cases?|state machine|multiple steps|several steps)\b"
    r"|然后|不变量|边界情况|状态机|多步)",
    re.IGNORECASE,
)
_HOW_TO_CHANGE = re.compile(
    r"\bhow (?:do|can|should|would) (?:i|we)\s+"
    r"(?:(?!implement\b|fix\b|add\b|change\b|update\b|modify\b|write\b|"
    r"refactor\b|create\b|edit\b|redesign\b|rewrite\b|overhaul\b)\w+\s+){0,6}"
    r"(implement|fix|add|change|update|modify|write|refactor|create|edit|"
    r"redesign|rewrite|overhaul)\b",
    re.IGNORECASE,
)
_LEADING_CHANGE = re.compile(
    rf"^\s*(?:please\s+)?(?:(?:{_EN_CHANGE})\b|(?:请)?(?:{_ZH_CHANGE}))",
    re.IGNORECASE,
)
_READONLY_CUE = re.compile(
    r"("
    r"\b(?:explain|describe|compare|summarize)\b"
    r"|\bwhat (?:do|does|is|are)\b"
    r"|\bhow does\b"
    r"|\bwhy does\b"
    r"|\btell me\b"
    r"|don't\s+(?:edit|change|modify|write|touch|rewrite)"
    r"|do not\s+(?:edit|change|modify|write|touch|rewrite)"
    r"|without\s+(?:editing|changing|modifying)"
    r"|read-?only"
    r"|just\s+(?:explain|tell|show|read|answer)"
    r"|no changes"
    r"|不要改|不要修改|别改|只解释|仅解释|只读|怎么改"
    r")",
    re.IGNORECASE,
)
_BROAD_SURVEY = re.compile(
    r"(?:"
    r"\b(?:codebase|repository|across the (?:repo|project|codebase)|"
    r"all modules|whole project)\b"
    r"|代码库|整个仓库|整个项目|所有模块|全库"
    r")",
    re.IGNORECASE,
)
_EXPLICIT_COLLABORATION = re.compile(
    r"\b(sub-?agents?|separate reviewer|agent_type=.workflow)\b",
    re.IGNORECASE,
)
_TRIVIAL = re.compile(
    r"(?:"
    r"\b(?:typo|one function|single function|single file|one file|docstring)\b"
    r"|只改一个文件|单文件|一个函数|错别字|文档字符串"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DelegationDecision:
    mode: str
    write_paths: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ParentDelegationGate:
    """Optional hold on parent writes until a workflow call returns.

    ``handoff`` stays set when the hint is workflow, even if the write gate
    is off, so path autofill and the failed-workflow solo note still run.
    ``pending`` is the write block, and it is set only when the gate is on.
    """

    pending: bool
    message: str
    write_paths: tuple[str, ...] = ()
    handoff: bool = False


_PARENT_WRITE_TOOLS = frozenset({
    "edit_file",
    "write_file",
    "patch_file",
    "modify_file",
    "executor_test",
})
_SHELLS = frozenset({"bash", "sh", "node", "powershell", "pwsh", "cmd"})
_PACKAGE_MUTATORS = frozenset({
    "pip", "pip3", "npm", "make", "cmake", "cargo", "go", "dotnet",
})
_READONLY_GIT = frozenset({"status", "diff", "log", "show", "rev-parse"})


def source_paths(task_text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_CODE_PATH.findall(task_text or "")))


def _opening_lines(text: str) -> list[str]:
    """First paragraph, at most three lines. Later constraints are not the ask."""

    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if lines:
                break
            continue
        lines.append(line)
        if len(lines) == 3:
            break
    if not lines and (text or "").strip():
        lines.append((text or "").strip())
    return lines


def _opening_clause(text: str) -> str:
    lines = _opening_lines(text)
    if not lines:
        return ""
    split = re.split(r"(?<=[.!?。？])\s+", lines[0], maxsplit=1)
    return split[0].strip()


def _imperative_opening(text: str) -> bool:
    return any(_LEADING_CHANGE.search(line) for line in _opening_lines(text))


def _is_readonly(text: str) -> bool:
    """A question or an explanation, including one that mentions editing.

    Cues apply to the opening, not a later sentence. An imperative change in
    the first paragraph stays a change even if a constraint says "what is".
    """

    if _HOW_TO_CHANGE.search(text) or _LEADING_CHANGE.search(text) or _imperative_opening(text):
        return False
    opening = _opening_clause(text)
    if opening.rstrip().endswith(("?", "？", "吗")):
        return True
    return _READONLY_CUE.search(opening) is not None


def decide_delegation(
    task_text: str,
    *,
    routing_enabled: bool,
    implementation_owned: bool,
) -> DelegationDecision:
    """Return solo, workflow, or explore for one user request."""

    text = task_text or ""
    paths = source_paths(text)
    if implementation_owned or not routing_enabled:
        return DelegationDecision("solo", paths, "parent owns this turn")
    if _TRIVIAL.search(text):
        return DelegationDecision("solo", paths, "trivial edit")
    if _is_readonly(text):
        if _BROAD_SURVEY.search(text):
            return DelegationDecision("explore", paths, "broad read-only survey")
        return DelegationDecision("solo", paths, "read-only question")
    if _EXPLICIT_COLLABORATION.search(text) and _CHANGE.search(text):
        return DelegationDecision("workflow", paths, "explicit collaboration")
    if paths and _REDESIGN.search(text) and _REDESIGN_SCOPE.search(text):
        return DelegationDecision("workflow", paths, "single-file redesign")
    if len(paths) >= 2 and _CHANGE.search(text):
        return DelegationDecision("workflow", paths, "multiple source files")
    if _BROAD_SURVEY.search(text) and not _CHANGE.search(text):
        return DelegationDecision("explore", paths, "broad read-only survey")
    if _CHANGE.search(text) or len(paths) == 1:
        return DelegationDecision("solo", paths, "local change")
    return DelegationDecision("solo", paths, "local request")


def _workflow_choice(write_gate: bool) -> str:
    if write_gate:
        return (
            "Delegate it before editing. Writes stay blocked until that "
            "workflow call returns."
        )
    return "You choose whether to delegate."


def delegation_prompt_note(
    task_text: str,
    *,
    routing_enabled: bool,
    implementation_owned: bool,
    write_gate: bool = False,
) -> str:
    """A per-turn hint. The write gate, not this line, blocks a tool."""

    if implementation_owned:
        return (
            "This turn is a host-owned implementation. Implement and verify it "
            "yourself. Do not call task."
        )
    decision = decide_delegation(
        task_text,
        routing_enabled=routing_enabled,
        implementation_owned=False,
    )
    if decision.mode == "workflow" and decision.reason == "single-file redesign":
        shown = ", ".join(decision.write_paths) or "the file named in the request"
        return (
            "Hint: this looks like a single-file redesign "
            f"({shown}). task(agent_type='workflow', write_paths=[...], "
            "verification_commands=[...]) lets a separate reviewer check it. "
            + _workflow_choice(write_gate)
        )
    if decision.mode == "workflow":
        shown = ", ".join(decision.write_paths) or "the files named in the request"
        return (
            "Hint: this change spans several modules "
            f"({shown}). task(agent_type='workflow', write_paths=[...], "
            "verification_commands=[...]) is a good fit. "
            + _workflow_choice(write_gate)
        )
    if decision.reason == "read-only question":
        return (
            "Hint: this is a read-only question. Answer it directly from the "
            "named files."
        )
    if decision.mode == "explore":
        return (
            "Hint: this is a broad survey. task(agent_type='explore') fits."
        )
    if decision.reason == "local change":
        return (
            "Hint: this looks like a single-function or local edit. "
            "Implement and verify it yourself."
        )
    return ""


def delegation_coach_message(decision: DelegationDecision) -> str:
    path_arg = json.dumps(list(decision.write_paths)) if decision.write_paths else "[...]"
    if decision.reason == "single-file redesign":
        shown = ", ".join(decision.write_paths) or "the file in the request"
        lead = f"This single-file redesign benefits from a separate reviewer ({shown}). "
    elif len(decision.write_paths) >= 2:
        lead = (
            "This change covers multiple source files ("
            + ", ".join(decision.write_paths)
            + "). "
        )
    elif decision.write_paths:
        lead = (
            "This change asks for a separate implementer ("
            + ", ".join(decision.write_paths)
            + "). "
        )
    else:
        lead = "This change asks for a separate implementer. "
    return (
        lead
        + "With sub-agent routing enabled, delegate it before editing:\n"
        'task(agent_type="workflow", '
        'prompt="<objective, observed failure, constraints, acceptance>", '
        f"write_paths={path_arg}, "
        'verification_commands=[["<the command that checks this change>"]])\n'
        "Do not also start a plan or explore agent for this change. "
        "edit_file, write_file, and shell writes stay blocked until this "
        "workflow call returns. If it fails, finish the edit yourself "
        "instead of calling workflow again."
    )


def workflow_failure_solo_message(write_paths: tuple[str, ...]) -> str:
    """One note after a failed handoff. The parent edits; the loop does not retry."""

    shown = ", ".join(write_paths) or "the files named in the request"
    return (
        "Workflow failed, so this handoff is closed. "
        f"Finish the change yourself on {shown}: edit the files and run "
        "verification. Do not call task(workflow) again for this turn."
    )


def _path_key(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def _union_paths(existing: object, required: tuple[str, ...]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            key = _path_key(cleaned)
            if cleaned and key not in seen:
                paths.append(cleaned)
                seen.add(key)
    for path in required:
        key = _path_key(path)
        if key and key not in seen:
            paths.append(path)
            seen.add(key)
    return paths


def complete_workflow_write_paths(tool_input: dict, required: tuple[str, ...]) -> None:
    """Authorize every source file the user named, without dropping the model's paths.

    An explicit workflow_request is updated in place. Convenience write_paths
    are filled when that request is absent. The two shapes stay mutually exclusive.
    """

    if tool_input.get("agent_type") != "workflow" or not required:
        return
    request = tool_input.get("workflow_request")
    if isinstance(request, dict):
        scope = request.get("scope")
        if not isinstance(scope, dict):
            scope = {}
            request["scope"] = scope
        scope["writePaths"] = _union_paths(scope.get("writePaths"), required)
        return
    tool_input["write_paths"] = _union_paths(tool_input.get("write_paths"), required)


def handoff_blocks_tool(tool_name: str, tool_input: object) -> bool:
    """True when this call could write source before the workflow handoff."""

    if tool_name in _PARENT_WRITE_TOOLS:
        return True
    if tool_name != "run_command" or not isinstance(tool_input, dict):
        return False
    command = str(tool_input.get("command") or "").strip()
    raw_args = tool_input.get("args")
    args = raw_args if isinstance(raw_args, list) else []
    blob = " ".join([command, *[str(arg) for arg in args]])
    if ">" in blob:
        return True
    if command in _SHELLS or command in _PACKAGE_MUTATORS:
        return True
    if command in {"python", "python3"}:
        return not (
            len(args) >= 2
            and str(args[0]) == "-m"
            and str(args[1]) in {"pytest", "unittest"}
        )
    if command == "git":
        subcommand = str(args[0]) if args else ""
        return subcommand not in _READONLY_GIT
    if command == "sed" and any(
        str(arg) == "-i" or str(arg).startswith("-i") for arg in args
    ):
        return True
    return False


def parent_delegation_gate(
    task_text: str,
    *,
    routing_enabled: bool,
    implementation_owned: bool,
    agent_depth: int,
    write_gate: bool = False,
) -> ParentDelegationGate:
    """Record a workflow hint. Block writes only when the gate is on.

    Nested workers and host-owned specs are neither hinted nor blocked.
    """

    if agent_depth != 0 or implementation_owned:
        return ParentDelegationGate(False, "")
    decision = decide_delegation(
        task_text,
        routing_enabled=routing_enabled,
        implementation_owned=False,
    )
    if decision.mode != "workflow":
        return ParentDelegationGate(False, "")
    if not write_gate:
        return ParentDelegationGate(
            False, "", decision.write_paths, handoff=True,
        )
    return ParentDelegationGate(
        True,
        delegation_coach_message(decision),
        decision.write_paths,
        handoff=True,
    )


def task_text_from_messages(messages: list) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            text = "\n".join(parts).strip()
            if text:
                return text
    return ""
