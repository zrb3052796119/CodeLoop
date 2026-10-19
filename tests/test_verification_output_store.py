"""Full verification bytes are retained out of band; no provider or task fixture.

The managed-command test uses a pass-through test sandbox and real pytest.
It proves stream persistence/retrieval, not the OS sandbox's confinement.
"""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck, _BoundedStreamCapture
from minicode.tooling import ToolExecutionAbandoned

PATCH = "sha256:" + "a" * 64


class TestSandbox:
    __test__ = False

    def wrap_command(self, *, argv, **_kwargs):
        return argv


def _verifier():
    executable = str(Path(sys.executable).resolve())
    return DeterministicVerifier(sandbox_backend=TestSandbox(),
        trusted_executables={"python3": executable}, explicit_trusted_executables={"python3": executable})


def _run(verifier, cwd, **kwargs):
    return verifier.run(checks=(VerificationCheck("check_output", "tests",
        ("python3", "-m", "pytest", "-q", "-s", "test_output.py"), kwargs.pop("timeout", 10)),),
        cwd=str(cwd), workflow_id="wf_output", task_id="task_output",
        execution_assignment_id="asg_output", patch_digest=PATCH, **kwargs)


def test_managed_failed_check_retains_full_output_while_evidence_stays_bounded(tmp_path):
    marker = "SYNTHETIC_MIDDLE_FAILURE_DETAIL"
    (tmp_path / "test_output.py").write_text(
        "import sys\n"
        f"OUT = 'H'*13000 + {marker!r} + 'T'*14000\n"
        f"ERR = 'E'*13000 + {marker!r} + 'R'*14000\n"
        "def emit():\n    sys.stdout.write(OUT); sys.stderr.write(ERR)\n"
        "def test_public():\n    emit()\n    assert 0 == 1\n")
    manifests, evidence = [], []
    # Compatibility seam: the exact old-production test executes real pytest
    # and fails on missing durable bytes, never a new constructor/import error.
    kwargs = {}
    store = None
    if importlib.util.find_spec("minicode.verification_output") is not None:
        from minicode.verification_output import VerificationOutputStore
        store = VerificationOutputStore(tmp_path.parent / (tmp_path.name + "-logs"))
        kwargs = {"output_store": store, "output_sink": manifests.append}
    result = _run(_verifier(), tmp_path, evidence_sink=lambda _ref, item: evidence.append(item), **kwargs)
    assert result["status"] == "failed" and result["checks"][0]["exitCode"] == 1
    assert len(evidence) == 1
    assert all(evidence[0][name]["byteCount"] > 27000 and evidence[0][name]["truncated"]
               for name in ("stdout", "stderr"))
    assert all(marker not in evidence[0][name]["preview"] for name in ("stdout", "stderr"))
    assert len(manifests) == 1, "A real failed check discarded the full streams; only the bounded preview remains"
    manifest = manifests[0]
    assert manifest["complete"] is True and manifest["patchDigest"] == PATCH
    assert (manifest["workflowId"], manifest["assignmentId"], manifest["checkId"]) == (
        "wf_output", "asg_output", "check_output")
    assert len(json.dumps(manifest)) < 2000
    for stream in ("stdout", "stderr"):
        entry = manifest["streams"][stream]
        assert entry["storageComplete"] is True
        assert (entry["byteCount"], entry["digest"]) == (
            evidence[0][stream]["byteCount"], evidence[0][stream]["digest"])
        parts, offset = [], 0
        while True:
            page = store.read(entry["outputRef"], allowed_refs=frozenset({entry["outputRef"]}),
                              offset=offset, limit=4093)
            parts.append(base64.b64decode(page["contentBase64"], validate=True))
            assert page["captureComplete"] is True
            offset = page["nextOffset"]
            if page["eof"]:
                break
        raw = b"".join(parts)
        assert marker.encode() in raw and len(raw) == entry["byteCount"]
        assert "sha256:" + hashlib.sha256(raw).hexdigest() == entry["digest"]
    assert set(evidence[0]["stdout"]) == {"byteCount", "digest", "preview", "previewBase64", "truncated"}


@pytest.fixture
def store(tmp_path):
    from minicode.verification_output import VerificationOutputStore
    return VerificationOutputStore(tmp_path / "logs")


def _capture(store, raw, *, complete=True, assignment="asg_output"):
    capture = store.begin(workflow_id="wf_output", assignment_id=assignment,
                          check_id="check_output", patch_digest=PATCH)
    stdout = _BoundedStreamCapture(sink=lambda data: capture.add("stdout", data))
    stderr = _BoundedStreamCapture(sink=lambda data: capture.add("stderr", data))
    for start in range(0, len(raw), 173):
        stdout.add(raw[start:start + 173])
    return capture.finish(stdout=stdout.evidence(), stderr=stderr.evidence(), complete=complete)


@pytest.mark.parametrize("limit", [1, 7, 8192])
def test_multibyte_and_binary_pages_reconstruct_original_digest_after_reopen(store, limit):
    raw = ("中文é🙂\n" * 1000).encode() + b"\xff\x00"
    manifest = _capture(store, raw)
    from minicode.verification_output import VerificationOutputStore
    reopened = VerificationOutputStore(store.root)
    ref = manifest["streams"]["stdout"]["outputRef"]
    offset, parts, replaced = 0, [], False
    while True:
        page = reopened.read(ref, allowed_refs=frozenset({ref}), offset=offset, limit=limit)
        part = base64.b64decode(page["contentBase64"], validate=True)
        assert len(part) <= limit and page["nextOffset"] == offset + len(part)
        if page["textExact"]:
            assert page["text"].encode() == part
        else:
            replaced = True
        parts.append(part)
        offset = page["nextOffset"]
        if page["eof"]:
            break
    assert b"".join(parts) == raw and replaced
    assert "sha256:" + hashlib.sha256(b"".join(parts)).hexdigest() == manifest["streams"]["stdout"]["digest"]


