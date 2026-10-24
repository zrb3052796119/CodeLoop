"""Memory Pipeline — unified facade for the complete memory lifecycle.

Design principle: ONE class, FOUR methods. All memory operations flow
through this single entry point. No scattered ad-hoc calls.

Architecture:
  MemoryPipeline
    ├── read(task, files) → DomainClassifier → canonical BM25 retrieval
    ├── inject(task, files, messages) → read + append to system prompt
    ├── write(task, trace) → ReflectionEngine → TaskContext → MemoryManager
    └── maintain() → CuratorAgent → consolidate/validate/promote/link

Sub-components (internal, not exposed):
  - DomainClassifier: auto-detects active domains from files/intent
  - MemoryInjectionController: PID-controlled injection budgets
  - CanonicalMemoryRetriever: single retrieval/ranking authority
  - MemoryCuratorAgent: background optimization during idle

Hybrid retrieval is evidence-gated. A request with missing, stale, malformed,
or failing promotion evidence stays on the byte-equivalent lexical path. Once
valid evidence authorizes Hybrid for a run, runtime bootstrap failure is
fail-closed and cannot silently downgrade that run to lexical injection.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import re
import stat
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from minicode.logging_config import get_logger
from minicode.model_call_control import ModelCallDeadlineExceeded, checkpoint_model_call
from minicode.run_events import emit_event_safely, verification_corroboration
from minicode.turn_cancellation import TurnCancellationRequested

if TYPE_CHECKING:
    from minicode.memory import MemoryEntry
    from minicode.memory_injector import (
        MemoryInjectionController,
        MemoryInjectionDecision,
    )
    from minicode.memory_retrieval import RetrievalDetailLevel
    from minicode.memory_write_contract import MemoryWriteResult
    from minicode.project_facts import ProjectFactsWritePlan

logger = get_logger("memory_pipeline")

_PIPELINE_STATE_SCHEMA_VERSION = 1
_MAX_PIPELINE_STATE_BYTES = 64 * 1024
_MAX_PIPELINE_COUNTER = 1_000_000_000
_DEFAULT_MAX_MEMORIES = 5
_DEFAULT_MIN_RELEVANCE = 0.3
_DEFAULT_MAX_TOKENS_PER_MEMORY = 200


class _FailClosedHybridProvider:
    """Keep an authorized Hybrid run closed when runtime bootstrap fails."""

    @staticmethod
    def adjudicate(**_kwargs: Any) -> None:
        return None


@dataclass(frozen=True, slots=True)
class MemoryPromptReceipt:
    """Exact durable-memory text and IDs rendered for a prompt candidate.

    Preparing a receipt records retrieval, but it is deliberately not an
    injection. A caller must install it through
    :meth:`MemoryPipeline.materialize_prompt_receipt` with an exact rollback,
    then commit accounting through
    :meth:`MemoryPipeline.commit_prompt_receipt`.
    """

    prompt_text: str = ""
    selected_ids: tuple[str, ...] = ()
    rendered_ids: tuple[str, ...] = ()
    # Full-authority versions captured by canonical retrieval. These bind
    # delayed and end-of-Turn feedback to the exact entry version that was
    # materialized, unlike render proofs which may cover truncated text.
    rendered_versions: tuple[tuple[str, str], ...] = ()
    authority_revision: str = ""
    receipt_id: str = ""
    # Content-bound proofs for the exact rendered entry fragments. An ID alone
    # cannot prove that the current approved version is already in a prompt.
    rendered_proofs: tuple[tuple[str, str], ...] = ()
    # The materializable fragment text behind ``rendered_proofs``.  Keeping
    # this separate preserves the public pair-based proof API while allowing a
    # compaction boundary to carry independently verifiable prompt metadata.
    rendered_fragments: tuple[tuple[str, str, str], ...] = ()
    # IDs whose text is already present in the preserved prompt for this Turn.
    # Session compaction may reuse that text without rendering or accounting it
    # a second time.
    reused_ids: tuple[str, ...] = ()


def _prompt_receipt_commit_digest(
    receipt: MemoryPromptReceipt,
    *,
    record_retrieval: bool,
) -> str:
    """Bind a replay key to the exact prompt/accounting event it names."""
    encoded = json.dumps(
        {
            "prompt_text": receipt.prompt_text,
            "selected_ids": receipt.selected_ids,
            "rendered_ids": receipt.rendered_ids,
            "rendered_versions": receipt.rendered_versions,
            "authority_revision": receipt.authority_revision,
            "rendered_proofs": receipt.rendered_proofs,
            "rendered_fragments": receipt.rendered_fragments,
            "reused_ids": receipt.reused_ids,
            "record_retrieval": record_retrieval,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _call_memory_accounting(
    method: Callable[..., Any],
    *args: object,
    observation_id: str,
) -> Any:
    """Pass retry identity to current managers while preserving old facades."""

    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        supports_observation_id = True
    else:
        supports_observation_id = any(
            parameter.name == "observation_id"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    if supports_observation_id:
        return method(*args, observation_id=observation_id)
    return method(*args)


def _validated_pipeline_counter(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_PIPELINE_COUNTER
    ):
        raise ValueError("invalid pipeline counter")
    return value


def _read_pipeline_state(path: Path) -> dict[str, int] | None:
    """Read bounded telemetry without following links or special files."""
    descriptor: int | None = None
    try:
        path_info = os.lstat(path)
        if (
            not stat.S_ISREG(path_info.st_mode)
            or path_info.st_size > _MAX_PIPELINE_STATE_BYTES
        ):
            return None
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        descriptor_info = os.fstat(descriptor)
        current_info = os.lstat(path)
        if (
            not stat.S_ISREG(descriptor_info.st_mode)
            or descriptor_info.st_size > _MAX_PIPELINE_STATE_BYTES
            or descriptor_info.st_dev != current_info.st_dev
            or descriptor_info.st_ino != current_info.st_ino
            or path_info.st_dev != current_info.st_dev
            or path_info.st_ino != current_info.st_ino
        ):
            return None
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(16_384, _MAX_PIPELINE_STATE_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_PIPELINE_STATE_BYTES:
                return None
        payload = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        schema_version = payload.get(
            "schema_version",
            _PIPELINE_STATE_SCHEMA_VERSION,
        )
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != _PIPELINE_STATE_SCHEMA_VERSION
        ):
            return None
        return {
            "read_count": _validated_pipeline_counter(payload.get("read_count", 0)),
            "write_count": _validated_pipeline_counter(payload.get("write_count", 0)),
            "maintain_count": _validated_pipeline_counter(
                payload.get("maintain_count", 0)
            ),
        }
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
        RecursionError,
        UnicodeDecodeError,
        ValueError,
    ):
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


_MEMORY_RENDER_PROOF_SCHEMA = "memory-render-v1"
_MAX_MEMORY_PROOF_FRAGMENT_CHARS = 20_000
_MEMORY_PROMPT_BLOCK_SCHEMA = "memory-prompt-block-v1"
_MAX_MEMORY_PROMPT_BLOCK_CHARS = 100_000


def _memory_render_digest(entry_id: str, fragment: str) -> str:
    payload = (f"{_MEMORY_RENDER_PROOF_SCHEMA}\0{entry_id}\0{fragment}").encode()
    return hashlib.sha256(payload).hexdigest()


def memory_rendered_proofs(
    result: object | None,
) -> tuple[tuple[str, str, str], ...]:
    """Return content-bound proofs for exact canonical rendered fragments."""
    proofs: list[tuple[str, str, str]] = []
    for memory in getattr(result, "rendered", ()) or ():
        entry_id = getattr(memory, "entry_id", "")
        category = getattr(memory, "category", "")
        content = getattr(memory, "content", "")
        if not all(isinstance(value, str) for value in (entry_id, category, content)):
            continue
        if not entry_id or not content:
            continue
        fragment = f"[{category}] {content}"
        if len(fragment) > _MAX_MEMORY_PROOF_FRAGMENT_CHARS:
            continue
        proofs.append((entry_id, _memory_render_digest(entry_id, fragment), fragment))
    return tuple(proofs)


def memory_authority_versions(
    result: object | None,
) -> tuple[tuple[str, str], ...]:
    """Project exact full-authority versions from one rendered result."""
    versions: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for memory in getattr(result, "rendered", ()) or ():
        entry_id = getattr(memory, "entry_id", "")
        digest = getattr(memory, "authority_digest", "")
        if (
            not isinstance(entry_id, str)
            or not entry_id
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            continue
        version = (entry_id, digest)
        if version not in seen:
            seen.add(version)
            versions.append(version)
    return tuple(versions)


def _normalized_authority_versions(
    raw_versions: object,
    *,
    allowed_ids: set[str] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Validate an untrusted receipt's bounded version pairs."""
    if not isinstance(raw_versions, (list, tuple)):
        return ()
    normalized: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_version in raw_versions:
        if not isinstance(raw_version, (list, tuple)) or len(raw_version) != 2:
            continue
        entry_id, digest = raw_version
        if (
            not isinstance(entry_id, str)
            or not entry_id
            or (allowed_ids is not None and entry_id not in allowed_ids)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            continue
        version = (entry_id, digest)
        if version not in seen:
            seen.add(version)
            normalized.append(version)
    return tuple(normalized)


def verified_receipt_memory_fragments(
    receipt: MemoryPromptReceipt,
) -> tuple[tuple[str, str, str], ...]:
    """Validate receipt triples against its exact pair contract and text."""
    if not isinstance(receipt, MemoryPromptReceipt):
        return ()
    expected = {
        (entry_id, digest)
        for entry_id, digest in receipt.rendered_proofs
        if isinstance(entry_id, str) and isinstance(digest, str)
    }
    verified: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw_proof in receipt.rendered_fragments:
        if not isinstance(raw_proof, (list, tuple)) or len(raw_proof) != 3:
            continue
        entry_id, digest, fragment = raw_proof
        if not all(isinstance(value, str) for value in (entry_id, digest, fragment)):
            continue
        proof = (entry_id, digest, fragment)
        if (
            (entry_id, digest) not in expected
            or not entry_id
            or not fragment
            or len(fragment) > _MAX_MEMORY_PROOF_FRAGMENT_CHARS
            or fragment not in receipt.prompt_text
            or _memory_render_digest(entry_id, fragment) != digest
            or proof in seen
        ):
            continue
        seen.add(proof)
        verified.append(proof)
    return tuple(verified)


def memory_prompt_block_proof(block: str) -> tuple[str, str]:
    """Bind one exact model-visible Memory block to tamper-evident metadata."""
    if not isinstance(block, str) or not block:
        return ("", "")
    if len(block) > _MAX_MEMORY_PROMPT_BLOCK_CHARS:
        return ("", "")
    payload = f"{_MEMORY_PROMPT_BLOCK_SCHEMA}\0{block}".encode()
    return hashlib.sha256(payload).hexdigest(), block


def _verified_memory_prompt_blocks(
    message: dict[str, Any],
) -> tuple[str, ...]:
    visible = str(message.get("content", ""))
    raw_blocks = message.get("_memory_prompt_blocks", ())
    if not isinstance(raw_blocks, (list, tuple)):
        return ()
    verified: list[str] = []
    for raw_block in raw_blocks:
        if not isinstance(raw_block, (list, tuple)) or len(raw_block) != 2:
            continue
        digest, block = raw_block
        if not isinstance(digest, str) or not isinstance(block, str):
            continue
        if not (
            "## Relevant Context from Memory\n\n" in block
            or block.startswith("## Relevant Durable Memory\n\n")
        ):
            continue
        expected_digest, _text = memory_prompt_block_proof(block)
        if expected_digest and digest == expected_digest and block in visible:
            verified.append(block)
    return tuple(dict.fromkeys(verified))


def _strip_canonical_memory_block(text: str) -> str:
    """Fail-closed fallback for generated task-start Memory text."""
    from minicode.memory_retrieval import (
        MEMORY_DIRECT_VERIFICATION_POLICY,
        MEMORY_INJECTION_FOOTER,
    )

    header = "## Relevant Context from Memory\n\n"
    search_from = 0
    while True:
        start = text.find(header, search_from)
        if start < 0:
            break
        end = text.find(MEMORY_INJECTION_FOOTER, start + len(header))
        if end < 0:
            # A marked prompt whose generated footer disappeared cannot be
            # trusted. Remove the remaining candidate block, never preserve
            # partially forged Memory as ordinary system text.
            text = text[:start].rstrip()
            break
        end += len(MEMORY_INJECTION_FOOTER)
        policy_suffix = "\n\n" + MEMORY_DIRECT_VERIFICATION_POLICY
        if text.startswith(policy_suffix, end):
            end += len(policy_suffix)
        removal_start = start
        while removal_start > 0 and text[removal_start - 1] == "\n":
            removal_start -= 1
        text = text[:removal_start] + text[end:]
        search_from = removal_start
    return text


def _remove_memory_prompt_metadata(message: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(message)
    for field_name in (
        "_memory_authority_revision",
        "_memory_prompt_blocks",
        "_memory_rendered_ids",
        "_memory_rendered_proofs",
    ):
        sanitized.pop(field_name, None)
    return sanitized


def _strip_hot_draft_message(message: dict[str, Any]) -> dict[str, Any]:
    """Remove the previous turn's ephemeral lane independently of authority."""
    sanitized = dict(message)
    block = sanitized.pop("_memory_draft_block", None)
    sanitized.pop("_memory_draft_ids", None)
    sanitized.pop("_memory_draft_expires_at", None)
    sanitized.pop("_memory_pending_draft_proofs", None)
    if isinstance(block, str) and block.startswith("## Recent draft notes\n\n"):
        visible = str(sanitized.get("content", ""))
        position = visible.rfind(block)
        if position >= 0:
            start = position
            while start > 0 and visible[start - 1] == "\n":
                start -= 1
            sanitized["content"] = visible[:start] + visible[position + len(block):]
    return sanitized


def _strip_materialized_memory_message(
    message: dict[str, Any],
) -> dict[str, Any] | None:
    """Remove generated Memory while retaining unrelated system content."""
    if message.get("_session_memory_context"):
        return None
    sanitized = dict(message)
    visible = str(sanitized.get("content", ""))
    blocks = _verified_memory_prompt_blocks(sanitized)
    for block in blocks:
        prefixed = "\n" + block
        position = visible.rfind(prefixed)
        if position >= 0:
            visible = visible[:position] + visible[position + len(prefixed) :]
            continue
        position = visible.rfind(block)
        if position >= 0:
            visible = visible[:position] + visible[position + len(block) :]
    # Metadata is a removal hint, never an authority grant. If it was absent,
    # malformed, or incomplete, remove the canonical generated section by its
    # bounded header/footer grammar.
    visible = _strip_canonical_memory_block(visible)
    sanitized["content"] = visible
    return _remove_memory_prompt_metadata(sanitized)


def verified_materialized_memory_fragments(
    messages: list[dict[str, Any]],
) -> tuple[tuple[str, str, str], ...]:
    """Validate prompt metadata against text that is actually model-visible."""
    verified: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for message in messages:
        if message.get("role") != "system":
            continue
        visible_text = str(message.get("content", ""))
        raw_proofs = message.get("_memory_rendered_proofs", ())
        if not isinstance(raw_proofs, (list, tuple)):
            continue
        for raw_proof in raw_proofs:
            if not isinstance(raw_proof, (list, tuple)) or len(raw_proof) != 3:
                continue
            entry_id, digest, fragment = raw_proof
            if not all(
                isinstance(value, str) for value in (entry_id, digest, fragment)
            ):
                continue
            if (
                not entry_id
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or not fragment
                or len(fragment) > _MAX_MEMORY_PROOF_FRAGMENT_CHARS
                or fragment not in visible_text
                or _memory_render_digest(entry_id, fragment) != digest
            ):
                continue
            proof = (entry_id, digest, fragment)
            if proof not in seen:
                seen.add(proof)
                verified.append(proof)
    return tuple(verified)


def verified_materialized_memory_proofs(
    messages: list[dict[str, Any]],
) -> tuple[tuple[str, str], ...]:
    """Return ID/digest pairs whose exact fragments are model-visible."""
    return tuple(
        (entry_id, digest)
        for entry_id, digest, _fragment in (
            verified_materialized_memory_fragments(messages)
        )
    )


# Durable signals that carry their own independent verification chain.
# Lessons wearing one of these are auto-approved on write; the rest still
# wait for explicit user review.
_CONVERSATION_FACT_TECHNICAL_TERMS = frozenset(
    {
        "代码",
        "文件",
        "函数",
        "类",
        "模块",
        "项目",
        "测试",
        "修复",
        "实现",
        "添加",
        "删除",
        "运行",
        "安装",
        "配置",
        "部署",
        "重构",
        "优化",
        "调试",
        "报错",
        "错误",
        "bug",
        "api",
        "python",
        "javascript",
        "typescript",
        "react",
        "docker",
        "git",
        "pytest",
        "sql",
    }
)
_CONVERSATION_FACT_ZH_MARKERS = (
    "是",
    "喜欢",
    "不喜欢",
    "讨厌",
    "爱",
    "住在",
    "在",
    "来自",
    "叫",
    "有",
    "想",
    "希望",
    "偏好",
    "使用",
    "唯一",
    "最好",
    "工作",
)

# Deterministic personal-facet classification for USER-scope observations.
# Curated patterns, most specific first; None means "not a profile facet"
# and the statement stays an ordinary conversation fact.  The classifier
# only decides WHERE an observation lands — approval always stays human.
_USER_FACET_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "compliance",
        re.compile(
            r"遵守|合规|等保|监管|审计要求|PCI|SOC\s?2|GDPR|HIPAA|规范要求",
            re.IGNORECASE,
        ),
    ),
    (
        "skill",
        re.compile(
            r"擅长|熟练|主要(写|用|开发)|skilled|proficient|expert\s+in",
            re.IGNORECASE,
        ),
    ),
    (
        "domain",
        re.compile(
            r"所在行业|负责.{0,16}(系统|业务|平台|服务)|电商|支付|清结算|风控|"
            r"金融科技|fintech|e-?commerce|广告|游戏|医疗",
            re.IGNORECASE,
        ),
    ),
    (
        "concern",
        re.compile(
            r"对.{0,16}敏感|经常排查|关注.{0,12}(性能|QPS|延迟|稳定性|成本)",
            re.IGNORECASE,
        ),
    ),
    (
        "preference",
        re.compile(r"我(喜欢|偏好|倾向)|prefer", re.IGNORECASE),
    ),
    (
        "role",
        re.compile(
            r"我是.{0,24}?(工程师|开发|架构师|经理|分析师|developer|engineer|"
            r"architect|manager)|职业是|职位是|i\s+am\s+an?\s",
            re.IGNORECASE,
        ),
    ),
)


