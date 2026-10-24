"""Deterministic evidence extraction for post-task reflection.

This module treats execution traces as untrusted, bounded data.  It never
executes trace content and exposes one extraction interface used by both the
reflection engine and its evaluator.
"""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal
from urllib.parse import urlparse

from minicode.memory_deposition_experiment import deposition_feature_enabled

EpistemicStatus = Literal["confirmed", "inferred", "unknown"]

TRACE_SCHEMA_VERSION = 2
TRACE_MAX_EVENTS = 500
EVIDENCE_MAX_TEXT_CHARS = 600
EVIDENCE_MAX_LIST_ITEMS = 64
EVIDENCE_MAX_DEPTH = 5

PATH_KEYS = {
    "path",
    "file_path",
    "filepath",
    "paths",
    "files",
    "files_read",
    "files_changed",
    "changed_files",
    "referenced_files",
}

ToolFileRole = Literal["files_read", "files_changed", "referenced_files"]
ReflectionValueClass = Literal["read", "search", "list", "format", "command", "network"]


@dataclass(frozen=True, slots=True)
class ToolSemantic:
    """Stable trace semantics, independent of runtime concurrency capability.

    ``ToolCapability.READ_ONLY`` answers whether calls may run concurrently. It
    cannot say whether a path was read, changed, merely referenced, or whether a
    successful call is routine reflection noise. Those durable trace semantics
    live here so evidence extraction and value classification cannot drift.
    """

    canonical_name: str
    file_role: ToolFileRole | None = None
    reflection_value_class: ReflectionValueClass | None = None
    accepts_directory_path: bool = False
    legacy_alias: bool = False


def _semantic(
    name: str,
    *,
    file_role: ToolFileRole | None = None,
    value_class: ReflectionValueClass | None = None,
    accepts_directory_path: bool = False,
    legacy_alias: bool = False,
) -> ToolSemantic:
    return ToolSemantic(
        canonical_name=name,
        file_role=file_role,
        reflection_value_class=value_class,
        accepts_directory_path=accepts_directory_path,
        legacy_alias=legacy_alias,
    )


TOOL_SEMANTICS: dict[str, ToolSemantic] = {
    # Current core tools. Keep these names aligned with
    # ``create_default_tool_registry`` rather than with provider wording.
    "read_file": _semantic("read_file", file_role="files_read", value_class="read"),
    "file_line_count": _semantic(
        "file_line_count", file_role="files_read", value_class="read"
    ),
    "grep_files": _semantic("grep_files", file_role="files_read", value_class="search"),
    "find_symbols": _semantic(
        "find_symbols", file_role="files_read", value_class="search"
    ),
    "find_references": _semantic(
        "find_references", file_role="files_read", value_class="search"
    ),
    "get_ast_info": _semantic("get_ast_info", file_role="files_read", value_class="read"),
    "code_review": _semantic("code_review", file_role="files_read", value_class="read"),
    "diff_viewer": _semantic("diff_viewer", file_role="files_read", value_class="read"),
    "list_files": _semantic(
        "list_files",
        file_role="files_read",
        value_class="list",
        accepts_directory_path=True,
    ),
    "file_tree": _semantic(
        "file_tree",
        file_role="files_read",
        value_class="list",
        accepts_directory_path=True,
    ),
    "write_file": _semantic("write_file", file_role="files_changed"),
    "edit_file": _semantic("edit_file", file_role="files_changed"),
    "patch_file": _semantic("patch_file", file_role="files_changed"),
    "batch_copy": _semantic("batch_copy", file_role="files_changed"),
    "batch_move": _semantic("batch_move", file_role="files_changed"),
    "batch_delete": _semantic("batch_delete", file_role="files_changed"),
    "run_command": _semantic(
        "run_command", file_role="referenced_files", value_class="command"
    ),
    "test_runner": _semantic(
        "test_runner", file_role="referenced_files", value_class="command"
    ),
    "git": _semantic("git", value_class="command"),
    "web_search": _semantic("web_search", value_class="network"),
    "web_fetch": _semantic("web_fetch", value_class="network"),
    "http_request": _semantic("http_request", value_class="network"),
    # Historical trace aliases. They remain readable but are deliberately
    # marked so tests cannot mistake them for the current registry surface.
    "search_files": _semantic(
        "grep_files",
        file_role="files_read",
        value_class="search",
        legacy_alias=True,
    ),
    "list_directory": _semantic(
        "list_files",
        file_role="files_read",
        value_class="list",
        accepts_directory_path=True,
        legacy_alias=True,
    ),
    "directory_tree": _semantic(
        "file_tree",
        file_role="files_read",
        value_class="list",
        accepts_directory_path=True,
        legacy_alias=True,
    ),
    "format_file": _semantic(
        "format_file", file_role="files_read", value_class="format", legacy_alias=True
    ),
    "formatter": _semantic("format_file", value_class="format", legacy_alias=True),
    "modify_file": _semantic(
        "write_file", file_role="files_changed", legacy_alias=True
    ),
    "create_file": _semantic(
        "write_file", file_role="files_changed", legacy_alias=True
    ),
    "delete_file": _semantic(
        "batch_delete", file_role="files_changed", legacy_alias=True
    ),
    "move_file": _semantic("batch_move", file_role="files_changed", legacy_alias=True),
    "execute_command": _semantic(
        "run_command",
        file_role="referenced_files",
        value_class="command",
        legacy_alias=True,
    ),
    **{
        name: _semantic(
            "test_runner",
            file_role="referenced_files",
            value_class="command",
            legacy_alias=True,
        )
        for name in ("compile", "build", "pytest", "unittest", "ruff", "pyright", "mypy")
    },
}


def tool_semantic(tool_name: object) -> ToolSemantic | None:
    """Return bounded semantics for a current tool name or historical alias."""
    return TOOL_SEMANTICS.get(_normalize_name(tool_name))


_CHANGE_TOOLS = frozenset(
    name for name, semantic in TOOL_SEMANTICS.items() if semantic.file_role == "files_changed"
)
_COMMAND_TOOLS = frozenset(
    name
    for name, semantic in TOOL_SEMANTICS.items()
    if semantic.reflection_value_class == "command"
)
_OPERATIONAL_RESOURCE_NOISE = {
    "backend",
    "bin",
    "build",
    "dist",
    "frontend",
    "lib",
    "node_modules",
    "python",
    "python3",
    "pytest",
    "ruff",
    "src",
    "test",
    "tests",
    "workspace",
}
_OPERATIONAL_ENGINES = {
    "cargo",
    "eslint",
    "go",
    "jest",
    "mypy",
    "npm",
    "pnpm",
    "pyright",
    "pytest",
    "ruff",
    "tsc",
    "unittest",
    "vitest",
    "yarn",
}
_GENERIC_RECOVERY_MAX_CALL_GAP = 8
_GENERIC_INPUT_TOKEN_NOISE = {
    "args",
    "command",
    "compatible",
    "content",
    "false",
    "file_path",
    "limit",
    "max_results",
    "mode",
    "offset",
    "path",
    "query",
    "resource",
    "strict",
    "timeout",
    "true",
}
_SENSITIVE_INPUT_KEY_RE = re.compile(
    r"(?i)(?:api[_-]?key|authorization|credential|password|token|secret)"
)
_MANIFEST_NAMES = {
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "cargo.toml",
    "go.mod",
}
_DECLARED_MANIFEST_NAMES = {
    "requirements.txt", "requirements-dev.txt", "package.json", "pyproject.toml", "pipfile",
}
_POLICY_BASENAMES = {
    "policy.md",
    "project-policy.md",
    "project_policy.md",
}
_NORMATIVE_POLICY_RE = re.compile(
    r"(?i)\b(?:must|shall|required|requires|always|never|do not|don't|"
    r"cannot|may not)\b|(?:必须|需要|不得|禁止|始终|一律)"
)
_POLICY_SCOPE_RE = re.compile(
    r"(?i)^(?:(?:this|the)\s+(?:rule|policy|requirement)|it)\s+"
    r"(?:applies\b|does\s+not\s+apply\b)|^(?:applies\b|does\s+not\s+apply\b)"
)
_POLICY_TOOL_HEADER_RE = re.compile(
    r"^(?:FILE|OFFSET|END|TOTAL_CHARS|TRUNCATED):"
)
_POLICY_CONSTRAINT_MAX_CHARS = 480
_IMPORT_ALIASES = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "pil": "pillow",
    "yaml": "pyyaml",
}
_STANDARD_LIBRARY_IMPORTS = {
    "argparse",
    "collections",
    "dataclasses",
    "datetime",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "logging",
    "math",
    "os",
    "pathlib",
    "re",
    "shlex",
    "sys",
    "time",
    "typing",
    "urllib",
}
_MENTION_LIBRARIES = {
    "angular",
    "django",
    "fastapi",
    "flask",
    "gin",
    "jest",
    "next",
    "nuxt",
    "pytest",
    "react",
    "redux",
    "ruff",
    "svelte",
    "tailwind",
    "uvicorn",
    "vitest",
    "vue",
    "zod",
}

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:[a-z][a-z0-9]*[_-])*(?:api[_-]?key|authorization|credential|password|token|secret(?:[_-]?key)?))\b"
    r"(\s*[:=]\s*)((?!\[redacted)[^\s,;]+)"
)
# Values that cannot carry a secret, so redacting them only corrupts prose.
# A real run stored a correct root-cause explanation as "sets `_token=[REDACTED]
# BEFORE the increment", because any assignment to a name ending in "token"
# was rewritten. The exemptions stay deliberately narrow:
#   * a short number -- a credential worth hiding is not four digits long;
#   * a language literal;
#   * an attribute chain with no digits in it. Credentials in this position
#     are JWTs or base64, which are longer and effectively always contain
#     digits, so requiring digit-free short segments keeps them redacted
#     while letting "self._store_token" through.
# Anything else is still treated as a secret.
_NON_SECRET_VALUE_RE = re.compile(
    r"^(?:"
    r"[0-9]{1,4}(?:\.[0-9]{1,4})?"
    r"|(?:None|True|False|null|nil|undefined|NULL|NaN)"
    r"|(?:str|int|float|bool|bytes|dict|list|set|tuple|Any)"
    r"|[A-Za-z_][A-Za-z_]{0,15}(?:\.[A-Za-z_][A-Za-z_]{0,15}){1,3}"
    r")[)\]}\"'`,.;:]*$"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+(?!\[redacted)[a-z0-9._~+/-]+")
_OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[a-zA-Z0-9_-]{8,}\b")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_ERROR_TYPE_RE = re.compile(r"(?:^|[\[\s])([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))[]:]?")
_TRANSIENT_ENVIRONMENTAL_ERROR_TYPE_RE = re.compile(
    r"(?i)(?:^|[._])(?:"
    r"RateLimit|TooManyRequests|"
    r"(?:Connect|Read|Write|Pool)?Timeout|"
    r"Network|Connect|Connection|ConnectionReset|ConnectionAborted|"
    r"ConnectionClosed|BrokenPipe|RemoteProtocol|Proxy|Gai|"
    r"SSL(?:CertVerification)?|TLS|Certificate|"
    r"InternalServer|BadGateway|ServiceUnavailable|GatewayTimeout|"
    r"LockTimeout|LockContention|Deadlock"
    r")(?:Error|Exception)?$"
)
_TRANSIENT_ENVIRONMENTAL_MESSAGE_RE = re.compile(
    r"(?i)(?:\bHTTP\s*429\b|\b429\s+Too\s+Many\s+Requests\b|"
    r"\b(?:rate[ _-]?limit(?:ed|ing|error|exception)?|"
    r"too[ _-]?many[ _-]?requests(?:error|exception)?)\b|"
    r"\b(?:connect|read|write|pool)?timeout(?:error|exception)?\b|"
    r"\b(?:timed?\s+out|deadline exceeded)\b|"
    r"\bnetwork\s+(?:error|failure|unavailable|interruption|interrupted)\b|"
    r"\bconnection\s+(?:reset|aborted|closed|dropped|interrupted|refused)\b|"
    r"\b(?:broken pipe|temporary failure in name resolution)\b|"
    r"\b(?:dns|name)[ -]?resolution\s+(?:failed|failure|error)\b|"
    r"\b(?:getaddrinfo failed|name or service not known|"
    r"nodename nor servname provided)\b|"
    r"\b(?:cannot|could not|failed to) connect to (?:the )?proxy\b|"
    r"\b(?:proxy connection|tunnel connection)\s+"
    r"(?:failed|failure|error|refused)\b|"
    r"\b(?:ssl|tls)(?: certificate)? handshake\s+(?:failed|failure|error)\b|"
    r"\bcertificate verify failed\b|"
    r"\bHTTP\s*5\d{2}\b|"
    r"\b(?:status(?: code)?|response(?: status)?|server returned)"
    r"\s*[:=]?\s*5\d{2}\b|"
    r"\b(?:internal server error|bad gateway|service unavailable|"
    r"gateway timeout)\b|"
    r"\b(?:database(?: table| schema)? is locked|lock contention|"
    r"lock wait timeout|resource busy)\b|"
    r"\b(?:could not|failed to) acquire(?: the| a)? lock\b|"
    r"\bdeadlock (?:detected|found)\b)"
)
_DETERMINISTIC_INPUT_ERROR_TYPE_RE = re.compile(
    r"(?i)\b(?:Validation|Argument|Schema|Usage|Syntax|Parse|FileNotFound|"
    r"NotADirectory|IsADirectory|Permission|CommandNotFound|CalledProcess)"
    r"(?:Error|Exception)\b"
)
_DETERMINISTIC_INPUT_SUBJECT_RE = re.compile(
    r"(?i)\b(?:argument|option|flag|parameter|input|schema|path|file|"
    r"directory|command|old_string|new_string)\b"
)
_DETERMINISTIC_INPUT_MESSAGE_RE = re.compile(
    r"(?i)\b(?:invalid|unsupported|unrecognized|unknown|missing)\s+"
    r"(?:argument|option|flag|parameter|input|schema|path|file|"
    r"directory|command)\b"
)


def append_trace_event(
    trace: list[dict[str, Any]],
    event: dict[str, Any],
    *,
    max_events: int = TRACE_MAX_EVENTS,
) -> bool:
    """Append one Trace Contract v2 event, returning whether it was accepted."""
    if len(trace) >= max_events:
        return False
    existing_ids = {
        str(existing.get("event_id"))
        for existing in trace
        if existing.get("event_id")
    }
    normalized = dict(event)
    supplied_id = str(normalized.get("event_id", "")).strip()
    if supplied_id:
        if supplied_id in existing_ids:
            raise ValueError(f"duplicate trace event_id: {supplied_id}")
        event_id = supplied_id
    else:
        sequence = len(trace) + 1
        event_id = f"event-{sequence:06d}"
        while event_id in existing_ids:
            sequence += 1
            event_id = f"event-{sequence:06d}"
    normalized["trace_schema_version"] = TRACE_SCHEMA_VERSION
    normalized["event_id"] = event_id
    trace.append(normalized)
    return True


# Real tool output is coloured. pytest, ruff and cargo all emit SGR sequences,
# and without this they reach durable memory verbatim -- a claim then reads
# "When run_command fails with \x1b[31mF\x1b[0m ... [100%]". Stripping here
# rather than at each call site also keeps terminal control characters out of
# anything that later renders a memory.
#
# The intermediate-byte run is capped at two. ECMA-48 puts "." in that class,
# and upstream bounding cuts a sequence in half before this ever runs: a
# trailing "\x1b[33" followed by the appended "...[truncated]" let an
# unbounded run swallow "\x1b[33...[" in one bite, so a real memory recorded
# "When run_command fails with lease.transfer(truncated]".
# ESC is deliberately excluded from the bare-control-character class. Left in,
# it won the race against the sequence branches whenever the final byte had
# been cut away: the lone ESC was eaten as a control character and its
# parameters stayed behind as ordinary text, so a memory recorded
# "When run_command fails with lease.transfer([3".
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]{0,32}[ -/]{0,2}[@-~]|\x1b[@-Z\\-_]|[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]"
)
# Whatever is left of a sequence whose final byte was cut away, ESC included.
# Intermediates are omitted on purpose: a cut sequence is indistinguishable
# from the text after it, and consuming two "intermediates" ate two dots of
# the appended "...[truncated]" marker, leaving ".[truncated]" behind.
_PARTIAL_ANSI_RE = re.compile(r"\x1b\[?[0-9;?]{0,32}")


_DECISION_MAX_CHARS = 240
# Chinese sentences carry no space after their terminator, so requiring
# whitespace left a whole paragraph as one "sentence".
# The digit lookbehind keeps an enumeration marker attached to its item.
# Splitting "This means either: 1. The failure already got fixed" after the
# "1." separated the option from the "either" that made it speculative, and
# the option was then stored as a decision.
_SENTENCE_SPLIT_RE = re.compile(r"(?<![0-9]\.)(?<=[.!?])\s+|(?<=[。！？])|\n+")
# Weighing options is not deciding. A model listing "this means either: 1. ...
# 2. ..." is still thinking, and the trigger word that matched often sits
# inside one of the options rather than in a conclusion.
_SPECULATIVE_RE = re.compile(
    r"(?i)\b(?:either|maybe|perhaps|probably|might|could be|possibly|"
    r"not sure|unclear|assuming|suppose)\b|(?:可能|也许|或许|大概|不确定)"
)
# Markdown the model writes for the reader, meaningless once quoted.
_MARKDOWN_NOISE_RE = re.compile(r"\*\*|\*|`+|^#{1,6}\s*|^\s*[-*+]\s+|^\s*\d+\.\s+", re.M)
_DECISION_TRIGGER_RE = re.compile(
    r"(?i)\b(?:choose|chose|decide|decided|select|selected|caused|fixes|root cause is)\b"
    r"|(?:选择|决定|导致|根因是)"
)


