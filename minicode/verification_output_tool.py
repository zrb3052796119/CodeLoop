"""Revocable, paginated access to host-captured public verification output."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import uuid

from minicode.capability_contracts import (
    CapabilityDomain, CapabilityEffects, CapabilityProfile, CapabilityScope,
)
from minicode.task_deadline import raise_task_deadline
from minicode.tooling import ToolDefinition, ToolExecutionAbandoned, ToolResult


class VerificationOutputLedger:
    """One host workflow's public logs; never an acceptance-evidence authority."""

    def __init__(self, workflow_id: str, *, journal=None):
        self.workflow_id = workflow_id
        self._journal = journal
        self._store = None
        self._temporary = None
        self._manifests = []
        self._refs = set()
        self._closed = False
        self._lock = threading.RLock()

    def store(self, context):
        from minicode.subagent_journal import SubagentRunJournal
        from minicode.verification_output import VerificationOutputStore

        with self._lock:
            if self._closed:
                raise ValueError("verification output scope is closed")
            if self._store is None:
                journal = self._journal
                if journal is None:
                    opener = getattr(getattr(context, "_event_sink", None), "open_subagent_journal", None)
                    journal = opener() if callable(opener) else None
                if isinstance(journal, SubagentRunJournal):
                    # An unguessable per-ledger directory prevents direct turns
                    # sharing the same Run journal from sharing output authority.
                    root = journal._run_dir / "verification-output" / uuid.uuid4().hex
                else:
                    self._temporary = tempfile.TemporaryDirectory(prefix="minicode-verification-output-")
                    root = Path(self._temporary.name)
                self._store = VerificationOutputStore(root)
            return self._store

    def record(self, manifest: dict) -> None:
        with self._lock:
            if self._closed or manifest.get("workflowId") != self.workflow_id:
                raise ValueError("verification output belongs to another workflow")
            document = json.loads(json.dumps(manifest, ensure_ascii=False))
            self._manifests.append(document)
            for stream in document.get("streams", {}).values():
                if stream.get("storageComplete") is True:
                    self._refs.add(stream["outputRef"])

    def directory(self) -> bytes:
        with self._lock:
            if self._closed:
                raise ValueError("verification output scope is closed")
            return json.dumps({"verificationOutputs": self._manifests}, ensure_ascii=False,
                              sort_keys=True, separators=(",", ":")).encode("utf-8")

    def read(self, output_ref: str, *, offset: int, limit: int):
        with self._lock:
            if self._closed or self._store is None:
                raise ValueError("verification output scope is closed or empty")
            return self._store.read(output_ref, allowed_refs=frozenset(self._refs),
                                    offset=offset, limit=limit)

    def contains(self, output_ref: str) -> bool:
        with self._lock:
            return not self._closed and output_ref in self._refs

    def index(self, *, offset: int, limit: int, check_id: str | None = None) -> dict:
        """Reference discovery without the large encoded manifest directory."""
        with self._lock:
            if self._closed:
                raise ValueError("verification output scope is closed")
            manifests = [item for item in self._manifests
                         if check_id is None or item.get("checkId") == check_id]
            if offset > len(manifests):
                raise ValueError("verification output index offset exceeds record count")
            selected = manifests[offset:offset + limit]
            outputs = [{**{key: item.get(key) for key in
                          ("checkId", "assignmentId", "patchDigest", "complete")},
                        "streams": {name: {key: stream.get(key) for key in
                                           ("outputRef", "byteCount", "storageComplete")}
                                    for name, stream in item.get("streams", {}).items()}}
                       for item in selected]
            next_offset = offset + len(selected)
            return {"outputs": outputs, "offset": offset, "total": len(manifests),
                    "nextOffset": next_offset if next_offset < len(manifests) else None}

    def close(self):
        with self._lock:
            self._closed = True
            self._refs.clear()
            # Journal-backed bytes remain for audit; unjournaled data only
            # outlives the candidate clones, not the owning turn/workflow.
            if self._temporary is not None:
                self._temporary.cleanup()
                self._temporary = None


