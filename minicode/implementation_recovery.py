"""A smaller repair request view without discarding the Executor transcript.

This module neither grants tools nor changes a budget. The owner can use the
projection when deciding whether a repair and its handoff still fit the same
allowance. Calls, results, authority documents, and the complete canonical
transcript remain available to their existing owners.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any

_READ_TOOLS = frozenset({"read_file", "grep_files"})
_RECENT_RESULTS = 5
_CONTEXT_RETIRED = (
    "[The initial source inspection packet is retained in the complete "
    "Executor transcript and omitted from this repair request. Its source "
    "observations may predate the current candidate. Task instructions, "
    "tool calls, recent reads, and implementation feedback remain below.]"
)
_READ_RETIRED = (
    "[Earlier inspection output is retained in the complete Executor "
    "transcript and omitted from this repair request. The original tool "
    "arguments remain unchanged. Read the authorized current file again "
    "if exact text from this window is needed.]"
)
_SOURCE_HEADER = re.compile(
    r"^FILE (?P<path>[^\r\n\x00]+?) \| sha256=[0-9a-f]{64} \| [0-9]+ lines"
    r"(?: \| local imported API)?$", re.MULTILINE,
)
_SOURCE_BEGIN = "\n=== IMPLEMENTATION SOURCE CONTEXT ===\n"
_SOURCE_END = "\n=== END IMPLEMENTATION SOURCE CONTEXT ===\n"


def project_implementation_progress_messages(
    messages: list[dict[str, Any]],
    *,
    changed_paths: frozenset[str],
) -> list[dict[str, Any]]:
    """Retire only initial file versions the host observed actually changing.

    ``changed_paths`` is the host's workspace-relative byte-change observation,
    never a model declaration or a successful tool-result claim. Empty input
    cannot activate projection. No filesystem access, permissions, read-result
    shortening, or canonical-history replacement occurs here. Unchanged file
    and API sections remain exact, including evidence not subsequently read.
    """
    paths = {
        path for path in changed_paths
        if isinstance(path, str) and path and "\x00" not in path
        and not PurePosixPath(path).is_absolute()
        and ".." not in PurePosixPath(path).parts
        and str(PurePosixPath(path)) == path
    }
    projected = [dict(message) for message in messages]
    if not paths:
        return projected
    for index, message in enumerate(messages):
        content = message.get("content")
        if (message.get("role") != "user"
                or message.get("_implementation_context") is not True
                or not isinstance(content, str)
                or not content.startswith(_SOURCE_BEGIN)
                or not content.endswith(_SOURCE_END)):
            continue
        headers = list(_SOURCE_HEADER.finditer(content))
        footer = len(content) - len(_SOURCE_END)
        pieces: list[str] = []
        previous = 0
        for position, header in enumerate(headers):
            if header["path"] not in paths:
                continue
            boundary = headers[position + 1].start() if position + 1 < len(headers) else footer
            end = header.end()
            # Unavailable-path diagnostics can sit between FILE sections.
            # Only the renderer's own source/outline lines belong to this
            # file; preserve every other line, including missing new tests.
            for line in content[end:boundary].splitlines(keepends=True):
                if (line.strip() and not line.startswith(("Outline: ", "AST unavailable;"))
                        and re.fullmatch(r"(?:[0-9]+ \| .*|\[OMITTED lines [0-9]+-[0-9]+\])\n?", line) is None):
                    break
                end += len(line)
            replacement = (
                header[0] + "\n[Initial source version omitted after a host-observed byte change. "
                "The original section remains in the canonical transcript; read the authorized "
                "current file for updated source. All explicit read results remain below.]\n"
            )
            if len(replacement) >= end - header.start():
                continue
            pieces.extend((content[previous:header.start()], replacement))
            previous = end
        if pieces:
            pieces.append(content[previous:])
            projected[index]["content"] = "".join(pieces)
    # Reuse the existing provenance-checked projections without invoking the
    # recovery view's whole-packet or same-path read retirement.
    return _retire_superseded_edit_turns(_project_implementation_feedback(projected))


def project_recovery_messages(
    messages: list[dict[str, Any]],
    *,
    changed_paths: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Project expendable old inspection text, preserving message boundaries.

    Every message is shallow-copied; retained call arguments are left untouched.
    The latest successful read/search result for each literal path and the five latest
    tool results retain their exact bodies. Failed reads, unpaired results,
    user corrections, and system/authority messages are retained. Only host-
    marked superseded check previews may be removed from non-read results;
    identities, outcomes and digest metadata remain. Superseded, wholly successful edit-only provider turns may be
    replaced as a whole with host summaries retaining their exact results;
    no retained call is rewritten and no result is left without its call.
    Only host-marked initial inspection packets and delivered superseded
    check previews are exempt from user-message preservation. When host-observed ``changed_paths`` are
    supplied, preserve initial sections for every unchanged source/API/test;
    only the existing progress projection retires changed source versions.
    No disk access or model summarization is involved, and applying this
    projection again is safe.
    """
    if changed_paths is not None:
        messages = project_implementation_progress_messages(messages, changed_paths=changed_paths)
    projected = [dict(message) for message in messages]
    result_indices = [
        index for index, message in enumerate(messages)
        if message.get("role") == "tool_result"
    ]
    protected = set(result_indices[-_RECENT_RESULTS:])
    calls: dict[str, dict[str, Any]] = {}
    read_paths: dict[int, str] = {}
    latest_path: dict[str, int] = {}
    for index, message in enumerate(messages):
        identity = message.get("toolUseId")
        if message.get("role") == "assistant_tool_call":
            if isinstance(identity, str) and identity:
                calls[identity] = message
            continue
        if (message.get("role") != "tool_result"
                or message.get("toolName") not in _READ_TOOLS
                or not isinstance(identity, str)):
            continue
        call = calls.get(identity)
        if call is None or call.get("toolName") != message.get("toolName"):
            continue
        arguments = call.get("input")
        path = arguments.get("path") if isinstance(arguments, dict) else None
        if not isinstance(path, str) or not path:
            continue
        read_paths[index] = path
        if message.get("isError") is False:
            latest_path[path] = index
    protected.update(latest_path.values())

    for index, message in enumerate(messages):
        if (message.get("role") == "user"
                and message.get("_implementation_context") is True
                and isinstance(message.get("content"), str)
                and changed_paths is None):
            projected[index]["content"] = _CONTEXT_RETIRED
        elif (index in read_paths and index not in protected
                and message.get("isError") is False
                and message.get("_must_deliver") is not True
                and isinstance(message.get("content"), str)):
            # A short observation costs less than the explanatory marker and
            # may already be a dedup stub. Keep it rather than grow the view.
            if len(message["content"]) > len(_READ_RETIRED):
                projected[index]["content"] = _READ_RETIRED
    return _retire_superseded_edit_turns(_project_implementation_feedback(projected))