def _decision_sentence(text: str) -> str:
    """Reduce an assistant turn to the one sentence that states the decision.

    Taking the whole turn stored a page of thinking-aloud as a "decision":
    "Crucial finding: **"collected 154 items"** ... So this means either:
    1. The user's reported failure already got fixed ...". The trigger word is
    what made this a decision at all, so the sentence carrying it is the claim
    and the rest is working-out.

    Returns "" when every sentence carrying a trigger is speculative, so the
    caller records no decision rather than one the model never committed to.
    """
    cleaned = _MARKDOWN_NOISE_RE.sub("", text)
    sentences = [item.strip() for item in _SENTENCE_SPLIT_RE.split(cleaned) if item.strip()]
    matched = False
    for sentence in sentences:
        if not _DECISION_TRIGGER_RE.search(sentence):
            continue
        matched = True
        if not _SPECULATIVE_RE.search(sentence):
            return " ".join(sentence.split())[:_DECISION_MAX_CHARS]
    if matched:
        return ""
    condensed = " ".join(cleaned.split())
    return condensed[:_DECISION_MAX_CHARS]


_ELISION_MARKER = "\n...[truncated middle]...\n"
_CONSTRAINT_MAX_CHARS = 200
# Types a tool layer stamps on someone else's failure.
_GENERIC_WRAPPER_TYPES = {"toolerror", "unknownerror", "exception", "error"}


def bound_keeping_both_ends(text: str, limit: int) -> str:
    """Bound text by eliding its middle, not its tail.

    Tools print their summary last: pytest ends with
    "FAILED tests/test_renew.py::test_renew - StaleTokenError", cargo and npm
    the same. Cutting from the end keeps the progress bar and throws away the
    line that names what failed, so a memory built from the result could not
    name the failing test.
    """
    if len(text) <= limit:
        return text
    budget = max(0, limit - len(_ELISION_MARKER))
    head = budget // 2
    return text[:head] + _ELISION_MARKER + text[-(budget - head):]


def _bound_error_message(value: Any) -> str:
    """Bound a failing tool's whole output while keeping both of its ends.

    A failure's "message" is everything the tool printed. Head-first
    truncation discards exactly the part that carries the lesson, because
    pytest, cargo and npm all print their summary last: a real 832-character
    pytest failure kept the progress bar and the banner and lost
    "FAILED tests/test_renew.py::test_renew_after_transfer - StaleTokenError",
    so the memory built from it could no longer name the failing test.

    Redaction still runs over the full text first, so nothing secret is
    admitted by widening the window.
    """
    text = sanitize_evidence_text(value, EVIDENCE_MAX_TEXT_CHARS * 8)
    return bound_keeping_both_ends(text, EVIDENCE_MAX_TEXT_CHARS)


def _is_transient_environmental_failure(error: ErrorEvidence) -> bool:
    """Return whether a failure can recover independently of changed input.

    Changed-input correlation is not causal proof for provider throttling and
    other environmental failures.  Those failures need an explicit recovery
    event or stronger evidence than a later successful invocation.
    """
    text = f"{error.error_type or ''} {error.message}"
    if (
        _DETERMINISTIC_INPUT_ERROR_TYPE_RE.search(text)
        and _DETERMINISTIC_INPUT_SUBJECT_RE.search(error.message)
    ):
        return False
    if _DETERMINISTIC_INPUT_MESSAGE_RE.search(error.message):
        return False
    error_type = (error.error_type or "").strip()
    if _TRANSIENT_ENVIRONMENTAL_ERROR_TYPE_RE.search(error_type):
        return True
    return bool(_TRANSIENT_ENVIRONMENTAL_MESSAGE_RE.search(error.message))


def _redact_secret_assignment(match: re.Match[str]) -> str:
    """Redact an assignment unless its value provably cannot be a secret."""
    value = match.group(3)
    if _NON_SECRET_VALUE_RE.match(value):
        return match.group(0)
    return f"{match.group(1)}{match.group(2)}[REDACTED]"


def sanitize_evidence_text(value: Any, limit: int = EVIDENCE_MAX_TEXT_CHARS) -> str:
    """Safely stringify, redact, and bound untrusted trace text."""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        text = "[unprintable]"
    text = _PARTIAL_ANSI_RE.sub("", _ANSI_RE.sub("", text))
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(_redact_secret_assignment, text)
    text = _OPENAI_STYLE_KEY_RE.sub("[REDACTED_API_KEY]", text)
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


@dataclass(frozen=True)
class FileEvidence:
    path: str
    role: Literal["read", "changed", "referenced"]
    event_ids: tuple[str, ...]
    call_id: str | None = None
    epistemic_status: EpistemicStatus = "confirmed"


@dataclass(frozen=True)
class ToolEvidence:
    tool_name: str
    call_id: str | None
    call_event_id: str
    result_event_ids: tuple[str, ...]
    status: Literal["success", "failed", "unknown"]
    # Bounded, redacted rendering of the structured input (command, args,
    # cwd, timeout). Without it the downstream learner sees that a call
    # failed and later succeeded but not what actually changed between the
    # two attempts.
    invocation: str = ""


@dataclass(frozen=True)
class LibraryEvidence:
    name: str
    status: Literal["confirmed", "declared", "weak_mention"]
    event_ids: tuple[str, ...]
    import_name: str | None = None
    epistemic_status: EpistemicStatus = "confirmed"


@dataclass(frozen=True)
class ErrorEvidence:
    error_id: str
    call_id: str | None
    tool_name: str | None
    error_type: str | None
    message: str
    source_event_ids: tuple[str, ...]
    epistemic_status: EpistemicStatus = "confirmed"


@dataclass(frozen=True)
class RecoveryEvidence:
    recovery_id: str
    related_error_ids: tuple[str, ...]
    action: str
    event_ids: tuple[str, ...]
    files_changed: tuple[str, ...]
    epistemic_status: EpistemicStatus
    # Bounded old->new excerpt of the edit that performed the repair, e.g.
    # "src/a.py: 'from tenacity import retry' -> 'from tenacity import
    # retry, stop_after_attempt'". A recovery without it says WHAT was
    # touched but not what to actually do next time.
    change_summary: str = ""
    # Calls whose successful tool_result directly verifies this recovery.
    # This supports non-command tools such as read_file/edit_file/web_search,
    # whose success is real evidence but is not a test/lint verification.
    verification_call_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoverySuggestionEvidence:
    suggestion_id: str
    related_error_ids: tuple[str, ...]
    suggestion: str
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class DecisionEvidence:
    decision_id: str
    statement: str
    rationale: str | None
    event_ids: tuple[str, ...]
    epistemic_status: EpistemicStatus
    source_kind: Literal[
        "assistant_decision",
        "user_constraint",
        "user_correction",
        "config_constraint",
        "old_memory_disproved",
        "inferred_rationale",
    ] = "assistant_decision"


@dataclass(frozen=True)
class VerificationEvidence:
    verification_id: str
    tool_name: str | None
    call_id: str | None
    command_kind: str | None
    scope: Literal["targeted", "full", "unknown"]
    result: Literal["passed", "failed", "unknown"]
    event_ids: tuple[str, ...]
    summary: str = ""


@dataclass
class TaskEvidence:
    files_read: list[FileEvidence] = field(default_factory=list)
    files_changed: list[FileEvidence] = field(default_factory=list)
    referenced_files: list[FileEvidence] = field(default_factory=list)
    tool_calls: list[ToolEvidence] = field(default_factory=list)
    libraries: list[LibraryEvidence] = field(default_factory=list)
    errors: list[ErrorEvidence] = field(default_factory=list)
    recoveries: list[RecoveryEvidence] = field(default_factory=list)
    recovery_suggestions: list[RecoverySuggestionEvidence] = field(default_factory=list)
    decisions: list[DecisionEvidence] = field(default_factory=list)
    verification: list[VerificationEvidence] = field(default_factory=list)
    outcome: Literal["success", "failed", "unknown"] = "unknown"
    had_errors: bool = False
    errors_recovered: bool = False
    # The agent's own final causal summary (last assistant turn, bounded).
    # It carries the "why" that structured events cannot express, and feeds
    # approach claims for successfully verified work.
    final_summary: str = ""
    final_summary_event_ids: tuple[str, ...] = ()
    diagnostics: list[str] = field(default_factory=list)
    event_positions: dict[str, int] = field(default_factory=dict)

    def to_dict(self, max_items: int = EVIDENCE_MAX_LIST_ITEMS) -> dict[str, Any]:
        """Return deterministic JSON-compatible evidence metadata."""
        def bound(value: Any, depth: int = 0) -> Any:
            if depth > EVIDENCE_MAX_DEPTH:
                return "[truncated]"
            if isinstance(value, dict):
                return {
                    str(key): bound(nested, depth + 1)
                    for key, nested in list(value.items())[:max_items]
                }
            if isinstance(value, list):
                return [bound(nested, depth + 1) for nested in value[:max_items]]
            if isinstance(value, tuple):
                return tuple(bound(nested, depth + 1) for nested in value[:max_items])
            return value

        return bound(asdict(self))


@dataclass(frozen=True)
class _Event:
    index: int
    event_id: str
    event_type: str
    call_id: str | None
    tool_name: str | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class _CallAttempt:
    """One tool_call/tool_result pair reduced to what it acted on and how it went."""

    call_id: str
    tool_name: str
    target: str
    status: str
    index: int
    event_ids: tuple[str, ...]
    input_event_id: str | None = None
    invocation: str = ""
    objective_kind: str | None = None
    resource_keys: tuple[str, ...] = ()
    engine_keys: tuple[str, ...] = ()
    # Whole workspace-relative paths from the invocation.  ``resource_keys``
    # are similarity heuristics; these are identity, and only identity may
    # pair two calls that both carry directory context.
    path_resources: tuple[str, ...] = ()
    # Normalized working directory of the structured call, when provided.
    # A relative path only identifies a file once anchored against it.
    cwd: str = ""
    # Selection flags with values (``-k=expired``, ``::node``).  A passing
    # rerun that selects different work never verifies the failed run.
    selection: tuple[str, ...] = ()
    # Parsed call-objective contract for this invocation.  The extractor,
    # validator, and episode slicer all reason through this one object.
    objective: CallObjective | None = None


def _safe_get(mapping: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return mapping.get(key, default)
    except Exception:
        return default


def _safe_sequence_length(value: Any) -> int:
    try:
        return len(value)
    except Exception:
        return 0


def _normalize_name(value: Any) -> str:
    return "_".join(sanitize_evidence_text(value, 120).strip().lower().split())


def _normalize_message(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))[:EVIDENCE_MAX_LIST_ITEMS]


def _policy_constraint_statements(text: str) -> list[str]:
    """Rebuild bounded Markdown policy paragraphs before finding constraints.

    ``read_file`` preserves physical line wrapping. Treating each line as an
    independent fact can approve a syntactically valid but incomplete lesson.
    Join only lines in the same Markdown paragraph/list item, then attach an
    immediately following paragraph when it explicitly scopes that rule.
    """
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if not current:
            return
        block = " ".join(current).strip()
        current.clear()
        if block:
            blocks.append(block)

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if (
            not stripped
            or _POLICY_TOOL_HEADER_RE.match(stripped)
            or stripped.startswith(("#", "```"))
        ):
            flush()
            continue
        list_item = re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)(.+)$", stripped)
        if list_item:
            flush()
            current.append(list_item.group(1).strip())
            continue
        current.append(stripped.removeprefix("> "))
    flush()

    statements: list[str] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        index += 1
        if not _NORMATIVE_POLICY_RE.search(block):
            continue
        statement = block
        if index < len(blocks) and _POLICY_SCOPE_RE.search(blocks[index]):
            scoped = f"{statement} {blocks[index]}"
            if len(scoped) <= _POLICY_CONSTRAINT_MAX_CHARS:
                statement = scoped
                index += 1
        if len(statement) <= _POLICY_CONSTRAINT_MAX_CHARS:
            statements.append(statement)
    return list(dict.fromkeys(statements))[:EVIDENCE_MAX_LIST_ITEMS]


def _normalize_path(path: str) -> str:
    cleaned = path.strip().strip("'\"")
    if re.match(r"^[A-Za-z]:\\", cleaned):
        return cleaned
    return cleaned.replace("\\", "/")


# Trailing structured modifiers appended by ``_call_invocation``: the cwd
# is a resolution anchor, not a business resource, and must not leak into
# resource fingerprints as a spurious shared path.
_INVOCATION_MODIFIERS_RE = re.compile(
    r"\s*\(\s*(?:cwd:[^;)]+|timeout:\s*\d+)\s*"
    r"(?:;\s*(?:cwd:[^;)]+|timeout:\s*\d+)\s*)*\)$"
)


def _invocation_command_source(invocation: str) -> str:
    """Strip the rendered `` (cwd: ...; timeout: N)`` tail from an invocation."""
    return _INVOCATION_MODIFIERS_RE.sub("", invocation).strip()


# Flags that narrow what a command selects (``-k expired``, ``--deselect
# node``, pytest markers).  ``-q``/``-v`` style presentation flags are
# absent: they change output, not the set of work being verified.  ``-m`` is
# contextual (interpreter module vs pytest marker) and is classified by
# ``_classify_flag_m``; unclassifiable contexts set ``ambiguous_selection``
# and the pairing stays a candidate instead of being confirmed.
_SELECTION_FLAG_RE = re.compile(
    r"-{1,2}(?:k|keyword|marker|deselect|ignore|spec|m)"
)
# Flags whose value is an output artifact.  A shared report file is not a
# shared business target and must never pair two different tests.
_OUTPUT_FLAG_NAMES = {
    "junitxml",
    "resultlog",
    "result-log",
    "html",
    "cov-report",
    "report",
    "output",
    "out",
    "o",
    "logfile",
    "log-file",
}
_SHELL_WRAPPERS = {"bash", "sh", "zsh", "dash", "ksh"}
_INTERPRETER_RE = re.compile(r"^(?:python\d*(?:\.\d+)?|py)$")
_KNOWN_MODULE_RUNNERS = {"pytest", "unittest", "py.test"}
# Executable spellings that mean the same runner; keeps the objective's
# executable comparable across the agent's tool wrappers and vendored bins.
_EXECUTABLE_ALIASES = {"test_runner": "pytest", "py.test": "pytest"}
_NODE_SELECTOR_RE = re.compile(r"::[^/\s]+")
_FLAG_TOKEN_RE = re.compile(r"^(-{1,2}[A-Za-z][\w-]*)(?:=(.*))?$")
# Flags proven to affect presentation only.  Anything outside this set is
# recorded in ``unknown_flags`` verbatim: unknown arguments whose spelling
# or value differs between two calls keep the pair at candidate level --
# "we could not classify it" must never become "it did not change scope".
# Arity belongs to the executable, not to the spelling of a flag in an
# arbitrary tool. In particular pytest's -q/-v never consume a test path.
_DISPLAY_SWITCHES = {
    "pytest": {"q", "v", "s", "quiet", "verbose", "showlocals", "l", "no-header", "no-summary"},
    "unittest": {"q", "v", "quiet", "verbose"},
    "ruff": {"q", "v", "quiet", "verbose", "silent", "no-cache"},
}
_DISPLAY_VALUES = {
    "pytest": {"tb", "color", "durations", "durations-min", "capture"},
    "ruff": {"output-format"},
}
_OPAQUE_VALUE_FLAGS = {
    "pytest": {"p", "c", "o", "override-ini", "rootdir", "basetemp", "confcutdir", "maxfail"},
}
_TEST_RUNNERS = {"pytest", "unittest", "nose", "nosetests", "vitest", "jest"}
_NON_EXECUTING_FLAGS = {"help", "version", "V", "h", "collect-only", "co"}
_INCOMPLETE_INVOCATION_RE = re.compile(r"\.\.\.\[truncated\]|\.\.\. \d+ more items")


@dataclass(frozen=True)
class CallObjective:
    """What one invocation actually acted on, split by role.

    One contract shared by the rule extractor, the claim validator, and the
    episode slicer: executable (wrapper- and interpreter-normalized), input
    targets, output artifacts, directory anchors (structured cwd and inline
    ``cd``), and the selection scope (flags with values, ``::node``
    selectors).  ``ambiguous_selection`` marks input we could not classify;
    such calls may propose relationships but never confirm them.
    """

    executable: str
    input_paths: tuple[str, ...] = ()
    output_paths: tuple[str, ...] = ()
    anchor_dirs: tuple[str, ...] = ()
    selection: tuple[str, ...] = ()
    bare_operands: tuple[str, ...] = ()
    ambiguous_selection: bool = False
    # Flags we could not classify, kept as normalized ``-flag=value`` entries
    # (sorted).  Identical spellings on both sides of a pairing are treated
    # as harmless; any difference is an unresolved scope change.
    unknown_flags: tuple[str, ...] = ()
    # Help/version/collection can exit zero without exercising any target.
    executes_work: bool = True


def _expand_command_tokens(source: str) -> list[str]:
    """Decode one command layer, preserving each quoted argv element.

    Only a recognized shell's -c/-lc argument may be decoded again by the
    caller. A -k expression or a path containing spaces is already one arg.
    """
    return shlex.split(source, posix=True)


def _flag_name(token: str) -> str:
    # Flags are case-sensitive: pytest -V prints its version; -v runs tests.
    return token.lstrip("-").split("=", 1)[0]


def _normalize_input_path(token: str) -> str:
    return _normalize_path(token).strip("'\"`.,;:()[]{}").lower()


def _looks_like_path_token(token: str) -> bool:
    if not token or len(token) > 300:
        return False
    if token.startswith("-"):
        return False
    without_node = token.split("::", 1)[0]
    return _looks_like_local_file(without_node) or "/" in without_node


def _interpreter_module_head(tokens: list[str], index: int) -> int | None:
    """If ``tokens[index]`` is an interpreter, consume ``-m module`` after it."""
    if index + 2 < len(tokens) + 1 and index + 1 < len(tokens) and tokens[index + 1].split("=", 1)[0] == "-m":
        module = tokens[index + 2] if index + 2 < len(tokens) else ""
        return index + 2 if module else None
    return None