def _user_facet_statement(task_description: str) -> str | None:
    """Extract a personal statement for facet observation.

    Mirrors the conversation-fact gate (first-person fact shape, short,
    non-imperative) but deliberately drops its technical-term exclusion:
    skill statements are made *of* technical terms ("我擅长Python和Go"),
    which is exactly the vocabulary that disqualifies them from the
    conversation-fact lane and exactly what a personal facet carries.
    """

    text = str(task_description or "").strip()
    if not 2 <= len(text) <= 200:
        return None
    if re.search(r"(?:帮我|请|帮忙|修复|实现|添加|删除|创建|生成|运行|安装)", text):
        return None
    is_zh_fact = "我" in text and any(
        marker in text for marker in _CONVERSATION_FACT_ZH_MARKERS
    )
    is_en_fact = bool(
        re.search(
            r"(?i)(?:^|[.!?]\s*)(?:i am|i'm|my |our |i like|i prefer|"
            r"i use|i live in|i work|i want)\b",
            text,
        )
    )
    if not (is_zh_fact or is_en_fact):
        return None
    return text.rstrip("。.!！?？ ")


def _extract_user_preference(task_description: str) -> str | None:
    """Extract a short first-person statement without its accompanying command."""
    pattern = re.compile(
        r"^(?:我(?:叫|是|(?:更)?(?:喜欢|偏好)|(?:习惯)?用)|"
        r"我希望你(?:称呼|叫|用|使用|回复|回答)|"
        r"(?:i am|i'm|my name is|i prefer|i like)\b|"
        r"i want you to\s+(?:call me|reply|respond|speak|use)\b)",
        re.IGNORECASE,
    )
    for sentence in re.split(r"[。！？!?\n;；]|(?<!\w)\.(?:\s|$)|(?<=\w)\.\s+", str(task_description or "")):
        # A request after a comma is a separate task, not part of the preference.
        statement = re.split(
            r"[,，]\s*(?:请|帮我|帮忙|然后|修复|实现|添加|删除|创建|生成|运行|安装|"
            r"please\b|fix\b|implement\b|create\b|run\b|install\b)",
            sentence, maxsplit=1, flags=re.IGNORECASE,
        )[0].strip().rstrip("。. ")
        if not 2 <= len(statement) <= 200 or not pattern.search(statement):
            continue
        if re.search(r"(?:帮我|帮忙|修复|实现|删除|创建|生成|安装)|\b(?:fix|implement|delete|install)\b", statement, re.IGNORECASE):
            continue
        return statement
    return None


def _classify_user_facet(text: str) -> str | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    # One statement can carry several signals ("我是资深后端,主要写Go") —
    # the earliest match is the sentence's subject and wins.
    best: tuple[int, str] | None = None
    for facet_key, pattern in _USER_FACET_PATTERNS:
        match = pattern.search(normalized)
        if match is not None and (best is None or match.start() < best[0]):
            best = (match.start(), facet_key)
    return best[1] if best is not None else None

_STRONG_DURABLE_SIGNALS = frozenset(
    {
        "confirmed_error_recovery_verified",
        "verified_solution",
        "verified_approach",
        "stable_project_constraint",
        "stable_verification_rule",
        "user_correction",
        "old_memory_disproved",
    }
)


def assess_trace_memory_safety(execution_trace: list[dict[str, Any]]):
    """Scan untrusted trace text before any automatic reflection write."""
    from minicode.memory import (
        MemorySafetyResult,
        assess_memory_safety,
        persistence_text_contains_secret,
    )
    from minicode.reflection_evidence import TRACE_MAX_EVENTS

    def iter_strings(value: Any, depth: int = 0):
        if depth > 4:
            return
        if isinstance(value, str):
            yield value[:2000]
        elif isinstance(value, dict):
            for nested in value.values():
                yield from iter_strings(nested, depth + 1)
        elif isinstance(value, list):
            for nested in value[:20]:
                yield from iter_strings(nested, depth + 1)

    for event in execution_trace[:TRACE_MAX_EVENTS]:
        for text in iter_strings(event):
            # Empty strings in structured tool input represent an omitted or
            # root/default argument (for example ``list_files(path="")``).
            # They contain no untrusted instruction text to assess.  Keep the
            # stricter empty-content rejection in ``assess_memory_safety`` for
            # actual durable Memory bodies.
            if not text.strip():
                continue
            if persistence_text_contains_secret(text):
                return MemorySafetyResult(
                    "suspicious",
                    "trace contains credential-like text requiring redaction",
                    "medium",
                )
            safety = assess_memory_safety(text, source="trace")
            if not safety.allowed:
                return MemorySafetyResult(
                    "suspicious",
                    f"trace contains untrusted unsafe-looking text: {safety.reason}",
                    "medium",
                )
    return MemorySafetyResult("safe", "", "low")