class VerificationOutputAccess:
    """Only this live owner may read explicitly captured workflow references."""

    def __init__(self, ledger: VerificationOutputLedger, *, checkpoint,
                 owner_channel=None, owns_ledger=False):
        self._ledger = ledger
        self._checkpoint = checkpoint
        self._channel = owner_channel
        self._owns_ledger = owns_ledger
        self._closed = False
        self._direct_owner = None
        self.tool = ToolDefinition(
            name="verification_output_read",
            description=("Read complete host-captured stdout/stderr for authorized public checks. "
                         "Use action=list for a compact reference index (optional checkId filter; offset counts records). "
                         "Use outputRef from verificationOutputs; omit outputRef to read the JSON reference directory. "
                         "offset/limit/nextOffset are bytes, not characters. contentBase64 preserves exact bytes; "
                         "text is a display projection and textExact reports split UTF-8 boundaries. "
                         "The returned page may be smaller than limit to fit the tool response; follow nextOffset. "
                         "This does not run checks or grant approval."),
            input_schema={"type": "object", "additionalProperties": False,
                "properties": {"action": {"type": "string", "enum": ["list"]},
                               "checkId": {"type": "string", "minLength": 1},
                               "outputRef": {"type": "string", "minLength": 1},
                               "offset": {"type": "integer", "minimum": 0, "default": 0},
                               "limit": {"type": "integer", "minimum": 1, "maximum": 8192, "default": 4096}}},
            validator=self._validate, run=self._run,
            capability_profile=CapabilityProfile(domain=CapabilityDomain.EXECUTION,
                scope=CapabilityScope.READONLY, effects=CapabilityEffects(read_only=True,
                destructive=False, external=False, executes_code=False, delegates=False),
                purposes=("Read complete authorized verification diagnostics",)),
        )

    def bind_direct_owner(self, context):
        # Agent loops rebuild ToolContext per invocation. Existing turn/actor
        # capabilities stay stable, whereas cwd alone is shared by other turns.
        owner_fields = ("_agent_budget", "_event_sink", "_subagent_mailbox",
                        "_cancellation_token", "_subagent_lifecycle")
        owners = tuple(getattr(context, name, None) for name in owner_fields)
        identity = (str(Path(context.cwd).resolve()), getattr(context, "_agent_depth", 0),
                    id(getattr(context, "permissions", None)), tuple(id(owner) for owner in owners),
                    id(context) if not any(owner is not None for owner in owners) else None)
        if self._direct_owner is None:
            self._direct_owner = identity
        elif self._direct_owner != identity:
            raise ValueError("verification output belongs to another owner")

    def _active(self, context):
        from minicode.reviewer_result_submission import _channel_live

        if self._closed:
            raise ValueError("verification output access is closed")
        self._checkpoint(context)
        abandoned = getattr(context, "_tool_abandoned", None)
        if abandoned is not None and abandoned.is_set():
            raise ToolExecutionAbandoned("verification output owner was abandoned")
        raise_task_deadline(context)
        if self._channel is not None:
            if (getattr(context, "_subagent_mailbox", None) is not self._channel
                    or not _channel_live(self._channel)):
                raise ValueError("verification output belongs to another or revoked Assignment")
        else:
            if self._direct_owner is None:
                raise ValueError("verification output has not been authorized by a check")
            self.bind_direct_owner(context)

    @staticmethod
    def _validate(value):
        if isinstance(value, dict) and value.get("action") == "list":
            if (set(value) - {"action", "checkId", "offset", "limit"}
                    or ("checkId" in value and (not isinstance(value["checkId"], str) or not value["checkId"]))
                    or type(value.get("offset", 0)) is not int or value.get("offset", 0) < 0
                    or type(value.get("limit", 10)) is not int or not 1 <= value.get("limit", 10) <= 20):
                raise ValueError("list accepts optional checkId, record offset and limit 1..20")
            return value
        if (not isinstance(value, dict) or set(value) - {"outputRef", "offset", "limit"}
                or ("outputRef" in value and (not isinstance(value["outputRef"], str) or not value["outputRef"]))
                or type(value.get("offset", 0)) is not int or value.get("offset", 0) < 0
                or type(value.get("limit", 4096)) is not int or not 1 <= value.get("limit", 4096) <= 8192):
            raise ValueError("choose an authorized outputRef and non-negative byte offset, limit 1..8192")
        return value

    def _run(self, value, context):
        value = self._validate(value)
        self._active(context)
        if value.get("action") == "list":
            limit = value.get("limit", 10)
            while True:
                result = self._ledger.index(offset=value.get("offset", 0), limit=limit,
                                            check_id=value.get("checkId"))
                self._active(context)
                encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
                if len(encoded) <= 28000:
                    return ToolResult(ok=True, output=encoded)
                # Valid identifiers may expand through JSON escaping. Keep
                # complete records and their real cursor; never generic clip.
                if limit == 1:
                    raise ValueError("verification output index record exceeds response size")
                limit = max(1, limit // 2)
        if "outputRef" in value and not self._ledger.contains(value["outputRef"]):
            self._active(context)
            return ToolResult(ok=False, output=json.dumps({
                "errorCode": "verification_output_ref_unavailable",
                "message": "Use an outputRef from this owner's stream index, not an artifact ID.",
                "recovery": {"tool": "verification_output_read", "input": {"action": "list"}}}))
        offset, limit = value.get("offset", 0), value.get("limit", 4096)
        while True:
            if "outputRef" in value:
                result = self._ledger.read(value["outputRef"], offset=offset, limit=limit)
            else:
                data = self._ledger.directory()
                if offset > len(data):
                    raise ValueError("verification output offset exceeds byte count")
                part = data[offset:offset + limit]
                try:
                    text, exact = part.decode("utf-8"), True
                except UnicodeDecodeError:
                    text, exact = part.decode("utf-8", errors="replace"), False
                result = {"outputRef": None, "offset": offset, "nextOffset": offset + len(part),
                          "eof": offset + len(part) == len(data), "byteCount": len(data),
                          "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                          "contentBase64": base64.b64encode(part).decode("ascii"), "text": text, "textExact": exact}
            self._active(context)
            encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
            if len(encoded) <= 28000:
                return ToolResult(ok=True, output=encoded)
            # Preserve exact bytes and the real nextOffset. Never let generic
            # tool truncation turn a successful page into invalid JSON.
            limit = max(1, limit // 2)

    def close(self):
        self._closed = True
        if self._owns_ledger:
            self._ledger.close()