def call_objective(
    invocation: str, *, cwd: str = "", argv: Sequence[str] | None = None
) -> CallObjective:
    """Parse a rendered invocation (or structured-JSON tool input) by role."""
    source = _invocation_command_source(invocation)
    # Both the evidence renderer and the runtime trace emitter use explicit
    # truncation markers. Lost argv elements cannot be treated as operands.
    if _INCOMPLETE_INVOCATION_RE.search(source):
        return CallObjective(executable="", ambiguous_selection=True)
    anchors: set[str] = set()
    if cwd:
        normalized_cwd = _normalize_path(cwd).strip().lower().rstrip("/")
        if normalized_cwd:
            anchors.add(normalized_cwd)

    head = source.split(None, 1)
    if len(head) == 2 and head[1].startswith("{"):
        try:
            payload = json.loads(head[1])
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            inputs: list[str] = []
            for key in ("path", "file_path", "file", "paths", "files"):
                value = _safe_get(payload, key)
                if isinstance(value, str):
                    inputs.append(_normalize_input_path(value))
                elif isinstance(value, list):
                    inputs.extend(
                        _normalize_input_path(item)
                        for item in value[:EVIDENCE_MAX_LIST_ITEMS]
                        if isinstance(item, str)
                    )
            return CallObjective(
                executable=_EXECUTABLE_ALIASES.get(
                    _normalize_name(head[0]), _normalize_name(head[0])
                ),
                input_paths=tuple(
                    item for item in _ordered_unique(inputs) if item
                ),
            )

    try:
        tokens = list(argv) if argv is not None else _expand_command_tokens(source)
    except ValueError:
        return CallObjective(executable="", ambiguous_selection=True)
    inputs: list[str] = []
    outputs: list[str] = []
    selection: list[str] = []
    bare: list[str] = []
    unknown: list[str] = []
    ambiguous = False
    executable = ""
    executes_work = True
    shell_depth = 0

    index = 0
    # Consume wrapper prefixes: ``bash -lc '...'`` keeps its inner command;
    # ``cd <dir> &&`` contributes an anchor, not a resource; ``VAR=x``
    # assignments prefix the real executable without being it.
    while index < len(tokens):
        token = tokens[index]
        if token in _SHELL_WRAPPERS and index + 1 < len(tokens) and _flag_name(tokens[index + 1]) in {"lc", "c"}:
            shell_depth += 1
            if shell_depth > 3 or index + 3 != len(tokens):
                return CallObjective(executable="", ambiguous_selection=True)
            script = tokens[index + 2]
            if "$" in script or "`" in script:
                return CallObjective(executable="", ambiguous_selection=True)
            try:
                tokens[index:] = _expand_command_tokens(script)
            except ValueError:
                return CallObjective(executable="", ambiguous_selection=True)
            continue
        if token == "cd" and index + 1 < len(tokens):
            directory = _normalize_input_path(tokens[index + 1])
            if directory:
                anchors.add(directory.rstrip("/"))
            index += 2
            if index < len(tokens) and tokens[index] in {"&&", ";"}:
                index += 1
            continue
        if _ENV_ASSIGNMENT_RE.match(token):
            index += 1
            continue
        break

    if index < len(tokens):
        candidate_exe = tokens[index]
        exe_basename = _normalize_name(candidate_exe.split("/")[-1])
        if _INTERPRETER_RE.match(exe_basename) and (
            (module_end := _interpreter_module_head(tokens, index)) is not None
        ):
            module = tokens[module_end] if module_end < len(tokens) else ""
            module_name = _normalize_name(module.split("/")[-1].replace(".py", ""))
            executable = (
                module_name if module_name in _KNOWN_MODULE_RUNNERS else f"{exe_basename} -m {module_name}"
            )
            index = module_end + 1
        else:
            executable = _EXECUTABLE_ALIASES.get(exe_basename, exe_basename)
            index += 1
    runner_context = executable in _TEST_RUNNERS
    positional_only = False

    while index < len(tokens):
        token = tokens[index]
        if token in {"&&", "||", ";", "|", ">", ">>", "2>", "2>>", "<"}:
            if token in {">", ">>", "2>", "2>>"} and index + 1 < len(tokens):
                outputs.append(_normalize_input_path(tokens[index + 1]))
                index += 1
            index += 1
            # A command separator may start a new ``cd`` anchor.
            if index + 1 < len(tokens) and tokens[index] == "cd":
                directory = _normalize_input_path(tokens[index + 1])
                if directory:
                    anchors.add(directory.rstrip("/"))
                index += 2
                if index < len(tokens) and tokens[index] in {"&&", ";"}:
                    index += 1
            continue
        if token == "--":
            positional_only = True
            index += 1
            continue
        if not positional_only and token.startswith("-") and len(token) > 1:
            flag = _flag_name(token)
            value = token.split("=", 1)[1] if "=" in token else None
            display_switch = flag in _DISPLAY_SWITCHES.get(executable, set())
            if executable == "pytest" and re.fullmatch(r"-[qv]+", token):
                display_switch = True
            display_value = flag in _DISPLAY_VALUES.get(executable, set())
            selection_value = runner_context and bool(_SELECTION_FLAG_RE.fullmatch(f"-{flag}"))
            output_value = runner_context and flag in _OUTPUT_FLAG_NAMES and flag != "o"
            opaque_value = flag in _OPAQUE_VALUE_FLAGS.get(executable, set())
            takes_value = display_value or selection_value or output_value or opaque_value
            if value is None and takes_value and index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                value = tokens[index + 1]
                index += 1
            if takes_value and value is None:
                ambiguous = True
            if flag in _NON_EXECUTING_FLAGS:
                executes_work = False
                unknown.append(f"-{flag}={value}")
                index += 1
                continue
            if flag == "m":
                if runner_context:
                    selection.append(f"-m={value}")
                else:
                    # ``python -m module`` is consumed at the head; a stray
                    # ``-m`` here has unclear meaning.
                    ambiguous = True
                index += 1
                continue
            if selection_value:
                selection.append(f"-{flag}={value}")
                index += 1
                continue
            if output_value:
                if value is not None and _looks_like_path_token(str(value)):
                    outputs.append(_normalize_input_path(str(value)))
                index += 1
                continue
            if not display_switch and not display_value:
                # Unresolved argument: kept verbatim so a later comparison can
                # tell "same unknown argument on both sides" (harmless) from
                # "the retry added, dropped, or changed one" (a possible
                # scope change we cannot classify -- candidate, not confirm).
                unknown.append(f"-{flag}={value}")
                if not takes_value and value is None:
                    # Unknown arity: preserve following operands as opaque
                    # evidence as well as parsing paths below. Never guess
                    # that the next token is a disposable option value.
                    for operand in tokens[index + 1:]:
                        if operand.startswith("-"):
                            break
                        unknown.append(f"operand:{operand}")
            index += 1
            continue
        if _looks_like_path_token(token):
            node = ""
            path_part = token
            if "::" in token:
                path_part, node = token.split("::", 1)
                node = node.lower()
            normalized = _normalize_input_path(path_part)
            if normalized:
                inputs.append(normalized)
            if node:
                selection.append(f"::{node}")
            index += 1
            continue
        if len(token) > 2 and not token.isdigit() and not _ENV_ASSIGNMENT_RE.match(token):
            bare.append(token.lower())
        index += 1

    return CallObjective(
        executable=executable,
        input_paths=tuple(item for item in _ordered_unique(inputs) if item),
        output_paths=tuple(item for item in _ordered_unique(outputs) if item),
        anchor_dirs=tuple(sorted(anchors)),
        selection=tuple(selection),
        bare_operands=tuple(sorted(set(bare))),
        ambiguous_selection=ambiguous,
        unknown_flags=tuple(unknown),
        executes_work=executes_work,
    )


def _anchored_objective_paths(objective: CallObjective) -> set[str]:
    """Resolve input targets against the anchors the call itself provides."""
    anchored: set[str] = set()
    for path in objective.input_paths:
        if path.startswith("/"):
            anchored.add(path)
        elif objective.anchor_dirs:
            anchored.update(
                f"{anchor}/{path}" for anchor in objective.anchor_dirs
            )
        else:
            anchored.add(f"rel:{path}")
    return anchored


ObjectiveRelation = Literal["match", "conflict", "insufficient"]


def objective_scope_conflict(
    failed: CallObjective, success: CallObjective
) -> bool:
    """An explicit contradiction no fallback or exception may override.

    Different selection scopes, unclassifiable selections, differing unknown
    arguments, or different executables say the two calls did different
    work.  Heuristics may only add evidence where the contract has none,
    never overrule this.
    """
    return (
        not failed.executes_work
        or not success.executes_work
        or failed.ambiguous_selection
        or success.ambiguous_selection
        or failed.selection != success.selection
        or failed.unknown_flags != success.unknown_flags
        or failed.executable != success.executable
    )


def objective_relation(
    failed: CallObjective, success: CallObjective
) -> ObjectiveRelation:
    """Classify the relationship between two invocations' work.

    ``match``: the success run verified the failed run's work.
    ``conflict``: the two provably did different work (scope, executable,
    or uncovered input targets) -- nothing downstream may confirm a pair in
    this state.
    ``insufficient``: neither side names file targets, so the contract has
    no identity to compare; consumers may apply bounded fallbacks here.
    """
    if objective_scope_conflict(failed, success):
        return "conflict"
    failed_inputs = _anchored_objective_paths(failed)
    success_inputs = _anchored_objective_paths(success)
    if failed_inputs and success_inputs:
        return "match" if failed_inputs <= success_inputs else "conflict"
    if not failed_inputs and not success_inputs:
        if set(failed.bare_operands) & set(success.bare_operands):
            return "match"
        return "insufficient"
    # One side names targets and the other cannot cover them.
    return "conflict"


def objectives_same_target(
    failed: CallObjective, success: CallObjective
) -> bool:
    """Whether the success invocation verified the failed invocation's work."""
    return objective_relation(failed, success) == "match"


def _objective_pair_matches(failed: "_CallAttempt", success: "_CallAttempt") -> bool:
    """Pair a failed call with the later success that verified its work.

    Beyond the plain objective contract, two bounded relaxations exist and
    are reported by :func:`_objective_pair_relation`:

    - a rerun whose only objective difference is the working-directory
      anchor (the same command from a different cwd) is a precondition
      repair, not a conflicting target;
    - a rerun that only ADDED flags the failed run did not carry
      (``--fix``-style self-repair) is a corrective variant. Removing or
      swapping flags keeps the conflict — that is a different task.
    """
    return _objective_pair_relation(failed, success) != ""


def _objective_pair_relation(
    failed: "_CallAttempt", success: "_CallAttempt"
) -> str:
    """Classify how a failed/success pair may relate: match/cwd/flags/""."""
    failed_objective = failed.objective or call_objective(
        failed.invocation, cwd=failed.cwd
    )
    success_objective = success.objective or call_objective(
        success.invocation, cwd=success.cwd
    )
    if objective_relation(failed_objective, success_objective) == "match":
        return "match"
    if failed.cwd and success.cwd and failed.cwd != success.cwd:
        # Same relative target from a different directory is a precondition
        # repair only when the launcher is the same entrypoint: ``python -m
        # pytest`` and a bare ``pytest`` are different environments even
        # though both normalize to executable ``pytest``.
        failed_head = failed.invocation.split()[:1]
        success_head = success.invocation.split()[:1]
        if failed_head and success_head and failed_head == success_head:
            reanchored = replace(
                success_objective, anchor_dirs=failed_objective.anchor_dirs
            )
            if objective_relation(failed_objective, reanchored) == "match":
                return "cwd"
    added_flags = set(success_objective.unknown_flags) - set(
        failed_objective.unknown_flags
    )
    if added_flags and not set(failed_objective.unknown_flags) - set(
        success_objective.unknown_flags
    ):
        stripped = replace(
            success_objective,
            unknown_flags=tuple(
                flag
                for flag in success_objective.unknown_flags
                if flag not in added_flags
            ),
        )
        if objective_relation(failed_objective, stripped) == "match":
            return "flags"
    return ""


def _proven_path_exception(
    failed: CallObjective,
    success: CallObjective,
    errors: list[ErrorEvidence],
    *,
    tool_name: str,
) -> bool:
    """The relocation/default-scope exceptions to target identity.

    They only ever relax the *path relationship* and only behind a
    missing-target failure whose missing object is the requested target
    itself.  They never relax a selection, executable, ambiguity, or
    unknown-argument difference -- those are conflicts by
    ``objective_scope_conflict`` and stay conflicts.
    """
    if objective_scope_conflict(failed, success):
        return False
    missing_targets = _missing_requested_targets(errors, failed.input_paths)
    if not missing_targets:
        return False
    # Only list_files defines an empty path as listing the workspace root.
    # A command with a bare directory (``pytest billing``), or an arbitrary
    # tool with no recognized file argument, proves no default-scope retry.
    if (
        tool_name == "list_files"
        and failed.executable == success.executable == "list_files"
        and not success.input_paths
        and not success.bare_operands
        and set(failed.input_paths) <= missing_targets
    ):
        return True

    successful_paths = _anchored_objective_paths(success)
    relocated_missing_target = False
    for path in failed.input_paths:
        original_paths = _anchored_objective_paths(
            replace(failed, input_paths=(path,))
        )
        if original_paths <= successful_paths:
            continue
        # The exception may relocate only the object the error named as
        # missing. Every other input must still be verified at its original
        # anchored identity; any dropped input rejects the whole pairing.
        if path not in missing_targets or not _relocated_path_matches(
            {path}, set(success.input_paths)
        ):
            return False
        relocated_missing_target = True
    return relocated_missing_target


_INVALID_ARGUMENT_RE = re.compile(
    r"(?i)(?:unrecognized (?:arguments?|options?)\s*:?\s*(.+)"
    r"|unknown (?:option|argument)\s*[:'\"]?\s*['\"]?(-{1,2}[\w-]+))"
)
_ARGUMENT_ERROR_EXECUTION_RE = re.compile(
    r"(?im)(?:\b\d+\s+(?:failed|passed|skipped|errors?)\b|"
    r"\bcollected\s+\d+\s+items?\b|Captured\s+(?:stderr|stdout)|"
    r"Traceback \(most recent call last\)|^\s*(?:FAILED|ERROR)\s+\S+::|"
    r"^\s*={2,}\s*FAILURES|\bin test_[\w]+)"
)


def _argument_parser_matches_objective(
    program: str, objective: CallObjective
) -> bool:
    """Recognize the outer program named by an argument parser diagnostic."""
    normalized = " ".join(program.strip().lower().split())
    executable = objective.executable
    if normalized.rsplit("/", 1)[-1] == executable:
        return True
    module = re.fullmatch(r"(?:\S*/)?python\d*(?:\.\d+)?\s+-m\s+(\S+)", normalized)
    return bool(module and module.group(1) == executable)


def _outer_argument_error_diagnostic(
    message: str, objective: CallObjective
) -> str | None:
    """Return only a diagnostic attributable to pre-execution outer parsing.

    A test may run another parser and capture its usage error. That does not
    invalidate the same-named flag accepted by the test runner. Require a
    standalone typed usage error, or an initial usage envelope and error
    line naming this invocation's program; execution evidence disqualifies
    the message even when a matching diagnostic also occurs in it.
    """
    if _ARGUMENT_ERROR_EXECUTION_RE.search(message):
        return None
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) == 1:
        typed = re.fullmatch(r"(?:UsageError|ArgumentError):\s*(.+)", lines[0])
        if typed:
            return typed.group(1)
    usage = re.match(r"(?i)^(?:ERROR:\s*)?usage:\s*(.+)", lines[0])
    if not usage:
        return None
    for line in lines[1:]:
        diagnostic = re.fullmatch(r"(?i)(.+?):\s*error:\s*(.+)", line)
        if diagnostic is None:
            continue
        program = diagnostic.group(1)
        if not _argument_parser_matches_objective(program, objective):
            continue
        # Both the initial usage banner and the error must name the outer
        # program. A nested parser following a different usage banner is
        # not licensed by a coincidentally matching token elsewhere.
        if not usage.group(1).lower().startswith(program.lower() + " "):
            continue
        return diagnostic.group(2)
    return None


def _invalid_argument_flags_named_by(
    errors: list[ErrorEvidence],
    objective: CallObjective,
) -> set[str]:
    """Flags a usage error itself names as invalid, in ``-flag=value`` form.

    ``UsageError: unrecognized arguments: --bad-option`` proves the flag was
    the failure's cause; dropping it in the retry is a correction, not an
    unclassifiable scope change.
    """
    named: set[str] = set()
    if any(_ARGUMENT_ERROR_EXECUTION_RE.search(error.message) for error in errors):
        return named
    for error in errors:
        diagnostic = _outer_argument_error_diagnostic(error.message, objective)
        if diagnostic is None:
            continue
        for match in _INVALID_ARGUMENT_RE.finditer(diagnostic):
            for token in match.group(0).split():
                if token.startswith("-") and len(token) > 1:
                    flag = _flag_name(token)
                    value = token.split("=", 1)[1] if "=" in token else None
                    named.add(f"-{flag}={value}")
    return named


def _without_error_named_arguments(
    objective: CallObjective, errors: list[ErrorEvidence]
) -> CallObjective:
    named = _invalid_argument_flags_named_by(errors, objective)
    if not named:
        return objective
    remaining = [
        item for item in objective.unknown_flags if item not in named
    ]
    if len(remaining) == len(objective.unknown_flags):
        return objective
    return replace(objective, unknown_flags=tuple(remaining))


_RENDERED_CWD_RE = re.compile(r"\(\s*cwd:\s*([^;)]+)")


def call_objective_from_rendered(invocation: str) -> CallObjective:
    """CallObjective for a rendered invocation, with its cwd restored.

    The one adapter every consumer of rendered invocations must use: the
    modifier tail is where the working directory lives, and dropping it
    silently changes which calls look like the same target.  Extractor-side
    attempts carry a structured cwd; validators and episode slicers see only
    the rendered string and recover the anchor here.
    """
    rendered_cwd = ""
    match = _RENDERED_CWD_RE.search(invocation)
    if match:
        rendered_cwd = match.group(1).strip()
    return call_objective(invocation, cwd=rendered_cwd)


def _invocation_selection(command_source: str) -> tuple[str, ...]:
    """Selection scope of an invocation, per the shared CallObjective parse."""
    return call_objective(command_source).selection


def _invocation_path_resources(invocation: str) -> tuple[str, ...]:
    """Input-target paths of an invocation, per the shared CallObjective parse.

    Absolute spellings keep their leading ``/`` so they can never collide
    with a relative spelling; the cwd/timeout modifier tail is stripped
    first, and output artifacts (``--junitxml`` and friends) are excluded --
    a shared report path is not a shared target.
    """
    return call_objective(invocation).input_paths