class MemoryPipeline:
    """Unified memory operations facade.

    Usage:
        pipeline = MemoryPipeline(memory_manager)
        pipeline.initialize(model_adapter, workspace_path)

        # On task start
        memories = pipeline.read("Create login form", ["src/Login.tsx"])
        messages = pipeline.inject("Create login form", ["src/Login.tsx"], messages)

        # On task end
        pipeline.write("Create login form", execution_trace)

        # Background (every ~10 tasks)
        report = pipeline.maintain()
    """

    def __init__(
        self,
        memory_manager: Any | None = None,
        *,
        hybrid_provider_factory: Any | None = None,
        injection_controller: MemoryInjectionController | None = None,
        retrieval_detail_level: RetrievalDetailLevel | str | None = None,
        contribution_reviewer: Any | None = None,
    ):
        from minicode.memory_retrieval import RetrievalDetailLevel

        self._memory = memory_manager
        self._workspace: str | None = None

        # Subsystems (lazy init via initialize())
        self._controller = injection_controller
        self._curator: Any = None
        self._curator_schedule: Any = None
        self._reflection: Any = None
        self._retriever: Any = None
        self._max_memories = _DEFAULT_MAX_MEMORIES
        self._min_relevance = _DEFAULT_MIN_RELEVANCE
        self._max_tokens_per_memory = _DEFAULT_MAX_TOKENS_PER_MEMORY

        self._initialized = False
        self._read_count = 0
        self._write_count = 0
        self._maintain_count = 0
        self._last_injected_ids: list[str] = []
        self._last_injected_draft_ids: tuple[str, ...] = ()
        self._hot_store: Any = None
        self._last_injected_versions: list[tuple[str, str]] = []
        self._committed_receipt_digests: dict[str, str] = {}
        self._durably_accounted_receipt_digests: dict[str, str] = {}
        self._receipt_materialized_ids: dict[str, tuple[str, ...]] = {}
        self._feedback_delivery_nonce = uuid.uuid4().hex
        self._last_committed_receipt: MemoryPromptReceipt | None = None
        self._last_written_ids: list[str] = []
        self._last_written_versions: list[tuple[str, str]] = []
        self._last_write_result: MemoryWriteResult | None = None
        self._last_retrieval_result: Any = None
        self._last_injection_decision: MemoryInjectionDecision | None = None
        self._feedback_recorded = False
        self._contribution_reviewer = contribution_reviewer
        self._contribution_review_recorded = False
        self._project_facts: Any = None
        self._hybrid_activation: Any = None
        self._hybrid_verifier_binding = "not_applicable"
        self._hybrid_provider_factory = hybrid_provider_factory
        self._retrieval_detail_level = (
            RetrievalDetailLevel.FULL
            if retrieval_detail_level is None
            else (
                retrieval_detail_level
                if isinstance(retrieval_detail_level, RetrievalDetailLevel)
                else RetrievalDetailLevel(str(retrieval_detail_level))
            )
        )

    # ── Lifecycle ──────────────────────────────────────────────────

    def initialize(
        self,
        model_adapter: Any | None = None,
        workspace_path: str | None = None,
        enable_reranker: bool = False,
        enable_vector: bool = False,
        reflection_engine: Any | None = None,
        hybrid_model_path: str | Path | None = None,
        hybrid_evidence_path: str | Path | None = None,
        hybrid_embedding_provider: str = "local-e5",
        allow_remote_memory_embedding: bool = False,
        hybrid_verifier_binding: str = "",
        _hybrid_evaluation_bootstrap: bool = False,
    ) -> None:
        """Initialize all subsystems. Call once after MemoryManager is ready."""
        if enable_reranker:
            raise ValueError(
                "enable_reranker cannot be enabled: it is not part of "
                "canonical retrieval"
            )
        from minicode.memory_hybrid import assess_hybrid_activation

        self._hybrid_activation = assess_hybrid_activation(
            requested=enable_vector,
            evidence_path=hybrid_evidence_path,
            model_path=hybrid_model_path,
            embedding_provider=hybrid_embedding_provider,
            allow_remote_embedding=allow_remote_memory_embedding,
            _evaluation_bootstrap=_hybrid_evaluation_bootstrap,
        )
        self._workspace = workspace_path
        self._hybrid_verifier_binding = str(hybrid_verifier_binding).strip()[:64] or (
            "configured_match" if self._hybrid_activation.active else "not_applicable"
        )

        # Deterministic project facts live outside the lesson pipeline.
        if workspace_path:
            from minicode.project_facts import ProjectFactsStore

            self._project_facts = ProjectFactsStore(workspace_path)

        # The Pipeline owns the single production controller/retriever pair.
        # ``MemoryInjector`` remains a standalone compatibility facade only.
        if self._memory:
            from minicode.memory_injector import MemoryInjectionController
            from minicode.memory_retrieval import CanonicalMemoryRetriever

            if self._controller is None:
                self._controller = MemoryInjectionController()
            self._retriever = CanonicalMemoryRetriever(
                self._memory,
                controller=self._controller,
                detail_level=self._retrieval_detail_level,
            )
            hybrid_runtime_required = bool(self._hybrid_activation.active)
            if self._hybrid_activation.active:
                if model_adapter is None:
                    self._hybrid_activation = replace(
                        self._hybrid_activation,
                        active=False,
                        reason="verifier_adapter_unavailable",
                    )

                if self._hybrid_activation.active:
                    factory = self._hybrid_provider_factory
                    using_default_factory = factory is None
                    if factory is None:
                        try:
                            from minicode.memory_hybrid_runtime import (
                                create_hybrid_candidate_provider,
                            )

                            factory = create_hybrid_candidate_provider
                        except Exception:
                            factory = None
                    try:
                        provider = (
                            factory(
                                activation=self._hybrid_activation,
                                model_adapter=model_adapter,
                                workspace_path=workspace_path,
                                **({"strict": True} if using_default_factory else {}),
                            )
                            if factory is not None
                            else None
                        )
                    except Exception as exc:
                        provider = None
                        initialization_reason = str(
                            getattr(exc, "reason", "provider_initialization_failed")
                        )[:96]
                    else:
                        initialization_reason = "provider_initialization_failed"
                    if provider is None:
                        self._hybrid_activation = replace(
                            self._hybrid_activation,
                            active=False,
                            reason=initialization_reason,
                        )
                    else:
                        self._retriever = CanonicalMemoryRetriever(
                            self._memory,
                            controller=self._controller,
                            hybrid_provider=provider,
                            detail_level=self._retrieval_detail_level,
                        )
            if hybrid_runtime_required and not self._hybrid_activation.active:
                self._retriever = CanonicalMemoryRetriever(
                    self._memory,
                    controller=self._controller,
                    hybrid_provider=_FailClosedHybridProvider(),
                    detail_level=self._retrieval_detail_level,
                )

        # Curator (background optimization)
        from minicode.memory_curator_agent import MemoryCuratorAgent

        self._curator = MemoryCuratorAgent(
            memory_manager=self._memory,
            model_adapter=model_adapter,
            workspace_path=workspace_path,
        )
        if workspace_path:
            try:
                from minicode.curator_schedule import CuratorScheduleStore

                self._curator_schedule = CuratorScheduleStore(workspace_path)
            except Exception:
                # Scheduling is an authority boundary: if its durable store
                # cannot even be constructed, non-forced maintenance must
                # abstain instead of falling back to a volatile counter.
                self._curator_schedule = None

        # Reflection engine (write path)
        if reflection_engine is not None:
            self._reflection = reflection_engine
        else:
            from minicode.agent_reflection import ReflectionEngine

            self._reflection = ReflectionEngine(memory_manager=None)

        # Contribution reviewer (per-memory evidence-cited feedback: the last
        # committed prompt receipt read against the execution trace).
        if self._contribution_reviewer is None:
            from minicode.memory_contribution_reviewer import (
                ContributionReviewConfig,
                MemoryContributionReviewer,
            )

            review_config = ContributionReviewConfig.from_runtime()
            review_client = None
            if review_config.mode == "llm" and model_adapter is not None:
                from minicode.reflection_llm import (
                    ModelAdapterStructuredGenerationClient,
                )

                review_client = ModelAdapterStructuredGenerationClient(model_adapter)
            self._contribution_reviewer = MemoryContributionReviewer(
                review_config,
                client=review_client,
            )

        self._initialized = True
        self._last_injected_ids: list[str] = []
        self._last_injected_versions = []
        self._committed_receipt_digests = {}
        self._durably_accounted_receipt_digests = {}
        self._receipt_materialized_ids = {}
        self._feedback_delivery_nonce = uuid.uuid4().hex
        self._last_committed_receipt = None
        self._last_retrieval_result = None
        self._last_injection_decision = None
        self._feedback_recorded = False
        self._contribution_review_recorded = False
        logger.info("MemoryPipeline initialized: canonical_retrieval=true")

        # Restore persisted state
        self._load_state()

    # ── State persistence ────────────────────────────────────────────

    def _state_path(self) -> str | None:
        """Path for pipeline state file."""
        if not self._workspace:
            return None
        return os.path.join(self._workspace, ".mini-code-memory", "pipeline_state.json")

    def save_state(self) -> None:
        """Persist pipeline state to disk (cache stats, counters, curator history)."""
        path = self._state_path()
        if not path:
            return
        try:
            import tempfile

            target = Path(path)
            parent = target.parent
            if target.is_symlink() or parent.is_symlink():
                logger.warning(
                    "MemoryPipeline save_state skipped: unsafe symlink path %s",
                    target,
                )
                return
            os.makedirs(parent, exist_ok=True)
            state = {
                "schema_version": _PIPELINE_STATE_SCHEMA_VERSION,
                "read_count": self._read_count,
                "write_count": self._write_count,
                "maintain_count": self._maintain_count,
                "timestamp": time.time(),
            }
            fd, tmp_path = tempfile.mkstemp(
                dir=str(parent),
                prefix=".pipeline_state.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
                os.replace(tmp_path, target)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.debug("MemoryPipeline save_state failed: %s", e)

    def _load_state(self) -> None:
        """Restore pipeline state from disk."""
        path = self._state_path()
        if not path:
            return
        state = _read_pipeline_state(Path(path))
        if state is None:
            self._read_count = 0
            self._write_count = 0
            self._maintain_count = 0
            return
        self._read_count = state["read_count"]
        self._write_count = state["write_count"]
        self._maintain_count = state["maintain_count"]
        logger.debug(
            "MemoryPipeline: restored state (%d reads, %d writes)",
            self._read_count,
            self._write_count,
        )

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def injection_controller(self) -> MemoryInjectionController | None:
        """The controller shared by every canonical Pipeline retrieval."""
        return self._controller

    @property
    def last_injection_decision(self) -> MemoryInjectionDecision | None:
        """Most recent structured controller decision, if retrieval ran."""
        return self._last_injection_decision

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "read_count": self._read_count,
            "write_count": self._write_count,
            "maintain_count": self._maintain_count,
            # Deprecated compatibility keys. Canonical retrieval has no
            # separate reranker/vector authority.
            "reranker_enabled": False,
            "reranker_cache_hit_rate": 0.0,
            "vector_enabled": False,
            "hybrid_requested": bool(
                self._hybrid_activation and self._hybrid_activation.requested
            ),
            "hybrid_active": bool(
                self._hybrid_activation and self._hybrid_activation.active
            ),
            "hybrid_inactive_reason": (
                self._hybrid_activation.reason
                if self._hybrid_activation and not self._hybrid_activation.active
                else ""
            ),
            "hybrid_fallback": bool(
                self._hybrid_activation
                and self._hybrid_activation.requested
                and not self._hybrid_activation.active
            ),
            "hybrid_verifier_binding": self._hybrid_verifier_binding,
        }

    def _attach_hybrid_runtime_diagnostics(self, result: Any) -> Any:
        """Join activation state to a content-free retrieval observation."""
        from dataclasses import replace

        diagnostics = dict(getattr(result, "diagnostics", {}) or {})
        retrieval = diagnostics.get("hybrid", {})
        retrieval = retrieval if isinstance(retrieval, dict) else {}
        provider = retrieval.get("provider", {})
        provider = provider if isinstance(provider, dict) else {}
        requested = bool(self._hybrid_activation and self._hybrid_activation.requested)
        active = bool(self._hybrid_activation and self._hybrid_activation.active)
        adjudication_fallback = bool(retrieval.get("fallback"))
        reason = "not_requested"
        if requested and not active:
            reason = str(self._hybrid_activation.reason or "inactive")[:96]
        elif requested and adjudication_fallback:
            reason = str(retrieval.get("failure_reason") or "provider_fail_closed")[:96]
        elif requested:
            reason = "activated"
        diagnostics["hybrid_runtime"] = {
            "requested": requested,
            "active": active,
            "fallback": bool(requested and (not active or adjudication_fallback)),
            "reason": reason,
            "embedding_provider": str(
                getattr(self._hybrid_activation, "embedding_provider", "") or ""
            )[:32],
            "verifier_binding": self._hybrid_verifier_binding,
            "provider_cache_reused": bool(provider.get("provider_cache_reused", False)),
            "adjudication_cache_hit": bool(provider.get("cache_hit", False)),
        }
        updated = replace(result, diagnostics=diagnostics)
        project = getattr(updated, "with_detail_level", None)
        detail_level = getattr(updated, "detail_level", None)
        if callable(project) and detail_level is not None:
            return project(detail_level)
        return updated

    # ── READ: Memory retrieval ─────────────────────────────────────

    def read(
        self,
        task_description: str,
        current_files: list[str] | None = None,
        active_domains: list[str] | None = None,
        max_results: int = 15,
        *,
        max_total_tokens: int = 1200,
        max_tokens_per_memory: int | None = None,
        context_usage: float = 0.5,
        min_relevance: float | None = None,
        source_entrypoint: Any = None,
        recent_failure: bool = False,
        _record_retrieval: bool = True,
        observation_id: str | None = None,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run canonical retrieval without modifying a prompt or recording injection."""
        if not self._memory or not self._retriever:
            self._last_retrieval_result = None
            return []

        self._read_count += 1
        from minicode.memory_retrieval import MemoryRetrievalRequest, RetrievalSource

        source = source_entrypoint or RetrievalSource.PIPELINE_READ
        per_memory = (
            max_tokens_per_memory
            if max_tokens_per_memory is not None
            else self._max_tokens_per_memory
        )
        relevance = min_relevance if min_relevance is not None else self._min_relevance
        result = self._retriever.retrieve(
            MemoryRetrievalRequest(
                query=task_description,
                current_files=tuple(current_files or ()),
                active_domains=tuple(active_domains or ()),
                context_usage=context_usage,
                max_memories=max_results,
                max_total_tokens=max_total_tokens,
                max_tokens_per_memory=per_memory,
                min_relevance=relevance,
                source_entrypoint=source,
                recent_failure=recent_failure,
            ),
            agent_budget=agent_budget,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )
        result = self._attach_hybrid_runtime_diagnostics(result)
        self._last_retrieval_result = result
        from minicode.memory_injector import (
            MemoryInjectionDecision,
            MemoryInjectionMode,
        )

        controller = result.controller_decision
        self._last_injection_decision = MemoryInjectionDecision(
            mode=MemoryInjectionMode(controller.get("mode", "none")),
            max_memories=int(controller.get("max_memories", 0)),
            min_relevance=float(controller.get("min_relevance", 1.0)),
            max_tokens_per_memory=int(controller.get("max_tokens_per_memory", 0)),
            reasons=list(controller.get("reasons", [])),
        )
        if (
            _record_retrieval
            and result.selected_ids
            and hasattr(self._memory, "record_retrievals")
        ):
            _call_memory_accounting(
                self._memory.record_retrievals,
                list(result.selected_ids),
                observation_id=observation_id or uuid.uuid4().hex,
            )
        return [
            {
                "id": memory.entry_id,
                "content": memory.content,
                "domain": list(result.diagnostics.get("active_domains", [])),
                "relevance": memory.score.final_score,
                "score": memory.score.to_dict(),
                "scope": memory.scope,
                "category": memory.category,
                "source": memory.source,
                "reason_codes": list(memory.reason_codes),
            }
            for memory in result.rendered
        ]

    def prepare_compaction_context(
        self,
        task_description: str,
        *,
        max_total_tokens: int = 6000,
        already_materialized_ids: tuple[str, ...] = (),
        already_materialized_proofs: tuple[tuple[str, str], ...] = (),
    ) -> MemoryPromptReceipt:
        """Retrieve a structured prompt candidate for Session Memory compact.

        Retrieval accounting happens here. Injection accounting is deferred
        until the compaction caller proves the returned prompt was actually
        materialized in the active message list.
        """
        if not self._initialized or not self._memory or not self._retriever:
            return MemoryPromptReceipt()

        from minicode.memory_retrieval import RetrievalSource

        receipt_id = uuid.uuid4().hex
        self.read(
            task_description,
            max_results=20,
            max_total_tokens=max_total_tokens,
            max_tokens_per_memory=max_total_tokens,
            context_usage=0.0,
            source_entrypoint=RetrievalSource.MANAGER_CONTEXT,
            _record_retrieval=True,
            observation_id=receipt_id,
        )
        result = self._last_retrieval_result
        if result is None:
            return MemoryPromptReceipt()
        # ``already_materialized_ids`` remains accepted for compatibility but
        # is intentionally not authoritative. A bare ID neither proves that
        # text is visible nor that it matches the current approved content.
        del already_materialized_ids
        verified_proofs: set[tuple[str, str]] = set()
        for raw_proof in already_materialized_proofs:
            if not isinstance(raw_proof, (list, tuple)) or len(raw_proof) != 2:
                continue
            entry_id, digest = raw_proof
            if (
                isinstance(entry_id, str)
                and entry_id
                and isinstance(digest, str)
                and len(digest) == 64
            ):
                verified_proofs.add((entry_id, digest))
        current_proofs = memory_rendered_proofs(result)
        current_digest_by_id = {
            entry_id: digest for entry_id, digest, _fragment in current_proofs
        }
        reused_ids = tuple(
            entry_id
            for entry_id in result.rendered_ids
            if (entry_id, current_digest_by_id.get(entry_id, "")) in verified_proofs
        )
        if reused_ids:
            suppress_rendered = getattr(
                self._retriever,
                "suppress_rendered_ids",
                None,
            )
            if callable(suppress_rendered):
                result = suppress_rendered(
                    result,
                    reused_ids,
                    reason="already_materialized_this_turn",
                )
                self._last_retrieval_result = result
            else:
                # A compatibility retriever cannot prove that its text can be
                # partitioned exactly, so fail closed instead of duplicating it.
                result = result.without_rendered("already_materialized_this_turn")
                self._last_retrieval_result = result
        if not result.prompt_text and not reused_ids:
            return MemoryPromptReceipt()
        return MemoryPromptReceipt(
            prompt_text=result.prompt_text,
            selected_ids=tuple(dict.fromkeys(result.selected_ids)),
            rendered_ids=tuple(dict.fromkeys(result.rendered_ids)),
            rendered_versions=memory_authority_versions(result),
            authority_revision=result.authority_revision,
            receipt_id=receipt_id,
            rendered_proofs=tuple(
                (entry_id, digest)
                for entry_id, digest, _fragment in memory_rendered_proofs(result)
            ),
            rendered_fragments=memory_rendered_proofs(result),
            reused_ids=reused_ids,
        )

    # ── INJECT: Memory into prompt ──────────────────────────────────

    def commit_prompt_receipt(
        self,
        receipt: MemoryPromptReceipt,
        *,
        record_retrieval: bool = False,
    ) -> tuple[str, ...]:
        """Commit one materialized prompt receipt to this Turn's ledger.

        The method is intentionally separate from retrieval so an ineffective
        or discarded compaction can never inflate injection/feedback counts.
        Repeated materialization of the same memory during one Turn is
        idempotent.  The volatile Turn ledger is authoritative for later
        feedback and is updated *before* best-effort durable counters: once a
        prompt has influenced the model, a store-write failure must not make
        that influence disappear from the feedback loop.

        ``record_retrieval`` is used by task-start injection, where retrieval
        accounting was deliberately deferred until prompt materialization.
        Compaction receipts have already recorded retrieval and leave it false.
        """
        if not isinstance(receipt, MemoryPromptReceipt):
            raise TypeError("receipt must be a MemoryPromptReceipt")
        if not self._memory or not receipt.prompt_text or not receipt.rendered_ids:
            return ()
        receipt_key = (
            receipt.receipt_id if receipt.receipt_id else f"legacy-object-{id(receipt)}"
        )
        receipt_digest = _prompt_receipt_commit_digest(
            receipt,
            record_retrieval=record_retrieval,
        )
        committed_digest = self._committed_receipt_digests.get(receipt_key)
        if committed_digest is not None:
            if committed_digest != receipt_digest:
                raise ValueError(
                    "receipt_id was already committed with a different payload"
                )
        durable_digest = self._durably_accounted_receipt_digests.get(receipt_key)
        if durable_digest is not None:
            if durable_digest != receipt_digest:
                raise ValueError(
                    "receipt_id was already accounted with a different payload"
                )
            return ()
        is_volatile_replay = committed_digest is not None
        if self._feedback_recorded and not is_volatile_replay:
            logger.warning(
                "Memory receipt ignored because Turn feedback is already final"
            )
            return ()
        if is_volatile_replay:
            newly_materialized = self._receipt_materialized_ids.get(
                receipt_key,
                (),
            )
        else:
            # Mark the model-visible commit before durable accounting. A
            # failed root publication may be retried through the separate
            # durable ledger without duplicating this volatile provenance.
            self._committed_receipt_digests[receipt_key] = receipt_digest
            self._last_committed_receipt = receipt

            rendered_id_set = {
                entry_id
                for entry_id in receipt.rendered_ids
                if isinstance(entry_id, str) and entry_id
            }
            receipt_versions = _normalized_authority_versions(
                receipt.rendered_versions,
                allowed_ids=rendered_id_set,
            )
            existing_versions = set(self._last_injected_versions)
            self._last_injected_versions.extend(
                version
                for version in receipt_versions
                if version not in existing_versions
            )

            existing = set(self._last_injected_ids)
            newly_materialized = tuple(
                entry_id
                for entry_id in dict.fromkeys(receipt.rendered_ids)
                if isinstance(entry_id, str) and entry_id and entry_id not in existing
            )
            self._receipt_materialized_ids[receipt_key] = newly_materialized
            if newly_materialized:
                # This ledger records what actually reached the prompt. Never
                # place it after persistent accounting: disk contention may
                # fail independently after model-visible mutation succeeded.
                self._last_injected_ids.extend(newly_materialized)

        selected_ids = tuple(
            entry_id
            for entry_id in dict.fromkeys(receipt.selected_ids)
            if isinstance(entry_id, str) and entry_id
        )
        if record_retrieval:
            record_both = getattr(
                self._memory,
                "record_retrievals_and_injections",
                None,
            )
            if callable(record_both):
                _call_memory_accounting(
                    record_both,
                    list(selected_ids),
                    list(newly_materialized),
                    observation_id=receipt_key,
                )
            else:
                record_retrievals = getattr(
                    self._memory,
                    "record_retrievals",
                    None,
                )
                if callable(record_retrievals) and selected_ids:
                    _call_memory_accounting(
                        record_retrievals,
                        list(selected_ids),
                        observation_id=receipt_key,
                    )
                if newly_materialized:
                    record_injections = getattr(
                        self._memory,
                        "record_injections",
                        None,
                    )
                    if callable(record_injections):
                        _call_memory_accounting(
                            record_injections,
                            list(newly_materialized),
                            observation_id=receipt_key,
                        )
                    else:
                        logger.warning(
                            "Memory injection counter unavailable; Turn ledger remains authoritative"
                        )
        elif newly_materialized:
            record_injections = getattr(self._memory, "record_injections", None)
            if callable(record_injections):
                _call_memory_accounting(
                    record_injections,
                    list(newly_materialized),
                    observation_id=receipt_key,
                )
            else:
                logger.warning(
                    "Memory injection counter unavailable; Turn ledger remains authoritative"
                )

        self._durably_accounted_receipt_digests[receipt_key] = receipt_digest
        return () if is_volatile_replay else newly_materialized

    def materialize_prompt_receipt(
        self,
        receipt: MemoryPromptReceipt,
        materialize: Callable[[], None],
        rollback: Callable[[], None],
    ) -> bool:
        """Validate authority and append one receipt while holding a short fence.

        Expensive retrieval has already completed outside the store lock.  The
        callback must only perform the in-memory prompt mutation; persistent
        accounting remains a separate post-materialization commit. ``rollback``
        must restore the exact pre-callback prompt if the snapshot context
        reports an error while exiting after mutation. Callers should make the
        rollback idempotent because an outer compatibility guard may repeat it.
        """
        if not isinstance(receipt, MemoryPromptReceipt):
            raise TypeError("receipt must be a MemoryPromptReceipt")
        if not callable(materialize):
            raise TypeError("materialize must be callable")
        if not callable(rollback):
            raise TypeError("rollback must be callable")
        if (
            not self._memory
            or not receipt.prompt_text
            or not receipt.rendered_ids
            or not receipt.authority_revision
        ):
            return False
        snapshot = getattr(self._memory, "retrieval_snapshot", None)
        if not callable(snapshot):
            return False
        materialization_started = False
        try:
            with snapshot() as snapshot_revision:
                authority_token = getattr(
                    self._memory,
                    "prompt_authority_revision",
                    None,
                )
                current_revision = (
                    str(authority_token())
                    if callable(authority_token)
                    else str(snapshot_revision)
                )
                if current_revision != receipt.authority_revision:
                    logger.info(
                        "Memory prompt receipt invalidated before materialization"
                    )
                    return False
                materialization_started = True
                materialize()
        except BaseException:
            if materialization_started:
                try:
                    rollback()
                except Exception:
                    logger.critical(
                        "Memory prompt rollback failed after authority error",
                        exc_info=True,
                    )
            raise
        return True

    def revalidate_prompt_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Revoke stale model-visible Memory before the next provider call.

        Prompt relevance is decided by canonical retrieval at Turn start. This
        method deals only with authority validity: an unchanged revision keeps
        the current Turn's Memory, while any changed/unavailable authority
        removes generated Memory fail-closed.
        """
        from minicode.memory_deposition_experiment import deposition_mode

        pending_proofs_valid = True
        pending_messages = [
            message for message in messages
            if message.get("_memory_pending_draft_proofs")
        ]
        if pending_messages:
            try:
                self._memory.refresh_if_stale()
                pending_proofs_valid = all(
                    self._pending_draft_proofs_current(
                        message["_memory_pending_draft_proofs"]
                    )
                    for message in pending_messages
                )
            except Exception:
                logger.debug("Pending draft authority revalidation failed", exc_info=True)
                pending_proofs_valid = False

        copied = []
        for message in messages:
            expiry = message.get("_memory_draft_expires_at")
            if "_memory_draft_block" in message and (
                deposition_mode() != "inject"
                or not isinstance(expiry, (int, float))
                or not math.isfinite(expiry)
                or not expiry > time.time()
                or (
                    message.get("_memory_pending_draft_proofs")
                    and not pending_proofs_valid
                )
            ):
                copied.append(_strip_hot_draft_message(message))
            else:
                copied.append(dict(message))
        marked = any(
            message.get("role") == "system"
            and (
                message.get("_memory_authority_revision") is not None
                or message.get("_memory_prompt_blocks") is not None
                or message.get("_session_memory_context")
            )
            for message in copied
        )
        if not marked:
            return copied

        current_revision = ""
        try:
            if self._memory is not None:
                refresh = getattr(self._memory, "refresh_if_stale", None)
                if callable(refresh):
                    refresh()
                revision = getattr(
                    self._memory,
                    "prompt_authority_revision",
                    None,
                )
                if callable(revision):
                    current_revision = str(revision())
        except Exception:
            logger.warning(
                "Memory authority revalidation failed; stripping prompt Memory",
                exc_info=True,
            )
            current_revision = ""

        revalidated: list[dict[str, Any]] = []
        for message in copied:
            if message.get("role") != "system":
                revalidated.append(message)
                continue
            stored_revision = message.get("_memory_authority_revision")
            is_marked = (
                stored_revision is not None
                or message.get("_memory_prompt_blocks") is not None
                or message.get("_session_memory_context")
            )
            if not is_marked or (
                isinstance(stored_revision, str)
                and stored_revision
                and stored_revision == current_revision
            ):
                revalidated.append(message)
                continue
            sanitized = _strip_materialized_memory_message(message)
            if sanitized is not None:
                revalidated.append(sanitized)
        return revalidated

    @property
    def injected_memory_ids(self) -> tuple[str, ...]:
        """Exact de-duplicated Memory IDs materialized in the current Turn."""
        return tuple(self._last_injected_ids)

    @property
    def injected_memory_versions(self) -> tuple[tuple[str, str], ...]:
        """Exact authority versions materialized in the current Turn."""
        return tuple(self._last_injected_versions)

    @property
    def injected_draft_ids(self) -> tuple[str, ...]:
        """Ephemeral prompt attribution, without granting durable authority."""
        return self._last_injected_draft_ids

    def _memory_hot_store(self) -> Any:
        if self._hot_store is None and os.environ.get("MINI_CODE_MEMORY_REDIS_URL", "").strip():
            from minicode.memory_hot_store import get_memory_hot_store

            self._hot_store = get_memory_hot_store(
                getattr(self._memory, "workspace", None) or self._workspace or Path.cwd()
            )
        return self._hot_store

    def _pending_draft_candidates(self) -> list[tuple[MemoryEntry, float, bool]]:
        """Read eligible pending notes without changing their approval authority.

        Explicit declared-dependency drafts precede ordinary pending notes
        whose review has aged out. Both remain excluded from canonical recall.
        """
        from minicode.memory import (
            MemoryScope,
            MemoryTier,
            _approval_hash_for_entry,
            assess_memory_safety,
        )
        from minicode.memory_authority_io import is_memory_entry_id
        from minicode.memory_deposition_experiment import (
            deposition_feature_enabled,
            deposition_mode,
            draft_ttl_seconds,
            pending_draft_age_seconds,
        )

        if deposition_mode() != "inject" or self._memory is None:
            return []
        memory = self._memory.memories.get(MemoryScope.PROJECT)
        if memory is None:
            return []
        now = time.time()
        declared: list[tuple[MemoryEntry, float, bool]] = []
        aged: list[tuple[MemoryEntry, float, bool]] = []
        for entry in memory.entries:
            try:
                if (
                    not is_memory_entry_id(entry.id)
                    or entry.scope != MemoryScope.PROJECT
                    or entry.approval_status != "pending"
                    or entry.safety_status != "safe"
                    or entry.lifecycle_status != "active"
                    or entry.curator_locked
                    or entry.tier == MemoryTier.ARCHIVAL
                    or not entry._authority_state_is_current()
                    or (
                        entry.approval_content_hash
                        and entry.approval_content_hash != _approval_hash_for_entry(entry)
                    )
                    or not assess_memory_safety(entry.content, source=entry.source).allowed
                ):
                    continue
                if "deposition_draft" in entry.metadata:
                    draft = entry.metadata["deposition_draft"]
                    if not isinstance(draft, dict):
                        continue
                    expiry = draft.get("expires_at")
                    if (
                        deposition_feature_enabled("declared_dependencies")
                        and draft.get("injectable") is True
                        and "declared_dependency" in draft.get("reason_codes", [])
                        and isinstance(expiry, (int, float))
                        and not isinstance(expiry, bool)
                        and math.isfinite(expiry)
                        and expiry > now
                    ):
                        declared.append((entry, float(expiry), False))
                elif (
                    deposition_feature_enabled("pending_drafts")
                    and isinstance(entry.updated_at, (int, float))
                    and not isinstance(entry.updated_at, bool)
                    and math.isfinite(entry.updated_at)
                    and now - entry.updated_at > pending_draft_age_seconds()
                ):
                    aged.append((entry, now + draft_ttl_seconds(), True))
            except (AttributeError, TypeError, ValueError):
                # Malformed optional notes cannot suppress the normal prompt.
                continue
        return declared + aged

    def _pending_draft_proofs_current(self, proofs: object) -> bool:
        """Bind the optional prompt lane to still-eligible pending versions."""
        from minicode.memory import _approval_hash_for_entry

        if not isinstance(proofs, (list, tuple)) or not proofs:
            return False
        current = {
            entry.id: _approval_hash_for_entry(entry)
            for entry, _expiry, _aged in self._pending_draft_candidates()
        }
        return all(
            isinstance(proof, (list, tuple))
            and len(proof) == 2
            and isinstance(proof[0], str)
            and isinstance(proof[1], str)
            and current.get(proof[0]) == proof[1]
            for proof in proofs
        )

    def _render_hot_drafts(
        self,
        lesson_block: str,
        result: Any,
        *,
        max_memories: int,
        max_total_tokens: int,
        max_tokens_per_memory: int,
    ) -> tuple[str, tuple[str, ...], float]:
        from minicode.context_manager import estimate_tokens
        from minicode.memory import (
            MemoryEntry,
            MemoryScope,
            _approval_hash_for_entry,
            assess_memory_safety,
        )
        from minicode.memory_authority_io import is_memory_entry_id
        from minicode.memory_deposition_experiment import (
            deposition_mode,
            draft_entry_retrievable,
        )

        self._rendered_pending_draft_proofs: tuple[tuple[str, str], ...] = ()
        self._rendered_aged_pending_ids: tuple[str, ...] = ()
        if deposition_mode() != "inject" or result is None:
            return "", (), 0.0
        store = self._memory_hot_store()
        decision = result.controller_decision
        count = min(max_memories, int(decision.get("max_memories", 0))) - len(result.rendered_ids)
        per_memory = min(max_tokens_per_memory, int(decision.get("max_tokens_per_memory", 0)))
        if count <= 0 or per_memory <= 0 or max_total_tokens <= 0:
            return "", (), 0.0
        from minicode.memory_hot_store import project_fingerprint

        fingerprint = project_fingerprint(
            getattr(self._memory, "workspace", None) or self._workspace or Path.cwd()
        )
        # A timed-out Redis write may complete after disk fallback. Disk wins
        # on the next healthy process, including when its copy was not selected.
        disk_contents = {
            entry.content
            for memory in self._memory.memories.values()
            for entry in memory.entries
            if entry.metadata.get("deposition_draft")
            and entry.is_active
            and draft_entry_retrievable(entry)
        }
        lines: list[str] = []
        ids: list[str] = []
        contents: set[str] = set()
        expires_at = float("inf")
        drafts = []
        if store is not None and store.available:
            try:
                with store.budget():
                    drafts = store.load_active_drafts(MemoryScope.PROJECT, fingerprint)
            except Exception:
                # The disk-backed pending lane remains usable during outages.
                logger.debug("Memory hot draft read failed", exc_info=True)
        for raw in drafts:
            try:
                entry = MemoryEntry.from_dict(raw)
                draft = entry.metadata.get("deposition_draft", {})
                expiry = draft.get("expires_at")
                if (
                    not is_memory_entry_id(entry.id)
                    or entry.scope != MemoryScope.PROJECT
                    or not entry.is_active
                    or not draft.get("injectable")
                    or not isinstance(expiry, (int, float))
                    or not math.isfinite(expiry)
                    or not expiry > time.time()
                    or not draft_entry_retrievable(entry)
                    or not assess_memory_safety(entry.content, source=entry.source).allowed
                    or entry.content in disk_contents
                    or entry.content in contents
                    or entry.id in ids
                    or entry.id in result.rendered_ids
                ):
                    continue
                line = f"{len(lines) + 1}. [draft] {entry.content}"
                candidate = "## Recent draft notes\n\n" + "\n".join((*lines, line))
                combined = "\n\n".join(block for block in (lesson_block, candidate) if block)
                if estimate_tokens(line) > per_memory or estimate_tokens(combined) > max_total_tokens:
                    continue
                lines.append(line)
                ids.append(entry.id)
                contents.add(entry.content)
                expires_at = min(expires_at, expiry)
                if len(ids) >= count:
                    break
            except Exception:
                # A malformed disposable entry cannot suppress canonical text.
                continue
        pending_proofs: list[tuple[str, str]] = []
        aged_pending_ids: list[str] = []
        for entry, expiry, aged in self._pending_draft_candidates():
            if len(ids) >= count:
                break
            if (
                entry.id in ids
                or entry.id in result.rendered_ids
                or entry.content in contents
                or entry.content in disk_contents
                or entry.content in lesson_block
            ):
                continue
            line = f"{len(lines) + 1}. [draft] {entry.content}"
            candidate = "## Recent draft notes\n\n" + "\n".join((*lines, line))
            combined = "\n\n".join(block for block in (lesson_block, candidate) if block)
            if estimate_tokens(line) > per_memory or estimate_tokens(combined) > max_total_tokens:
                continue
            lines.append(line)
            ids.append(entry.id)
            contents.add(entry.content)
            expires_at = min(expires_at, expiry)
            pending_proofs.append((entry.id, _approval_hash_for_entry(entry)))
            if aged:
                aged_pending_ids.append(entry.id)
        self._rendered_pending_draft_proofs = tuple(pending_proofs)
        self._rendered_aged_pending_ids = tuple(aged_pending_ids)
        if not lines:
            return "", (), 0.0
        return "## Recent draft notes\n\n" + "\n".join(lines), tuple(ids), expires_at

    @property
    def last_committed_prompt_receipt(self) -> MemoryPromptReceipt | None:
        """Most recent successfully installed prompt receipt in this Turn."""
        return self._last_committed_receipt

    def inject(
        self,
        task_description: str,
        current_files: list[str] | None,
        messages: list[dict],
        context_usage: float = 0.5,
        *,
        active_domains: list[str] | None = None,
        max_memories: int | None = None,
        max_total_tokens: int | None = None,
        max_tokens_per_memory: int | None = None,
        min_relevance: float | None = None,
        recent_failure: bool = False,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> list[dict]:
        """Read memories and inject into system prompt with adaptive cooldown.

        Adaptive cooldown (T1): τ_cool = τ_base × (1 - context_pressure).
        Returns modified messages with memory context appended to system message.
        """
        if not self._initialized:
            return messages
        store = self._memory_hot_store()
        if store is not None:
            store.begin_turn()
        self._last_injected_draft_ids = ()
        for index, message in enumerate(messages):
            if "_memory_draft_block" in message:
                messages[index] = _strip_hot_draft_message(message)
        self._last_injected_ids = []
        self._last_injected_versions = []
        self._committed_receipt_digests = {}
        self._durably_accounted_receipt_digests = {}
        self._receipt_materialized_ids = {}
        self._feedback_delivery_nonce = uuid.uuid4().hex
        self._last_committed_receipt = None
        self._last_retrieval_result = None
        self._last_injection_decision = None
        self._feedback_recorded = False
        self._contribution_review_recorded = False

        # Project Facts are deterministic workspace inventory and the User
        # Profile block is approved personal background; both are ambient
        # context rather than retrieved lessons, so they stay available when
        # no MemoryManager lessons match and while injection cools down.
        ambient_blocks = [
            block
            for block in (
                self._render_user_profile(),
                self._render_project_facts(),
            )
            if block
        ]
        facts_block = "\n\n".join(ambient_blocks)

        def inject_facts_only() -> list[dict]:
            if not facts_block:
                return messages
            system_index = next(
                (
                    index
                    for index, message in enumerate(messages)
                    if message.get("role") == "system"
                ),
                None,
            )
            if system_index is None:
                return messages
            existing = str(messages[system_index].get("content", ""))
            if facts_block in existing:
                return messages
            messages[system_index] = {
                **messages[system_index],
                "content": existing + "\n" + facts_block,
            }
            return messages

        if not self._memory:
            return inject_facts_only()

        # Adaptive cooldown check
        now = time.time()
        cooldown = self._adaptive_cooldown(context_usage)
        if context_usage < 0.90 and hasattr(self, "_last_inject_time"):
            if now - self._last_inject_time < cooldown:
                return inject_facts_only()  # Lessons are still in cooldown.
        self._last_inject_time = now

        from minicode.memory_retrieval import (
            MEMORY_DIRECT_VERIFICATION_POLICY,
            RetrievalSource,
        )

        effective_max_memories = (
            max_memories if max_memories is not None else self._max_memories
        )
        effective_per_memory = (
            max_tokens_per_memory
            if max_tokens_per_memory is not None
            else self._max_tokens_per_memory
        )
        effective_total_tokens = (
            max_total_tokens
            if max_total_tokens is not None
            else max(0, effective_max_memories * effective_per_memory + 64)
        )
        prompt_receipt_id = uuid.uuid4().hex
        try:
            self.read(
                task_description,
                current_files=current_files,
                active_domains=active_domains,
                max_results=effective_max_memories,
                max_total_tokens=effective_total_tokens,
                max_tokens_per_memory=effective_per_memory,
                context_usage=context_usage,
                min_relevance=min_relevance,
                source_entrypoint=RetrievalSource.PIPELINE_INJECT,
                recent_failure=recent_failure,
                _record_retrieval=False,
                agent_budget=agent_budget,
                event_sink=event_sink,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            result = self._last_retrieval_result
            lesson_block = result.prompt_text if result is not None else ""
            if lesson_block:
                from minicode.context_manager import estimate_tokens

                policy_candidate = (
                    f"{lesson_block}\n\n{MEMORY_DIRECT_VERIFICATION_POLICY}"
                )
                if estimate_tokens(policy_candidate) <= effective_total_tokens:
                    lesson_block = policy_candidate
            draft_block, draft_ids, draft_expiry = self._render_hot_drafts(
                lesson_block,
                result,
                max_memories=effective_max_memories,
                max_total_tokens=effective_total_tokens,
                max_tokens_per_memory=effective_per_memory,
            )
            pending_draft_proofs = self._rendered_pending_draft_proofs
            aged_pending_ids = self._rendered_aged_pending_ids
            prompt_blocks = [block for block in (lesson_block, facts_block, draft_block) if block]
            if not prompt_blocks:
                if result is not None and result.selected_ids:
                    _call_memory_accounting(
                        self._memory.record_retrievals,
                        list(result.selected_ids),
                        observation_id=prompt_receipt_id,
                    )
                return messages
            system_index = next(
                (
                    index
                    for index, message in enumerate(messages)
                    if message.get("role") == "system"
                ),
                None,
            )
            if system_index is None:
                if result is not None and result.selected_ids:
                    _call_memory_accounting(
                        self._memory.record_retrievals,
                        list(result.selected_ids),
                        observation_id=prompt_receipt_id,
                    )
                if result is not None:
                    self._last_retrieval_result = result.without_rendered(
                        "system_message_missing"
                    )
                return messages
            original_system_message = messages[system_index]
            render_proofs = (
                memory_rendered_proofs(result)
                if lesson_block and result is not None
                else ()
            )

            def materialize_prompt() -> None:
                current_draft_block = draft_block
                if pending_draft_proofs and not self._pending_draft_proofs_current(
                    pending_draft_proofs
                ):
                    current_draft_block = ""
                prompt_addition = "\n\n".join(
                    block for block in (lesson_block, facts_block, current_draft_block)
                    if block
                )
                if not prompt_addition:
                    return
                updated_system_message = {
                    **messages[system_index],
                    "content": (
                        str(messages[system_index].get("content", ""))
                        + "\n"
                        + prompt_addition
                    ),
                }
                if current_draft_block:
                    updated_system_message["_memory_draft_block"] = current_draft_block
                    updated_system_message["_memory_draft_ids"] = draft_ids
                    updated_system_message["_memory_draft_expires_at"] = draft_expiry
                    if pending_draft_proofs:
                        updated_system_message["_memory_pending_draft_proofs"] = (
                            pending_draft_proofs
                        )
                if lesson_block and result is not None:
                    existing_rendered_ids = tuple(
                        entry_id
                        for entry_id in messages[system_index].get(
                            "_memory_rendered_ids",
                            (),
                        )
                        if isinstance(entry_id, str) and entry_id
                    )
                    updated_system_message["_memory_rendered_ids"] = tuple(
                        dict.fromkeys((*existing_rendered_ids, *result.rendered_ids))
                    )
                    raw_existing_proofs = messages[system_index].get(
                        "_memory_rendered_proofs",
                        (),
                    )
                    existing_proofs = tuple(
                        tuple(raw_proof)
                        for raw_proof in (
                            raw_existing_proofs
                            if isinstance(raw_existing_proofs, (list, tuple))
                            else ()
                        )
                        if isinstance(raw_proof, (list, tuple))
                        and len(raw_proof) == 3
                        and all(isinstance(value, str) for value in raw_proof)
                    )
                    updated_system_message["_memory_rendered_proofs"] = tuple(
                        dict.fromkeys((*existing_proofs, *render_proofs))
                    )
                    raw_existing_blocks = messages[system_index].get(
                        "_memory_prompt_blocks",
                        (),
                    )
                    existing_blocks = tuple(
                        tuple(raw_block)
                        for raw_block in (
                            raw_existing_blocks
                            if isinstance(raw_existing_blocks, (list, tuple))
                            else ()
                        )
                        if isinstance(raw_block, (list, tuple))
                        and len(raw_block) == 2
                        and all(isinstance(value, str) for value in raw_block)
                    )
                    block_proof = memory_prompt_block_proof(lesson_block)
                    if block_proof[0]:
                        updated_system_message["_memory_prompt_blocks"] = tuple(
                            dict.fromkeys((*existing_blocks, block_proof))
                        )
                    updated_system_message["_memory_authority_revision"] = (
                        result.authority_revision
                    )
                messages[system_index] = updated_system_message

            receipt: MemoryPromptReceipt | None = None
            if lesson_block and result is not None:
                receipt = MemoryPromptReceipt(
                    prompt_text=lesson_block,
                    selected_ids=tuple(result.selected_ids),
                    rendered_ids=tuple(result.rendered_ids),
                    rendered_versions=memory_authority_versions(result),
                    authority_revision=result.authority_revision,
                    receipt_id=prompt_receipt_id,
                    rendered_proofs=tuple(
                        (entry_id, digest)
                        for entry_id, digest, _fragment in render_proofs
                    ),
                    rendered_fragments=render_proofs,
                )
                if not self.materialize_prompt_receipt(
                    receipt,
                    materialize_prompt,
                    lambda: messages.__setitem__(
                        system_index,
                        original_system_message,
                    ),
                ):
                    if facts_block:
                        messages[system_index] = {
                            **messages[system_index],
                            "content": (
                                str(messages[system_index].get("content", ""))
                                + "\n"
                                + facts_block
                            ),
                        }
                    if result.selected_ids:
                        _call_memory_accounting(
                            self._memory.record_retrievals,
                            list(result.selected_ids),
                            observation_id=prompt_receipt_id,
                        )
                    self._last_retrieval_result = result.without_rendered(
                        "authority_changed_before_prompt"
                    )
                    return messages
            elif pending_draft_proofs:
                try:
                    with self._memory.retrieval_snapshot():
                        materialize_prompt()
                except BaseException:
                    messages[system_index] = original_system_message
                    raise
            else:
                materialize_prompt()
            self._last_injected_draft_ids = tuple(
                messages[system_index].get("_memory_draft_ids", ())
            )
            rendered_aged_ids = tuple(
                entry_id for entry_id in aged_pending_ids
                if entry_id in self._last_injected_draft_ids
            )
            if rendered_aged_ids:
                logger.info(
                    "MemoryPipeline: aged pending rendered as draft count=%d ids=%s",
                    len(rendered_aged_ids),
                    ",".join(rendered_aged_ids),
                )
            if result is not None:
                if lesson_block and receipt is not None:
                    try:
                        self.commit_prompt_receipt(
                            receipt,
                            record_retrieval=True,
                        )
                    except Exception:
                        # The prompt is already installed.  Preserve the exact
                        # volatile ledger for final feedback and report only the
                        # durable-counter failure.
                        logger.warning(
                            "Memory prompt installed but durable accounting failed",
                            exc_info=True,
                        )
                elif result.selected_ids:
                    _call_memory_accounting(
                        self._memory.record_retrievals,
                        list(result.selected_ids),
                        observation_id=prompt_receipt_id,
                    )
            logger.info(
                "MemoryPipeline: injected %d memories, project_facts=%s (mode=%s)",
                len(self._last_injected_ids),
                bool(facts_block),
                (
                    result.controller_decision.get("mode", "none")
                    if result is not None
                    else "none"
                ),
            )
        except Exception as exc:
            from minicode.model_call_control import ModelCallDeadlineExceeded
            from minicode.turn_cancellation import TurnCancellationRequested

            if isinstance(
                exc,
                (TurnCancellationRequested, ModelCallDeadlineExceeded),
            ):
                raise
            logger.warning("MemoryPipeline injection failed safely: %s", exc)
            if self._last_retrieval_result is not None and not self._last_injected_ids:
                self._last_retrieval_result = (
                    self._last_retrieval_result.without_rendered(
                        "prompt_injection_failed"
                    )
                )
            # Project Facts are deterministic workspace inventory and do not
            # depend on lesson retrieval authority. A failed lesson guard must
            # not suppress this independent prompt layer.
            inject_facts_only()

        return messages

    # ── WRITE: Memory persistence ──────────────────────────────────

    def write(
        self,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        *,
        operation_id: str | None = None,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> str | None:
        """Persist one reflection and return its legacy representative ID."""
        return self.write_result(
            task_description,
            execution_trace,
            operation_id=operation_id,
            agent_budget=agent_budget,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
            provenance=provenance,
        ).representative_id

    def write_result(
        self,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        *,
        operation_id: str | None = None,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> MemoryWriteResult:
        """Persist one logical write with a structured, replay-safe outcome.

        Project lessons are committed as one Project-scope authority replace.
        Project Facts are an advisory post-commit effect and can fail without
        erasing an already durable lesson.  ``operation_id`` identifies the
        logical write across process retries; reusing it with changed payload
        fails closed.
        """
        from minicode.memory import (
            MemoryStoreConflict,
            reflection_batch_payload_digest,
        )
        from minicode.memory_write_contract import (
            MemoryWriteResult,
            new_memory_operation_id,
        )

        current_operation_id = operation_id or new_memory_operation_id()
        self._write_count += 1
        self._last_written_ids = []
        self._last_written_versions = []
        self._last_write_result = None
        reflection_result = None
        authority_commit = None
        trace_safety = None
        accepted = False
        draft_entries: list[Any] = []
        preference_captured = False
        self._pending_user_facet_text = None
        payload_digest = self._empty_write_payload_digest(
            current_operation_id,
            "pipeline_unavailable",
        )
        outcome = MemoryWriteResult(
            operation_id=current_operation_id,
            payload_digest=payload_digest,
            reason_codes=("pipeline_unavailable",),
        )
        if not self._reflection or not self._memory:
            self._log_deposition_funnel(None, outcome, [])
            return self._publish_write_result(outcome)

        try:
            trace_safety = self._trace_memory_assessment(execution_trace)
            reflection_result = self._reflection.reflect(
                task_description,
                execution_trace,
                defer_shadow=True,
                agent_budget=agent_budget,
                event_sink=event_sink,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            value_decision = (
                getattr(reflection_result, "value_decision", None)
                if reflection_result
                else None
            )
            claim_validation = (
                getattr(reflection_result, "claim_validation", None)
                if reflection_result
                else None
            )
            valid_claims = getattr(claim_validation, "valid_claims", [])
            accepted_claim_ids = {
                claim_id
                for claim_id in getattr(
                    value_decision,
                    "accepted_claim_ids",
                    [],
                )
                if isinstance(claim_id, str)
            }
            valid_by_id = {
                claim.claim_id: claim
                for claim in valid_claims
                if isinstance(getattr(claim, "claim_id", None), str)
            }
            accepted_claims = []
            seen_accepted_claim_ids: set[str] = set()
            for proposed_claim in (
                getattr(reflection_result, "structured_claims", []) or []
            ):
                claim_id = getattr(proposed_claim, "claim_id", None)
                if (
                    claim_id not in accepted_claim_ids
                    or claim_id not in valid_by_id
                    or claim_id in seen_accepted_claim_ids
                ):
                    continue
                seen_accepted_claim_ids.add(claim_id)
                # Persist the exact validator-authorized object, never a
                # same-ID proposal carrying different unvalidated content.
                accepted_claims.append(valid_by_id[claim_id])
            accepted = bool(
                reflection_result
                and value_decision is not None
                and value_decision.accepted
                and accepted_claims
            )

            if not accepted:
                raw_reasons = getattr(
                    value_decision,
                    "reason_codes",
                    ["legacy_result_default_denied"],
                )
                logger.info(
                    "MemoryPipeline: skipped reflection write reasons=%s",
                    ",".join(str(code) for code in raw_reasons[:8]),
                )
                # Permissive deposition experiment: value-gate rejections
                # with validator-clean claims become draft entries instead
                # of being dropped. Strict mode (default) returns None here,
                # leaving the historical behavior byte-for-byte intact.
                draft_entries = self._commit_deposition_drafts(
                    current_operation_id,
                    reflection_result,
                    accepted_claim_ids=accepted_claim_ids,
                    valid_by_id=valid_by_id,
                    trace_safety=trace_safety,
                    task_description=task_description,
                    execution_trace=execution_trace,
                    provenance=provenance,
                )
                conversation_plan = self._prepare_conversation_fact(
                    task_description,
                    execution_trace,
                    reflection_result,
                    provenance=provenance,
                )
                if conversation_plan is None and trace_safety.allowed:
                    conversation_plan = self._prepare_user_preference(
                        task_description,
                        execution_trace,
                        operation_id=current_operation_id,
                        provenance=provenance,
                    )
                    preference_captured = conversation_plan is not None
                if conversation_plan is not None:
                    payload_digest = self._aggregate_write_payload_digest(
                        conversation_fact_payload_digest=conversation_plan[2],
                    )
                    self._reconcile_write_operation(
                        operation_id=current_operation_id,
                        payload_digest=payload_digest,
                        authority_kind="conversation_fact",
                        authority_payload_digest=conversation_plan[2],
                    )
                    commit_fact = (
                        self._commit_user_preference
                        if preference_captured else self._commit_conversation_fact
                    )
                    authority_commit = commit_fact(
                        conversation_plan,
                        operation_id=current_operation_id,
                        operation_payload_digest=payload_digest,
                    )
                    outcome = MemoryWriteResult(
                        operation_id=current_operation_id,
                        payload_digest=payload_digest,
                        authority_kind="conversation_fact",
                        authority_commit=authority_commit,
                        reason_codes=(
                            "user_preference_replayed" if preference_captured and authority_commit.replayed
                            else "user_preference_committed" if preference_captured
                            else "conversation_fact_replayed"
                            if authority_commit.replayed
                            else "conversation_fact_committed",
                        ),
                    )
                    # Personal statements also feed the USER-scope facet
                    # observer.  Recorded post-commit under its own USER
                    # transaction; never blocks or fails the write.
                    self._pending_user_facet_text = str(
                        conversation_plan[1].get("content") or ""
                    )
                else:
                    payload_digest = self._aggregate_write_payload_digest()
                    self._reconcile_write_operation(
                        operation_id=current_operation_id,
                        payload_digest=payload_digest,
                        authority_kind="none",
                    )
                    self._memory.bind_memory_write_operation(
                        operation_id=current_operation_id,
                        payload_digest=payload_digest,
                    )
                    outcome = MemoryWriteResult(
                        operation_id=current_operation_id,
                        payload_digest=payload_digest,
                        reason_codes=("reflection_not_persistable",),
                    )
            else:
                facts_plan, facts_effect = self._prepare_project_facts(
                    reflection_result,
                    accepted_claims=accepted_claims,
                    task_description=task_description,
                    execution_trace=execution_trace,
                    provenance=provenance,
                )
                lesson_claims = [
                    claim
                    for claim in accepted_claims
                    if claim.claim_type != "dependency"
                    or self._is_declared_dependency(claim)
                ]
                if lesson_claims:
                    claim_writes = self._build_reflection_claim_writes(
                        reflection_result,
                        lesson_claims,
                        trace_safety=trace_safety,
                        task_description=task_description,
                        execution_trace=execution_trace,
                        provenance=provenance,
                        operation_id=current_operation_id,
                    )
                    lesson_payload_digest = reflection_batch_payload_digest(
                        claim_writes
                    )
                    payload_digest = self._aggregate_write_payload_digest(
                        lesson_payload_digest=lesson_payload_digest,
                        project_facts_payload_digest=(
                            facts_plan.payload_digest
                            if facts_plan is not None
                            else None
                        ),
                    )
                    self._reconcile_write_operation(
                        operation_id=current_operation_id,
                        payload_digest=payload_digest,
                        authority_kind="lesson",
                        authority_payload_digest=lesson_payload_digest,
                        project_facts_payload_digest=(
                            facts_plan.payload_digest
                            if facts_plan is not None
                            else None
                        ),
                    )
                    authority_commit = self._memory.commit_reflection_batch(
                        operation_id=current_operation_id,
                        payload_digest=payload_digest,
                        authority_payload_digest=lesson_payload_digest,
                        claims=claim_writes,
                    )
                    outcome = MemoryWriteResult(
                        operation_id=current_operation_id,
                        payload_digest=payload_digest,
                        authority_kind="lesson",
                        authority_commit=authority_commit,
                        reason_codes=(
                            "lesson_authority_replayed"
                            if authority_commit.replayed
                            else "lesson_authority_committed",
                        ),
                    )
                    # Publish the durable authority before advisory effects or
                    # shadow work so neither can make a committed write vanish
                    # from the caller's observation bridge.
                    self._publish_write_result(outcome)
                    if facts_plan is not None:
                        facts_effect = self._persist_project_facts_plan(
                            facts_plan,
                            operation_id=current_operation_id,
                        )
                    outcome = replace(
                        outcome,
                        project_facts_effect=facts_effect,
                    )
                else:
                    if facts_plan is not None:
                        payload_digest = self._aggregate_write_payload_digest(
                            project_facts_payload_digest=(facts_plan.payload_digest),
                        )
                        self._reconcile_write_operation(
                            operation_id=current_operation_id,
                            payload_digest=payload_digest,
                            authority_kind="none",
                            project_facts_payload_digest=(facts_plan.payload_digest),
                        )
                        self._memory.bind_memory_write_operation(
                            operation_id=current_operation_id,
                            payload_digest=payload_digest,
                        )
                        facts_effect = self._persist_project_facts_plan(
                            facts_plan,
                            operation_id=current_operation_id,
                        )
                    else:
                        payload_digest = self._aggregate_write_payload_digest()
                        self._reconcile_write_operation(
                            operation_id=current_operation_id,
                            payload_digest=payload_digest,
                            authority_kind="none",
                        )
                        self._memory.bind_memory_write_operation(
                            operation_id=current_operation_id,
                            payload_digest=payload_digest,
                        )
                    outcome = MemoryWriteResult(
                        operation_id=current_operation_id,
                        payload_digest=payload_digest,
                        project_facts_effect=facts_effect,
                        reason_codes=("project_facts_only",),
                    )
        except MemoryStoreConflict:
            # A reused operation with changed payload, or any authority
            # conflict, is observable but never partially retried here.
            outcome = MemoryWriteResult(
                operation_id=current_operation_id,
                payload_digest=payload_digest,
                reason_codes=("memory_write_conflict",),
            )
        except TurnCancellationRequested:
            if authority_commit is None:
                raise
            outcome = replace(
                outcome,
                post_commit_interrupted=True,
                reason_codes=self._append_write_reason(
                    outcome.reason_codes,
                    "post_commit_interrupted",
                ),
            )
        except ModelCallDeadlineExceeded:
            if authority_commit is None:
                logger.info("MemoryPipeline: reflection skipped at Agent deadline")
                payload_digest = self._empty_write_payload_digest(
                    current_operation_id,
                    "reflection_deadline_exceeded",
                )
                outcome = MemoryWriteResult(
                    operation_id=current_operation_id,
                    payload_digest=payload_digest,
                    reason_codes=("reflection_deadline_exceeded",),
                )
            else:
                outcome = replace(
                    outcome,
                    post_commit_interrupted=True,
                    reason_codes=self._append_write_reason(
                        outcome.reason_codes,
                        "post_commit_interrupted",
                    ),
                )
        except Exception as error:
            from minicode.memory import sanitize_for_persistence

            task_record = sanitize_for_persistence({"task": task_description})
            task_reference = (
                task_record.get("task", "[TASK_UNAVAILABLE]")
                if isinstance(task_record, dict)
                else "[TASK_UNAVAILABLE]"
            )
            logger.error(
                "MemoryPipeline: reflection write failed task_ref=%s error_type=%s",
                task_reference,
                type(error).__name__,
            )
            if authority_commit is None:
                payload_digest = self._empty_write_payload_digest(
                    current_operation_id,
                    "reflection_write_failed",
                )
                outcome = MemoryWriteResult(
                    operation_id=current_operation_id,
                    payload_digest=payload_digest,
                    reason_codes=("reflection_write_failed",),
                )
            else:
                outcome = replace(
                    outcome,
                    post_commit_interrupted=True,
                    reason_codes=self._append_write_reason(
                        outcome.reason_codes,
                        "post_commit_interrupted",
                    ),
                )

        self._publish_write_result(outcome)
        # A failed, conflicted, or interrupted write must leave every
        # authority byte untouched (terminal-no-effect contract); a facet
        # observation is only legitimate after a cleanly finished write.
        outcome_failed = outcome.post_commit_interrupted or bool(
            set(outcome.reason_codes)
            & {"memory_write_conflict", "reflection_write_failed"}
        )
        if not outcome_failed:
            if not preference_captured and trace_safety is not None and trace_safety.allowed and accepted:
                # Technical lessons and personal preferences commonly coexist.
                # Keep the USER transaction separate from the PROJECT batch.
                preference_operation_id = "memoryop_" + hashlib.sha256(
                    f"user-preference:{current_operation_id}".encode()
                ).hexdigest()[:32]
                try:
                    preference_plan = self._prepare_user_preference(
                        task_description, execution_trace,
                        operation_id=preference_operation_id, provenance=provenance,
                    )
                    if preference_plan is not None:
                        preference_captured = True
                        preference_commit = self._commit_user_preference(
                            preference_plan,
                            operation_id=preference_operation_id,
                            operation_payload_digest=preference_plan[2],
                        )
                        draft_entries.extend(self._entries_for_commit(preference_commit))
                        self._pending_user_facet_text = preference_plan[1]["content"]
                except Exception:
                    logger.info("MemoryPipeline: user preference side write failed safely", exc_info=True)
            self._record_pending_user_facet(
                event_sink, task_description=str(task_description or "")
            )
        if (
            reflection_result is not None
            and hasattr(self._reflection, "complete_shadow")
            # A trace with no groundable evidence at all — no errors,
            # recoveries, decisions, verification, edits, or libraries —
            # cannot yield a persistable claim from either branch, so the
            # shadow LLM call would be pure cost (benchmark: zero-failure
            # discovery turns each paid a comparison call for nothing).
            # Any evidence class present keeps the shadow evaluation alive.
            and self._reflection_has_groundable_evidence(reflection_result)
        ):
            try:
                self._reflection.complete_shadow(
                    reflection_result,
                    agent_budget=agent_budget,
                    event_sink=event_sink,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
            except TurnCancellationRequested:
                if not self._write_has_committed_effect(outcome):
                    raise
                outcome = replace(
                    outcome,
                    post_commit_interrupted=True,
                    reason_codes=self._append_write_reason(
                        outcome.reason_codes,
                        "post_commit_interrupted",
                    ),
                )
            except ModelCallDeadlineExceeded:
                logger.info(
                    "MemoryPipeline: shadow reflection stopped at Agent deadline"
                )
                if self._write_has_committed_effect(outcome):
                    outcome = replace(
                        outcome,
                        post_commit_interrupted=True,
                        reason_codes=self._append_write_reason(
                            outcome.reason_codes,
                            "post_commit_interrupted",
                        ),
                    )
            except Exception:
                logger.info("MemoryPipeline: shadow comparison failed safely")

        self._log_deposition_funnel(
            reflection_result, outcome, draft_entries,
            preference_captured=preference_captured,
        )
        return self._publish_write_result(outcome)

    @staticmethod
    def _reflection_has_groundable_evidence(reflection: Any) -> bool:
        """Whether any evidence class could ground a claim from either branch."""

        evidence = getattr(reflection, "task_evidence", None)
        if evidence is None:
            return False
        validation = getattr(reflection, "claim_validation", None)
        if validation is not None and getattr(validation, "valid_claims", None):
            return True
        return any(
            getattr(evidence, field, None)
            for field in (
                "errors",
                "recoveries",
                "recovery_suggestions",
                "decisions",
                "verification",
                "files_changed",
                "libraries",
            )
        )

    def _entries_for_commit(self, commit: Any) -> list[Any]:
        if commit is None or self._memory is None:
            return []
        ids = {
            item.entry_id for item in commit.materializations
            if item.remains_materialized
        }
        return [
            entry for memory in self._memory.memories.values()
            for entry in memory.entries if entry.id in ids
        ]

    def _log_deposition_funnel(
        self, reflection: Any, outcome: MemoryWriteResult, draft_entries: list[Any],
        *, preference_captured: bool = False,
    ) -> None:
        """Content-free counts of actual materializations, including side lanes."""
        from minicode.memory_deposition_experiment import deposition_mode

        candidate = getattr(reflection, "reflection_candidate", None)
        validation = getattr(reflection, "claim_validation", None)
        decision = getattr(reflection, "value_decision", None)
        candidates = len(getattr(candidate, "claims", []) or []) + int(preference_captured)
        validated = len(getattr(validation, "valid_claims", []) or []) + int(preference_captured)
        value_accepted = len(getattr(decision, "accepted_claim_ids", []) or []) + int(preference_captured)
        entries = {
            entry.id: entry
            for entry in (*self._entries_for_commit(outcome.authority_commit), *draft_entries)
        }
        captured_claim_ids = {
            item.claim_id for item in getattr(outcome.authority_commit, "materializations", ())
            if item.remains_materialized
        }
        captured_claim_ids.update(
            entry.metadata.get("claim_identity", {}).get("claim_id")
            for entry in draft_entries
            if entry.metadata.get("claim_identity", {}).get("claim_id")
        )
        if outcome.project_facts_effect.status in {"committed", "replayed"}:
            captured_claim_ids.update(
                claim.claim_id for claim in getattr(validation, "valid_claims", [])
                if claim.claim_type == "dependency" and claim.epistemic_status == "confirmed"
                and claim.claim_id in (getattr(decision, "accepted_claim_ids", []) or [])
            )
        preference_count = int(any(entry.category == "user_preference" for entry in entries.values()))
        # Ordinary conversation facts are an existing non-reflection intake.
        facts = sum(entry.category == "conversation_fact" for entry in entries.values())
        candidates += facts
        validated += facts
        value_accepted += facts
        reflected_ids = {claim.claim_id for claim in getattr(validation, "valid_claims", []) or []}
        captured = len(captured_claim_ids & reflected_ids) + preference_count + facts
        logger.info(
            "MemoryPipeline: deposition candidates=%d validated=%d value_accepted=%d "
            "auto_approved=%d pending=%d drafted=%d lost=%d mode=%s",
            candidates, validated, value_accepted,
            sum(entry.approval_status == "approved" for entry in entries.values()),
            sum(entry.approval_status == "pending" for entry in entries.values()),
            sum(bool(entry.metadata.get("deposition_draft")) for entry in entries.values()),
            max(0, candidates - captured), deposition_mode(),
        )

    @staticmethod
    def _empty_write_payload_digest(operation_id: str, reason: str) -> str:
        payload = f"memory-write-none-v1\0{operation_id}\0{reason}".encode()
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _aggregate_write_payload_digest(
        *,
        lesson_payload_digest: str | None = None,
        conversation_fact_payload_digest: str | None = None,
        project_facts_payload_digest: str | None = None,
    ) -> str:
        """Bind every possible durable effect of one logical write."""
        payload = json.dumps(
            {
                "schema": "memory_pipeline_write_v1",
                "lesson": lesson_payload_digest,
                "conversation_fact": conversation_fact_payload_digest,
                "project_facts": project_facts_payload_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def _reconcile_write_operation(
        self,
        *,
        operation_id: str,
        payload_digest: str,
        authority_kind: str,
        authority_payload_digest: str | None = None,
        project_facts_payload_digest: str | None = None,
    ) -> None:
        """Upgrade retained pre-envelope receipts under the fixed lock order."""

        receipt_guard = getattr(
            self._project_facts,
            "operation_receipt_guard",
            None,
        )
        if not callable(receipt_guard):
            # Compatibility test doubles and deployments without a Workspace
            # cannot contain canonical Project Facts receipts to reconcile.
            return
        self._memory.reconcile_pipeline_operation(
            project_facts_store=self._project_facts,
            operation_id=operation_id,
            aggregate_digest=payload_digest,
            authority_kind=authority_kind,
            authority_payload_digest=authority_payload_digest,
            project_facts_payload_digest=project_facts_payload_digest,
        )

    @staticmethod
    def _append_write_reason(
        reason_codes: tuple[str, ...],
        reason_code: str,
    ) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*reason_codes, reason_code)))

    @staticmethod
    def _write_has_committed_effect(result: MemoryWriteResult) -> bool:
        return bool(
            result.authority_commit is not None
            or result.project_facts_effect.status in {"committed", "replayed"}
        )

    def _publish_write_result(
        self,
        result: MemoryWriteResult,
    ) -> MemoryWriteResult:
        self._last_write_result = result
        self._last_written_versions = list(result.authority_versions)
        self._last_written_ids = [
            entry_id for entry_id, _digest in result.authority_versions
        ]
        self.save_state()
        return result

    def _commit_deposition_drafts(
        self,
        primary_operation_id: str,
        result: Any,
        *,
        accepted_claim_ids: set[str],
        valid_by_id: dict[str, Any],
        trace_safety: Any,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        provenance: dict[str, Any] | None,
    ) -> list[Any]:
        """Best-effort draft capture for the permissive deposition experiment.

        Runs only when ``MINI_CODE_MEMORY_DEPOSITION_MODE`` is non-strict and
        validator-clean claims exist that the value gate rejected. The draft
        batch is its own deterministic operation, committed after the primary
        write outcome is decided; any failure is logged and swallowed so the
        experiment can never disturb the durable lesson path. The primary
        outcome's reason codes and payload digest are intentionally left
        untouched.
        """
        from minicode.memory import reflection_batch_payload_digest
        from minicode.memory_deposition_experiment import (
            apply_draft_metadata,
            deposition_mode,
            draft_operation_id,
            draft_ttl_seconds,
        )

        mode = deposition_mode()
        if mode == "strict":
            return []
        value_decision = getattr(result, "value_decision", None)
        if value_decision is None:
            return []
        reason_codes = list(value_decision.reason_codes or [])
        draft_claims = [
            claim
            for claim_id, claim in valid_by_id.items()
            if claim_id not in accepted_claim_ids
        ]
        if not draft_claims:
            return []
        declared_ids = {
            claim.claim_id for claim in draft_claims if self._is_declared_dependency(claim)
        }
        declared_operation_id = (
            draft_operation_id(primary_operation_id)
            if declared_ids and mode == "inject"
            else None
        )
        synthetic_result = replace(
            result,
            structured_claims=list(draft_claims),
            claim_validation=replace(
                result.claim_validation,
                valid_claims=list(draft_claims),
                rejected_claims=[],
            ),
            value_decision=replace(
                value_decision,
                accepted=True,
                durable_signals=[],
                accepted_claim_ids=[
                    claim.claim_id for claim in draft_claims
                ],
            ),
        )
        try:
            writes = list(
                self._build_reflection_claim_writes(
                    synthetic_result,
                    draft_claims,
                    trace_safety=trace_safety,
                    task_description=task_description,
                    execution_trace=execution_trace,
                    provenance=provenance,
                    operation_id=declared_operation_id,
                )
            )
            expires_at = (
                (
                    self._deposition_expiry(declared_operation_id, claims=draft_claims)
                    if declared_ids
                    else time.time() + draft_ttl_seconds()
                )
                if mode == "inject"
                else None
            )
            draft_writes = [
                replace(
                    write,
                    add_entry_kwargs=apply_draft_metadata(
                        write.add_entry_kwargs,
                        reason_codes=reason_codes,
                        expires_at=expires_at,
                        injectable=(mode == "inject"),
                    ),
                )
                for write in writes
            ]
            if declared_ids:
                from minicode.memory import MemoryApprovalPolicy

                draft_writes = [
                    replace(write, add_entry_kwargs={
                        **write.add_entry_kwargs,
                        "approval_policy": MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
                        "metadata": {
                            **write.add_entry_kwargs["metadata"],
                            "deposition_draft": {
                                **write.add_entry_kwargs["metadata"]["deposition_draft"],
                                "reason_codes": ["declared_dependency", *reason_codes][:8],
                            },
                        },
                    }) if write.claim_id in declared_ids else write
                    for write in draft_writes
                ]
            store = self._memory_hot_store() if mode == "inject" else None
            if store is not None and store.available and not declared_ids:
                from minicode.memory import (
                    MemoryEntry,
                    MemoryScope,
                    assess_memory_safety,
                )
                from minicode.memory_hot_store import (
                    project_fingerprint,
                    redis_draft_ttl_seconds,
                )

                fingerprint = project_fingerprint(
                    getattr(self._memory, "workspace", None) or self._workspace or Path.cwd()
                )
                ttl = redis_draft_ttl_seconds(fallback=draft_ttl_seconds())
                hot_expires_at = time.time() + ttl
                all_stored = True
                stored_entries = []
                with store.budget():
                    for write in draft_writes:
                        kwargs = apply_draft_metadata(
                            write.add_entry_kwargs,
                            reason_codes=reason_codes,
                            expires_at=hot_expires_at,
                            injectable=True,
                        )
                        safety = assess_memory_safety(kwargs["content"], source=kwargs["source"])
                        kwargs.setdefault("safety_status", safety.status)
                        kwargs.setdefault("safety_reason", safety.reason)
                        identity = f"{draft_operation_id(primary_operation_id)}:{write.claim_id}"
                        entry = MemoryEntry(
                            id="draft_" + hashlib.sha256(identity.encode()).hexdigest()[:32],
                            scope=MemoryScope.PROJECT,
                            approval_status=(
                                "approved" if safety.allowed and trace_safety.allowed else "pending"
                            ),
                            **kwargs,
                        )
                        if not store.put_draft(MemoryScope.PROJECT, fingerprint, entry.to_dict(), ttl):
                            all_stored = False
                            break
                        stored_entries.append(entry)
                if all_stored:
                    return stored_entries
                # Even when the availability check passed, a failed SET must
                # fall back to the original atomic disk batch and its TTL.
            batch_digest = reflection_batch_payload_digest(draft_writes)
            commit = self._memory.commit_reflection_batch(
                operation_id=draft_operation_id(primary_operation_id),
                payload_digest=batch_digest,
                authority_payload_digest=batch_digest,
                claims=draft_writes,
            )
            logger.info(
                "MemoryPipeline: deposition draft committed mode=%s "
                "claims=%d replayed=%s reasons=%s",
                mode,
                len(draft_writes),
                commit.replayed,
                ",".join(str(code) for code in reason_codes[:8]),
            )
            return self._entries_for_commit(commit)
        except Exception:
            logger.exception(
                "MemoryPipeline: deposition draft commit failed "
                "(experiment side-channel; primary write unaffected)"
            )
            return []

    def _build_reflection_claim_writes(
        self,
        result: Any,
        lesson_claims: list[Any],
        *,
        trace_safety: Any,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        provenance: dict[str, Any] | None,
        operation_id: str | None = None,
    ):
        from minicode.memory import (
            MemoryApprovalPolicy,
            MemoryTier,
            ReflectionClaimWrite,
        )
        from minicode.reflection_synthesis import durable_signals_for_claim

        value_decision = result.value_decision
        decision_signals = set(value_decision.durable_signals or [])
        safety_kwargs = (
            {}
            if trace_safety.allowed
            else {
                "safety_status": trace_safety.status,
                "safety_reason": trace_safety.reason,
            }
        )
        event_ids = [
            str(event.get("event_id"))
            for event in execution_trace
            if isinstance(event, dict) and event.get("event_id")
        ][:64]
        writes = []
        declared_expiry = (
            self._deposition_expiry(operation_id, claims=lesson_claims)
            if any(self._is_declared_dependency(claim) for claim in lesson_claims)
            else None
        )
        for claim in lesson_claims:
            claim_signals = tuple(
                signal
                for signal in durable_signals_for_claim(claim)
                if signal in decision_signals
            )
            strong_signals = _STRONG_DURABLE_SIGNALS.intersection(claim_signals)
            # Auto-approval needs independently verifiable evidence. A
            # ``constraint`` claim derived from merely reading a repo-local
            # policy file is not verification — reading attacker-controllable
            # text must queue for human review. Constraints the user stated
            # directly (user_constraint / user_correction evidence events)
            # keep the short-circuit, as do user-authored corrections, because
            # the user is the approval authority in this trust model.
            user_authored_event_ids = {
                str(event.get("event_id"))
                for event in execution_trace
                if isinstance(event, dict)
                and event.get("type") in {"user_constraint", "user_correction"}
                and event.get("event_id")
            }
            claim_is_user_authored = any(
                evidence_id in user_authored_event_ids
                for evidence_id in claim.evidence_ids
            )
            claim_has_own_strong_evidence = claim.epistemic_status == "confirmed" and (
                bool(claim.verification_ids)
                or claim.claim_type == "correction"
                or (claim.claim_type == "constraint" and claim_is_user_authored)
            )
            approval_policy = (
                MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED
                if strong_signals
                and trace_safety.allowed
                and claim_has_own_strong_evidence
                else MemoryApprovalPolicy.USER_REVIEW_REQUIRED
            )
            single_result = replace(
                result,
                structured_claims=[claim],
                claim_validation=replace(
                    result.claim_validation,
                    valid_claims=[claim],
                    rejected_claims=[],
                    issues=[
                        issue
                        for issue in result.claim_validation.issues
                        if issue.claim_id in {None, claim.claim_id}
                    ],
                ),
                value_decision=replace(
                    value_decision,
                    durable_signals=list(claim_signals),
                    accepted_claim_ids=[claim.claim_id],
                ),
            )
            mem_data = single_result.to_memory_entry()
            metadata = dict(mem_data.get("metadata", {}))
            if self._is_declared_dependency(claim):
                from minicode.memory_deposition_experiment import (
                    apply_draft_metadata,
                    deposition_mode,
                )

                # Manifest declarations remain pending even in inject mode.
                # Only the separate draft renderer may expose them.
                metadata = apply_draft_metadata(
                    {"metadata": metadata},
                    reason_codes=["declared_dependency"],
                    expires_at=declared_expiry if deposition_mode() == "inject" else None,
                    injectable=deposition_mode() == "inject",
                )["metadata"]
                metadata["deposition_operation_id"] = operation_id
                approval_policy = MemoryApprovalPolicy.USER_REVIEW_REQUIRED
            metadata["claim_identity"] = {
                "claim_id": claim.claim_id,
                "semantic_key": claim.semantic_key,
            }
            reserved_authority_keys = {
                "approval_basis",
                "claim_id",
                "claim_type",
                "durable_signals",
                "evidence_kind",
                "semantic_key",
            }
            inherited_provenance = {
                key: value
                for key, value in dict(provenance or {}).items()
                if key not in reserved_authority_keys
            }
            entry_provenance = {
                **inherited_provenance,
                "task": task_description[:300],
                "trace_events": len(execution_trace),
                "event_ids": event_ids,
                "claim_id": claim.claim_id,
                "semantic_key": claim.semantic_key,
                "claim_type": claim.claim_type,
                "success": result.success,
                "confidence": result.confidence,
                "value_reason_codes": list(value_decision.reason_codes),
                "durable_signals": list(claim_signals),
                "approval_basis": (
                    ",".join(sorted(str(signal) for signal in strong_signals))
                    if approval_policy == MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED
                    else "user_review"
                ),
            }
            if claim.epistemic_status == "confirmed":
                causal_evidence_kind = {
                    "root_cause": "verified_root_cause",
                    "recovery": "verified_recovery",
                }.get(claim.claim_type)
                if causal_evidence_kind is not None:
                    entry_provenance["evidence_kind"] = causal_evidence_kind
            writes.append(
                ReflectionClaimWrite(
                    claim_id=claim.claim_id,
                    semantic_key=claim.semantic_key,
                    statement=claim.statement,
                    add_entry_kwargs={
                        "category": mem_data["category"],
                        "content": mem_data["content"],
                        "tags": mem_data["tags"],
                        "domains": mem_data.get("domains", []),
                        "metadata": metadata,
                        "tier": MemoryTier.SHORT_TERM,
                        "source": "reflection",
                        "provenance": entry_provenance,
                        "approval_policy": approval_policy,
                        **safety_kwargs,
                    },
                )
            )
        return tuple(writes)

    @property
    def last_written_ids(self) -> tuple[str, ...]:
        """All entries produced or reinforced by the latest ``write`` call.

        ``write`` keeps its historical single-ID return value for callers that
        only need a representative entry. Run provenance must use this complete
        snapshot so a later user verdict reaches every independently persisted
        claim from the turn.
        """
        return tuple(self._last_written_ids)

    @property
    def last_written_versions(self) -> tuple[tuple[str, str], ...]:
        """Authority versions captured by the latest atomic write paths."""
        return tuple(self._last_written_versions)

    @property
    def last_write_result(self) -> MemoryWriteResult | None:
        """Structured outcome from the latest logical write attempt."""
        return self._last_write_result

    def _trace_memory_assessment(self, execution_trace: list[dict[str, Any]]):
        """Conservatively scan trace text before automatic memory writes.

        Trace text is untrusted evidence. If it contains unsafe-looking
        instructions, the generated reflection is routed to pending approval
        rather than becoming injectable automatically.
        """
        return assess_trace_memory_safety(execution_trace)

    def _prepare_conversation_fact(
        self,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        result: Any,
        *,
        provenance: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any], str] | None:
        """Prepare an ordinary user statement as a pending review candidate.

        This is the deterministic conversation-fact intake behind MEM-001.
        It is deliberately separate from reflection: no tool evidence, error,
        recovery or code decision is required. The candidate is always
        USER_REVIEW_REQUIRED, so a statement can never silently become
        injectable context.
        """
        fact = self._conversation_fact_text(task_description)
        if fact is None:
            return None
        if self._trace_has_technical_work(execution_trace, result):
            return None
        from minicode.memory import (
            MemoryApprovalPolicy,
            MemoryTier,
            conversation_fact_payload_digest,
        )

        claim_id = f"conversation-fact-{hashlib.sha256(fact.encode()).hexdigest()[:16]}"
        add_entry_kwargs = {
            "category": "conversation_fact",
            "content": fact,
            "tags": ["conversation-fact", "user-statement"],
            "domains": ["memory"],
            "tier": MemoryTier.SHORT_TERM,
            "source": "conversation_fact",
            "provenance": {
                **dict(provenance or {}),
                "task": task_description[:300],
                "trace_events": len(execution_trace),
                "event_ids": [
                    str(event.get("event_id"))
                    for event in execution_trace
                    if isinstance(event, dict) and event.get("event_id")
                ][:64],
                "intake": "deterministic_conversation_fact",
                "approval_basis": "user_review",
                "scope_basis": "personal_fact",
            },
            "metadata": {
                "confidence": 0.6,
                "evidence_kind": "user_statement",
            },
            "approval_policy": MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
        }
        payload_digest = conversation_fact_payload_digest(
            claim_id=claim_id,
            add_entry_kwargs=add_entry_kwargs,
        )
        return claim_id, add_entry_kwargs, payload_digest

    @staticmethod
    def _is_declared_dependency(claim: Any) -> bool:
        from minicode.memory_deposition_experiment import deposition_feature_enabled

        return (
            deposition_feature_enabled("declared_dependencies")
            and claim.claim_type == "dependency"
            and claim.epistemic_status == "inferred"
        )

    def _deposition_expiry(
        self, operation_id: str | None, *, claims: list[Any] | None = None,
    ) -> float:
        """Keep TTL stable on a replay of the same logical experimental write."""
        from minicode.memory_deposition_experiment import draft_ttl_seconds

        if operation_id or claims:
            self._memory.refresh_if_stale()
            claim_identities = {
                (claim.semantic_key, claim.statement) for claim in (claims or [])
                if self._is_declared_dependency(claim)
            }
            recurring_expiries = []
            for memory in self._memory.memories.values():
                for entry in memory.entries:
                    draft = entry.metadata.get("deposition_draft", {})
                    if not isinstance(draft, dict):
                        continue
                    expiry = draft.get("expires_at")
                    if not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
                        continue
                    if operation_id and entry.metadata.get("deposition_operation_id") == operation_id:
                        return float(expiry)
                    identity = entry.metadata.get("claim_identity", {})
                    if (
                        isinstance(identity, dict)
                        and "declared_dependency" in draft.get("reason_codes", [])
                        and (identity.get("semantic_key"), identity.get("statement"))
                        in claim_identities
                    ):
                        recurring_expiries.append(float(expiry))
            # Recurrence keeps the original entry's metadata. Reuse its TTL
            # when building a later operation too, so retrying that operation
            # produces the same digest without extending the stored draft.
            if recurring_expiries:
                return min(recurring_expiries)
        return time.time() + draft_ttl_seconds()

    def _prepare_user_preference(
        self,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        *,
        operation_id: str,
        provenance: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any], str] | None:
        from minicode.memory import (
            MemoryApprovalPolicy,
            MemoryTier,
            assess_memory_safety,
            conversation_fact_payload_digest,
            persistence_text_contains_secret,
        )
        from minicode.memory_deposition_experiment import (
            apply_draft_metadata,
            deposition_feature_enabled,
            deposition_mode,
        )

        if not deposition_feature_enabled("user_preferences"):
            return None
        preference = _extract_user_preference(task_description)
        if (
            preference is None
            or persistence_text_contains_secret(preference)
            or not assess_memory_safety(preference, source="conversation_fact").allowed
        ):
            return None
        injectable = deposition_mode() == "inject"
        claim_id = "user-preference-" + hashlib.sha256(preference.encode()).hexdigest()[:16]
        kwargs = apply_draft_metadata(
            {
                "category": "user_preference",
                "content": preference,
                "tags": ["user-preference", "user-statement"],
                "domains": ["memory"],
                "tier": MemoryTier.SHORT_TERM,
                "source": "conversation_fact",
                "approval_policy": MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
                "metadata": {"evidence_kind": "user_statement", "deposition_operation_id": operation_id},
                "provenance": {
                    **dict(provenance or {}),
                    "task": task_description[:300],
                    "trace_events": len(execution_trace),
                    "intake": "deterministic_user_preference",
                    "scope_basis": "personal_preference",
                    "approval_basis": "experimental_user_preference" if injectable else "user_review",
                },
            },
            reason_codes=["user_preference"],
            expires_at=self._deposition_expiry(operation_id) if injectable else None,
            injectable=injectable,
        )
        return claim_id, kwargs, conversation_fact_payload_digest(claim_id=claim_id, add_entry_kwargs=kwargs)

    def _commit_user_preference(
        self,
        plan: tuple[str, dict[str, Any], str],
        *,
        operation_id: str,
        operation_payload_digest: str,
    ):
        claim_id, kwargs, digest = plan
        commit = self._memory.commit_conversation_fact(
            operation_id=operation_id,
            payload_digest=operation_payload_digest,
            authority_payload_digest=digest,
            claim_id=claim_id,
            add_entry_kwargs=kwargs,
        )
        logger.info("MemoryPipeline: user preference committed id=%s replayed=%s", commit.representative_id or "none", commit.replayed)
        return commit

    def _commit_conversation_fact(
        self,
        plan: tuple[str, dict[str, Any], str],
        *,
        operation_id: str,
        operation_payload_digest: str,
    ):
        claim_id, add_entry_kwargs, authority_payload_digest = plan
        commit = self._memory.commit_conversation_fact(
            operation_id=operation_id,
            payload_digest=operation_payload_digest,
            authority_payload_digest=authority_payload_digest,
            claim_id=claim_id,
            add_entry_kwargs=add_entry_kwargs,
        )
        logger.info(
            "MemoryPipeline: queued conversation fact for review id=%s",
            commit.representative_id or "none",
        )
        return commit

    @staticmethod
    def _conversation_fact_text(task_description: str) -> str | None:
        import re

        text = str(task_description or "").strip()
        if not 2 <= len(text) <= 200:
            return None
        lowered = text.lower()
        if any(term in lowered for term in _CONVERSATION_FACT_TECHNICAL_TERMS):
            return None
        if re.search(r"(?:帮我|请|帮忙|修复|实现|添加|删除|创建|生成)", text):
            return None
        is_zh_fact = "我" in text and any(
            marker in text for marker in _CONVERSATION_FACT_ZH_MARKERS
        )
        is_en_fact = bool(
            re.search(
                r"(?i)(?:^|[.!?]\s*)(?:i am|i'm|my |our |i like|i prefer|"
                r"i use|i live in|i live|i work|i want|i need)\b",
                text,
            )
        )
        if not (is_zh_fact or is_en_fact):
            return None
        return text.rstrip("。.!！?？ ")

    @staticmethod
    def _trace_has_technical_work(
        execution_trace: list[dict[str, Any]],
        result: Any,
    ) -> bool:
        evidence = getattr(result, "task_evidence", None) if result else None
        if evidence is not None:
            if any(
                (
                    getattr(evidence, "tool_calls", []),
                    getattr(evidence, "files_read", []),
                    getattr(evidence, "files_changed", []),
                    getattr(evidence, "referenced_files", []),
                    getattr(evidence, "errors", []),
                    getattr(evidence, "recoveries", []),
                    getattr(evidence, "decisions", []),
                    getattr(evidence, "libraries", []),
                    getattr(evidence, "verification", []),
                )
            ):
                return True
            return False
        for event in execution_trace or []:
            if not isinstance(event, dict):
                continue
            if event.get("type") in {
                "tool_call",
                "tool_result",
                "error",
                "recovery",
                "decision",
            }:
                return True
            if event.get("tool_name"):
                return True
        return False

    def _prepare_project_facts(
        self,
        result: Any,
        *,
        accepted_claims: list[Any],
        task_description: str,
        execution_trace: list[dict[str, Any]],
        provenance: dict[str, Any] | None,
    ) -> tuple[ProjectFactsWritePlan | None, Any]:
        """Build the exact evidence-bound advisory Project Facts payload."""
        from minicode.memory_write_contract import ProjectFactsEffect
        from minicode.project_facts import prepare_project_facts_write

        if self._project_facts is None or not bool(getattr(result, "success", False)):
            return None, ProjectFactsEffect()
        dependency_claims = [
            claim for claim in accepted_claims if claim.claim_type == "dependency"
        ]
        if not dependency_claims:
            return None, ProjectFactsEffect()
        evidence = getattr(result, "task_evidence", None)
        accepted_dependency_event_ids = {
            event_id
            for claim in dependency_claims
            for event_id in (getattr(claim, "evidence_ids", []) or [])
            if isinstance(event_id, str) and event_id
        }
        confirmed_libraries = [
            item
            for item in (getattr(evidence, "libraries", []) or [])
            if getattr(item, "status", "") == "confirmed"
            and accepted_dependency_event_ids.intersection(
                event_id
                for event_id in (getattr(item, "event_ids", ()) or ())
                if isinstance(event_id, str)
            )
        ]
        names = [item.name for item in confirmed_libraries]
        if not names:
            return None, ProjectFactsEffect(status="skipped")
        evidence_by_name = {
            item.name: list(getattr(item, "event_ids", ()) or ())
            for item in confirmed_libraries
        }
        facts_provenance = {
            **dict(provenance or {}),
            "source": "reflection_evidence",
            "task": task_description[:300],
            "outcome": "success" if result.success else "failed",
            "event_ids": [
                str(event.get("event_id"))
                for event in execution_trace
                if isinstance(event, dict) and event.get("event_id")
            ][:64],
            "dependency_evidence": evidence_by_name,
        }
        plan = prepare_project_facts_write(names, facts_provenance)
        if plan is None:
            return None, ProjectFactsEffect(status="skipped")
        return plan, ProjectFactsEffect()

    def _persist_project_facts_plan(
        self,
        plan: ProjectFactsWritePlan,
        *,
        operation_id: str,
    ):
        """Commit a prepared Project Facts effect after operation binding."""
        from minicode.memory_write_contract import ProjectFactsEffect

        try:
            observe_once = getattr(
                self._project_facts,
                "observe_dependencies_once",
                None,
            )
            if not callable(observe_once):
                logger.warning(
                    "MemoryPipeline: project facts store lacks idempotent write API"
                )
                return ProjectFactsEffect(
                    status="failed",
                    failure_code="project_facts_idempotency_unavailable",
                )

            effect = observe_once(
                list(plan.names),
                operation_id=operation_id,
                provenance=plan.provenance,
            )
            if effect.status == "committed":
                logger.info(
                    "MemoryPipeline: recorded %d project fact change(s): %s",
                    effect.added_count + effect.updated_count,
                    ", ".join(plan.names[:6]),
                )
            return effect
        except Exception:  # noqa: BLE001 - facts are advisory
            logger.warning("MemoryPipeline: project facts write failed safely")
            return ProjectFactsEffect(
                status="failed",
                failure_code="project_facts_write_failed",
            )

    def _find_recurred_lesson(self, claim: Any) -> tuple[str | None, bool, bool]:
        """Find an existing claim with the same semantic identity.

        Returns ``(entry_id, rejected, same_statement)``. A same-key changed
        statement is a correction candidate, not recurrence.
        """
        semantic_key = str(getattr(claim, "semantic_key", "") or "")
        statement = str(getattr(claim, "statement", "") or "").strip()
        if not semantic_key or not self._memory:
            return None, False, False
        from minicode.memory import MemoryScope

        try:
            entries = self._memory.memories[MemoryScope.PROJECT].entries
        except Exception:  # noqa: BLE001
            return None, False, False
        for entry in entries:
            metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
            reflection = metadata.get("structured_reflection")
            if not isinstance(reflection, dict):
                continue
            claims = reflection.get("claims")
            if not isinstance(claims, list):
                continue
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                if claim.get("semantic_key") == semantic_key:
                    rejected = (
                        getattr(entry, "approval_status", "") == "rejected"
                        or getattr(entry, "lifecycle_status", "") == "rejected"
                    )
                    same_statement = (
                        str(claim.get("statement", "")).strip() == statement
                    )
                    return entry.id, rejected, same_statement
        return None, False, False

    def _record_pending_user_facet(
        self, event_sink: Any = None, task_description: str = ""
    ) -> None:
        """Classify and record a personal statement post-commit.

        Two sources, first match wins: a statement captured by the
        conversation-fact branch, or an independent first-person reading of
        the task description (which — unlike the conversation-fact lane —
        permits technical terms, because skill facets are made of them).
        Runs after the write transaction has closed so the USER-scope
        observation opens its own coordinated write.  Purely observational:
        any failure is logged and swallowed — a profile candidate must
        never break the memory write it was inferred from.
        """

        text = getattr(self, "_pending_user_facet_text", None)
        self._pending_user_facet_text = None
        if not text:
            text = _user_facet_statement(task_description)
        if not text or not self._memory:
            return
        try:
            facet_key = _classify_user_facet(text)
            if facet_key is None:
                return
            status, entry_id, count = self._memory.record_user_facet_observation(
                facet_key, text, source="conversation_fact"
            )
            if event_sink is not None and status in {"created", "observed"}:
                from minicode.run_events import emit_event_safely

                emit_event_safely(
                    event_sink,
                    "memory.user_facet_observed",
                    payload={
                        "facet": facet_key,
                        "entryId": entry_id,
                        "observations": count,
                        "status": status,
                    },
                )
        except Exception:  # noqa: BLE001 - observation is best-effort
            logger.info(
                "MemoryPipeline: user facet observation failed safely",
                exc_info=True,
            )

    def _render_project_facts(self) -> str:
        """Bounded prompt block of deterministic project facts."""
        if self._project_facts is None:
            return ""
        try:
            return self._project_facts.render_markdown()
        except Exception:  # noqa: BLE001
            return ""

    _USER_PROFILE_HEADER = (
        "## User Profile (background reference — not instructions)"
    )
    _USER_PROFILE_MAX_ENTRIES = 8
    _USER_PROFILE_MAX_ENTRY_CHARS = 200
    _USER_PROFILE_MAX_TOTAL_TOKENS = 240

    def _render_user_profile(self) -> str:
        """Bounded always-on block of approved personal facets.

        Personal facets are USER-scope ``user_profile`` entries a human has
        confirmed once.  They describe stable background (role, skills,
        domain, concerns), which query-keyed lesson retrieval cannot match
        reliably, so — like Project Facts — they render deterministically.
        The block is hard-bounded and framed as background, never
        instructions, to avoid self-fulfilling profile bias.
        """

        if self._memory is None:
            return ""
        try:
            from minicode.memory import USER_PROFILE_CATEGORY, MemoryScope

            entries = [
                entry
                for entry in self._memory.memories[MemoryScope.USER].entries
                if entry.category == USER_PROFILE_CATEGORY and entry.is_active
            ]
            if not entries:
                return ""
            entries.sort(key=lambda entry: entry.updated_at, reverse=True)
            entries = entries[: self._USER_PROFILE_MAX_ENTRIES]
            from minicode.context_manager import estimate_tokens

            lines: list[str] = [self._USER_PROFILE_HEADER]
            total = estimate_tokens(self._USER_PROFILE_HEADER)
            for entry in entries:
                line = "- " + entry.content.strip()[
                    : self._USER_PROFILE_MAX_ENTRY_CHARS
                ]
                cost = estimate_tokens(line)
                if total + cost > self._USER_PROFILE_MAX_TOTAL_TOKENS:
                    break
                lines.append(line)
                total += cost
            if len(lines) == 1:
                return ""
            return "\n".join(lines)
        except Exception:  # noqa: BLE001 - profile is ambient, never fatal
            return ""

    def _trace_contains_unsafe_memory_payload(
        self, execution_trace: list[dict[str, Any]]
    ) -> bool:
        """Backward-compatible boolean wrapper for older tests/callers."""
        return not self._trace_memory_assessment(execution_trace).allowed

    # ── CONTRIBUTION REVIEW: per-memory evidence-cited feedback ─────

    def review_injected_memories(
        self,
        task_description: str,
        execution_trace: list[dict[str, Any]] | None = None,
        *,
        operation_id: str | None = None,
        observation_id: str | None = None,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ):
        """Adjudicate each memory rendered this Turn against the trace.

        Complements whole-turn :meth:`feedback`: instead of spreading one
        task-outcome boolean over every rendered entry, the reviewer reads the
        last committed prompt receipt plus the sanitized execution trace and
        returns per-memory verdicts (``helped`` / ``invalidated`` /
        ``undetermined``), then records fenced, idempotent feedback — small
        positive for cited help, corroborated independent-verification
        failure for cited refutation (the existing correction flow), and
        nothing when the contribution cannot be judged.
        """
        reviewer = self._contribution_reviewer
        if reviewer is None or self._contribution_review_recorded:
            return None
        receipt = self._last_committed_receipt
        if receipt is None or not getattr(receipt, "rendered_fragments", ()):
            return None
        effective_operation_id = (
            str(operation_id).strip()
            if isinstance(operation_id, str) and operation_id.strip()
            else (
                str(observation_id).strip()
                if isinstance(observation_id, str) and observation_id.strip()
                else f"contribution-review-{receipt.receipt_id}"
            )
        )
        # Consume the slot before running: a mid-review cancellation must not
        # trigger a second paid review for the same receipt in this Turn.
        self._contribution_review_recorded = True
        review = reviewer.review(
            task_description,
            receipt,
            execution_trace,
            operation_id=effective_operation_id,
            agent_budget=agent_budget,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )
        if self._memory is not None:
            from minicode.memory_contribution_reviewer import (
                apply_contribution_feedback,
            )

            try:
                apply_contribution_feedback(self._memory, review, receipt)
            except Exception:  # noqa: BLE001 - feedback stays best-effort
                logger.warning(
                    "Contribution review feedback application failed",
                    exc_info=True,
                )
        emit_event_safely(
            event_sink,
            "memory.contribution_reviewed",
            payload={
                "receiptId": review.receipt_id,
                "operationId": review.operation_id,
                "reviewSource": review.review_source,
                "failureCode": review.failure_code,
                "verdictCount": len(review.verdicts),
                "verdicts": [
                    {
                        "memoryId": verdict.memory_id,
                        "outcome": verdict.outcome,
                        "confidence": round(verdict.confidence, 3),
                        "basis": verdict.basis,
                        "downgradeReason": verdict.downgrade_reason,
                    }
                    for verdict in review.verdicts
                ],
            },
        )
        return review

    # ── FEEDBACK: Close the quality loop (F2) ────────────────────────

    def feedback(
        self,
        task_success: bool | str,
        injected_memory_ids: list[str] | None = None,
        *,
        verification_passed: int = 0,
        verification_failed: int = 0,
        verification_memory_ids: list[str] | None = None,
        observation_id: str | None = None,
    ) -> None:
        """Task outcome → memory utility. Closes the outermost learning loop.

        Success → boost injected memories (positive reinforcement).
        Failure → gentle decay (they may have misled the agent).

        ``verification_passed``/``verification_failed`` are an optional tally
        of independently executed test/build/lint/typecheck outcomes observed
        during this same turn. Counts alone do not identify which rendered
        Memory the verifier exercised. Corroborated feedback therefore also
        requires ``verification_memory_ids``: an exact, trusted binding to a
        subset of this turn's rendered IDs. The canonical Agent path currently
        omits that binding and records only ordinary whole-turn feedback rather
        than risking causal misattribution.
        """
        if not self._memory or self._feedback_recorded:
            return
        rendered_ids = list(self._last_injected_ids)
        if not rendered_ids:
            return
        rendered_versions = tuple(self._last_injected_versions)
        if not rendered_versions:
            logger.warning(
                "Memory feedback skipped because the materialized authority "
                "version is unavailable"
            )
            self._feedback_recorded = True
            return
        if (
            injected_memory_ids is not None
            and list(dict.fromkeys(injected_memory_ids)) != rendered_ids
        ):
            logger.warning(
                "Memory feedback rejected IDs outside this turn's rendered result"
            )
            return
        if isinstance(task_success, str):
            normalized = task_success.strip().lower()
            if normalized == "success":
                success: bool | None = True
            elif normalized in {"failed", "failure"}:
                success = False
            else:
                logger.info("Memory feedback skipped because turn outcome is unknown")
                return
        else:
            success = bool(task_success)
        if hasattr(self._memory, "record_feedback"):
            effective_observation_id = observation_id
            if effective_observation_id is None:
                # Legacy callers did not carry a Run ID. Give their one
                # Pipeline/Turn delivery a stable in-process identity so a
                # partial multi-scope commit can be retried safely. Entrypoints
                # that persist/retry across processes still pass their durable
                # Run ID explicitly.
                implicit_payload = json.dumps(
                    {
                        "nonce": self._feedback_delivery_nonce,
                        "receipts": sorted(self._committed_receipt_digests.items()),
                        "versions": sorted(rendered_versions),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                effective_observation_id = (
                    "pipeline-feedback-" + hashlib.sha256(implicit_payload).hexdigest()
                )
            feedback_kwargs: dict[str, object] = {
                "expected_versions": rendered_versions,
                "observation_id": effective_observation_id,
            }
            self._memory.record_feedback(
                rendered_ids,
                success,
                **feedback_kwargs,
            )
            self._feedback_recorded = True
            corroborated = verification_corroboration(
                verification_passed, verification_failed
            )
            bound_ids: list[str] = []
            if verification_memory_ids is not None:
                if not isinstance(verification_memory_ids, list) or any(
                    not isinstance(entry_id, str)
                    for entry_id in verification_memory_ids
                ):
                    logger.warning(
                        "Memory corroborated feedback rejected malformed verification binding"
                    )
                else:
                    candidate_ids = list(dict.fromkeys(verification_memory_ids))
                    if candidate_ids and all(
                        entry_id in rendered_ids for entry_id in candidate_ids
                    ):
                        bound_ids = candidate_ids
                    elif candidate_ids:
                        logger.warning(
                            "Memory corroborated feedback rejected IDs outside this turn's rendered result"
                        )
            bound_versions = tuple(
                version for version in rendered_versions if version[0] in bound_ids
            )
            if (
                corroborated is not None
                and bound_ids
                and not (isinstance(observation_id, str) and observation_id.strip())
            ):
                logger.warning(
                    "Memory corroborated feedback rejected missing observation identity"
                )
            elif (
                corroborated is not None
                and bound_ids
                and hasattr(self._memory, "record_corroborated_feedback")
            ):
                self._memory.record_corroborated_feedback(
                    bound_ids,
                    corroborated,
                    source="independent_verification",
                    observation_id=observation_id,
                    expected_versions=bound_versions,
                )

    @property
    def last_retrieval_result(self):
        """The exact result whose rendered IDs belong to the current turn."""
        return self._last_retrieval_result

    # ── MAINTAIN: Background optimization ───────────────────────────

    def maintain(
        self,
        force: bool = False,
        *,
        record_only: bool = False,
        completion_already_recorded: bool = False,
        completion_id: str | None = None,
        agent_depth: int = 0,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any] | None:
        """Run background memory optimization.

        Consolidates insights, archives duplicates, validates against codebase,
        promotes/demotes tiers, and links related memories.

        Returns CuratorReport as dict, or None if not ready.
        """
        if not isinstance(record_only, bool):
            raise TypeError("record_only must be a boolean")
        if not isinstance(completion_already_recorded, bool):
            raise TypeError("completion_already_recorded must be a boolean")
        if not self._curator:
            return None
        schedule = self._curator_schedule
        if schedule is None and not force:
            # Fresh per-Turn Pipelines make the legacy Curator counter
            # non-authoritative. Without a durable workspace store, abstain.
            return None
        prepared_outbox = False
        settled_outbox = False
        current_delivered_by_outbox = completion_already_recorded
        if not force and (
            agent_depth != 0
            or not isinstance(completion_id, str)
            or len(completion_id) != 64
            or any(character not in "0123456789abcdef" for character in completion_id)
        ):
            return None
        if not force and not completion_already_recorded:
            # Count the committed root task before waiting for the Memory
            # mutation lock. Even if maintenance is busy, its debt survives.
            prepare = getattr(
                event_sink,
                "prepare_curator_completion",
                None,
            )
            if callable(prepare):
                try:
                    prepare_result = prepare(completion_id)
                except Exception:
                    # Dashboard Turns have their own committed-Turn outbox;
                    # optional Run observation must never block delivery.
                    prepare_result = False
                if prepare_result == "settled":
                    settled_outbox = True
                elif prepare_result is False:
                    # Scheduler delivery may still establish the final durable
                    # state. The absence of an optional observation sidecar
                    # must not make this path weaker than event_sink=None.
                    prepare_result = None
                prepared_outbox = prepare_result is True

        if schedule is not None and event_sink is not None:
            replay = getattr(
                event_sink,
                "replay_pending_curator_completions",
                None,
            )
            replay_supports_release = False
            if callable(replay):
                try:
                    parameters = inspect.signature(replay).parameters.values()
                    replay_supports_release = any(
                        parameter.name == "release"
                        or parameter.kind is inspect.Parameter.VAR_KEYWORD
                        for parameter in parameters
                    )
                except (TypeError, ValueError):
                    replay_supports_release = False
            if callable(replay) and replay_supports_release:
                try:
                    with schedule.delivery_fence():
                        reconcile_dashboard = getattr(
                            event_sink,
                            "reconcile_dashboard_curator_completions",
                            None,
                        )
                        if callable(reconcile_dashboard) and (
                            reconcile_dashboard(schedule) is not True
                        ):
                            return None

                        def deliver(item):
                            return schedule.record_completion(
                                item,
                                pin=True,
                                promote_existing=True,
                            )

                        if prepared_outbox:
                            replay_complete = replay(
                                deliver,
                                defer_completion_id=completion_id,
                                release=schedule.acknowledge_completion,
                            )
                        else:
                            replay_complete = replay(
                                deliver,
                                release=schedule.acknowledge_completion,
                            )
                except Exception:
                    # The current CLI receipt was prepared before this replay,
                    # so both it and every older item remain durable.
                    return None
                if replay_complete is not True:
                    # Never deliver the newly enqueued receipt while an older
                    # delivery or exhaustive-scan proof remains uncertain.
                    return None
                current_delivered_by_outbox = prepared_outbox

        if not force:
            if settled_outbox:
                current_delivered_by_outbox = True
            if not current_delivered_by_outbox and prepared_outbox:
                # Compatibility fallback for an event sink that can prepare
                # but cannot replay. Keep the complete five-stage handshake
                # under the same workspace delivery fence.
                acknowledge = getattr(
                    event_sink,
                    "acknowledge_curator_completion",
                    None,
                )
                settle = getattr(
                    event_sink,
                    "settle_curator_completion",
                    None,
                )
                if not callable(acknowledge) or not callable(settle):
                    return None
                try:
                    with schedule.delivery_fence():
                        schedule.record_completion(
                            completion_id,
                            pin=True,
                            promote_existing=True,
                        )
                        if not bool(acknowledge(completion_id)):
                            return None
                        schedule.acknowledge_completion(completion_id)
                        if not bool(settle(completion_id)):
                            return None
                except Exception:
                    return None
                current_delivered_by_outbox = True
            if not current_delivered_by_outbox:
                try:
                    schedule.record_completion(completion_id)
                except Exception:
                    return None
            if record_only:
                return None

        def execute_claimed_cycle() -> dict[str, Any] | None:
            claim = None
            if schedule is not None:
                try:
                    decision = schedule.claim_due(
                        interval=self._curator.run_interval_tasks,
                        force=force,
                    )
                    claim = decision.claim
                except Exception:
                    return None
                if claim is None:
                    return None

            claim_checkpoint = None
            if claim is not None:
                from minicode.curator_schedule import CuratorClaimLost

                lease_seconds = float(getattr(schedule, "lease_seconds", 1.0))
                renewal_period = max(
                    0.05,
                    min(30.0, lease_seconds * 0.25),
                )
                next_renewal = time.monotonic() + renewal_period

                def renew_execution_claim() -> None:
                    nonlocal next_renewal
                    current = time.monotonic()
                    if current < next_renewal:
                        return
                    try:
                        renewed = schedule.renew_claim(
                            claim.claim_id,
                            allow_expired=bool(
                                self._memory is not None
                                and getattr(
                                    self._memory,
                                    "in_write_transaction",
                                    False,
                                )
                            ),
                        )
                    except Exception as error:
                        raise CuratorClaimLost(
                            "curator execution lease could not be renewed"
                        ) from error
                    if renewed is None:
                        raise CuratorClaimLost(
                            "curator execution lease is no longer current"
                        )
                    next_renewal = current + renewal_period

                claim_checkpoint = renew_execution_claim

            self._maintain_count += 1
            try:
                checkpoint_model_call(
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
                if claim_checkpoint is not None:
                    claim_checkpoint()
                report = self._curator.run_cycle(
                    force=True,
                    agent_budget=agent_budget,
                    event_sink=event_sink,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                    claim_checkpoint=claim_checkpoint,
                )
                if claim_checkpoint is not None:
                    claim_checkpoint()
                report_status = str(getattr(report, "status", "failed")).lower()
                if claim is not None:
                    schedule.finish_claim(
                        claim.claim_id,
                        outcome=(
                            report_status
                            if report_status in {"completed", "partial"}
                            else "failed"
                        ),
                    )
                self.save_state()
                return report.to_dict()
            except TurnCancellationRequested:
                if claim is not None:
                    try:
                        schedule.finish_claim(
                            claim.claim_id,
                            outcome="cancelled",
                        )
                    except Exception:
                        pass
                raise
            except Exception:
                if claim is not None:
                    try:
                        schedule.finish_claim(
                            claim.claim_id,
                            outcome="failed",
                        )
                    except Exception:
                        pass
                return None

        def execute_with_memory_fence() -> dict[str, Any] | None:
            coordinated_write = getattr(
                self._memory,
                "coordinated_write",
                None,
            )
            if callable(coordinated_write) and not getattr(
                self._memory,
                "in_write_transaction",
                False,
            ):
                from minicode.memory import MemoryScope

                try:
                    return coordinated_write(
                        tuple(MemoryScope),
                        execute_claimed_cycle,
                    )
                except TurnCancellationRequested:
                    raise
                except Exception:
                    return None
            return execute_claimed_cycle()

        # One global order prevents a Pipeline worker and an administrative
        # worker from deadlocking each other:
        # workspace execution fence -> Memory authorities -> schedule ledger.
        # A caller that already owns a Memory transaction entered in the
        # opposite order cannot safely start scheduled maintenance; its debt
        # remains durable for a later task.
        if schedule is not None:
            if getattr(self._memory, "in_write_transaction", False):
                return None
            try:
                with schedule.execution_fence():
                    return execute_with_memory_fence()
            except TurnCancellationRequested:
                raise
            except Exception:
                return None
        return execute_with_memory_fence()

    # ── Internal ────────────────────────────────────────────────────

    def _get_active_domains(
        self, current_files: list[str], task_description: str
    ) -> list[str]:
        try:
            from minicode.domain_classifier import get_active_domain_values

            return get_active_domain_values(
                current_files=current_files,
                intent_text=task_description,
            )
        except Exception:
            return []

    # ── T1: Memory Value Function + Adaptive Cooldown ───────────────

    # Formal definition:
    #   V(m, t, c) = relevance(m, t) × freshness(m) × utility(m, c)
    #   where:
    #     relevance(m, t) = BM25_score(m, t) ∈ [0, 1]
    #     freshness(m)    = exp(-age_days / τ) with τ = 30 days
    #     utility(m, c)   = 1 + α × I(m was used in similar context c)
    #
    # Adaptive cooldown:  τ_cool(c) = τ_base × (1 - context_pressure)
    #   High context pressure → shorter cooldown → faster injection
    #   Low context pressure → longer cooldown → less noise

    _TAU_FRESHNESS = 30.0  # days
    _ALPHA_UTILITY = 0.15

    def _memory_value(
        self, bm25_score: float, entry: Any, context_usage: float = 0.5
    ) -> float:
        """Compute V(m, t, c) for a single memory entry."""
        import math

        age_days = (time.time() - getattr(entry, "updated_at", time.time())) / 86400.0
        freshness = math.exp(-age_days / self._TAU_FRESHNESS)
        utility = 1.0 + self._ALPHA_UTILITY * math.log1p(
            getattr(entry, "usage_count", 0)
        )
        return bm25_score * freshness * utility

    def _adaptive_cooldown(self, context_usage: float) -> float:
        """Compute adaptive injection cooldown based on context pressure.

        τ_cool = τ_base × (1 - context_pressure), clamped to [5s, 120s].
        High pressure → shorter cooldown (memory is more needed).
        """
        base = 30.0  # seconds
        return max(5.0, min(120.0, base * (1.0 - context_usage)))

    # ── T2: Query Reformulation ─────────────────────────────────────

    # When BM25 returns poor results (top score < τ_low), attempt
    # reformulation: strip stopwords, try domain synonyms, expand abbreviations.
    # Max 3 attempts. If no improvement, keep original results.

    _QUERY_STOPWORDS = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "and",
        "or",
        "but",
        "not",
        "this",
        "that",
        "it",
        "i",
        "we",
        "add",
        "create",
        "make",
        "implement",
        "build",
        "set",
        "get",
    }

    _QUERY_REFORMULATIONS = [
        lambda q: " ".join(
            w for w in q.lower().split() if w not in MemoryPipeline._QUERY_STOPWORDS
        ),
        lambda q: q.lower().replace("  ", " ").strip(),
    ]

    def _reformulate_query(self, query: str) -> list[str]:
        """Generate reformulated query variants."""
        variants = [query]
        for reformulate in self._QUERY_REFORMULATIONS:
            v = reformulate(query)
            if v and v != query and v not in variants:
                variants.append(v)
        return variants[:3]

    def _try_search_with_reformulation(
        self, task_description: str, active_domains: list[str] | None, max_results: int
    ) -> list[Any]:
        """Search with query reformulation fallback for poor initial results."""

        entries = self._memory.search(
            task_description,
            limit=max_results,
            active_domains=active_domains,
        )

        if entries and len(entries) >= 3:
            return entries  # Good enough

        # Try reformulations
        for variant in self._reformulate_query(task_description):
            if variant == task_description:
                continue
            alt = self._memory.search(
                variant,
                limit=max_results,
                active_domains=active_domains,
            )
            if len(alt) > len(entries):
                logger.debug(
                    "Query reformulation improved: %d → %d results",
                    len(entries),
                    len(alt),
                )
                return alt

        return entries

    # ── T3: Spreading Activation ────────────────────────────────────

    # When memory m is retrieved, its related_to neighbors also receive
    # activation: score_neighbor += score(m) × decay × sim(m, neighbor)
    # depth=1, decay=0.5. This surfaces related memories the user might
    # not have explicitly searched for.

    _SPREAD_DECAY = 0.5
    _SPREAD_THRESHOLD = 0.3

    def _spread_activation(self, entries: list[Any]) -> list[Any]:
        """Enrich results via spreading activation through related_to graph.

        Concatenates directly-linked neighbors with decayed relevance.
        """
        if not self._memory or not entries:
            return entries

        seen_ids = {e.id for e in entries}
        neighbors = []

        for entry in entries[:5]:  # Only spread from top 5
            if not hasattr(entry, "related_to") or not entry.related_to:
                continue
            for rid in entry.related_to:
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                # Find neighbor in memory
                for scope_name in ["project", "local", "user"]:
                    try:
                        from minicode.memory import MemoryScope

                        scope = MemoryScope(scope_name)
                        if scope in self._memory.memories:
                            self._memory.memories[scope]._ensure_cache_valid()
                            nbr = self._memory.memories[scope]._id_index.get(rid)
                            if nbr and getattr(nbr, "is_active", True):
                                neighbors.append(nbr)
                                break
                    except (ValueError, KeyError):
                        continue

        return entries + neighbors
