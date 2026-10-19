"""Host-owned immutable verification streams, read only through exact grants.

These files are diagnostics, never verification or writeback authority. Large
streams stay outside the bounded scheduler artifact JSON and model history.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

_REF = re.compile(r"verification_output_([0-9a-f]{32})_(stdout|stderr)\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_READ_BYTES = 8192
_MAX_MANIFEST_BYTES = 16384


class VerificationOutputError(ValueError):
    """A diagnostic reference is unavailable, incomplete or not authorized."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _write_all(fd, data):
    position = 0
    while position < len(data):
        written = os.write(fd, data[position:])
        if written <= 0:
            raise OSError("short verification output write")
        position += written


class VerificationOutputStore:
    """The caller chooses a private run directory, never a model-supplied path."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise VerificationOutputError("safe verification output I/O unavailable")
        fd = self._directory()
        os.close(fd)

    def _directory(self):
        try:
            return os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as error:
            raise VerificationOutputError("verification output directory unavailable") from error

    def begin(self, *, workflow_id, assignment_id, check_id, patch_digest):
        fields = (workflow_id, assignment_id, check_id)
        if (any(not isinstance(value, str) or not value or "\x00" in value or len(value) > 256
                for value in fields) or not isinstance(patch_digest, str) or not _DIGEST.fullmatch(patch_digest)):
            raise VerificationOutputError("verification output binding is invalid")
        return _OutputCapture(self, {"outputVersion": 1, "invocationId": uuid.uuid4().hex,
            "workflowId": workflow_id, "assignmentId": assignment_id,
            "checkId": check_id, "patchDigest": patch_digest})

    def read(self, output_ref: str, *, allowed_refs: frozenset[str], offset: int = 0, limit: int = 8192):
        if (not isinstance(allowed_refs, frozenset) or not isinstance(output_ref, str)
                or output_ref not in allowed_refs or not (match := _REF.fullmatch(output_ref))):
            raise VerificationOutputError("verification output reference is not authorized")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= _MAX_READ_BYTES:
            raise VerificationOutputError("verification output byte window is invalid")
        directory = self._directory()
        descriptor = None
        try:
            manifest_fd = os.open(match[1] + ".json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                metadata = os.fstat(manifest_fd)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_MANIFEST_BYTES:
                    raise VerificationOutputError("verification output manifest is invalid")
                manifest = json.loads(os.read(manifest_fd, _MAX_MANIFEST_BYTES + 1))
            finally:
                os.close(manifest_fd)
            if not isinstance(manifest, dict) or type(manifest.get("complete")) is not bool:
                raise VerificationOutputError("verification output manifest is invalid")
            item = manifest["streams"][match[2]]
            if (not isinstance(item, dict) or manifest["invocationId"] != match[1]
                    or item.get("outputRef") != output_ref or item.get("storageComplete") is not True
                    or type(item.get("byteCount")) is not int or item["byteCount"] < 0
                    or not isinstance(item.get("digest"), str) or not _DIGEST.fullmatch(item["digest"])):
                raise VerificationOutputError("verification output is not complete")
            descriptor = os.open(output_ref + ".bin", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size != item["byteCount"]):
                raise VerificationOutputError("verification output file is invalid")
            digest = hashlib.sha256()
            while block := os.read(descriptor, 65536):
                digest.update(block)
            if "sha256:" + digest.hexdigest() != item["digest"]:
                raise VerificationOutputError("verification output digest mismatch")
            if offset > before.st_size:
                raise VerificationOutputError("verification output offset exceeds stream")
            os.lseek(descriptor, offset, os.SEEK_SET)
            data = os.read(descriptor, limit)
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise VerificationOutputError("verification output changed during read")
            try:
                text, exact = data.decode("utf-8"), True
            except UnicodeDecodeError:
                text, exact = data.decode("utf-8", errors="replace"), False
            return {"outputRef": output_ref, "offset": offset, "nextOffset": offset + len(data),
                "eof": offset + len(data) == before.st_size, "byteCount": before.st_size,
                "digest": item["digest"], "contentBase64": base64.b64encode(data).decode("ascii"),
                "text": text, "textExact": exact, "captureComplete": manifest["complete"]}
        except (OSError, ValueError, KeyError, TypeError) as error:
            if isinstance(error, VerificationOutputError):
                raise
            raise VerificationOutputError("verification output unavailable or invalid") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory)


class _OutputCapture:
    def __init__(self, store, binding):
        self.store, self.binding = store, binding
        self._directory = store._directory()
        self._streams = {}
        self._lock = threading.Lock()
        self._finished = False
        try:
            for name in ("stdout", "stderr"):
                ref = "verification_output_" + binding["invocationId"] + "_" + name
                fd = os.open(ref + ".part", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self._directory)
                self._streams[name] = {"ref": ref, "fd": fd, "error": None}
        except BaseException:
            for item in self._streams.values():
                os.close(item["fd"])
                os.unlink(item["ref"] + ".part", dir_fd=self._directory)
            os.close(self._directory)
            raise

    def add(self, stream: str, data: bytes):
        with self._lock:
            if self._finished:
                return
            item = self._streams[stream]
            if item["error"] is None:
                try:
                    _write_all(item["fd"], data)
                except OSError as error:
                    item["error"] = type(error).__name__

    def finish(self, *, stdout: dict, stderr: dict, complete: bool):
        with self._lock:
            if self._finished:
                raise VerificationOutputError("verification output capture already finalized")
            self._finished = True
            streams = {}
            try:
                for name, evidence in (("stdout", stdout), ("stderr", stderr)):
                    item = self._streams[name]
                    try:
                        os.fsync(item["fd"])
                        stored = os.fstat(item["fd"]).st_size
                    except OSError as error:
                        item["error"] = type(error).__name__
                        stored = -1
                    finally:
                        os.close(item["fd"])
                        item["fd"] = None
                    valid = item["error"] is None and stored == evidence["byteCount"]
                    if valid:
                        os.rename(item["ref"] + ".part", item["ref"] + ".bin",
                                  src_dir_fd=self._directory, dst_dir_fd=self._directory)
                    streams[name] = {"outputRef": item["ref"], "byteCount": evidence["byteCount"],
                                     "digest": evidence["digest"], "storageComplete": valid}
                    if item["error"] is not None:
                        streams[name]["storageError"] = item["error"]
                manifest = {**self.binding, "complete": bool(complete), "streams": streams}
                fd = os.open(self.binding["invocationId"] + ".json",
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self._directory)
                try:
                    _write_all(fd, _json(manifest))
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(self._directory)
                return manifest
            finally:
                for item in self._streams.values():
                    if item["fd"] is not None:
                        os.close(item["fd"])
                        item["fd"] = None
                os.close(self._directory)