def _workspace_paths_equivalent(
    failed: set[str],
    successful: set[str],
    *,
    failed_cwd: str = "",
    successful_cwd: str = "",
) -> bool:
    """Same file target: anchored spellings must literally coincide.

    Kept for anchor-level comparisons outside a full CallObjective (tests
    and the red-green fallback).  Equality after anchoring covers same-cwd
    relative retries and absolute/relative pairs resolved against a known
    cwd; shared basenames and suffixes never match.
    """
    def anchored(paths: set[str], cwd: str) -> set[str]:
        resolved: set[str] = set()
        base = cwd.rstrip("/") if cwd else ""
        for path in paths:
            if path.startswith("/"):
                resolved.add(path)
            elif base:
                resolved.add(f"{base}/{path}")
            else:
                resolved.add(f"rel:{path}")
        return resolved

    return bool(anchored(failed, failed_cwd) & anchored(successful, successful_cwd))


# A failure whose object verifiably did not exist (FileNotFoundError and
# friends).  Relocating to a deeper path is then a genuine correction, not a
# same-name substitution: an assertion failure on an existing file says the
# check ran and lost, which a different file passing cannot repair.
_MISSING_TARGET_RE = re.compile(
    r"(?i)\b(?:file(?: or directory)? not found|no such file|"
    r"path does not exist|does not exist|cannot find the (?:file|path)|"
    r"找不到(?:该?(?:文件|路径))?|(?:文件|路径)不存在)\b"
)
# The object reported missing: paths quoted after the missing-target
# phrasing, and paths immediately preceding it ("src/auth.py does not
# exist").  The exception only applies when this object is the requested
# target itself, not some fixture or config the target reads.
_MISSING_OBJECT_RE = re.compile(
    r"(?i)(?:no such file or directory|file not found|not found|"
    r"does not exist|cannot find)\s*[:'\"\s]*['\"]?([^'\"\s]+)['\"]?"
)
_MISSING_OBJECT_BEFORE_RE = re.compile(
    r"(?i)['\"]?([^'\"\s]+)['\"]?\s+(?:does not exist|not found|"
    r"cannot be found|不存在)"
)


def _is_missing_target_failure(errors: list[ErrorEvidence]) -> bool:
    return any(
        _MISSING_TARGET_RE.search(error.message) for error in errors
    )


def _missing_object_is_requested_target(
    errors: list[ErrorEvidence], requested_paths: tuple[str, ...]
) -> bool:
    """The missing object named by the error is the requested target itself.

    ``FileNotFoundError: 'fixtures/credentials.json'`` while running
    ``tests/test_auth.py`` proves a fixture was absent, not that the test
    file was: relocation is not licensed.  Only the object bound to the
    missing-target phrasing counts -- a full traceback naturally contains
    the requested path on its own line, which proves nothing about what was
    missing.
    """
    return bool(_missing_requested_targets(errors, requested_paths))


def _missing_requested_targets(
    errors: list[ErrorEvidence], requested_paths: tuple[str, ...]
) -> set[str]:
    """Return the requested objects specifically identified as missing."""
    requested = {_normalize_input_path(item) for item in requested_paths}
    missing_targets: set[str] = set()
    for error in errors:
        if not _MISSING_TARGET_RE.search(error.message):
            continue
        for pattern in (_MISSING_OBJECT_RE, _MISSING_OBJECT_BEFORE_RE):
            for match in pattern.finditer(error.message):
                missing = _normalize_input_path(match.group(1))
                if missing in requested:
                    missing_targets.add(missing)
    return missing_targets


def _relocated_path_matches(
    failed: set[str], successful: set[str]
) -> bool:
    """A deeper spelling of the exact same relative path.

    ``backend/src/auth.py`` relocates ``src/auth.py``; ``tests/test_auth.py``
    does not relocate ``backend/tests/test_auth.py``.  Only meaningful with
    a missing-target failure behind the failed call.
    """
    return any(
        path.endswith("/" + failed_path)
        for path in successful
        for failed_path in failed
        if not failed_path.startswith("/")
    )


def _looks_like_local_file(path: str) -> bool:
    candidate = path.strip().strip("'\"")
    if not candidate or len(candidate) > 300 or "\x00" in candidate or "\n" in candidate:
        return False
    parsed = urlparse(candidate)
    if parsed.scheme.lower() in {"http", "https", "ftp", "data"}:
        return False
    if candidate.startswith("-") or _ENV_ASSIGNMENT_RE.match(candidate):
        return False
    if any(token in candidate for token in (" && ", " || ", " | ", "; ")):
        return False
    without_selector = candidate.split("::", 1)[0]
    basename = re.split(r"[/\\]", without_selector)[-1].lower()
    if basename in _MANIFEST_NAMES or basename in {"dockerfile", "makefile", "license"}:
        return True
    if basename == "pipfile" and deposition_feature_enabled("declared_dependencies"):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", without_selector):
        return "." in basename
    return bool(("/" in without_selector or "." in basename) and re.search(r"\.[A-Za-z0-9]{1,12}$", basename))


def _looks_like_local_directory(path: str) -> bool:
    candidate = path.strip().strip("'\"")
    if not candidate or len(candidate) > 300 or "\x00" in candidate or "\n" in candidate:
        return False
    parsed = urlparse(candidate)
    if parsed.scheme.lower() in {"http", "https", "ftp", "data"}:
        return False
    if candidate.startswith("-") or _ENV_ASSIGNMENT_RE.match(candidate):
        return False
    if any(token in candidate for token in (" && ", " || ", " | ", "; ")):
        return False
    # Directory tools accept bare workspace-relative names such as ``src``.
    # This broader shape is used only for their explicit path/paths fields.
    return candidate in {".", "./"} or bool(
        re.fullmatch(r"[\w @+.,/\\()-]+", candidate, flags=re.UNICODE)
    )


def _path_values(
    value: Any,
    max_items: int = EVIDENCE_MAX_LIST_ITEMS,
    *,
    allow_directory: bool = False,
) -> list[str]:
    found: list[str] = []
    stack: list[tuple[Any, int]] = [(value, 0)]
    seen: set[int] = set()
    while stack and len(found) < max_items:
        current, depth = stack.pop()
        if depth > EVIDENCE_MAX_DEPTH:
            continue
        if isinstance(current, str):
            if _looks_like_local_file(current) or (
                allow_directory and _looks_like_local_directory(current)
            ):
                found.append(_normalize_path(current))
            continue
        if isinstance(current, (Mapping, list, tuple)):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
        if isinstance(current, Mapping):
            try:
                nested_values = list(current.values())[:max_items]
            except Exception:
                continue
            stack.extend((nested, depth + 1) for nested in reversed(nested_values))
        elif isinstance(current, (list, tuple)):
            stack.extend((nested, depth + 1) for nested in reversed(current[:max_items]))
    return list(dict.fromkeys(found))


def _find_key_values(value: Any, keys: set[str]) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = defaultdict(list)
    stack: list[tuple[Any, int]] = [(value, 0)]
    seen: set[int] = set()
    visited = 0
    while stack and visited < EVIDENCE_MAX_LIST_ITEMS * EVIDENCE_MAX_DEPTH * 4:
        current, depth = stack.pop()
        visited += 1
        if depth > EVIDENCE_MAX_DEPTH:
            continue
        if isinstance(current, (Mapping, list, tuple)):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
        if isinstance(current, Mapping):
            try:
                items = list(current.items())[:EVIDENCE_MAX_LIST_ITEMS]
            except Exception:
                continue
            for key, nested in reversed(items):
                normalized_key = _normalize_name(key)
                if normalized_key in keys:
                    found[normalized_key].append(nested)
                if normalized_key not in {"content", "output", "output_summary", "message"}:
                    if isinstance(nested, (Mapping, list, tuple)):
                        stack.append((nested, depth + 1))
        elif isinstance(current, (list, tuple)):
            stack.extend((nested, depth + 1) for nested in reversed(current[:EVIDENCE_MAX_LIST_ITEMS]))
    return found


