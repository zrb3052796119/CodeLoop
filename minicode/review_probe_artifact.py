"""Keep a review-probe receipt inside the scheduler artifact limit.

The host still stores the full pytest log outside this JSON. The receipt keeps
the command binding and, when the raw preview cannot fit, the failing test ids
and assertion lines.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re

from minicode.scheduler_artifacts import MAX_SCHEDULER_ARTIFACT_BYTES


_MARKER = "\n...<truncated>...\n"
_FAILED_LINE = re.compile(r"^FAILED\s+(\S+)")
_ASSERTION_LINE = re.compile(
    r"^(?:>\s+.*assert\b|E\s+.*|AssertionError\b|assert\s+)",
    re.IGNORECASE,
)


def _canonical_size(value: object) -> int:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return len(encoded.encode("utf-8"))


def _preview_text(stream: object) -> str:
    if not isinstance(stream, dict):
        return ""
    preview = stream.get("preview")
    return preview if isinstance(preview, str) else ""


def pytest_failure_summary(*texts: str, cases: object = None, assertion_limit: int = 40,
                           id_limit: int | None = None) -> str:
    """Collect failing node ids and assertion lines from pytest text and cases."""

    ids: list[str] = []
    assertions: list[str] = []
    seen_ids: set[str] = set()
    seen_assertions: set[str] = set()

    def add_id(node_id: str) -> None:
        node_id = node_id.split(" - ", 1)[0].strip()
        if node_id and node_id not in seen_ids:
            seen_ids.add(node_id)
            ids.append(node_id)

    if isinstance(cases, list):
        for case in cases:
            if (isinstance(case, dict) and case.get("outcome") == "failed"
                    and isinstance(case.get("nodeId"), str)):
                add_id(case["nodeId"])
    for text in texts:
        for raw in text.splitlines():
            line = raw.strip()
            match = _FAILED_LINE.match(line)
            if match:
                add_id(match.group(1))
                continue
            if _ASSERTION_LINE.match(line):
                clipped = line[:300]
                if clipped not in seen_assertions:
                    seen_assertions.add(clipped)
                    assertions.append(clipped)
    shown_ids = ids if id_limit is None else ids[:id_limit]
    omitted = len(ids) - len(shown_ids)
    lines = ["pytest summary (failing tests and assertion lines):"]
    if not shown_ids:
        lines.append("FAILED <none>")
    for node_id in shown_ids:
        lines.append("FAILED " + node_id)
    if omitted > 0:
        lines.append(f"omitted {omitted} failing tests")
    for line in assertions[:assertion_limit]:
        lines.append(line)
    return "\n".join(lines)


def _replace_stream(stream: object, summary: str, *, force: bool) -> object:
    """Return a contract-valid stream preview that carries ``summary``.

    A stream at or under 4,000 bytes is bound to its digest. It is rewritten
    only when the receipt otherwise cannot be stored.
    """

    if not isinstance(stream, dict):
        return stream
    byte_count = stream.get("byteCount")
    digest = stream.get("digest")
    if type(byte_count) is not int or byte_count < 0 or not isinstance(digest, str):
        return stream
    text = summary.strip() or "pytest output summarised"
    if byte_count > 4000 and not force:
        if _MARKER not in text:
            text += _MARKER
        while True:
            raw = text.encode("utf-8")
            encoded = base64.b64encode(raw).decode("ascii")
            if len(text) <= 5000 and len(encoded) <= 8000:
                break
            text = text[: max(len(_MARKER), len(text) - 400)]
            if _MARKER not in text:
                text = (text[:2000] + _MARKER)[:5000]
        raw = text.encode("utf-8")
        return {
            **stream,
            "preview": text,
            "previewBase64": base64.b64encode(raw).decode("ascii"),
            "truncated": True,
        }
    if byte_count <= 4000 and not force:
        return stream
    raw = text.encode("utf-8")
    if len(raw) > 4000:
        raw = raw[:4000]
        text = raw.decode("utf-8", errors="replace")
        raw = text.encode("utf-8")
    return {
        "byteCount": len(raw),
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "preview": text,
        "previewBase64": base64.b64encode(raw).decode("ascii"),
        "truncated": False,
    }


def _case_rows(report: object) -> list:
    if not isinstance(report, dict):
        return []
    case_report = report.get("caseReport")
    if not isinstance(case_report, dict):
        return []
    cases = case_report.get("cases")
    return cases if isinstance(cases, list) else []


def _apply_summary(document: dict, summary: str, *, force: bool) -> None:
    evidence = document.get("evidence")
    if not isinstance(evidence, dict):
        return
    stdout = evidence.get("stdout")
    stderr = evidence.get("stderr")
    stderr_text = _preview_text(stderr)
    stderr_summary = pytest_failure_summary(stderr_text, assertion_limit=10)
    if "FAILED " not in stderr_summary.replace("FAILED <none>", ""):
        stderr_summary = "stderr summarised; assertion lines are in the stdout summary"
    evidence["stdout"] = _replace_stream(stdout, summary, force=force)
    evidence["stderr"] = _replace_stream(stderr, stderr_summary, force=force)


def fit_review_probe_document(document: dict, *, limit: int = MAX_SCHEDULER_ARTIFACT_BYTES) -> dict:
    """Return ``document`` unchanged when it fits, otherwise a smaller valid copy."""

    if not isinstance(document, dict) or limit < 1:
        return document
    try:
        if _canonical_size(document) <= limit:
            return document
    except (TypeError, ValueError, UnicodeError):
        return document
    fitted = copy.deepcopy(document)
    fitted.pop("verificationOutputs", None)
    report = fitted.get("testReport")
    if isinstance(report, dict) and "executionReport" in report:
        fitted["testReport"] = {
            key: value for key, value in report.items() if key != "executionReport"
        }
        report = fitted["testReport"]
    cases = _case_rows(report)
    if _canonical_size(fitted) <= limit:
        evidence = fitted.get("evidence") if isinstance(fitted.get("evidence"), dict) else {}
        truncated = any(
            isinstance(evidence.get(name), dict) and evidence[name].get("truncated") is True
            for name in ("stdout", "stderr")
        )
        if not truncated:
            return fitted
        summary = pytest_failure_summary(
            _preview_text(evidence.get("stdout")),
            _preview_text(evidence.get("stderr")),
            cases=cases,
        )
        trial = copy.deepcopy(fitted)
        _apply_summary(trial, summary, force=False)
        if _canonical_size(trial) <= limit:
            return trial
        return fitted
    evidence = fitted.get("evidence") if isinstance(fitted.get("evidence"), dict) else {}
    full_summary = pytest_failure_summary(
        _preview_text(evidence.get("stdout")),
        _preview_text(evidence.get("stderr")),
        cases=cases,
    )
    for assertion_limit, id_limit, force in (
        (40, None, False),
        (8, None, False),
        (0, 20, False),
        (8, 20, True),
        (0, 8, True),
    ):
        summary = pytest_failure_summary(
            _preview_text(evidence.get("stdout")) if assertion_limit else "",
            _preview_text(evidence.get("stderr")) if assertion_limit else "",
            cases=cases,
            assertion_limit=assertion_limit,
            id_limit=id_limit,
        ) if assertion_limit or id_limit else full_summary
        if assertion_limit == 0:
            summary = pytest_failure_summary(cases=cases, assertion_limit=0, id_limit=id_limit)
        _apply_summary(fitted, summary, force=force)
        if _canonical_size(fitted) <= limit:
            return fitted
    if "executionBinding" in fitted and _canonical_size(fitted) > limit:
        fitted.pop("executionBinding", None)
    return fitted


def bound_review_test_output(result: dict, recovery: dict, *,
                             limit: int = MAX_SCHEDULER_ARTIFACT_BYTES) -> str:
    """JSON tool output at or under the artifact limit, with failure highlights."""

    payload = {**result, "recovery": recovery}
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        text = "{}"
    if len(text.encode("utf-8")) <= limit:
        return text
    fitted = fit_review_probe_document(result, limit=max(1, limit - 800))
    payload = {**fitted, "recovery": recovery}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(text.encode("utf-8")) <= limit:
        return text
    evidence = fitted.get("evidence") if isinstance(fitted.get("evidence"), dict) else {}
    report = fitted.get("testReport") if isinstance(fitted.get("testReport"), dict) else {}
    summary = pytest_failure_summary(
        _preview_text(evidence.get("stdout")),
        _preview_text(evidence.get("stderr")),
        cases=_case_rows(report),
        assertion_limit=12,
        id_limit=20,
    )
    minimal = {
        "probeId": result.get("probeId"),
        "patchDigest": result.get("patchDigest"),
        "status": result.get("status"),
        "receiptRef": result.get("receiptRef"),
        "sourceRef": result.get("sourceRef"),
        "pytestSummary": summary,
        "recovery": recovery,
    }
    return json.dumps(minimal, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


__all__ = [
    "bound_review_test_output",
    "fit_review_probe_document",
    "pytest_failure_summary",
]