def _bound_batch_feedback(messages: list[dict[str, Any]]) -> dict[int, tuple[dict, str, bool]]:
    """Recognize host batches, bound to unique, preceding write observations.

    The private flag is necessary; matching text alone is never provenance.
    A later independent provider turn witnesses delivery of the older batch.
    This deliberately preserves every batch from the current response, even
    when several write groups/checks completed before its next model request.
    """
    marker = "=== IMPLEMENTATION BATCH CHECK FEEDBACK ===\n"
    calls: dict[str, list[tuple[int, dict]]] = {}
    results: dict[str, list[tuple[int, dict]]] = {}
    documents: dict[int, dict] = {}
    batch_counts: dict[str, int] = {}
    call_counts: dict[str, int] = {}
    for index, message in enumerate(messages):
        identity = message.get("toolUseId")
        role = message.get("role")
        if isinstance(identity, str) and identity:
            if role == "assistant_tool_call":
                calls.setdefault(identity, []).append((index, message))
            elif role == "tool_result":
                results.setdefault(identity, []).append((index, message))
        content = message.get("content")
        if (role != "user" or message.get("_implementation_batch_feedback") is not True
                or not isinstance(content, str) or not content.startswith(marker)):
            continue
        try:
            document = json.loads(content[len(marker):])
        except (ValueError, TypeError):
            continue
        if not isinstance(document, dict):
            continue
        batch_id = document.get("batchId")
        if not isinstance(batch_id, str) or re.fullmatch(r"[0-9a-f]{32}", batch_id) is None:
            continue
        documents[index] = document
        batch_counts[batch_id] = batch_counts.get(batch_id, 0) + 1
        for field in ("changedCallIds", "uncertainWriteCallIds"):
            identities = document.get(field)
            if isinstance(identities, list):
                for identity in identities:
                    if isinstance(identity, str):
                        call_counts[identity] = call_counts.get(identity, 0) + 1

    bound: dict[int, tuple[dict, str, bool]] = {}
    for index, document in documents.items():
        changed, uncertain = document.get("changedCallIds"), document.get("uncertainWriteCallIds")
        patch, checks = document.get("patchDigest"), document.get("checks")
        if (messages[index].get("_must_deliver") is True
                or type(document.get("batchVersion")) is not int or document["batchVersion"] != 1
                or document.get("verification") != "implementation_feedback"
                or batch_counts[document["batchId"]] != 1
                or not isinstance(changed, list) or not isinstance(uncertain, list)
                or not changed + uncertain or not all(isinstance(v, str) and v for v in changed + uncertain)
                or not isinstance(patch, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", patch) is None
                or not isinstance(checks, list) or not checks):
            continue
        turns = set()
        for identity in changed + uncertain:
            paired_calls, paired_results = calls.get(identity, []), results.get(identity, [])
            if call_counts[identity] != 1 or len(paired_calls) != 1 or len(paired_results) != 1:
                break
            call_index, call = paired_calls[0]
            result_index, result = paired_results[0]
            turn = call.get("assistantTurnId")
            if (not call_index < result_index < index
                    or call.get("toolName") not in {"edit_file", "write_file", "executor_test"}
                    or result.get("toolName") != call.get("toolName")
                    or call.get("_must_deliver") is True or result.get("_must_deliver") is True
                    or type(result.get("isError")) is not bool
                    or identity in changed and result["isError"]
                    or not isinstance(turn, str) or not turn):
                break
            turns.add(turn)
        else:
            if len(turns) != 1:
                continue
            check_ids, sequences, statuses = set(), set(), []
            for receipt in checks:
                if not isinstance(receipt, dict):
                    break
                evidence, argv = receipt.get("evidence"), receipt.get("argv")
                identity, sequence, status = receipt.get("checkId"), receipt.get("observationSequence"), receipt.get("status")
                if (not isinstance(identity, str) or not identity or identity in check_ids
                        or receipt.get("verification") != "implementation_feedback"
                        or receipt.get("patchDigest") != patch
                        or type(sequence) is not int or sequence <= 0 or sequence in sequences
                        or status not in {"passed", "failed", "inconclusive"}
                        or not isinstance(argv, list) or not argv
                        or not all(isinstance(arg, str) and "\0" not in arg for arg in argv)
                        or not isinstance(evidence, dict) or evidence.get("checkId") != identity
                        or evidence.get("argv") != argv or evidence.get("status") != status
                        or evidence.get("commandDigest") != "sha256:" + hashlib.sha256("\0".join(argv).encode()).hexdigest()):
                    break
                check_ids.add(identity)
                sequences.add(sequence)
                statuses.append(status)
            else:
                expected = "failed" if "failed" in statuses else "passed" if all(s == "passed" for s in statuses) else "inconclusive"
                if document.get("status") != expected:
                    continue
                turn = next(iter(turns))
                delivered = any(position > index and call.get("assistantTurnId") != turn
                                and isinstance(call.get("assistantTurnId"), str) and call["assistantTurnId"]
                                for observations in calls.values() for position, call in observations)
                bound[index] = (document, turn, delivered)
    return bound


def _project_implementation_feedback(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Retain latest checks and retire only older captured stream previews.

    Eligibility is an internal boolean set by actual host implementation
    checks. Neither text that resembles a receipt nor an ordinary failed
    tool result can activate this projection. Receipt identities, command
    arguments, outcomes, patch bindings, and stream hashes remain unchanged.
    """
    marker = "=== IMPLEMENTATION CHECK FEEDBACK ==="
    parsed: dict[int, tuple[Any, list, int, int, str]] = {}
    latest: dict[tuple[str, str], str] = {}
    observations: dict[tuple[str, str], list[tuple[str, Any]]] = {}
    call_groups: dict[str, tuple[str, str] | None] = {}
    for message in messages:
        if message.get("role") != "assistant_tool_call":
            continue
        identity, turn = message.get("toolUseId"), message.get("assistantTurnId")
        if isinstance(identity, str) and identity:
            if identity in call_groups:
                call_groups[identity] = None
            elif isinstance(turn, str) and turn and isinstance(message.get("toolName"), str):
                call_groups[identity] = (message["toolName"], turn)

    def key(receipt: Any) -> tuple[str, str] | None:
        if not isinstance(receipt, dict):
            return None
        identity = receipt.get("checkId")
        evidence = receipt.get("evidence")
        digest = evidence.get("commandDigest") if isinstance(evidence, dict) else None
        if (not isinstance(identity, str) or not identity
                or not isinstance(digest, str) or not digest
                or not isinstance(receipt.get("patchDigest"), str)
                or receipt.get("status") not in {"passed", "failed", "inconclusive"}):
            return None
        return identity, digest

    batches = _bound_batch_feedback(messages)
    for index, message in enumerate(messages):
        if index in batches:
            payload, turn, _delivered = batches[index]
            receipts = payload["checks"]
            parsed[index] = (payload, receipts, len("=== IMPLEMENTATION BATCH CHECK FEEDBACK ===\n"),
                             len(message["content"]), turn)
            for receipt in receipts:
                identity = key(receipt)
                latest[identity] = turn
                observations.setdefault(identity, []).append((turn, receipt["observationSequence"]))
            continue
        if (message.get("role") != "tool_result"
                or type(message.get("_implementation_checks_failed")) is not bool
                or message.get("_must_deliver") is True
                or not isinstance(message.get("content"), str)):
            continue
        identity = message.get("toolUseId")
        group = call_groups.get(identity) if isinstance(identity, str) else None
        if group is None or group[0] != message.get("toolName"):
            continue
        content = message["content"]
        if message.get("toolName") == "executor_check":
            start = 0
        elif (message.get("toolName") in {"edit_file", "write_file", "executor_test"}
              and marker in content):
            start = content.index(marker) + len(marker)
        else:
            continue
        start += len(content[start:]) - len(content[start:].lstrip())
        try:
            payload, length = json.JSONDecoder().raw_decode(content[start:])
        except (ValueError, TypeError):
            continue
        receipts = payload if isinstance(payload, list) else [payload]
        if not receipts or not all(isinstance(receipt, dict) for receipt in receipts):
            continue
        parsed[index] = (payload, receipts, start, start + length, group[1])
        for receipt in receipts:
            identity = key(receipt)
            if identity is not None:
                latest[identity] = group[1]
                observations.setdefault(identity, []).append(
                    (group[1], receipt.get("observationSequence")))

    last_observation: dict[tuple[str, str], int] = {}
    for identity, recorded in observations.items():
        sequences = [sequence for turn, sequence in recorded if turn == latest[identity]]
        if sequences and all(type(sequence) is int and sequence > 0 for sequence in sequences):
            last_observation[identity] = max(sequences)

    projected = list(messages)
    for index, (payload, receipts, start, end, turn) in parsed.items():
        if index in batches and not batches[index][2]:
            continue
        changed = False
        for receipt in receipts:
            identity = key(receipt)
            if identity is None:
                continue
            if latest[identity] == turn:
                # A turn may contain parallel checks whose result delivery
                # follows call order. A host sequence orders observations
                # inside this turn; absent/malformed numbers retain the whole
                # group, and ties retain every tied result. Never compare
                # sequences across turns: a renewed capability can restart
                # its counter. Latest observed output is still not proof that
                # the current candidate passes the independent final Verifier.
                sequence = last_observation.get(identity)
                if sequence is None or receipt.get("observationSequence") == sequence:
                    continue
            omitted = False
            for name in ("stdout", "stderr"):
                stream = receipt["evidence"].get(name)
                if not isinstance(stream, dict):
                    continue
                for preview in ("preview", "previewBase64"):
                    if isinstance(stream.get(preview), str):
                        del stream[preview]
                        omitted = True
            if omitted:
                receipt["outputProjection"] = (
                    "Historical stream previews omitted; complete results remain in the canonical transcript"
                )
                changed = True
        if changed:
            message = messages[index]
            content = message["content"]
            projected_content = (content[:start] + json.dumps(payload, ensure_ascii=False)
                                 + content[end:])
            if len(projected_content) < len(content):
                projected[index] = {**message, "content": projected_content}
    return projected


def _is_complete_source_read(call: dict[str, Any], result: dict[str, Any], path: str) -> bool:
    """Recognize the exact character-mode envelope emitted by read_file.

    Line windows, compacted results and a bare TRUNCATED flag cannot prove
    that the replacement source is still present in this request view.
    """
    arguments = call.get("input")
    content = result.get("content")
    if (call.get("toolName") != "read_file"
            or not isinstance(arguments, dict)
            or "start_line" in arguments or "end_line" in arguments
            or arguments.get("offset", 0) != 0
            or not isinstance(content, str)):
        return False
    fields = content.split("\n", 5)
    if (len(fields) != 6 or fields[0] != f"FILE: {path}"
            or fields[1] != "OFFSET: 0" or fields[4] != "TRUNCATED: no"
            or re.fullmatch(r"END: [0-9]+", fields[2]) is None
            or re.fullmatch(r"TOTAL_CHARS: [0-9]+", fields[3]) is None):
        return False
    size = str(len(fields[5]))
    return fields[2] == f"END: {size}" and fields[3] == f"TOTAL_CHARS: {size}"


def _exact_edit_parts(call: dict[str, Any]) -> tuple[str, str] | None:
    """Accept only unambiguous, unique, exact replacement arguments."""
    arguments = call.get("input")
    if (not isinstance(arguments, dict) or "search" in arguments or "replace" in arguments
            or any(name in arguments and arguments[name] is not False
                   for name in ("fuzzy", "replace_all", "replaceAll"))):
        return None
    old, new = arguments.get("old"), arguments.get("new")
    # edit_file normalizes CRLF. Do not infer byte-exact coverage through a
    # normalization or through a deletion's empty replacement.
    if any(not isinstance(value, str) or not value or "\r" in value for value in (old, new)):
        return None
    return old, new


def _retire_superseded_edit_turns(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Retire old edit arguments only at a complete provider turn boundary.

    A successful edit result can still contain failed implementation checks.
    Preserve all result text received by this stage, regardless of those
    conclusions. The preceding host-feedback projection may already have
    retired historical stream previews while retaining their check metadata.
    Every affected path needs a later independent, successful complete read
    still present in this view. A single-edit turn can also be replaced by the
    immediately following unique exact edit consuming its entire new block.
    Merely editing the same file does not prove replacement of that region.
    Failed edits, latest provider
    turn, mixed-tool turns, and malformed tool transcripts remain exact.
    This is a request view, never a replacement of the owning transcript or
    its audit evidence.
    """
    calls_by_id: dict[str, list[int]] = {}
    results_by_id: dict[str, list[int]] = {}
    turns: dict[str, list[int]] = {}
    last_turn: str | None = None
    for index, message in enumerate(messages):
        identity = message.get("toolUseId")
        role = message.get("role")
        if role == "tool_result":
            if isinstance(identity, str) and identity:
                results_by_id.setdefault(identity, []).append(index)
        elif role == "assistant_tool_call":
            if isinstance(identity, str) and identity:
                calls_by_id.setdefault(identity, []).append(index)
            turn = message.get("assistantTurnId")
            if isinstance(turn, str) and turn:
                turns.setdefault(turn, []).append(index)
                last_turn = turn
    # Match the read-window projection's latest observation, including
    # ambiguous pairs. An ambiguous/latest partial read must not permit an
    # earlier complete read to retire edits before that read itself is retired.
    latest_read: dict[str, int] = {}
    prior_calls: dict[str, dict[str, Any]] = {}
    for index, message in enumerate(messages):
        identity = message.get("toolUseId")
        if not isinstance(identity, str) or not identity:
            continue
        if message.get("role") == "assistant_tool_call":
            prior_calls[identity] = message
            continue
        call = prior_calls.get(identity)
        if (message.get("role") != "tool_result" or call is None
                or message.get("toolName") not in _READ_TOOLS
                or call.get("toolName") != message.get("toolName")
                or message.get("isError") is not False):
            continue
        arguments = call.get("input")
        path = arguments.get("path") if isinstance(arguments, dict) else None
        if isinstance(path, str) and path:
            latest_read[path] = index

    successful_edits: dict[int, tuple[int, str]] = {}
    complete_reads: dict[str, tuple[int, str]] = {}
    tool_positions = [index for index, message in enumerate(messages)
                      if message.get("role") in {"assistant_tool_call", "tool_result"}]
    next_tool = dict(zip(tool_positions, tool_positions[1:]))
    for identity, call_indices in calls_by_id.items():
        result_positions = results_by_id.get(identity, ())
        if len(call_indices) != 1 or len(result_positions) != 1:
            continue
        call_index, result_index = call_indices[0], result_positions[0]
        call, result = messages[call_index], messages[result_index]
        arguments = call.get("input")
        path = arguments.get("path") if isinstance(arguments, dict) else None
        if (result.get("toolName") != call.get("toolName")
                or result.get("isError") is not False
                or result_index <= call_index
                or not isinstance(result.get("content"), str)
                or not isinstance(path, str) or not path):
            continue
        if call.get("toolName") == "edit_file":
            successful_edits[call_index] = (result_index, path)
        elif (result_index == latest_read.get(path)
                and isinstance(call.get("assistantTurnId"), str)
                and call["assistantTurnId"]
                and _is_complete_source_read(call, result, path)):
            complete_reads[path] = (call_index, call["assistantTurnId"])

    replacements: dict[int, tuple[int, dict[str, Any]]] = {}
    for turn, call_indices in turns.items():
        if turn == last_turn or any(index not in successful_edits for index in call_indices):
            continue
        result_positions = [successful_edits[index][0] for index in call_indices]
        begin, end = min(call_indices), max(result_positions)
        group_indices = set(call_indices) | set(result_positions)
        # No user instructions, other tool results, or interrupted provider
        # turns may be swallowed by this substitution.
        if group_indices != set(range(begin, end + 1)):
            continue
        if any(messages[index].get("_must_deliver") is True for index in group_indices):
            continue
        read_replaced = all(path in complete_reads
                            and complete_reads[path][0] > end
                            and complete_reads[path][1] != turn
                            for _result_index, path in (successful_edits[index] for index in call_indices))
        block_replaced = False
        successor = next_tool.get(end)
        if len(call_indices) == 1 and successor in successful_edits:
            later = messages[successor]
            later_turn = later.get("assistantTurnId")
            earlier_parts = _exact_edit_parts(messages[begin])
            later_parts = _exact_edit_parts(later)
            # Any intervening tool interaction is a barrier, including writes
            # through path aliases or opaque tools. Chained retirements end at
            # a retained edit (latest/mixed turn) or a still-visible full read.
            block_replaced = (
                isinstance(later_turn, str) and later_turn != turn
                and len(turns.get(later_turn, ())) == 1
                and next_tool.get(successor) == successful_edits[successor][0]
                and successful_edits[successor][1] == successful_edits[begin][1]
                and earlier_parts is not None and later_parts is not None
                and later_parts[0] == earlier_parts[1]
            )
        if not read_replaced and not block_replaced:
            continue
        replacement_evidence = (
            "complete source reads of these paths remain below. " if read_replaced else
            "the next exact edit consumes the entire preceding replacement block; "
            "its replacement chain remains below. "
        )
        parts = [(
            "[Host projection of earlier completed edits. The original call "
            "arguments remain in the complete Executor transcript; later "
            + replacement_evidence + "The following "
            "tool results retain their historical outcomes and metadata; any "
            "omitted old stream previews are explicitly marked. Any checks "
            "inside them are historical implementation feedback, not final "
            "verification or writeback authority.]"
        )]
        for call_index in call_indices:
            result_index, path = successful_edits[call_index]
            call, result = messages[call_index], messages[result_index]
            parts.extend([
                json.dumps({"toolUseId": call["toolUseId"], "toolName": "edit_file",
                            "path": path, "isError": False}, ensure_ascii=False, sort_keys=True),
                result["content"],
            ])
        summary = {
            "role": "assistant", "content": "\n\n".join(parts),
            "_implementation_recovery_summary": True,
            "_retired_assistant_turn_id": turn,
        }
        # A projection must actually free space; tiny old edits are cheaper
        # to replay faithfully than to explain with a host summary.
        old_size = sum(len(json.dumps(messages[index], ensure_ascii=False))
                       for index in range(begin, end + 1))
        if len(json.dumps(summary, ensure_ascii=False)) < old_size:
            replacements[begin] = (end, summary)

    projected: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        replacement = replacements.get(index)
        if replacement is None:
            projected.append(messages[index])
            index += 1
        else:
            end, summary = replacement
            projected.append(summary)
            index = end + 1
    return projected


class InputTokenCalibration:
    """Keep the uncounted provider overhead paired with its actual request.

    A smaller message view can lower input estimates without forgetting the
    positive difference observed for tool schemas/framing/tokenization. A
    timeout or unavailable usage cannot associate an old provider count with
    a newer request estimate and accidentally erase that difference.
    """

    def __init__(self) -> None:
        self._positive_residual = 0

    def observe(self, actual_input: int | None, sent_local: int) -> bool:
        if (type(actual_input) is not int or actual_input <= 0
                or type(sent_local) is not int or sent_local < 0):
            return False
        self._positive_residual = max(0, actual_input - sent_local)
        return True

    def estimate(self, local: int) -> int:
        if type(local) is not int or local < 0:
            raise ValueError("local input estimate must be a non-negative integer")
        return local + self._positive_residual