def test_partial_capture_is_readable_but_explicitly_incomplete(store):
    manifest = _capture(store, b"received before cancellation", complete=False)
    ref = manifest["streams"]["stdout"]["outputRef"]
    page = store.read(ref, allowed_refs=frozenset({ref}))
    assert page["captureComplete"] is False and page["text"] == "received before cancellation"
    assert manifest["streams"]["stdout"]["storageComplete"] is True


def test_repeated_check_on_same_patch_keeps_distinct_old_and_new_streams(store):
    first, second = _capture(store, b"first failure"), _capture(store, b"later failure", assignment="asg_repair")
    refs = [item["streams"]["stdout"]["outputRef"] for item in (first, second)]
    assert refs[0] != refs[1]
    assert [store.read(ref, allowed_refs=frozenset(refs))["text"] for ref in refs] == ["first failure", "later failure"]


@pytest.mark.parametrize("mutation", ["wrong_grant", "unknown_ref", "negative_offset", "oversize", "tamper", "symlink", "manifest"])
def test_output_read_rejects_out_of_scope_invalid_or_changed_data(store, mutation, tmp_path):
    from minicode.verification_output import VerificationOutputError
    manifest = _capture(store, b"retained diagnostic")
    ref = manifest["streams"]["stdout"]["outputRef"]
    allowed = frozenset({ref})
    kwargs = {}
    path = store.root / (ref + ".bin")
    if mutation == "wrong_grant":
        allowed = frozenset()
    elif mutation == "unknown_ref":
        ref = "verification_output_" + "0" * 32 + "_stdout"
        allowed = frozenset({ref})
    elif mutation == "negative_offset":
        kwargs["offset"] = -1
    elif mutation == "oversize":
        kwargs["limit"] = 8193
    elif mutation == "tamper":
        path.write_bytes(b"changed diagnostic!")
    elif mutation == "symlink":
        target = tmp_path / "unrelated.txt"
        target.write_text("retained diagnostic")
        path.unlink()
        path.symlink_to(target)
    elif mutation == "manifest":
        (store.root / (manifest["invocationId"] + ".json")).write_text("[]")
    with pytest.raises(VerificationOutputError):
        store.read(ref, allowed_refs=allowed, **kwargs)


def test_storage_write_failure_is_not_a_complete_log(store, monkeypatch):
    import minicode.verification_output as module
    write = module._write_all
    def fail_raw(fd, data):
        if data == b"lost output":
            raise OSError("synthetic disk write failure")
        return write(fd, data)
    monkeypatch.setattr(module, "_write_all", fail_raw)
    manifest = _capture(store, b"lost output")
    entry = manifest["streams"]["stdout"]
    assert entry["storageComplete"] is False and entry["storageError"] == "OSError"
    with pytest.raises(module.VerificationOutputError, match="not complete"):
        store.read(entry["outputRef"], allowed_refs=frozenset({entry["outputRef"]}))


def test_timeout_seals_received_bytes_without_claiming_check_success(tmp_path):
    from minicode.verification_output import VerificationOutputStore
    store = VerificationOutputStore(tmp_path / "logs")
    (tmp_path / "test_output.py").write_text(
        "import sys,time\ndef test_public():\n"
        "    print('BEFORE_TIMEOUT', flush=True)\n    time.sleep(5)\n")
    manifests = []
    result = _run(_verifier(), tmp_path, timeout=1, output_store=store, output_sink=manifests.append)
    assert result["status"] == "inconclusive" and result["checks"][0]["exitCode"] is None
    assert len(manifests) == 1 and manifests[0]["complete"] is False
    ref = manifests[0]["streams"]["stdout"]["outputRef"]
    page = store.read(ref, allowed_refs=frozenset({ref}))
    assert "BEFORE_TIMEOUT" in page["text"] and page["captureComplete"] is False


def test_abandonment_keeps_original_exception_and_seals_received_bytes(tmp_path):
    from minicode.verification_output import VerificationOutputStore
    store = VerificationOutputStore(tmp_path / "logs")
    (tmp_path / "test_output.py").write_text(
        "import time\ndef test_public():\n    print('BEFORE_ABANDON', flush=True)\n    time.sleep(5)\n")
    manifests = []
    def checkpoint():
        if any(path.stat().st_size for path in store.root.glob("*_stdout.part")):
            raise ToolExecutionAbandoned("synthetic owner abandoned")
    with pytest.raises(ToolExecutionAbandoned, match="synthetic owner abandoned"):
        _run(_verifier(), tmp_path, output_store=store, output_sink=manifests.append,
             cancellation_checkpoint=checkpoint)
    assert len(manifests) == 1 and manifests[0]["complete"] is False
    ref = manifests[0]["streams"]["stdout"]["outputRef"]
    page = store.read(ref, allowed_refs=frozenset({ref}))
    assert "BEFORE_ABANDON" in page["text"] and page["captureComplete"] is False