def _command_paths(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except (TypeError, ValueError):
        return []
    paths: list[str] = []
    skip_next = False
    for token in tokens[:EVIDENCE_MAX_LIST_ITEMS * 2]:
        if skip_next:
            skip_next = False
            continue
        if token in {">", ">>", "<", "2>", "2>>"}:
            skip_next = True
            continue
        if token.startswith("-") or _ENV_ASSIGNMENT_RE.match(token):
            continue
        if urlparse(token).scheme.lower() in {"http", "https", "ftp", "data"}:
            continue
        candidate = token.split("::", 1)[0]
        if _looks_like_local_file(candidate):
            paths.append(_normalize_path(candidate))
    return list(dict.fromkeys(paths))[:EVIDENCE_MAX_LIST_ITEMS]


def extract_tool_file_roles(
    tool_name: str,
    payload: Any,
    *,
    event_type: str = "tool_call",
) -> dict[str, list[str]]:
    """Extract role-specific files from explicit fields and known tool semantics."""
    normalized_tool = _normalize_name(tool_name)
    result = {"files_read": [], "files_changed": [], "referenced_files": []}
    if not isinstance(payload, Mapping):
        return result

    semantic = tool_semantic(normalized_tool)

    explicit = _find_key_values(
        payload,
        {"files_read", "files_changed", "changed_files", "referenced_files"},
    )
    for value in explicit.get("files_read", []):
        result["files_read"].extend(_path_values(value))
    for key in ("files_changed", "changed_files"):
        for value in explicit.get(key, []):
            result["files_changed"].extend(_path_values(value))
    for value in explicit.get("referenced_files", []):
        result["referenced_files"].extend(_path_values(value))

    known_role: str | None = None
    if event_type in {"recovery", "fix", "recovery/fix"}:
        known_role = "files_changed"
    elif event_type == "error":
        known_role = "files_read"
    elif semantic is not None:
        known_role = semantic.file_role

    if known_role:
        values = _find_key_values(payload, PATH_KEYS)
        role_keys = {
            "files_read",
            "files_changed",
            "changed_files",
            "referenced_files",
        }
        for key, nested_values in values.items():
            if key in role_keys:
                continue
            for value in nested_values:
                result[known_role].extend(
                    _path_values(
                        value,
                        allow_directory=bool(
                            semantic is not None
                            and semantic.accepts_directory_path
                            and key in {"path", "paths"}
                        ),
                    )
                )

    if normalized_tool in _COMMAND_TOOLS:
        commands = _find_key_values(payload, {"command"}).get("command", [])
        for command in commands:
            if isinstance(command, str):
                result["referenced_files"].extend(_command_paths(command))
        # Structured ``args`` carry the same operands the legacy command
        # string did (``command="pytest", args=["tests/test_b.py"]``); the
        # same path extraction applies or the target file disappears from
        # the evidence entirely.
        for args_value in _find_key_values(payload, {"args"}).get("args", []):
            if not isinstance(args_value, list):
                continue
            for item in args_value[: EVIDENCE_MAX_LIST_ITEMS * 2]:
                if isinstance(item, str):
                    result["referenced_files"].extend(_command_paths(item))

    return {
        key: sorted(dict.fromkeys(values))[:EVIDENCE_MAX_LIST_ITEMS]
        for key, values in result.items()
    }


class TraceEvidenceExtractor:
    """Convert an untrusted execution trace into bounded deterministic facts."""

    def extract(
        self,
        task_description: str,
        execution_trace: list[dict[str, Any]],
    ) -> TaskEvidence:
        del task_description  # Facts come from trace events, not task wording.
        events, diagnostics = self._normalize_events(execution_trace)
        file_evidence = self._extract_files(events)
        tools = self._extract_tools(events)
        errors = self._extract_errors(events)
        verification = self._extract_verification(events)
        recoveries = self._extract_recoveries(
            events,
            errors,
            file_evidence,
            verification,
        )
        verification.extend(
            self._extract_recovery_verifications(
                events,
                recoveries,
                verification,
            )
        )
        verification = verification[:EVIDENCE_MAX_LIST_ITEMS]
        suggestions = self._extract_recovery_suggestions(events, errors)
        libraries = self._extract_libraries(events, file_evidence)
        decisions = self._extract_decisions(events)
        outcome = self._extract_outcome(events, verification)

        by_role: dict[str, list[FileEvidence]] = defaultdict(list)
        for item in file_evidence:
            by_role[item.role].append(item)
        had_errors = bool(errors)
        errors_recovered = bool(
            errors
            and recoveries
            and outcome == "success"
            and (verification or any(event.event_type == "task_result" for event in events))
        )
        final_summary, final_summary_event_ids = self._extract_final_summary(events)
        return TaskEvidence(
            files_read=by_role["read"],
            files_changed=by_role["changed"],
            referenced_files=by_role["referenced"],
            tool_calls=tools,
            libraries=libraries,
            errors=errors,
            recoveries=recoveries,
            recovery_suggestions=suggestions,
            decisions=decisions,
            verification=verification,
            outcome=outcome,
            had_errors=had_errors,
            errors_recovered=errors_recovered,
            final_summary=final_summary,
            final_summary_event_ids=final_summary_event_ids,
            diagnostics=diagnostics[:EVIDENCE_MAX_LIST_ITEMS],
            event_positions={event.event_id: event.index for event in events},
        )

    @staticmethod
    def _extract_final_summary(
        events: list["_Event"],
    ) -> tuple[str, tuple[str, ...]]:
        """Return the agent's last assistant explanation, bounded.

        The final turn is where the model states what it did and why; earlier
        turns are it thinking out loud. One event only, newest wins.
        """
        for event in reversed(events):
            if event.event_type not in {"assistant", "assistant_step"}:
                continue
            text_value = (
                _safe_get(event.raw, "text")
                or _safe_get(event.raw, "content")
                or _safe_get(event.raw, "summary")
                or ""
            )
            if not isinstance(text_value, str):
                continue
            text = sanitize_evidence_text(text_value, 400)
            if text.strip():
                return text, (event.event_id,)
        return "", ()

    def _normalize_events(
        self, execution_trace: Any
    ) -> tuple[list[_Event], list[str]]:
        events: list[_Event] = []
        diagnostics: list[str] = []
        if not isinstance(execution_trace, list):
            return events, ["execution_trace is not a list"]
        seen_ids: set[str] = set()
        tools_by_call: dict[str, str] = {}
        for index, raw in enumerate(execution_trace[:TRACE_MAX_EVENTS]):
            if not isinstance(raw, Mapping):
                diagnostics.append(f"trace[{index}] is not an object")
                continue
            supplied_id = sanitize_evidence_text(_safe_get(raw, "event_id", ""), 120).strip()
            event_id = supplied_id
            if not event_id or event_id in seen_ids:
                if event_id in seen_ids:
                    diagnostics.append(f"duplicate event_id: {event_id}")
                event_id = f"legacy-event-{index + 1:06d}"
                suffix = 1
                while event_id in seen_ids:
                    event_id = f"legacy-event-{index + 1:06d}-{suffix}"
                    suffix += 1
            seen_ids.add(event_id)
            call_value = _safe_get(raw, "call_id")
            call_id = sanitize_evidence_text(call_value, 120).strip() if call_value is not None else None
            call_id = call_id or None
            tool_value = (
                _safe_get(raw, "tool_name")
                or _safe_get(raw, "name")
                or _safe_get(raw, "toolName")
            )
            tool_name = _normalize_name(tool_value) if tool_value else None
            if call_id and tool_name:
                previous_tool = tools_by_call.setdefault(call_id, tool_name)
                if previous_tool != tool_name:
                    diagnostics.append(
                        f"conflicting tool names for call_id {call_id}: "
                        f"{previous_tool} vs {tool_name}"
                    )
            event_type = _normalize_name(_safe_get(raw, "type", ""))
            events.append(_Event(index, event_id, event_type, call_id, tool_name, raw))
        if _safe_sequence_length(execution_trace) > TRACE_MAX_EVENTS:
            diagnostics.append(f"trace truncated at {TRACE_MAX_EVENTS} events")
        return events, diagnostics

    def _extract_files(self, events: list[_Event]) -> list[FileEvidence]:
        merged: dict[tuple[str, str, str | None], dict[str, Any]] = {}
        role_names = {
            "files_read": "read",
            "files_changed": "changed",
            "referenced_files": "referenced",
        }
        for event in events:
            roles = extract_tool_file_roles(
                event.tool_name or "",
                event.raw,
                event_type=event.event_type,
            )
            for role_key, role in role_names.items():
                for path in roles[role_key]:
                    key = (role, path, event.call_id)
                    record = merged.setdefault(
                        key,
                        {"path": path, "role": role, "event_ids": [], "call_id": event.call_id},
                    )
                    record["event_ids"].append(event.event_id)
        return [
            FileEvidence(
                path=record["path"],
                role=record["role"],
                event_ids=_ordered_unique(record["event_ids"]),
                call_id=record["call_id"],
            )
            for record in merged.values()
        ][: EVIDENCE_MAX_LIST_ITEMS * 3]

    def _extract_tools(self, events: list[_Event]) -> list[ToolEvidence]:
        records: dict[str, dict[str, Any]] = {}
        for event in events:
            if not event.tool_name:
                continue
            key = f"call:{event.call_id}" if event.call_id else f"event:{event.event_id}"
            record = records.setdefault(
                key,
                {
                    "tool_name": event.tool_name,
                    "call_id": event.call_id,
                    "call_event_id": event.event_id,
                    "result_event_ids": [],
                    "statuses": [],
                    "input_event": None,
                },
            )
            if event.event_type == "tool_call":
                record["call_event_id"] = event.event_id
                record["input_event"] = event
            elif event.event_type in {"tool_result", "error", "verification"}:
                record["result_event_ids"].append(event.event_id)
            status = _normalize_name(_safe_get(event.raw, "status", ""))
            if event.event_type == "error" or _safe_get(event.raw, "is_error") or status in {"error", "failed", "failure"}:
                record["statuses"].append("failed")
            elif status in {"success", "completed", "ok", "passed"}:
                record["statuses"].append("success")
        evidence: list[ToolEvidence] = []
        for record in records.values():
            statuses = record["statuses"]
            status: Literal["success", "failed", "unknown"] = "unknown"
            if statuses:
                status = statuses[-1]
            input_event = record["input_event"]
            invocation = (
                self._call_invocation(
                    _normalize_name(record["tool_name"]), [input_event]
                )
                if input_event is not None
                else ""
            )
            evidence.append(
                ToolEvidence(
                    tool_name=record["tool_name"],
                    call_id=record["call_id"],
                    call_event_id=record["call_event_id"],
                    result_event_ids=_ordered_unique(record["result_event_ids"]),
                    status=status,
                    invocation=invocation,
                )
            )
        return evidence[: EVIDENCE_MAX_LIST_ITEMS]

    def _error_fields(self, event: _Event) -> tuple[str, str | None]:
        message_value = (
            _safe_get(event.raw, "message")
            or _safe_get(event.raw, "error")
            or _safe_get(event.raw, "output_summary")
            or _safe_get(event.raw, "content")
            or ""
        )
        message = _bound_error_message(message_value)
        error_type_value = _safe_get(event.raw, "error_type") or _safe_get(event.raw, "type_name")
        error_type = sanitize_evidence_text(error_type_value, 120).strip() if error_type_value else None
        if not error_type:
            match = _ERROR_TYPE_RE.search(message)
            if match:
                error_type = match.group(1)
        return message, error_type

    def _error_fingerprint(self, message: str, error_type: str | None) -> str:
        normalized = _normalize_message(message)
        normalized = re.sub(r"\bline\s+\d+\b", "line <n>", normalized)
        if error_type:
            type_name = _normalize_message(error_type)
            position = normalized.rfind(type_name)
            if position >= 0:
                normalized = normalized[position:]
        return re.sub(r"[^\w./\\\-\u4e00-\u9fff]+", " ", normalized).strip()

    def _extract_errors(self, events: list[_Event]) -> list[ErrorEvidence]:
        records: dict[tuple[str, str, str], dict[str, Any]] = {}
        for event in events:
            status = _normalize_name(_safe_get(event.raw, "status", ""))
            is_failure = event.event_type == "error" or (
                event.event_type == "tool_result"
                and (_safe_get(event.raw, "is_error") or status in {"error", "failed", "failure"})
            )
            if not is_failure:
                continue
            message, error_type = self._error_fields(event)
            if not message.strip():
                continue
            fingerprint = self._error_fingerprint(message, error_type)
            identity = event.call_id or f"event:{event.event_id}"
            key = (identity, event.tool_name or "", fingerprint)
            record = records.setdefault(
                key,
                {
                    "call_id": event.call_id,
                    "tool_name": event.tool_name,
                    "error_type": error_type,
                    "message": message,
                    "sources": [],
                },
            )
            record["sources"].append(event.event_id)
            if error_type and (not record["error_type"] or event.event_type == "error"):
                record["error_type"] = error_type
            if event.event_type == "error" or len(message) > len(record["message"]):
                record["message"] = message
        return [
            ErrorEvidence(
                error_id=f"error-{index:06d}",
                call_id=record["call_id"],
                tool_name=record["tool_name"],
                error_type=record["error_type"],
                message=record["message"],
                source_event_ids=_ordered_unique(record["sources"]),
            )
            for index, record in enumerate(
                self._merge_restated_failures(records), start=1
            )
        ][:EVIDENCE_MAX_LIST_ITEMS]

    @staticmethod
    def _merge_restated_failures(
        records: dict[tuple[str, str, str], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Collapse one failure that the trace stated twice.

        A crashing tool emits both a ``tool_result`` carrying the wrapper type
        and an ``error`` carrying the underlying one. The fingerprint truncates
        the message from its error type, so the same crash keyed twice and
        produced two error_pattern claims -- "run_command / FileNotFoundError"
        and "run_command / ToolError" over one identical message. Truncation
        makes the more specific fingerprint a suffix of the other, which is
        exactly the relation matched here; unrelated failures of the same call
        share no such suffix and stay separate.
        """
        merged: list[dict[str, Any]] = []
        anchors: dict[tuple[str | None, str | None], list[tuple[str, dict[str, Any]]]] = (
            defaultdict(list)
        )
        for (_, _, fingerprint), record in records.items():
            group = anchors[(record["call_id"], record["tool_name"])]
            match = next(
                (
                    existing
                    for existing_print, existing in group
                    if fingerprint
                    and existing_print
                    and (
                        fingerprint.endswith(existing_print)
                        or existing_print.endswith(fingerprint)
                    )
                ),
                None,
            )
            if match is None:
                group.append((fingerprint, record))
                merged.append(record)
                continue
            match["sources"].extend(record["sources"])
            if len(record["message"]) > len(match["message"]):
                match["message"] = record["message"]
            # Prefer the underlying exception over the wrapper that reported it.
            if record["error_type"] and _normalize_message(
                record["error_type"]
            ) not in _GENERIC_WRAPPER_TYPES:
                match["error_type"] = record["error_type"]
        return merged

    def _related_error_ids(
        self,
        event: _Event,
        errors: list[ErrorEvidence],
        event_positions: dict[str, int],
    ) -> tuple[str, ...]:
        requested_calls = _safe_get(event.raw, "related_error_call_ids", [])
        call_ids = {
            sanitize_evidence_text(value, 120).strip()
            for value in requested_calls[:EVIDENCE_MAX_LIST_ITEMS]
        } if isinstance(requested_calls, list) else set()
        if event.event_type == "recovery_suggestion" and event.call_id:
            call_ids.add(event.call_id)
        linked = [error.error_id for error in errors if error.call_id in call_ids]
        if linked:
            return tuple(linked)
        prior = [
            error
            for error in errors
            if max((event_positions.get(source, -1) for source in error.source_event_ids), default=-1)
            < event.index
        ]
        return (prior[-1].error_id,) if prior else ()

    def _extract_recoveries(
        self,
        events: list[_Event],
        errors: list[ErrorEvidence],
        files: list[FileEvidence],
        verification: list[VerificationEvidence],
    ) -> list[RecoveryEvidence]:
        event_positions = {event.event_id: event.index for event in events}
        result_events_by_call: dict[str, list[str]] = defaultdict(list)
        for event in events:
            if event.call_id and event.event_type == "tool_result":
                result_events_by_call[event.call_id].append(event.event_id)
        recoveries: list[RecoveryEvidence] = []
        for event in events:
            if event.event_type not in {"recovery", "fix", "recovery/fix"}:
                continue
            action_value = (
                _safe_get(event.raw, "action")
                or _safe_get(event.raw, "message")
                or _safe_get(event.raw, "content")
                or _safe_get(event.raw, "summary")
                or ""
            )
            action = sanitize_evidence_text(action_value)
            if not action.strip():
                continue
            changed = [
                item.path
                for item in files
                if item.role == "changed"
                and (event.event_id in item.event_ids or (event.call_id and item.call_id == event.call_id))
            ]
            status_value = _normalize_name(_safe_get(event.raw, "epistemic_status", "confirmed"))
            status: EpistemicStatus = status_value if status_value in {"confirmed", "inferred", "unknown"} else "unknown"  # type: ignore[assignment]
            # An explicit recovery may name the calls whose successful
            # results verify it. Only calls that actually produced a
            # tool_result in this trace are kept: the claim-side validator
            # treats a direct call link as the strongest verification
            # relationship, so the link must point at something real.
            raw_verification_calls = _safe_get(event.raw, "verification_call_ids", [])
            verification_calls = tuple(
                sanitize_evidence_text(item, 120)
                for item in (
                    raw_verification_calls[:EVIDENCE_MAX_LIST_ITEMS]
                    if isinstance(raw_verification_calls, list)
                    else []
                )
                if isinstance(item, str) and item in result_events_by_call
            )
            event_ids = [event.event_id]
            if event.call_id:
                event_ids.extend(result_events_by_call[event.call_id])
            recoveries.append(
                RecoveryEvidence(
                    recovery_id=f"recovery-{len(recoveries) + 1:06d}",
                    related_error_ids=self._related_error_ids(event, errors, event_positions),
                    action=action,
                    event_ids=_ordered_unique(event_ids),
                    files_changed=tuple(dict.fromkeys(changed)),
                    epistemic_status=status,
                    verification_call_ids=verification_calls,
                )
            )
        recoveries.extend(
            self._derive_recoveries(
                events,
                errors,
                files,
                verification,
                recoveries,
            )
        )
        return recoveries[:EVIDENCE_MAX_LIST_ITEMS]

    def _derive_recoveries(
        self,
        events: list[_Event],
        errors: list[ErrorEvidence],
        files: list[FileEvidence],
        verification: list[VerificationEvidence],
        explicit: list[RecoveryEvidence],
    ) -> list[RecoveryEvidence]:
        """Recover the fix-verified loop that no runtime event reports.

        Nothing in the agent emits a "recovery"/"fix" event, so RecoveryEvidence
        was only ever produced by test fixtures. That starved every claim type
        built on it -- ``recovery`` and confirmed ``root_cause`` -- and left
        bare ``error_pattern`` as the one path with a live feed.

        The loop is nonetheless fully recorded in the trace already: a call
        fails, files change, the same call succeeds. This reads that shape back
        out. It requires an intervening file change on purpose: a same-target
        retry that succeeds with nothing altered in between is flakiness, and
        minting a "recovery" from it would reintroduce exactly the contentless
        memory this whole path is meant to stop producing. Transient provider,
        network, and lock failures are excluded for the same reason: an
        unrelated edit cannot establish that it caused those conditions to
        clear.
        """
        calls = self._call_attempts(events)
        if not calls:
            return []
        errors_by_call: dict[str, list[ErrorEvidence]] = defaultdict(list)
        for error in errors:
            if error.call_id:
                errors_by_call[error.call_id].append(error)
        already_linked = {
            error_id for recovery in explicit for error_id in recovery.related_error_ids
        }
        changes = self._changed_file_positions(events, files)

        # One repair, one record. Attempts that failed on the same target
        # before the same success describe a single fix, so they are grouped
        # rather than emitted one-per-failure -- fragmenting one root cause
        # across several entries is the noise pattern this path exists to
        # avoid, not to reproduce.
        repairs: dict[str, list[_CallAttempt]] = {}
        successes: list[_CallAttempt] = []
        # Per-success weakest pairing relation: a "flags" pairing keeps the
        # derived recovery below ``confirmed`` even when a file change
        # follows (2026-09-09 objective-contract review R2).
        pair_kind: dict[str, str] = {}
        for failure in calls:
            if failure.status != "error":
                continue
            attempt_errors = [
                error
                for error in errors_by_call.get(failure.call_id, [])
                if error.error_id not in already_linked
            ]
            if not attempt_errors:
                continue
            if any(
                _is_transient_environmental_failure(error)
                for error in attempt_errors
            ):
                continue
            paired = next(
                (
                    (item, kind)
                    for item in calls
                    if item.status == "success"
                    and item.index > failure.index
                    and item.tool_name == failure.tool_name
                    # Identity comes from the full objective contract, not
                    # the display target: the target truncates at six
                    # tokens, so a sixth dropped file or a trailing ``-m``
                    # change used to hide inside the cut.
                    and (kind := _objective_pair_relation(failure, item))
                ),
                None,
            )
            if paired is None:
                continue
            success, relation = paired
            if success.call_id not in repairs:
                repairs[success.call_id] = []
                successes.append(success)
                pair_kind[success.call_id] = relation
            elif relation == "flags":
                pair_kind[success.call_id] = "flags"
            repairs[success.call_id].append(failure)

        derived: list[RecoveryEvidence] = []
        deferred_pairs: list[tuple[_CallAttempt, list[_CallAttempt]]] = []
        for success in successes:
            failures = repairs[success.call_id]
            # Whatever changed after the *last* failure is the actual repair:
            # anything earlier demonstrably did not fix it, since the call
            # failed again afterwards.
            last_attempt = max(item.index for item in failures)
            window = [
                item for item in changes if last_attempt < item[1] < success.index
            ]
            if not window:
                # Execution-precondition repair (rerun from a different
                # working directory, or with a materially adjusted
                # invocation) is deferred: the operational and generic
                # families below get first claim on the pair so their
                # richer, better-classified records are not shadowed by a
                # generic one. Only unclaimed pairs fall through to the
                # deferred pass before this method returns.
                deferred_pairs.append((success, failures))
                continue
            repaired = list(dict.fromkeys(path for path, _, _ in window))
            # The repair *is* the change; the passing re-run that follows is
            # the verification of it. Keeping the successful call out of
            # event_ids is what lets _passed_verifications_after see that
            # verification as coming after the recovery, which is the only
            # way the claim reaches "confirmed".
            event_positions = {event.event_id: event.index for event in events}
            fix_event_ids = [
                event_id
                for _, _, event_ids in window
                for event_id in event_ids
                # A successful mutating call is both the corrective action
                # (its tool_call) and the proof that the action completed (its
                # later tool_result).  Keep only the action side in the
                # recovery boundary; otherwise _passed_verifications_after
                # sees the proof at the same position and incorrectly
                # downgrades a real edit recovery to ``inferred``.
                if event_positions.get(event_id, success.index) < success.index
            ]
            related = _ordered_unique(
                [
                    error.error_id
                    for item in failures
                    for error in errors_by_call.get(item.call_id, [])
                    if error.error_id not in already_linked
                ]
            )
            shown = ", ".join(repaired[:3])
            if len(repaired) > 3:
                shown = f"{shown} (+{len(repaired) - 3} more)"
            change_summary = self._change_summary_for_window(
                events, last_attempt, success.index
            )
            derived.append(
                RecoveryEvidence(
                    recovery_id=f"recovery-{len(explicit) + len(derived) + 1:06d}",
                    related_error_ids=tuple(related),
                    # No trailing period: the claim template appends one.
                    action=sanitize_evidence_text(
                        f"Changed {shown}, after which {success.tool_name} "
                        f"succeeded on {success.target}"
                    ),
                    event_ids=_ordered_unique(
                        [
                            *(
                                event_id
                                for item in failures
                                for event_id in item.event_ids
                            ),
                            *fix_event_ids,
                        ]
                    ),
                    files_changed=tuple(repaired),
                    epistemic_status=(
                        "inferred"
                        if pair_kind.get(success.call_id) == "flags"
                        else "confirmed"
                    ),
                    change_summary=change_summary,
                    verification_call_ids=(success.call_id,),
                )
            )
        derived.extend(
            self._derive_operational_recoveries(
                calls,
                errors,
                verification,
                [*explicit, *derived],
            )
        )
        derived.extend(
            self._derive_generic_tool_recoveries(
                calls,
                errors,
                [*explicit, *derived],
            )
        )
        self._append_deferred_precondition_recoveries(
            derived,
            deferred_pairs,
            errors_by_call,
            explicit,
        )
        return derived

    @staticmethod
    def _append_deferred_precondition_recoveries(
        derived: list[RecoveryEvidence],
        deferred_pairs: list[tuple["_CallAttempt", list["_CallAttempt"]]],
        errors_by_call: dict[str, list[ErrorEvidence]],
        explicit: list[RecoveryEvidence],
    ) -> None:
        """Claim the execution-precondition pairs no other family took.

        A rerun from a different working directory is structurally
        classifiable, so it lands ``confirmed``; a merely adjusted
        invocation keeps ``inferred`` — the 2026-09-09 objective-contract
        review keeps unclassified argument differences below confirmed.
        """
        linked = {
            error_id
            for recovery in [*explicit, *derived]
            for error_id in recovery.related_error_ids
        }
        for success, failures in deferred_pairs:
            related = _ordered_unique(
                [
                    error.error_id
                    for item in failures
                    for error in errors_by_call.get(item.call_id, [])
                    if error.error_id not in linked
                ]
            )
            if not related:
                continue
            latest_failure = max(failures, key=lambda item: item.index)
            if (
                latest_failure.cwd
                and success.cwd
                and latest_failure.cwd != success.cwd
            ):
                action_text = (
                    f"Reran {success.tool_name} on {success.target} from "
                    f"cwd: {success.cwd} after the cwd: {latest_failure.cwd} "
                    "attempt failed; the working directory is a hidden "
                    "precondition of the command"
                )
                status = "confirmed"
            elif (
                latest_failure.invocation
                and latest_failure.invocation != success.invocation
            ):
                action_text = (
                    f"Reran {success.tool_name} on {success.target} with an "
                    "adjusted invocation after the original arguments failed"
                )
                status = "inferred"
            else:
                # An identical retry that succeeded with nothing altered is
                # flakiness; the derivation doctrine still excludes it.
                continue
            linked.update(related)
            derived.append(
                RecoveryEvidence(
                    recovery_id=f"recovery-{len(explicit) + len(derived) + 1:06d}",
                    related_error_ids=tuple(related),
                    # No trailing period: the claim template appends one.
                    action=sanitize_evidence_text(action_text),
                    event_ids=_ordered_unique(
                        [
                            event_id
                            for item in failures
                            for event_id in item.event_ids
                        ]
                    ),
                    files_changed=(),
                    epistemic_status=status,
                    change_summary="",
                    verification_call_ids=(success.call_id,),
                )
            )

    def _derive_operational_recoveries(
        self,
        calls: list[_CallAttempt],
        errors: list[ErrorEvidence],
        verification: list[VerificationEvidence],
        existing: list[RecoveryEvidence],
    ) -> list[RecoveryEvidence]:
        """Derive verified command/tool corrections that do not edit files.

        A command repair is not a same-input retry.  It is a materially changed
        invocation that verifies the same bounded objective: for example,
        replacing literal pipe arguments with ``bash -lc`` or replacing the
        agent's interpreter with the project's virtual-environment Python.

        Correlation stays deliberately strict.  Both calls must describe the
        same verification kind, the successful call must have an independently
        parsed passing verification, and path-bearing calls must share a
        non-generic resource fingerprint. Transient provider, network, service,
        and lock failures are excluded because a later success cannot prove
        that the changed invocation caused recovery. This prevents unrelated
        or coincidental ``ruff``/``pytest`` success from laundering an earlier
        failure into a durable lesson.
        """
        passed_calls = {
            item.call_id
            for item in verification
            if item.call_id and item.result == "passed"
        }
        errors_by_call: dict[str, list[ErrorEvidence]] = defaultdict(list)
        for error in errors:
            if error.call_id:
                errors_by_call[error.call_id].append(error)
        already_linked = {
            error_id for recovery in existing for error_id in recovery.related_error_ids
        }

        repairs: dict[str, list[_CallAttempt]] = {}
        successes: list[_CallAttempt] = []
        for failure in calls:
            if failure.status != "error" or not failure.objective_kind:
                continue
            attempt_errors = [
                error
                for error in errors_by_call.get(failure.call_id, [])
                if error.error_id not in already_linked
            ]
            if not attempt_errors:
                continue
            if any(
                _is_transient_environmental_failure(error)
                for error in attempt_errors
            ):
                continue
            success = next(
                (
                    item
                    for item in calls
                    if item.status == "success"
                    and item.call_id in passed_calls
                    and item.index > failure.index
                    and self._same_operational_objective(
                        failure, item, failure_errors=attempt_errors
                    )
                ),
                None,
            )
            if success is None:
                continue
            if success.call_id not in repairs:
                repairs[success.call_id] = []
                successes.append(success)
            repairs[success.call_id].append(failure)

        result: list[RecoveryEvidence] = []
        for success in successes:
            failures = repairs[success.call_id]
            last_failure = max(failures, key=lambda item: item.index)
            related = _ordered_unique(
                [
                    error.error_id
                    for attempt in failures
                    for error in errors_by_call.get(attempt.call_id, [])
                    if error.error_id not in already_linked
                ]
            )
            if not related or not success.input_event_id:
                continue
            failed_invocation = sanitize_evidence_text(last_failure.invocation, 240)
            successful_invocation = sanitize_evidence_text(success.invocation, 320)
            action = sanitize_evidence_text(
                f"Verified recovery: use the corrected {success.objective_kind} "
                f"invocation `{successful_invocation}`; do not reuse the failed "
                f"invocation `{failed_invocation}`"
            )
            result.append(
                RecoveryEvidence(
                    recovery_id=(
                        f"recovery-{len(existing) + len(result) + 1:06d}"
                    ),
                    related_error_ids=tuple(related),
                    action=action,
                    # The successful tool_call is the observed corrective
                    # action.  Its tool_result remains after this position and
                    # therefore serves as the independent verification.
                    event_ids=_ordered_unique(
                        [
                            *(
                                event_id
                                for attempt in failures
                                for event_id in attempt.event_ids
                            ),
                            success.input_event_id,
                        ]
                    ),
                    files_changed=(),
                    epistemic_status="confirmed",
                    verification_call_ids=(success.call_id,),
                )
            )
            already_linked.update(related)
        return result

    def _derive_generic_tool_recoveries(
        self,
        calls: list[_CallAttempt],
        errors: list[ErrorEvidence],
        existing: list[RecoveryEvidence],
    ) -> list[RecoveryEvidence]:
        """Derive changed-input retries for any tool, including future tools.

        Tool names are deliberately not classified here.  A failed call and a
        later successful call must use the same tool, carry materially changed
        structured input, and still describe the same target.  Path-bearing
        tools match by stable path fingerprints; targetless tools match by a
        bounded input-token overlap and a short call window.

        The successful ToolResult is direct proof that the corrected invocation
        is executable.  It becomes a ``tool_recovery`` VerificationEvidence in
        ``_extract_recovery_verifications`` so safe claims can be auto-approved
        without pretending that a generic retry nudge was the fix. Transient
        provider, network, service, and lock failures are excluded because
        changed-input correlation does not establish causation for them.
        """
        errors_by_call: dict[str, list[ErrorEvidence]] = defaultdict(list)
        for error in errors:
            if error.call_id:
                errors_by_call[error.call_id].append(error)
        already_linked = {
            error_id for recovery in existing for error_id in recovery.related_error_ids
        }
        call_positions = {item.call_id: index for index, item in enumerate(calls)}

        repairs: dict[str, list[_CallAttempt]] = {}
        successes: list[_CallAttempt] = []
        for failure in calls:
            if failure.status != "error" or not failure.invocation:
                continue
            attempt_errors = [
                error
                for error in errors_by_call.get(failure.call_id, [])
                if error.error_id not in already_linked
            ]
            if not attempt_errors:
                continue
            if any(
                _is_transient_environmental_failure(error)
                for error in attempt_errors
            ):
                continue
            failure_position = call_positions[failure.call_id]
            success = next(
                (
                    item
                    for item in calls[failure_position + 1 :]
                    if 0
                    < call_positions[item.call_id] - failure_position
                    <= _GENERIC_RECOVERY_MAX_CALL_GAP
                    and item.status == "success"
                    and item.input_event_id
                    and self._same_generic_tool_objective(
                        failure, item, failure_errors=attempt_errors
                    )
                ),
                None,
            )
            if success is None:
                continue
            if success.call_id not in repairs:
                repairs[success.call_id] = []
                successes.append(success)
            repairs[success.call_id].append(failure)

        result: list[RecoveryEvidence] = []
        for success in successes:
            failures = repairs[success.call_id]
            last_failure = max(failures, key=lambda item: item.index)
            related = _ordered_unique(
                [
                    error.error_id
                    for attempt in failures
                    for error in errors_by_call.get(attempt.call_id, [])
                    if error.error_id not in already_linked
                ]
            )
            if not related or not success.input_event_id:
                continue
            failed_invocation = sanitize_evidence_text(last_failure.invocation, 240)
            successful_invocation = sanitize_evidence_text(success.invocation, 320)
            result.append(
                RecoveryEvidence(
                    recovery_id=(
                        f"recovery-{len(existing) + len(result) + 1:06d}"
                    ),
                    related_error_ids=tuple(related),
                    action=sanitize_evidence_text(
                        f"Verified recovery: use the corrected {success.tool_name} "
                        f"invocation `{successful_invocation}`; do not reuse the "
                        f"failed invocation `{failed_invocation}`"
                    ),
                    event_ids=_ordered_unique(
                        [
                            *(
                                event_id
                                for attempt in failures
                                for event_id in attempt.event_ids
                            ),
                            success.input_event_id,
                        ]
                    ),
                    files_changed=(),
                    epistemic_status="confirmed",
                    verification_call_ids=(success.call_id,),
                )
            )
            already_linked.update(related)
        return result

    def _same_generic_tool_objective(
        self,
        failure: _CallAttempt,
        success: _CallAttempt,
        *,
        failure_errors: list[ErrorEvidence] | None = None,
    ) -> bool:
        if failure.tool_name != success.tool_name:
            return False
        if not failure.invocation or not success.invocation:
            return False
        if " ".join(failure.invocation.lower().split()) == " ".join(
            success.invocation.lower().split()
        ):
            return False
        failed_objective = _without_error_named_arguments(
            failure.objective
            or call_objective(failure.invocation, cwd=failure.cwd),
            failure_errors or [],
        )
        success_objective = success.objective or call_objective(
            success.invocation, cwd=success.cwd
        )
        relation = objective_relation(failed_objective, success_objective)
        if relation == "match":
            return True
        if objective_scope_conflict(failed_objective, success_objective):
            # An explicit contradiction in scope, executable, ambiguity, or
            # unknown arguments may not be overridden by any exception or
            # heuristic.  ``pytest tests -m=expired`` vs ``-m=valid`` and
            # an ambiguous ``dbtool -m`` both land here.
            return False
        if _proven_path_exception(
            failed_objective, success_objective, failure_errors or [],
            tool_name=failure.tool_name,
        ):
            return True
        if failed_objective.input_paths or success_objective.input_paths:
            # A path disagreement the proven exception did not resolve is
            # a conflict, not missing evidence; overlap cannot overrule it.
            return False
        # ``insufficient``: neither side names file targets.  Pathless
        # structured tools (a search query, a bare mode) keep the bounded
        # token overlap as their only signal; the fallback only ever adds
        # evidence where the contract has none.
        failed_tokens = self._generic_invocation_tokens(failure.invocation)
        successful_tokens = self._generic_invocation_tokens(success.invocation)
        common = failed_tokens & successful_tokens
        union = failed_tokens | successful_tokens
        return bool(common) and len(common) / max(1, len(union)) >= 0.4

    @staticmethod
    def _generic_resource_keys(invocation: str) -> tuple[str, ...]:
        """Return path suffixes without discarding meaningful directories.

        Operational verifier matching intentionally treats ``src``/``tests``
        as noise.  Generic tool recovery cannot: those directories distinguish
        two same-named files.  Keep the basename for flat-path retries and the
        last two/three components for relocations that add an outer prefix.
        """
        candidates = re.findall(
            r"(?:[A-Za-z]:)?(?:\.{0,2}/|/)?"
            r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+",
            invocation,
        )
        keys: list[str] = []
        for candidate in candidates[: EVIDENCE_MAX_LIST_ITEMS * 2]:
            normalized = _normalize_path(candidate).strip("'\"`.,;:()[]{}").lower()
            parts = [part for part in normalized.split("/") if part and part != "."]
            if not parts:
                continue
            keys.append(parts[-1])
            if len(parts) >= 2:
                keys.append("/".join(parts[-2:]))
            if len(parts) >= 3:
                keys.append("/".join(parts[-3:]))
        return _ordered_unique(keys)

    @staticmethod
    def _generic_invocation_tokens(invocation: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9_.-]{3,}", invocation.lower())
            if token not in _GENERIC_INPUT_TOKEN_NOISE
            and token != "redacted"
            and not token.isdigit()
        }

    @staticmethod
    def _same_operational_objective(
        failure: _CallAttempt,
        success: _CallAttempt,
        *,
        failure_errors: list[ErrorEvidence] | None = None,
    ) -> bool:
        if not success.objective_kind or failure.objective_kind != success.objective_kind:
            return False
        if not failure.invocation or not success.invocation:
            return False
        if " ".join(failure.invocation.lower().split()) == " ".join(
            success.invocation.lower().split()
        ):
            # An unchanged retry can pass because of flakiness; it teaches no
            # executable recovery method.
            return False
        failed_objective = _without_error_named_arguments(
            failure.objective
            or call_objective(failure.invocation, cwd=failure.cwd),
            failure_errors or [],
        )
        success_objective = success.objective or call_objective(
            success.invocation, cwd=success.cwd
        )
        relation = objective_relation(failed_objective, success_objective)
        if relation == "match":
            return True
        if objective_scope_conflict(failed_objective, success_objective):
            # An explicit contradiction in scope, executable, ambiguity, or
            # unknown arguments may not be overridden by any exception or
            # heuristic.  A path-only disagreement can still be relaxed by
            # the proven relocation/default-scope exception below.
            return False
        return _proven_path_exception(
            failed_objective, success_objective, failure_errors or [],
            tool_name=failure.tool_name,
        )

    @staticmethod
    def _change_summary_for_window(
        events: list["_Event"], start_index: int, end_index: int
    ) -> str:
        """Bounded old->new excerpts of the edits inside a repair window.

        The recovery action alone says which file was touched; the excerpt is
        what makes the lesson executable next time. Multiline edits collapse
        to their first meaningful line per side.
        """
        parts: list[str] = []
        for event in events:
            if not (start_index < event.index < end_index):
                continue
            if event.event_type != "tool_call":
                continue
            tool = _normalize_name(event.tool_name or "")
            if tool not in _CHANGE_TOOLS:
                continue
            raw_input = _safe_get(event.raw, "input")
            if not isinstance(raw_input, Mapping):
                continue
            path = str(
                _safe_get(raw_input, "path")
                or _safe_get(raw_input, "file_path")
                or ""
            ).strip()
            # ``edit_file`` exposes old/new and normalizes legacy
            # search/replace.  Keep old_string/new_string compatibility for
            # historical traces, but do not let the extractor depend on keys
            # that the live tool schema never emits.
            old_value = next(
                (
                    _safe_get(raw_input, key)
                    for key in ("old_string", "old", "search")
                    if key in raw_input
                ),
                None,
            )
            new_value = next(
                (
                    _safe_get(raw_input, key)
                    for key in ("new_string", "new", "replace")
                    if key in raw_input
                ),
                None,
            )

            def _excerpt(value: Any) -> str:
                if not isinstance(value, str):
                    return ""
                lines = [line.strip() for line in value.splitlines() if line.strip()]
                return sanitize_evidence_text(lines[0] if lines else "", 80)

            old_line = _excerpt(old_value)
            new_line = _excerpt(new_value)
            location = path or tool
            if old_line and new_line and old_line != new_line:
                parts.append(f"{location}: '{old_line}' -> '{new_line}'")
            elif new_line:
                parts.append(f"{location}: '{new_line}'")
            elif path:
                parts.append(path)
            if len(parts) >= 3:
                break
        return "; ".join(parts)

    def _call_attempts(self, events: list[_Event]) -> list[_CallAttempt]:
        """Fold tool_call/tool_result pairs into one comparable attempt each."""
        inputs: dict[str, _Event] = {}
        attempts: list[_CallAttempt] = []
        for event in events:
            if not event.call_id:
                continue
            if event.event_type == "tool_call":
                inputs.setdefault(event.call_id, event)
                continue
            if event.event_type != "tool_result":
                continue
            tool_name = _normalize_name(
                event.tool_name
                or (inputs[event.call_id].tool_name if event.call_id in inputs else "")
            )
            if not tool_name:
                continue
            status = _normalize_name(_safe_get(event.raw, "status", ""))
            if _safe_get(event.raw, "is_error"):
                status = "error"
            if status not in {"success", "error"}:
                continue
            input_event = inputs.get(event.call_id)
            sources = [event] + ([input_event] if input_event is not None else [])
            target = self._call_target(tool_name, sources)
            invocation = self._call_invocation(tool_name, sources)
            if not target and not invocation:
                continue
            output = self._event_text(event)
            payload = self._structured_payload(sources)
            cwd = ""
            if payload is not None:
                cwd_value = _safe_get(payload, "cwd", "")
                if cwd_value:
                    cwd = _normalize_path(
                        sanitize_evidence_text(str(cwd_value), 120).strip()
                    ).lower()
            # The tool already supplies argv. Preserve its element boundaries
            # rather than decoding quoted option values as new command text.
            argv = None
            if payload is not None:
                raw_command = _safe_get(payload, "command", "")
                raw_args = _safe_get(payload, "args", [])
                if isinstance(raw_command, str) and raw_command and isinstance(raw_args, list) and raw_args:
                    argv = [sanitize_evidence_text(raw_command, 240), *(
                        sanitize_evidence_text(value, 320) for value in raw_args[:32]
                    )]
            objective = call_objective(invocation, cwd=cwd, argv=argv)
            attempts.append(
                _CallAttempt(
                    call_id=event.call_id,
                    tool_name=tool_name,
                    target=target,
                    status=status,
                    index=event.index,
                    event_ids=tuple(item.event_id for item in sources),
                    input_event_id=input_event.event_id if input_event else None,
                    invocation=invocation,
                    objective_kind=self._verification_kind(
                        tool_name,
                        invocation,
                        output,
                    ),
                    resource_keys=self._operational_resource_keys(invocation),
                    engine_keys=self._operational_engine_keys(tool_name, invocation),
                    path_resources=objective.input_paths,
                    cwd=cwd,
                    selection=objective.selection,
                    objective=objective,
                )
            )
        return attempts

    @staticmethod
    def _structured_payload(sources: list[_Event]) -> Mapping[str, Any] | None:
        for source in sources:
            if source.event_type != "tool_call":
                continue
            nested = _safe_get(source.raw, "input")
            payload = nested if isinstance(nested, Mapping) else source.raw
            if isinstance(payload, Mapping):
                return payload
        return None

    def _call_invocation(self, tool_name: str, sources: list[_Event]) -> str:
        """Render a bounded, redacted invocation from structured tool input."""
        payload: Mapping[str, Any] | None = None
        for source in sources:
            if source.event_type != "tool_call":
                continue
            nested = _safe_get(source.raw, "input")
            payload = nested if isinstance(nested, Mapping) else source.raw
            break
        if payload is None:
            return ""

        command_value = _safe_get(payload, "command", "")
        command = sanitize_evidence_text(command_value, 240).strip()
        raw_args = _safe_get(payload, "args", [])
        args = (
            [sanitize_evidence_text(value, 320) for value in raw_args[:32]]
            if isinstance(raw_args, list)
            else []
        )
        if command:
            rendered = ""
            if args:
                try:
                    rendered = shlex.join([command, *args])
                except (TypeError, ValueError):
                    rendered = ""
            else:
                rendered = command
            if rendered:
                # The working directory is part of the invocation's meaning:
                # ``python -m pytest ledgerlib/tests`` succeeds from ``src``
                # and fails from the repository root.  A recovery lesson that
                # omits it transfers a command whose hidden precondition no
                # longer holds in the next conversation.
                modifiers: list[str] = []
                cwd_value = _safe_get(payload, "cwd", "")
                cwd = (
                    sanitize_evidence_text(str(cwd_value), 120).strip()
                    if cwd_value
                    else ""
                )
                if cwd:
                    modifiers.append(f"cwd: {cwd}")
                # A budget parameter such as ``timeout`` is a real input:
                # raising ``timeout=1`` to ``timeout=120`` is a changed
                # invocation even when command and args are identical, and
                # the projection must say so instead of silently folding the
                # two attempts into one unchanged retry.
                timeout_value = _safe_get(payload, "timeout", None)
                if (
                    isinstance(timeout_value, (int, float))
                    and not isinstance(timeout_value, bool)
                ):
                    modifiers.append(f"timeout: {int(timeout_value)}")
                if modifiers:
                    rendered = f"{rendered} ({'; '.join(modifiers)})"
                if isinstance(raw_args, list) and len(raw_args) > 32:
                    rendered += " ...[truncated]"
                return sanitize_evidence_text(rendered, 560)

        structured = self._sanitize_method_input(payload)
        if not structured:
            return ""
        try:
            rendered = json.dumps(
                structured,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return ""
        return sanitize_evidence_text(f"{tool_name} {rendered}", 480)

    @staticmethod
    def _sanitize_method_input(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Bound and redact arbitrary structured input for a recovery method."""
        seen: set[int] = set()

        def walk(value: Any, *, key: str = "", depth: int = 0) -> Any:
            if key and _SENSITIVE_INPUT_KEY_RE.search(key):
                return "[REDACTED]"
            if depth > 3:
                return "[truncated]"
            if isinstance(value, str):
                return sanitize_evidence_text(value, 140)
            if isinstance(value, (int, float, bool)) or value is None:
                return value
            if isinstance(value, (Mapping, list, tuple)):
                identity = id(value)
                if identity in seen:
                    return "[cycle]"
                seen.add(identity)
            if isinstance(value, Mapping):
                result: dict[str, Any] = {}
                try:
                    items = sorted(
                        list(value.items())[:16],
                        key=lambda item: str(item[0]),
                    )
                except Exception:
                    return "[unreadable]"
                for nested_key, nested in items:
                    key_text = sanitize_evidence_text(nested_key, 80)
                    result[key_text] = walk(
                        nested,
                        key=key_text,
                        depth=depth + 1,
                    )
                return result
            if isinstance(value, (list, tuple)):
                return [walk(item, depth=depth + 1) for item in value[:16]]
            return sanitize_evidence_text(value, 140)

        sanitized = walk(payload)
        return sanitized if isinstance(sanitized, dict) else {}

    @staticmethod
    def _operational_resource_keys(invocation: str) -> tuple[str, ...]:
        """Return path fingerprints stable across absolute/relative retries."""
        candidates = re.findall(
            r"(?:[A-Za-z]:)?(?:\.{0,2}/|/)?"
            r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+",
            invocation,
        )
        keys: list[str] = []
        for candidate in candidates[: EVIDENCE_MAX_LIST_ITEMS * 2]:
            normalized = _normalize_path(candidate).strip("'\"`.,;:()[]{}").lower()
            parts = [part for part in normalized.split("/") if part and part != "."]
            if not parts:
                continue
            basename = parts[-1]
            if basename in _OPERATIONAL_RESOURCE_NOISE:
                continue
            keys.append(basename)
            if len(parts) >= 2 and parts[-2] not in _OPERATIONAL_RESOURCE_NOISE:
                keys.append("/".join(parts[-2:]))
        return _ordered_unique(keys)

    @staticmethod
    def _operational_engine_keys(tool_name: str, invocation: str) -> tuple[str, ...]:
        lowered = f"{tool_name} {invocation}".lower()
        engines = [
            engine
            for engine in sorted(_OPERATIONAL_ENGINES)
            if re.search(rf"(?<![a-z0-9_-]){re.escape(engine)}(?![a-z0-9_-])", lowered)
        ]
        if _normalize_name(tool_name) == "test_runner":
            engines.append("pytest")
        return _ordered_unique(engines)

    def _call_target(self, tool_name: str, sources: list[_Event]) -> str:
        """Name what a call acted on, so a retry of *the same thing* is detectable.

        Empty when nothing identifies the target; such a call is never matched,
        because pairing two calls that merely share a tool name would invent
        recoveries between unrelated work.
        """
        paths: list[str] = []
        for source in sources:
            raw_files = _safe_get(source.raw, "files", [])
            if isinstance(raw_files, list):
                paths.extend(
                    sanitize_evidence_text(item, 200)
                    for item in raw_files[:EVIDENCE_MAX_LIST_ITEMS]
                    if isinstance(item, str)
                )
        paths = sorted({item for item in paths if item})
        if paths:
            return "|".join(paths)
        for source in sources:
            if source.event_type != "tool_call":
                continue
            nested = _safe_get(source.raw, "input")
            payload = nested if isinstance(nested, Mapping) else source.raw
            if not isinstance(payload, Mapping):
                continue
            # Flags vary between a failing run and its passing retry
            # (`-q`, `--lf`); the executable and its operands do not.  The
            # operands of ``args`` are the selection: ``pytest`` with
            # ``tests/test_auth.py`` and ``pytest`` with
            # ``tests/test_billing.py`` are different targets, and pairing
            # them would mint a recovery for a failure that was never
            # retested.  Selection flags (``-k=expired``) narrow the same
            # way -- ``-k=valid`` skips the failing test -- and the working
            # directory resolves the operands, so both join the target.
            command_tokens: list[str] = []
            command_value = _safe_get(payload, "command", "")
            if isinstance(command_value, str):
                command_tokens.extend(sanitize_evidence_text(command_value, 400).split())
            raw_args = _safe_get(payload, "args", [])
            if isinstance(raw_args, list):
                command_tokens.extend(
                    sanitize_evidence_text(value, 200)
                    for value in raw_args[:EVIDENCE_MAX_LIST_ITEMS]
                    if isinstance(value, str)
                )
            tokens = [
                token
                for token in command_tokens
                if token and not token.startswith("-")
            ]
            tokens.extend(_invocation_selection(" ".join(command_tokens)))
            if not tokens:
                continue
            target = " ".join(tokens[:6])
            cwd_value = _safe_get(payload, "cwd", "")
            cwd = (
                sanitize_evidence_text(str(cwd_value), 120).strip()
                if cwd_value
                else ""
            )
            if cwd:
                target = f"{target} @ {cwd}"
            return target
        for source in sources:
            for command in _find_key_values(source.raw, {"command"}).get("command", []):
                if not isinstance(command, str):
                    continue
                command_text = sanitize_evidence_text(command, 400)
                tokens = [
                    token
                    for token in command_text.split()
                    if token and not token.startswith("-")
                ]
                tokens.extend(_invocation_selection(command_text))
                if tokens:
                    return " ".join(tokens[:6])
        return ""

    def _changed_file_positions(
        self, events: list[_Event], files: list[FileEvidence]
    ) -> list[tuple[str, int, tuple[str, ...]]]:
        """Locate each changed file in the trace, with the events that changed it."""
        positions = {event.event_id: event.index for event in events}
        changed: list[tuple[str, int, tuple[str, ...]]] = []
        for item in files:
            if item.role != "changed":
                continue
            known = [event_id for event_id in item.event_ids if event_id in positions]
            if known:
                changed.append((item.path, min(positions[event_id] for event_id in known), tuple(known)))
        return changed

    def _extract_recovery_suggestions(
        self, events: list[_Event], errors: list[ErrorEvidence]
    ) -> list[RecoverySuggestionEvidence]:
        positions = {event.event_id: event.index for event in events}
        suggestions: list[RecoverySuggestionEvidence] = []
        for event in events:
            if event.event_type != "recovery_suggestion":
                continue
            text_value = _safe_get(event.raw, "suggestion") or _safe_get(event.raw, "message") or ""
            suggestion = sanitize_evidence_text(text_value)
            if not suggestion.strip():
                continue
            suggestions.append(
                RecoverySuggestionEvidence(
                    suggestion_id=f"suggestion-{len(suggestions) + 1:06d}",
                    related_error_ids=self._related_error_ids(event, errors, positions),
                    suggestion=suggestion,
                    event_ids=(event.event_id,),
                )
            )
        return suggestions[:EVIDENCE_MAX_LIST_ITEMS]

    def _event_text(self, event: _Event) -> str:
        value = (
            _safe_get(event.raw, "output_summary")
            or _safe_get(event.raw, "message")
            or _safe_get(event.raw, "content")
            or _safe_get(event.raw, "summary")
            or ""
        )
        return sanitize_evidence_text(value)

    def _call_event_ids(self, events: list[_Event]) -> dict[str, tuple[str, ...]]:
        by_call: dict[str, list[str]] = defaultdict(list)
        for event in events:
            if event.call_id:
                by_call[event.call_id].append(event.event_id)
        return {call_id: _ordered_unique(ids) for call_id, ids in by_call.items()}

    @staticmethod
    def _declared_manifest_names(event: _Event, basename: str) -> list[str]:
        """Parse captured declarations, without treating a read as execution.

        Read-file envelopes and older numbered renderings are presentation,
        not manifest syntax. Structured documents must parse completely; a
        truncated JSON/TOML fragment cannot establish its enclosing section.
        """
        raw_text = (
            _safe_get(event.raw, "output_summary")
            or _safe_get(event.raw, "content")
            or _safe_get(event.raw, "message")
            or ""
        )
        text = sanitize_evidence_text(raw_text, EVIDENCE_MAX_TEXT_CHARS * 8)
        lines = text.splitlines()
        partial_first_line = False
        partial_last_line = False
        if lines and lines[0].startswith("FILE: "):
            header = re.compile(
                r"^(?:FILE|OFFSET|END|TOTAL_CHARS|TRUNCATED|REQUESTED_LINES|RETURNED_LINES):"
            )
            headers: list[str] = []
            while lines and header.match(lines[0]):
                headers.append(lines.pop(0))
            partial_first_line = any(
                re.match(r"OFFSET: [1-9]\d*", line) for line in headers
            ) and not any(line.startswith("REQUESTED_LINES:") for line in headers)
            partial_last_line = any(
                line.startswith("TRUNCATED: yes") for line in headers
            ) and not text.endswith("\n")
        lines = [re.sub(r"^\s*\d+\s*[|:]\s?", "", line) for line in lines]
        text = "\n".join(lines)

        def requirement_name(value: object) -> str | None:
            if not isinstance(value, str) or re.search(r"\[[^\]]*truncated[^\]]*\]", value):
                return None
            # Names only: versions, extras, markers and direct URL targets
            # must never become purported additional packages.
            match = re.match(
                r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)"
                r"(?=\s*(?:\[|[<>=!~;@#]|$))",
                value,
            )
            return match.group(1) if match else None

        names: list[str] = []
        if basename.startswith("requirements"):
            incomplete = {0} if partial_first_line else set()
            if partial_last_line:
                incomplete.add(len(lines) - 1)
            for index, line in enumerate(lines):
                if re.search(r"\[[^\]]*truncated[^\]]*\]", line):
                    # Trace bounding inserts newlines around an elided
                    # middle. Both adjacent fragments may be cut names.
                    incomplete.update({index - 1, index, index + 1})
            for index, line in enumerate(lines[: EVIDENCE_MAX_LIST_ITEMS * 4]):
                if index in incomplete:
                    continue
                if name := requirement_name(line):
                    names.append(name)
            return names[:EVIDENCE_MAX_LIST_ITEMS]

        try:
            if basename == "package.json":
                structured = _safe_get(event.raw, "structured_result")
                document = structured if isinstance(structured, Mapping) else json.loads(text)
                if not isinstance(document, Mapping):
                    return []
                for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                    dependencies = document.get(key, {})
                    if isinstance(dependencies, Mapping):
                        for name in list(dependencies)[:EVIDENCE_MAX_LIST_ITEMS]:
                            if isinstance(name, str) and re.fullmatch(
                                r"(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9][A-Za-z0-9_.-]*", name
                            ):
                                names.append(name)
            else:
                document = tomllib.loads(text)
                if basename == "pyproject.toml":
                    project = document.get("project", {})
                    dependencies = project.get("dependencies", []) if isinstance(project, dict) else []
                    if isinstance(dependencies, list):
                        names.extend(
                            name for value in dependencies[:EVIDENCE_MAX_LIST_ITEMS]
                            if (name := requirement_name(value))
                        )
                elif basename == "pipfile":
                    for key in ("packages", "dev-packages"):
                        dependencies = document.get(key, {})
                        if isinstance(dependencies, dict):
                            names.extend(
                                name for value in list(dependencies)[:EVIDENCE_MAX_LIST_ITEMS]
                                if (name := requirement_name(value))
                            )
        except (ValueError, TypeError, RecursionError):
            return []
        return names[:EVIDENCE_MAX_LIST_ITEMS]

    def _extract_libraries(
        self, events: list[_Event], files: list[FileEvidence]
    ) -> list[LibraryEvidence]:
        records: dict[str, dict[str, Any]] = {}
        declared_enabled = deposition_feature_enabled("declared_dependencies")
        local_modules: set[str] = set()
        for file in files:
            normalized_path = file.path.replace("\\", "/").strip("/")
            if not normalized_path.lower().endswith(".py"):
                continue
            parts = [part for part in normalized_path.split("/") if part]
            if not parts:
                continue
            stem = parts[-1][:-3].lower().replace("_", "-")
            if stem and stem != "__init__" and not stem.startswith("test-"):
                local_modules.add(stem)
            if "src" in parts:
                src_index = parts.index("src")
                if src_index + 1 < len(parts):
                    local_modules.add(
                        parts[src_index + 1].lower().replace("_", "-")
                    )
            if parts[-1] == "__init__.py" and len(parts) >= 2:
                local_modules.add(parts[-2].lower().replace("_", "-"))
        call_ids = self._call_event_ids(events)
        successful_call_ids = {
            event.call_id
            for event in events
            if event.call_id
            and event.event_type == "tool_result"
            and not bool(_safe_get(event.raw, "is_error"))
            and str(_safe_get(event.raw, "status", "")).strip().lower()
            in {"success", "succeeded", "ok", "passed", "completed"}
        }
        paths_by_call: dict[str, set[str]] = defaultdict(set)
        tools_by_call = {
            event.call_id: event.tool_name
            for event in events if event.call_id and event.tool_name
        }
        for file in files:
            if file.call_id:
                paths_by_call[file.call_id].add(file.path.lower())

        def add(
            name: str,
            status: Literal["confirmed", "declared", "weak_mention"],
            evidence_ids: Sequence[str],
            import_name: str | None = None,
        ) -> None:
            normalized = name.strip().lower().replace("_", "-")
            if not normalized or len(normalized) > 100:
                return
            existing = records.get(normalized)
            if existing is None:
                records[normalized] = {
                    "name": normalized,
                    "status": status,
                    "event_ids": list(evidence_ids),
                    "import_name": import_name,
                }
                return
            if declared_enabled:
                ranks = {"weak_mention": 0, "declared": 1, "confirmed": 2}
                if ranks[status] < ranks[existing["status"]]:
                    return
                if ranks[status] > ranks[existing["status"]]:
                    # A later use proves this package, not every other
                    # declaration sharing the manifest-read event.
                    existing["event_ids"] = []
                    existing["status"] = status
            existing["event_ids"].extend(evidence_ids)
            if status == "confirmed":
                existing["status"] = "confirmed"
            if import_name:
                existing["import_name"] = import_name

        for event in events:
            text = self._event_text(event)
            lowered = text.lower()
            successful_result = (
                event.event_type == "tool_result"
                and event.call_id in successful_call_ids
            )
            evidence_ids = call_ids.get(event.call_id, (event.event_id,)) if event.call_id else (event.event_id,)
            manifest_paths = paths_by_call.get(event.call_id or "", set())
            basenames = {re.split(r"[/\\]", path)[-1] for path in manifest_paths}
            is_manifest_read = (
                declared_enabled
                and (event.tool_name or tools_by_call.get(event.call_id)) == "read_file"
                and bool(basenames & _DECLARED_MANIFEST_NAMES)
            )
            if is_manifest_read:
                if (
                    successful_result
                    and not bool(_safe_get(event.raw, "is_error"))
                    and _normalize_name(_safe_get(event.raw, "status", ""))
                    in {"success", "succeeded", "ok", "passed", "completed"}
                ):
                    for basename in sorted(basenames & _DECLARED_MANIFEST_NAMES):
                        for name in self._declared_manifest_names(event, basename):
                            add(name, "declared", evidence_ids)
                # Manifest text (including comments containing imports or
                # commands) cannot also act as confirmation of actual use.
                continue
            structured = _safe_get(event.raw, "structured_result")
            if isinstance(structured, Mapping) and successful_result:
                for key in ("dependencies", "devDependencies", "optionalDependencies"):
                    values = _safe_get(structured, key)
                    if isinstance(values, Mapping):
                        try:
                            names = list(values.keys())[:EVIDENCE_MAX_LIST_ITEMS]
                        except Exception:
                            names = []
                        for name in names:
                            add(sanitize_evidence_text(name, 100), "confirmed", (event.event_id,))
                installed = _safe_get(structured, "installed")
                if isinstance(installed, list):
                    for name in installed[:EVIDENCE_MAX_LIST_ITEMS]:
                        add(sanitize_evidence_text(name, 100), "confirmed", evidence_ids)

            if successful_result and basenames & _MANIFEST_NAMES:
                if any(name.startswith("requirements") for name in basenames):
                    for line in text.splitlines()[:EVIDENCE_MAX_LIST_ITEMS]:
                        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*(?:\[|[<>=!~]|$)", line)
                        if match:
                            add(match.group(1), "confirmed", (event.event_id,))
                if "pyproject.toml" in basenames:
                    dependency_blocks = re.findall(r"(?i)dependencies\s*=\s*\[([^\]]*)\]", text)
                    for block in dependency_blocks:
                        for name in re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", block):
                            if name.lower() not in {"python"}:
                                add(name, "confirmed", (event.event_id,))

            if successful_result:
                for match in re.finditer(r"(?m)^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", text):
                    import_name = match.group(1).split(".", 1)[0].lower()
                    if (
                        import_name not in _STANDARD_LIBRARY_IMPORTS
                        and import_name.replace("_", "-") not in local_modules
                    ):
                        add(
                            _IMPORT_ALIASES.get(import_name, import_name),
                            "confirmed",
                            (event.event_id,),
                            import_name if import_name in _IMPORT_ALIASES else None,
                        )
                for match in re.finditer(r"(?m)\bfrom\s+['\"]([^'\"]+)['\"]", text):
                    package = match.group(1).split("/", 1)[0]
                    if package and not package.startswith("."):
                        add(package, "confirmed", (event.event_id,))

            commands = _find_key_values(event.raw, {"command"}).get("command", [])
            for command in commands:
                if not isinstance(command, str):
                    continue
                try:
                    tokens = shlex.split(command)
                except ValueError:
                    continue
                lowered_tokens = [token.lower() for token in tokens]
                install_index = next(
                    (index for index, token in enumerate(lowered_tokens) if token in {"install", "add"}),
                    None,
                )
                if install_index is not None:
                    install_status: Literal["confirmed", "weak_mention"] = (
                        "confirmed"
                        if event.call_id in successful_call_ids
                        else "weak_mention"
                    )
                    for token in tokens[install_index + 1 : install_index + 1 + EVIDENCE_MAX_LIST_ITEMS]:
                        if token.startswith("-") or _looks_like_local_file(token):
                            continue
                        package = re.split(r"[<>=!~@\[]", token, maxsplit=1)[0]
                        if re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", package):
                            add(package, install_status, evidence_ids)
                if lowered_tokens[:2] == ["ruff", "format"] or lowered_tokens[:2] == ["ruff", "check"]:
                    add(
                        "ruff",
                        (
                            "confirmed"
                            if event.call_id in successful_call_ids
                            else "weak_mention"
                        ),
                        evidence_ids,
                    )

            if event.event_type == "existing_memory" and re.search(r"(?i)\bruff\s+format\b", text):
                add("ruff", "confirmed", (event.event_id,))

            if event.event_type in {"assistant", "assistant_step"}:
                for name in sorted(_MENTION_LIBRARIES):
                    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])"
                    for match in re.finditer(pattern, lowered):
                        prefix = lowered[max(0, match.start() - 32) : match.start()]
                        local_path = re.search(r"(?:^|[\s'\"])(?:src|tests?|lib)/$", prefix)
                        negated = re.search(
                            r"(?:do\s+not|does\s+not|did\s+not|don't|doesn't|not)\s+(?:add\s+)?$",
                            prefix,
                        )
                        if not local_path and not negated:
                            add(name, "weak_mention", (event.event_id,))
                        break

        return [
            LibraryEvidence(
                name=record["name"],
                status=record["status"],
                event_ids=_ordered_unique(record["event_ids"]),
                import_name=record["import_name"],
                epistemic_status="inferred" if record["status"] == "declared" else "confirmed",
            )
            for record in records.values()
        ][:EVIDENCE_MAX_LIST_ITEMS]

    def _verification_kind(self, tool_name: str, command: str, output: str) -> str | None:
        tool = _normalize_name(tool_name)
        command_lower = command.lower().strip()
        output_lower = output.lower()
        if tool in {"pytest", "unittest", "test_runner"}:
            return "test"
        if tool in {"ruff", "eslint", "flake8", "pylint"}:
            return "lint"
        if tool in {"pyright", "mypy", "tsc"}:
            return "type_check"
        if tool in {"compile"}:
            return "compile"
        if tool in {"build"}:
            return "build"
        # ``run_command`` commonly wraps project tools in ``bash -lc`` or
        # invokes them through a virtual-environment path.  Prefix-only token
        # checks miss both shapes; a real Ruff success was consequently tagged
        # as a test merely because its output contained "passed".
        if re.search(
            r"(?:^|[/\\\s'\"])(?:ruff|eslint)(?:\.exe)?\s+check(?:\s|$)",
            command_lower,
        ) or "all checks passed" in output_lower:
            return "lint"
        if re.search(
            r"(?:^|[/\\\s'\"])(?:pytest|unittest)(?:\.exe)?(?:\s|$)",
            command_lower,
        ) or re.search(
            r"(?:^|[/\\\s'\"])(?:python|python3)(?:\.exe)?\s+-m\s+"
            r"(?:pytest|unittest)(?:\s|$)",
            command_lower,
        ):
            return "test"
        try:
            command_tokens = shlex.split(command_lower)
        except ValueError:
            command_tokens = []
        if (
            command_tokens[:1] in (["pytest"], ["unittest"])
            or command_tokens[:3] in (["python", "-m", "pytest"], ["python", "-m", "unittest"])
            or command_tokens[:2] in (["npm", "test"], ["cargo", "test"], ["go", "test"])
        ):
            return "test"
        if re.search(r"\b(?:tests?|fixtures|passed)\b", output_lower) or any(
            marker in output for marker in ("测试通过", "测试失败", "测试未通过")
        ):
            return "test"
        if re.search(r"(?:^|\s)(?:ruff|eslint)\s+check(?:\s|$)", command_lower) or re.search(
            r"\blint(?:ed|ing)?\b", output_lower
        ):
            return "lint"
        if re.search(r"\b(?:pyright|mypy|type[ -]?check)\b", f"{command_lower} {output_lower}"):
            return "type_check"
        if re.search(r"\b(?:compile|compileall)\b", f"{command_lower} {output_lower}"):
            return "compile"
        if re.search(r"\bbuild\b", f"{command_lower} {output_lower}"):
            return "build"
        return None

    def _verification_result(
        self, status: str, output: str, *, invocation: str = ""
    ) -> Literal["passed", "failed", "unknown"]:
        lowered = output.lower()
        if status in {"error", "failed", "failure"}:
            return "failed"
        if invocation:
            objective = call_objective_from_rendered(invocation)
            if not objective.executes_work or objective.ambiguous_selection or not objective.executable:
                return "unknown"
        # Shell pipelines can return ``tail``'s zero exit status while pytest
        # itself failed before collection.  Such an output must never verify a
        # recovery lesson, even when the generic tool status says success.
        if (
            "no tests ran" in lowered
            or re.search(r"\bcollected\s+0\s+items?\b", lowered)
            or re.search(r"\bran\s+0\s+tests?\b", lowered)
            or re.search(r"\berror:\s+file or directory not found\b", lowered)
        ):
            return "failed"
        if (
            re.search(r"\b[1-9]\d*\s+(?:\w+\s+)?failed\b", lowered)
            # Mixed summaries such as "1 passed, 1 error in 0.15s": the
            # process exited zero, the run still contains failures.  An
            # erroring fixture never verifies a recovery, and "no errors" /
            # "0 errors" never match this pattern.
            or re.search(r"\b[1-9]\d*\s+(?:\w+\s+)?errors?\b", lowered)
            or re.search(r"\b(?:failures|errors)=[1-9]\d*\b", lowered)
            or "测试失败" in output
            or "测试未通过" in output
        ):
            return "failed"
        if re.search(r"\b(?:passed|succeeded|successful|success|no errors?)\b", lowered) or "测试通过" in output:
            return "passed"
        if status in {"success", "completed", "ok", "passed"}:
            return "passed"
        return "unknown"

    def _verification_scope(self, command: str, output: str) -> Literal["targeted", "full", "unknown"]:
        lowered = f"{command} {output}".lower()
        objective = call_objective_from_rendered(command)
        if objective.ambiguous_selection or not objective.executes_work:
            return "unknown"
        if objective.executable in _TEST_RUNNERS:
            if objective.input_paths or objective.bare_operands or objective.selection:
                return "targeted"
            if objective.unknown_flags:
                return "unknown"
            return "full"
        if "full suite" in lowered:
            return "full"
        if _command_paths(command) or re.search(
            r"\b(?:targeted|focused|service tests?|memory tests?|consistency tests?|compatibility tests?|security fixtures?|cache consistency)\b",
            lowered,
        ) or "一致性测试" in output:
            return "targeted"
        return "unknown"

    def _extract_recovery_verifications(
        self,
        events: list[_Event],
        recoveries: list[RecoveryEvidence],
        existing: list[VerificationEvidence],
    ) -> list[VerificationEvidence]:
        """Promote directly linked successful ToolResults to recovery checks."""
        existing_calls = {item.call_id for item in existing if item.call_id}
        requested_calls = {
            call_id
            for recovery in recoveries
            for call_id in recovery.verification_call_ids
            if call_id and call_id not in existing_calls
        }
        if not requested_calls:
            return []
        inputs: dict[str, _Event] = {}
        results: dict[str, _Event] = {}
        for event in events:
            if not event.call_id or event.call_id not in requested_calls:
                continue
            if event.event_type == "tool_call":
                inputs.setdefault(event.call_id, event)
            elif event.event_type == "tool_result":
                results.setdefault(event.call_id, event)

        verification: list[VerificationEvidence] = []
        for call_id in sorted(
            requested_calls,
            key=lambda item: results[item].index if item in results else len(events),
        ):
            result = results.get(call_id)
            if result is None:
                continue
            status = _normalize_name(_safe_get(result.raw, "status", ""))
            if _safe_get(result.raw, "is_error"):
                status = "error"
            output = self._event_text(result)
            input_event = inputs.get(call_id)
            invocation = self._call_invocation(
                result.tool_name or (input_event.tool_name if input_event else ""),
                [result, *([input_event] if input_event else [])],
            )
            if self._verification_result(status, output, invocation=invocation) != "passed":
                continue
            event_ids = (
                (input_event.event_id, result.event_id)
                if input_event is not None
                else (result.event_id,)
            )
            verification.append(
                VerificationEvidence(
                    verification_id=(
                        f"verify-{len(existing) + len(verification) + 1:06d}"
                    ),
                    tool_name=result.tool_name or (
                        input_event.tool_name if input_event is not None else None
                    ),
                    call_id=call_id,
                    command_kind="tool_recovery",
                    scope="targeted",
                    result="passed",
                    event_ids=event_ids,
                    summary=output,
                )
            )
        return verification

    def _extract_verification(self, events: list[_Event]) -> list[VerificationEvidence]:
        call_events_by_id: dict[str, list[_Event]] = defaultdict(list)
        recovery_calls: set[str] = set()
        for event in events:
            if event.call_id:
                if event.event_type == "tool_call":
                    call_events_by_id[event.call_id].append(event)
                if event.event_type in {"recovery", "fix", "recovery/fix"}:
                    recovery_calls.add(event.call_id)
        verification: list[VerificationEvidence] = []
        failed_tools_seen: dict[str, int] = defaultdict(int)
        for event in events:
            if event.event_type not in {"tool_result", "verification"}:
                continue
            call_events = call_events_by_id.get(event.call_id or "", [])
            command_values: list[str] = []
            for candidate in call_events + [event]:
                for command in _find_key_values(candidate.raw, {"command"}).get("command", []):
                    if isinstance(command, str):
                        command_values.append(sanitize_evidence_text(command))
            tool_name = event.tool_name or (call_events[0].tool_name if call_events else "")
            # Prefer the rendered structured invocation so args such as
            # ``["check", "src", "|", "tail"]`` participate in verifier
            # classification.  Looking only at ``command`` reduced that real
            # failed call to the single token "ruff" and erased the red side
            # of the later verified recovery.
            command = self._call_invocation(
                _normalize_name(tool_name),
                [event, *call_events],
            )
            if not command:
                command = command_values[0] if command_values else ""
            output = self._event_text(event)
            status = _normalize_name(_safe_get(event.raw, "status", ""))
            kind = _normalize_name(_safe_get(event.raw, "command_kind", "")) or self._verification_kind(
                tool_name,
                command,
                output,
            )
            if not kind and event.call_id in recovery_calls and status in {"error", "failed", "failure"}:
                kind = "recovery_check"
            if (
                not kind
                and status in {"error", "failed", "failure"}
                and event.tool_name
                and failed_tools_seen[event.tool_name] > 0
            ):
                kind = "retry_check"
            if status in {"error", "failed", "failure"} and event.tool_name:
                failed_tools_seen[event.tool_name] += 1
            if not kind:
                continue
            event_ids = [candidate.event_id for candidate in call_events]
            event_ids.append(event.event_id)
            verification.append(
                VerificationEvidence(
                    verification_id=f"verify-{len(verification) + 1:06d}",
                    tool_name=event.tool_name or (call_events[0].tool_name if call_events else None),
                    call_id=event.call_id,
                    command_kind=kind,
                    scope=self._verification_scope(command, output),
                    result=self._verification_result(status, output, invocation=command),
                    event_ids=_ordered_unique(event_ids),
                    summary=output,
                )
            )
        return verification[:EVIDENCE_MAX_LIST_ITEMS]

    def _decision_tokens(self, text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[A-Za-z0-9_.()]+|[\u4e00-\u9fff]{2,}", text.lower())
            if len(token) > 2 and token not in {"because", "will", "choose", "decided"}
        }

    def _extract_decisions(self, events: list[_Event]) -> list[DecisionEvidence]:
        candidates: list[dict[str, Any]] = []
        has_error_evidence = any(
            event.event_type == "error"
            or _safe_get(event.raw, "is_error")
            or _normalize_name(_safe_get(event.raw, "status", "")) in {"error", "failed", "failure"}
            for event in events
        )
        has_verification_evidence = any(
            event.event_type == "tool_result"
            and self._verification_kind(
                event.tool_name or "",
                "",
                self._event_text(event),
            )
            is not None
            for event in events
        )
        for event in events:
            text = self._event_text(event).strip()
            if not text:
                continue
            if event.event_type in {"user_constraint", "user_correction"}:
                candidates.append({
                    "statement": text,
                    "rationale": None,
                    "event_ids": [event.event_id],
                    "status": "confirmed",
                    "kind": event.event_type,
                })
                continue
            if (
                event.event_type == "tool_result"
                and event.tool_name == "read_file"
                and not bool(_safe_get(event.raw, "is_error"))
                and _normalize_name(_safe_get(event.raw, "status", ""))
                in {"success", "succeeded", "ok", "passed", "completed"}
            ):
                roles = extract_tool_file_roles(
                    event.tool_name,
                    event.raw,
                    event_type=event.event_type,
                )
                policy_paths = [
                    path
                    for path in roles["files_read"]
                    if re.split(r"[/\\]", path)[-1].lower()
                    in _POLICY_BASENAMES
                ]
                if policy_paths:
                    for statement in _policy_constraint_statements(text):
                        candidates.append({
                            "statement": statement,
                            "rationale": f"Declared in {policy_paths[0]}",
                            "event_ids": [event.event_id],
                            "status": "confirmed",
                            "kind": "config_constraint",
                        })
                    if any(
                        candidate["event_ids"] == [event.event_id]
                        and candidate["kind"] == "config_constraint"
                        for candidate in candidates
                    ):
                        continue
            if event.event_type == "tool_result" and "requires-python" in text.lower():
                # Only the declaration is the constraint. Pasting the whole
                # tool result carried the read_file header, the rest of the
                # manifest and whatever the model said next into a durable
                # claim, which then read "Project constraint: Python 3.11
                # project constraint: FILE: pyproject.toml OFFSET: 0 ...
                # This is very revealing. There was a memory entry ...".
                declaration = next(
                    (
                        line.strip()
                        for line in text.splitlines()
                        if "requires-python" in line.lower()
                    ),
                    "",
                )[:_CONSTRAINT_MAX_CHARS]
                version = re.search(
                    r"(?:>=|~=|==|>)\s*([0-9]+(?:\.[0-9]+)+)", declaration or text
                )
                version_text = version.group(1) if version else "unknown"
                candidates.append({
                    "statement": (
                        f"Python {version_text} is required: {declaration}"
                        if declaration
                        else f"Python {version_text} is required."
                    ),
                    "rationale": declaration or text,
                    "event_ids": [event.event_id],
                    "status": "confirmed",
                    "kind": "config_constraint",
                })
                continue
            if event.event_type not in {"assistant", "assistant_step"}:
                continue
            lowered = text.lower()
            if re.search(r"\b(?:i will|start by)\s+(?:read|list|inspect|format)\b", lowered):
                continue
            explicit = bool(
                re.search(r"\b(?:i\s+)?(?:choose|chose|decide|decided|select|selected)\b", lowered)
                or re.search(r"\bi will preserve\b", lowered)
                or re.search(r"\b(?:caused|fixes|root cause is)\b", lowered)
                or re.search(r"(?:选择|决定|导致|根因是)", text)
            )
            if not explicit or re.search(r"\broot cause is not yet known\b", lowered):
                continue
            rationale: str | None = None
            # Only the sentence that states the decision; the rest of the turn
            # is the model working out loud.
            choice = _decision_sentence(text)
            if not choice:
                continue
            choice_lowered = choice.lower()
            if " because " in choice_lowered:
                split_at = choice_lowered.index(" because ")
                rationale = choice[split_at + len(" because ") :].strip()
                choice = choice[:split_at].strip()
            candidate = {
                "statement": choice,
                "rationale": rationale,
                "event_ids": [event.event_id],
                "status": (
                    "inferred"
                    if re.search(r"\b(?:caused|fixes|root cause is)\b", lowered)
                    and not (has_error_evidence and has_verification_evidence)
                    else "confirmed"
                ),
                "kind": "assistant_decision",
            }
            # User/config evidence is authoritative text.  A semantically
            # overlapping assistant sentence must remain a separate decision;
            # merging it would launder model-authored content through the
            # stronger user/config provenance.
            candidates.append(candidate)
            if rationale and re.search(r"\b(?:may|might|probably|likely)\b", rationale.lower()):
                candidates.append({
                    "statement": rationale,
                    "rationale": rationale,
                    "event_ids": [event.event_id],
                    "status": "inferred",
                    "kind": "inferred_rationale",
                })
        return [
            DecisionEvidence(
                decision_id=f"decision-{index:06d}",
                statement=sanitize_evidence_text(candidate["statement"]),
                rationale=sanitize_evidence_text(candidate["rationale"]) if candidate["rationale"] else None,
                event_ids=_ordered_unique(candidate["event_ids"]),
                epistemic_status=candidate["status"],
                source_kind=candidate["kind"],
            )
            for index, candidate in enumerate(candidates[:EVIDENCE_MAX_LIST_ITEMS], start=1)
        ]

    def _extract_outcome(
        self,
        events: list[_Event],
        verification: list[VerificationEvidence],
    ) -> Literal["success", "failed", "unknown"]:
        positions = {event.event_id: event.index for event in events}
        task_results = [event for event in events if event.event_type == "task_result"]
        last_task = task_results[-1] if task_results else None
        failed_verification_positions = [
            max((positions.get(event_id, -1) for event_id in item.event_ids), default=-1)
            for item in verification
            if item.result == "failed"
        ]
        if last_task:
            later = [
                item
                for item in verification
                if max((positions.get(event_id, -1) for event_id in item.event_ids), default=-1)
                > last_task.index
                and item.result in {"passed", "failed"}
            ]
            if later:
                return "success" if later[-1].result == "passed" else "failed"
            status = _normalize_name(_safe_get(last_task.raw, "final_outcome", "")) or _normalize_name(
                _safe_get(last_task.raw, "status", "")
            )
            if status in {"success", "completed", "ok", "passed"}:
                return "success"
            if status in {"failed", "failure", "error"}:
                return "failed"
            return "unknown"
        if failed_verification_positions:
            return "failed"
        if any(item.result == "passed" for item in verification):
            return "success"
        if any(
            event.event_type in {"assistant", "assistant_step"}
            and _normalize_name(_safe_get(event.raw, "content_kind", "")) == "final"
            for event in events
        ):
            return "success"
        return "unknown"


__all__ = [
    "DecisionEvidence",
    "EpistemicStatus",
    "ErrorEvidence",
    "FileEvidence",
    "LibraryEvidence",
    "RecoveryEvidence",
    "RecoverySuggestionEvidence",
    "TRACE_MAX_EVENTS",
    "TRACE_SCHEMA_VERSION",
    "TaskEvidence",
    "ToolEvidence",
    "TraceEvidenceExtractor",
    "VerificationEvidence",
    "append_trace_event",
    "extract_tool_file_roles",
    "sanitize_evidence_text",
]
