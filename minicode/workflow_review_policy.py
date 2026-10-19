"""Host-owned, risk-based selection of independent completion review.

Passing tests are evidence of execution, not proof of natural-language
requirements. Policy v5 accepts that residual risk only for one ordinary
behavior criterion in a small, non-sensitive, non-complex change, and only
when the original user explicitly supplies its concrete acceptance command. Model
claims of coverage cannot relax these limits or a host-bound review request.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shlex


# A false positive keeps an existing gate. Avoid interpreting word order as
# permission to omit a review that the original user may have requested.
_REVIEW_REQUEST = re.compile(
    r"\b(?:review(?:er|ing|ed|s)?|audit(?:or|ing|ed|s)?)\b|审查|审核|审计|审阅|复核|评审",
    re.IGNORECASE,
)

_HOST_SCOPE_REASONS = ("host_scope_unconfirmed", "write_scope_not_files",
                       "scope_alias_or_hardlink", "acceptance_target_unconfirmed")


def requests_independent_review(text: object) -> bool:
    return isinstance(text, str) and bool(_REVIEW_REQUEST.search(text))


def explicit_user_acceptance_commands(text: object) -> tuple[tuple[str, ...], ...]:
    """Read only unquoted top-level acceptance directives from host user text.

    Deliberately not a natural-language intent classifier. The user can write
    ``Acceptance command: `pytest -q tests/test_case.py``` or its Chinese
    equivalent. Fenced examples, block quotes, nested lists, and task/model
    prompts do not grant this input. A general suite command is insufficient.
    """
    if not isinstance(text, str):
        return ()
    commands: list[tuple[str, ...]] = []
    fence: str | None = None
    quoted_tags: list[str] = []
    for line in text.splitlines():
        marker = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        tag = re.fullmatch(r"\s*<(/?)([A-Za-z][A-Za-z0-9_-]*)\b[^>]*>\s*", line)
        if tag:
            if tag[1] and quoted_tags and quoted_tags[-1] == tag[2]:
                quoted_tags.pop()
            elif not tag[1] and not line.rstrip().endswith("/>"):
                quoted_tags.append(tag[2])
            continue
        if quoted_tags:
            continue
        match = re.fullmatch(r"(?:Acceptance command|验收命令)\s*[:：]\s*`([^`]+)`[.。]?\s*", line, re.IGNORECASE)
        if match is None:
            continue
        try:
            argv = tuple(shlex.split(match[1]))
        except ValueError:
            continue
        if _concrete_test_command(argv) and argv not in commands:
            commands.append(argv)
    return tuple(commands)


def _concrete_test_command(argv: object) -> bool:
    from minicode.verification_policy import verification_argv_is_safe

    if (not isinstance(argv, (list, tuple)) or not argv
            or any(not isinstance(item, str) for item in argv)
            or not verification_argv_is_safe(list(argv))):
        return False
    return bool(_test_targets(argv))


def _test_targets(argv) -> tuple[str, ...]:
    if not argv:
        return ()
    if argv[0] == "pytest":
        args = argv[1:]
    elif tuple(argv[:3]) in {("python", "-m", "pytest"), ("python3", "-m", "pytest")}:
        args = argv[3:]
    else:
        return ()
    # This exemption deliberately recognizes a smaller language than the
    # verifier. No option values, selectors, directories or discovery defaults
    # can be mistaken for fixed acceptance files. Other safe commands still run
    # normally, with Reviewer retained.
    flags = {"-q", "-qq", "-s", "-v", "-vv", "-x", "--disable-warnings", "--strict-config", "--strict-markers"}
    targets = []
    for item in args:
        if item in flags:
            continue
        if (item.startswith(("/", "-")) or ".." in item.split("/")
                or re.fullmatch(r"[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_.\[\]-]+)*", item) is None):
            return ()
        targets.append(item.split("::", 1)[0])
    return tuple(targets)


def _acceptance_is_writable(request, commands) -> bool:
    from minicode.scheduler_permissions import scheduler_paths_overlap

    targets = tuple(target for command in commands for target in _test_targets(command))
    return any(
        path.rsplit("/", 1)[-1].casefold() in {"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg"}
        or any(scheduler_paths_overlap(path, target) for target in targets)
        for path in request.scope.write_paths
    )


def _sensitive_paths(paths) -> bool:
    # Keep historical v2-v4 inference unchanged. The new exemption has its own
    # stricter floor, including plural and CamelCase component spellings.
    from minicode.multi_agent_contracts import _SENSITIVE_PATH_TERMS

    terms = {term for path in paths
             for term in re.split(r"[^a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", path).casefold())
             if term}
    terms.update(term[:-1] for term in tuple(terms) if term.endswith("s"))
    return bool(terms & _SENSITIVE_PATH_TERMS)


def observe_review_scope(request, workspace, commands) -> tuple[str, ...]:
    """Observe host files before dispatch; never accept request-JSON labels.

    Existing, unaliased regular files are the intentionally narrow first
    version. Unknown/new paths and any symlink or hardlink keep Reviewer.
    Inspect the source as well as its snapshot: copying may break hardlinks.
    """
    if workspace is None:
        return ("host_scope_unconfirmed",)
    root = Path(workspace).resolve(strict=True)
    reasons: set[str] = set()

    def ordinary_file(relative):
        path = root / relative
        try:
            parts = path.relative_to(root).parts
            if any((root.joinpath(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)):
                reasons.add("scope_alias_or_hardlink")
                return False
            if not path.is_file() or not path.resolve(strict=True).is_relative_to(root):
                return False
            if path.stat().st_nlink != 1:
                reasons.add("scope_alias_or_hardlink")
                return False
            return True
        except (OSError, ValueError):
            return False

    for path in request.scope.write_paths:
        if not ordinary_file(path):
            reasons.add("write_scope_not_files")
    requested_tests = {check.argv for check in request.verification_checks if check.kind == "tests"}
    targets = {target for command in commands if command in requested_tests for target in _test_targets(command)}
    for path in targets:
        if not ordinary_file(path):
            reasons.add("acceptance_target_unconfirmed")
    return tuple(reason for reason in _HOST_SCOPE_REASONS if reason in reasons)


def validate_unreviewed_change_set(request, change_set, *, relative_cwd=".") -> None:
    """Recheck the actual frozen file set, without directory-prefix authority."""
    files = change_set.get("files") if isinstance(change_set, dict) else None
    prefix = str(relative_cwd).strip("/")
    prefix = "" if prefix in {"", "."} else prefix + "/"
    allowed = {prefix + path for path in request.scope.write_paths}
    if (not isinstance(files, list) or not 1 <= len(files) <= 2
            or any(not isinstance(item, dict) or item.get("path") not in allowed for item in files)
            or _sensitive_paths([item["path"] for item in files])):
        raise ValueError("final change exceeds the host-confirmed review exemption")


def review_risk_reasons(request, profile) -> tuple[str, ...]:
    """Closed risk facts; criterion/check associations never prove semantics."""
    if request.kind != "change":
        return ()
    reasons: list[str] = []
    if not request.structured:
        reasons.append("legacy_request")
    if profile.risk.value != "low":
        reasons.append("elevated_risk")
    if profile.complex:
        reasons.append("complex_change")
    if _sensitive_paths(request.scope.write_paths):
        reasons.append("sensitive_write_scope")
    if not profile.scope_clear or not profile.acceptance_executable:
        reasons.append("unbounded_scope_or_acceptance")
    # One blanket command linked to many clauses does not establish narrow
    # acceptance. Separate requirements retain semantic completion review.
    if len(request.criteria) != 1:
        reasons.append("multiple_requirements")
    if any(item.kind != "behavior" for item in request.criteria):
        reasons.append("judgment_required")
    test_criteria = {criterion_id for check in request.verification_checks
                     if check.kind == "tests" for criterion_id in check.criterion_ids}
    if not request.criteria or test_criteria != {item.criterion_id for item in request.criteria}:
        reasons.append("behavior_test_evidence_missing")
    if any(requests_independent_review(item.text) for item in request.criteria):
        reasons.append("criterion_requests_review")
    return tuple(reasons)


def review_routing_decision(
    *, request, profile, workflow_id: str, request_digest: str,
    objective_digest: str, base_digest: str | None,
    user_request_digest: str | None, user_review_requested: bool,
    delegation_review_requested: bool,
    user_acceptance_commands: tuple[tuple[str, ...], ...] = (),
    host_scope_reasons: tuple[str, ...] = ("host_scope_unconfirmed",),
) -> dict[str, object]:
    reasons = list(review_risk_reasons(request, profile))
    if request.kind == "change":
        reasons.extend(host_scope_reasons)
        requested_tests = {check.argv for check in request.verification_checks if check.kind == "tests"}
        bound_commands = tuple(command for command in user_acceptance_commands if command in requested_tests)
        if not bound_commands:
            reasons.append("host_acceptance_missing")
        elif _acceptance_is_writable(request, bound_commands):
            reasons.append("host_acceptance_is_writable")
        if user_review_requested:
            reasons.append("user_requests_review")
        if delegation_review_requested:
            reasons.append("delegation_requests_review")
    return {
        "decisionVersion": 1,
        "resultType": "ReviewRoutingDecision.v1",
        "policyVersion": 5,
        "workflowId": workflow_id,
        "taskId": request.task_id,
        "requestDigest": request_digest,
        "objectiveDigest": objective_digest,
        "baseDigest": base_digest,
        "userRequestDigest": user_request_digest,
        "userAcceptanceCommands": [list(command) for command in user_acceptance_commands],
        "hostScopeReasons": list(host_scope_reasons),
        "userReviewRequested": user_review_requested,
        "delegationReviewRequested": delegation_review_requested,
        "independentReviewRequired": bool(reasons),
        "reasons": reasons or ["bounded_risk_test_acceptance" if request.kind == "change" else "read_only"],
        "semanticCoverageProven": False,
    }


def text_digest(text: object) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def review_routing_artifact_id(request_digest: str) -> str:
    return "artifact_review_routing_" + request_digest.removeprefix("sha256:")[:32]


def validate_review_routing_decision(
    document, *, request, profile, workflow_id: str, request_digest: str,
    objective_digest: str, base_digest: str | None,
) -> bool:
    """Replay host risk facts without reinterpreting recorded policy versions."""
    if not isinstance(document, dict):
        raise ValueError("review routing decision is missing")
    if base_digest is not None and (not isinstance(base_digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", base_digest) is None):
        raise ValueError("review routing baseline identity is invalid")
    for key in ("userReviewRequested", "delegationReviewRequested"):
        if type(document.get(key)) is not bool:
            raise ValueError("review routing request signal is invalid")
    user_digest = document.get("userRequestDigest")
    if (user_digest is not None and
            (not isinstance(user_digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", user_digest) is None)):
        raise ValueError("review routing user identity is invalid")
    raw_commands = document.get("userAcceptanceCommands")
    if (not isinstance(raw_commands, list)
            or any(not isinstance(command, list) or not _concrete_test_command(command) for command in raw_commands)):
        raise ValueError("host acceptance commands are invalid")
    commands = tuple(tuple(command) for command in raw_commands)
    if len(set(commands)) != len(commands):
        raise ValueError("host acceptance commands are duplicated")
    if (document["userReviewRequested"] or commands) and user_digest is None:
        raise ValueError("review request lacks its host user identity")
    host_reasons = document.get("hostScopeReasons")
    if (not isinstance(host_reasons, list)
            or host_reasons != [reason for reason in _HOST_SCOPE_REASONS if reason in host_reasons]):
        raise ValueError("host scope evidence is invalid")
    expected = review_routing_decision(
        request=request, profile=profile, workflow_id=workflow_id,
        request_digest=request_digest, objective_digest=objective_digest,
        base_digest=base_digest, user_request_digest=user_digest,
        user_review_requested=document["userReviewRequested"],
        delegation_review_requested=document["delegationReviewRequested"],
        user_acceptance_commands=commands,
        host_scope_reasons=tuple(host_reasons),
    )
    if document != expected:
        raise ValueError("review routing decision does not match host risk policy")
    return bool(expected["independentReviewRequired"])
