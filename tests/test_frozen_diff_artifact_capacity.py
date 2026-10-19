"""Exact artifact capacity, independent audit binding, and review source access."""

import base64
import difflib
import hashlib
import json

import pytest

from minicode.review_context import build_review_context
from minicode.scheduler_artifacts import (
    MAX_SCHEDULER_ARTIFACT_BYTES,
    MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES,
    SchedulerArtifactError,
    SchedulerArtifactStore,
)
from minicode.scheduler_audit import SchedulerAuditValidationError, _validate_frozen_diff
from minicode.workflow_workspace import _frozen_diff_document


BASE = "sha256:" + "0" * 64
MARKER = b"\n...<truncated>...\n"


def full_document(patch):
    # Independent expected envelope; do not validate producer output by
    # invoking that same producer again in the audit assertions.
    return {"diffVersion": 1, "baseDigest": BASE,
            "patchDigest": "sha256:" + hashlib.sha256(patch).hexdigest(),
            "byteCount": len(patch), "complete": True,
            "encoding": "utf-8", "diff": patch.decode("utf-8")}


def publish(document):
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    artifact = store.publish("artifact_frozen_diff", document, authority=authority)
    assert len(artifact.content.encode("utf-8")) <= MAX_SCHEDULER_ARTIFACT_BYTES == 20_000
    return artifact


def audit(document, *, require_complete=True):
    _validate_frozen_diff(document, base_digest=BASE,
                         patch_digest=document["patchDigest"], require_complete=require_complete)


@pytest.mark.parametrize("rows", [210, 250])
def test_complete_mid_size_diff_reaches_store_audit_and_review_source(tmp_path, rows):
    before = "def build():\n" + "".join(
        f"    item_{i:03} = 'old-xxxxxxxxxxxx'\n" for i in range(rows)) + "    return item_000\n"
    after = before.replace("old-", "new-")
    patch = "".join(difflib.unified_diff(before.splitlines(keepends=True),
        after.splitlines(keepends=True), fromfile="a/app.py", tofile="b/app.py")).encode()
    assert 14_000 <= len(patch) <= 18_000
    expected = full_document(patch)
    # This complete envelope really fits the unchanged channel before the
    # producer is called; raw byte length is not the acceptance oracle.
    sealed_expected = publish(expected)
    document = _frozen_diff_document(patch=patch, base_digest=BASE,
                                     patch_digest=expected["patchDigest"])
    assert document["complete"] is True, "A fitting complete UTF-8 diff was prematurely truncated."
    assert document == expected
    assert publish(document).content == sealed_expected.content
    audit(json.loads(sealed_expected.content))
    (tmp_path / "app.py").write_text(after)
    packet = build_review_context(tmp_path, ["app.py"], [], "", None, document)
    assert "def build():" in packet
    assert f"item_{rows - 1:03} = 'new-xxxxxxxxxxxx'" in packet
    assert "return item_000" in packet
    assert "OMITTED complete changed function" not in packet


@pytest.mark.parametrize("patch", ["界".encode() * 5_000, (b'"\\\n' + b"x" * 15) * 800],
                         ids=["non-ascii", "fitting-escapes"])
def test_utf8_and_escaped_complete_documents_fit_actual_store(patch):
    expected = full_document(patch)
    sealed = publish(expected)
    document = _frozen_diff_document(patch=patch, base_digest=BASE,
                                     patch_digest=expected["patchDigest"])
    assert document == expected, "The full fitting JSON envelope must retain its exact UTF-8 bytes."
    audit(json.loads(sealed.content))


@pytest.mark.parametrize("patch", [b"x" * (MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES + 1),
                                  b"\0" * (MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES // 6 + 1),
                                  b'"\\\n' * (MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES // 6 + 1)],
                         ids=["raw-overflow", "nul-json-overflow", "escaped-json-overflow"])
def test_raw_or_escaping_overflow_retains_bounded_incomplete_evidence(patch):
    expected = full_document(patch)
    with pytest.raises(SchedulerArtifactError, match="4 MiB"):
        publish(expected)
    with pytest.raises(SchedulerAuditValidationError):
        audit(expected)
    document = _frozen_diff_document(patch=patch, base_digest=BASE,
                                     patch_digest=expected["patchDigest"])
    assert document["complete"] is False
    assert document["byteCount"] == len(patch)
    assert document["patchDigest"] == expected["patchDigest"]
    publish(document)
    audit(document, require_complete=False)
    with pytest.raises(SchedulerAuditValidationError, match="must be complete"):
        audit(document)


@pytest.mark.parametrize("patch", [b"\xff" * 15_000, b"x" * 11_999 + b"\xff"],
                         ids=["large-invalid-utf8", "small-invalid-utf8"])
def test_non_utf8_preserves_legacy_bounded_base64(patch):
    digest = "sha256:" + hashlib.sha256(patch).hexdigest()
    document = _frozen_diff_document(patch=patch, base_digest=BASE, patch_digest=digest)
    assert document["complete"] is False and document["encoding"] == "base64"
    expected = patch if len(patch) <= 12_000 else patch[:6_000] + MARKER + patch[-6_000:]
    assert base64.b64decode(document["diff"], validate=True) == expected
    publish(document)
    audit(document, require_complete=False)
    with pytest.raises(SchedulerAuditValidationError, match="must be complete"):
        audit(document)


def test_independent_audit_rejects_tampering_and_keeps_legacy_partial_unapprovable():
    patch = b"a" * 15_000
    original = full_document(patch)
    publish(original)
    audit(original)
    for change in ({"byteCount": 14_999}, {"diff": "b" + "a" * 14_999},
                   {"patchDigest": "sha256:" + "1" * 64},
                   {"encoding": "base64", "diff": base64.b64encode(patch).decode()},
                   {"diff": "\ud800"}):
        with pytest.raises(SchedulerAuditValidationError):
            audit({**original, **change})
    legacy = {**original, "complete": False,
              "diff": (patch[:6_000] + MARKER + patch[-6_000:]).decode()}
    publish(legacy)
    audit(legacy, require_complete=False)
    with pytest.raises(SchedulerAuditValidationError, match="must be complete"):
        audit(legacy)
    with pytest.raises(SchedulerAuditValidationError):
        audit({**legacy, "complete": True})


def test_complete_diff_does_not_grant_read_or_publish_authority(tmp_path):
    patch = b"--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-SECRET = 1\n+SECRET = 2\n"
    document = _frozen_diff_document(patch=patch, base_digest=BASE,
                                     patch_digest=full_document(patch)["patchDigest"])
    (tmp_path / "app.py").write_text("SECRET = 2\n")

    class Deny:
        def ensure_path_access(self, _path, _intent):
            raise PermissionError("denied")

    packet = build_review_context(tmp_path, ["app.py"], [], "", Deny(), document)
    assert "SECRET" not in packet
    store = SchedulerArtifactStore(scheduler_authority=object())
    with pytest.raises(SchedulerArtifactError, match="authority"):
        store.publish("artifact_frozen_diff", document, authority=object())
