from __future__ import annotations

import http.client
import json
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

import minicode.permissions as permissions_module
from minicode.gateway import MiniCodeGatewayHandler
from minicode.permission_approval import PermissionApprovalBroker, PermissionApprovalError
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext, ToolDefinition
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.patch_file import patch_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.turn_cancellation import TurnCancellationToken


@pytest.fixture(autouse=True)
def isolated_permission_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setattr(
        permissions_module,
        "MINI_CODE_PERMISSIONS_PATH",
        tmp_path / "home" / "permissions.json",
    )
    permissions_module._normalize_path_cached.cache_clear()
    yield
    permissions_module._normalize_path_cached.cache_clear()


def _wait_pending(broker: PermissionApprovalBroker) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        items = broker.snapshot()["items"]
        if items:
            return items[0]
        time.sleep(0.005)
    raise AssertionError("permission request did not become pending")


def _http_request(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), json.loads(response.read())
    finally:
        connection.close()


def _http_pending(port: int) -> tuple[int, dict[str, str], dict[str, object]]:
    deadline = time.monotonic() + 2
    response = _http_request(port, "GET", "/api/v1/permissions/pending")
    while time.monotonic() < deadline and not response[2].get("items"):
        time.sleep(0.005)
        response = _http_request(port, "GET", "/api/v1/permissions/pending")
    return response


@contextmanager
def _pending_tool(
    workspace: Path,
    tool: ToolDefinition,
    input_data: dict[str, object],
) -> Iterator[
    tuple[
        PermissionApprovalBroker,
        str,
        dict[str, object],
        threading.Thread,
        dict[str, object],
    ]
]:
    turn_id = "turn_" + "a" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id="run_" + "b" * 32,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    outcome: dict[str, object] = {}
    validated = tool.validator(input_data)

    def run() -> None:
        session.tool_started(tool.name)
        try:
            outcome["result"] = tool.run(
                validated,
                ToolContext(cwd=str(workspace), permissions=manager),
            )
        except BaseException as error:  # noqa: BLE001 - asserted control flow
            outcome["error"] = error
        finally:
            session.tool_finished(tool.name)

    worker = threading.Thread(target=run)
    worker.start()
    item: dict[str, object] | None = None
    try:
        item = _wait_pending(broker)
        yield broker, turn_id, item, worker, outcome
    finally:
        if worker.is_alive() and item is not None:
            try:
                broker.decide(
                    permission_id=str(item["permissionId"]),
                    turn_id=turn_id,
                    decision="deny_once",
                )
            except Exception:
                pass
        broker.close()
        worker.join(timeout=2)


def _assert_safe_workspace_review(
    item: dict[str, object],
    *,
    workspace: Path,
    label: str,
) -> None:
    review = item["review"]
    assert isinstance(review, dict)
    assert item["kind"] == "edit"
    assert item["reviewable"] is True
    assert item["choices"] == ["allow_once", "deny_once"]
    assert review["targetPath"] == label
    assert review["complete"] is True
    assert review["truncated"] is False
    assert review["redacted"] is False
    diff = review["diffPreview"]
    assert isinstance(diff, str)
    assert diff.splitlines()[:2] == [f"--- a/{label}", f"+++ b/{label}"]
    serialized = json.dumps(item, ensure_ascii=False)
    assert str(workspace) not in serialized
    assert str(Path.home()) not in serialized
    assert "[LOCAL_PATH]" not in serialized


def _assert_fixed_deny_only_review(
    item: dict[str, object],
    *,
    workspace: Path,
    forbidden: str,
) -> None:
    review = item["review"]
    assert isinstance(review, dict)
    assert item["kind"] == "edit"
    assert item["reviewable"] is False
    assert item["choices"] == ["deny_once"]
    assert review == {
        "targetPath": "unsafe.txt",
        "diffPreview": "[REDACTED SENSITIVE REVIEW]",
        "complete": True,
        "truncated": False,
        "redacted": True,
    }
    serialized = json.dumps(item, ensure_ascii=False)
    escaped = json.dumps(forbidden, ensure_ascii=True)[1:-1]
    assert forbidden not in serialized
    assert escaped not in json.dumps(item, ensure_ascii=True)
    assert str(workspace) not in serialized


_SPLITLINES_CONTROLS = ["\x0b", "\x0c", "\x85", "\u2028", "\u2029"]
_C0_C1_CONTROLS = [
    "\x00",
    "\x07",
    "\x08",
    "\x0b",
    "\x0c",
    "\r",
    "\x1b",
    "\x7f",
    "\x80",
    "\x9f",
]
_UNICODE_FORMAT_CONTROLS = [
    *map(chr, range(0x200B, 0x2010)),
    *map(chr, range(0x202A, 0x202F)),
    *map(chr, range(0x2060, 0x2070)),
    "\ufeff",
]
_DANGEROUS_REVIEW_CHARACTERS = list(
    dict.fromkeys(
        [*_C0_C1_CONTROLS, *_SPLITLINES_CONTROLS, *_UNICODE_FORMAT_CONTROLS]
    )
)


@pytest.mark.parametrize(
    "dangerous_character",
    [*_DANGEROUS_REVIEW_CHARACTERS, "\ud800"],
    ids=lambda character: f"U+{ord(character):04X}",
)
@pytest.mark.parametrize(
    "initial_content",
    [None, "existing-safe\n"],
    ids=["new-file", "existing-file"],
)
def test_real_write_file_invisible_control_is_fixed_deny_only(
    tmp_path: Path,
    dangerous_character: str,
    initial_content: str | None,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "unsafe.txt"
    if initial_content is not None:
        target.write_text(initial_content, encoding="utf-8")

    with _pending_tool(
        workspace,
        write_file_tool,
        {
            "path": "unsafe.txt",
            "content": f"safe{dangerous_character}hidden\n",
        },
    ) as (broker, turn_id, item, worker, outcome):
        assert target.exists() is (initial_content is not None)
        if initial_content is not None:
            assert target.read_text(encoding="utf-8") == initial_content
        _assert_fixed_deny_only_review(
            item,
            workspace=workspace,
            forbidden=dangerous_character,
        )
        with pytest.raises(PermissionApprovalError) as error:
            broker.decide(
                permission_id=str(item["permissionId"]),
                turn_id=turn_id,
                decision="allow_once",
            )
        assert error.value.code == "permission_not_reviewable"
        assert target.exists() is (initial_content is not None)
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert target.exists() is (initial_content is not None)
    if initial_content is not None:
        assert target.read_text(encoding="utf-8") == initial_content
    assert "result" not in outcome
    assert isinstance(outcome.get("error"), RuntimeError)


@pytest.mark.parametrize(
    ("content", "forbidden"),
    [
        ("\u2028at-start\n", "\u2028"),
        ("at-end\u202e", "\u202e"),
        ("\x0b", "\x0b"),
    ],
    ids=["start", "end", "only-character"],
)
def test_real_write_file_control_at_content_boundaries_is_deny_only(
    tmp_path: Path,
    content: str,
    forbidden: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "unsafe.txt"
    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": "unsafe.txt", "content": content},
    ) as (broker, turn_id, item, worker, outcome):
        _assert_fixed_deny_only_review(
            item,
            workspace=workspace,
            forbidden=forbidden,
        )
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)
    assert not target.exists()
    assert "result" not in outcome


def _exercise_dangerous_existing_file_tool(
    *,
    workspace: Path,
    target: Path,
    tool: ToolDefinition,
    input_data: dict[str, object],
    forbidden: str,
) -> None:
    original = target.read_bytes()
    with _pending_tool(
        workspace,
        tool,
        input_data,
    ) as (broker, turn_id, item, worker, outcome):
        assert target.read_bytes() == original
        _assert_fixed_deny_only_review(
            item,
            workspace=workspace,
            forbidden=forbidden,
        )
        with pytest.raises(PermissionApprovalError) as error:
            broker.decide(
                permission_id=str(item["permissionId"]),
                turn_id=turn_id,
                decision="allow_once",
            )
        assert error.value.code == "permission_not_reviewable"
        assert target.read_bytes() == original
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert target.read_bytes() == original
    assert "result" not in outcome
    assert isinstance(outcome.get("error"), RuntimeError)


@pytest.mark.parametrize(
    "dangerous_character",
    ["\x0b", "\x85", "\u2028", "\u2029", "\u202e", "\u200b", "\ufeff"],
    ids=["vt", "nel", "line-separator", "paragraph-separator", "rlo", "zwsp", "bom"],
)
def test_real_edit_file_removing_invisible_control_stays_deny_only(
    tmp_path: Path,
    dangerous_character: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "unsafe.txt"
    original = f"safe{dangerous_character}hidden\n"
    target.write_text(original, encoding="utf-8")

    _exercise_dangerous_existing_file_tool(
        workspace=workspace,
        target=target,
        tool=edit_file_tool,
        input_data={"path": "unsafe.txt", "old": original, "new": "safehidden\n"},
        forbidden=dangerous_character,
    )


@pytest.mark.parametrize(
    ("initial", "input_data", "forbidden"),
    [
        (
            "before\n",
            {"path": "unsafe.txt", "old": "before", "new": "after\u202evalue"},
            "\u202e",
        ),
        (
            "safe safe\n",
            {
                "path": "unsafe.txt",
                "old": "safe",
                "new": "safe\u200b",
                "replace_all": True,
            },
            "\u200b",
        ),
        (
            "left\x0bright\n",
            {
                "path": "unsafe.txt",
                "old": "left\x0bright",
                "new": "changed\ufeffbody",
            },
            "\x0b",
        ),
    ],
    ids=["replacement-adds-bidi", "replace-all-adds-zero-width", "before-and-after"],
)
def test_real_edit_file_invisible_control_variants_are_deny_only(
    tmp_path: Path,
    initial: str,
    input_data: dict[str, object],
    forbidden: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "unsafe.txt"
    target.write_text(initial, encoding="utf-8")

    _exercise_dangerous_existing_file_tool(
        workspace=workspace,
        target=target,
        tool=edit_file_tool,
        input_data=input_data,
        forbidden=forbidden,
    )


@pytest.mark.parametrize(
    ("replacements", "forbidden"),
    [
        ([{"search": "alpha", "replace": "alpha\x85hidden"}], "\x85"),
        (
            [
                {"search": "alpha", "replace": "first"},
                {"search": "beta", "replace": "second\u2066hidden"},
            ],
            "\u2066",
        ),
    ],
    ids=["single-replacement", "multi-replacement"],
)
def test_real_patch_file_invisible_control_is_deny_only(
    tmp_path: Path,
    replacements: list[dict[str, str]],
    forbidden: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "unsafe.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    _exercise_dangerous_existing_file_tool(
        workspace=workspace,
        target=target,
        tool=patch_file_tool,
        input_data={"path": "unsafe.txt", "replacements": replacements},
        forbidden=forbidden,
    )


@pytest.mark.parametrize(
    "content",
    [
        "\tprint('python tab')\n",
        "\tconsole.log('javascript tab');\n",
        "line one\nline two\n",
        "line one\r\nline two\r\n",
        "普通中文内容\n",
        "café naïve λ\n",
        "ordinary emoji 🧭🚀\n",
        "no final newline",
        "one\ntwo\nthree\n",
    ],
    ids=[
        "python-tab",
        "javascript-tab",
        "lf",
        "crlf",
        "chinese",
        "latin-unicode",
        "emoji",
        "no-final-newline",
        "multiline",
    ],
)
def test_real_write_file_safe_text_remains_reviewable(
    tmp_path: Path,
    content: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": "safe.txt", "content": content},
    ) as (broker, turn_id, item, worker, _outcome):
        _assert_safe_workspace_review(item, workspace=workspace, label="safe.txt")
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)


@pytest.mark.parametrize(
    "dangerous_character",
    [*_DANGEROUS_REVIEW_CHARACTERS, "\udfff"],
    ids=lambda character: f"U+{ord(character):04X}",
)
def test_file_review_precheck_never_passes_raw_control_to_permission_prompt(
    tmp_path: Path,
    dangerous_character: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "unsafe.txt"
    prompts: list[dict[str, object]] = []
    manager = PermissionManager(
        str(workspace),
        prompt=lambda request: prompts.append(request) or {"decision": "deny_once"},
    )

    with pytest.raises(RuntimeError):
        write_file_tool.run(
            write_file_tool.validator(
                {
                    "path": "unsafe.txt",
                    "content": f"prefix{dangerous_character}suffix\n",
                }
            ),
            ToolContext(cwd=str(workspace), permissions=manager),
        )

    assert not target.exists()
    assert len(prompts) == 1
    request = prompts[0]
    assert request["review"]["diffPreview"] == "[REDACTED SENSITIVE REVIEW]"
    assert dangerous_character not in json.dumps(request, ensure_ascii=False)


def test_real_write_file_absolute_workspace_path_has_safe_relative_review(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "code" / "hello.py"

    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": str(target), "content": 'print("hello")\n'},
    ) as (broker, turn_id, item, worker, outcome):
        assert not target.exists()
        _assert_safe_workspace_review(
            item,
            workspace=workspace,
            label="code/hello.py",
        )
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)

    assert not target.exists()
    assert isinstance(outcome.get("error"), RuntimeError)


def test_real_edit_file_absolute_workspace_path_has_safe_relative_review(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "message.txt"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")

    with _pending_tool(
        workspace,
        edit_file_tool,
        {"path": str(target), "old": "before", "new": "after"},
    ) as (broker, turn_id, item, worker, _outcome):
        assert target.read_text(encoding="utf-8") == "before\n"
        _assert_safe_workspace_review(
            item,
            workspace=workspace,
            label="src/message.txt",
        )
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)

    assert target.read_text(encoding="utf-8") == "before\n"


def test_real_patch_file_absolute_workspace_path_has_safe_relative_review(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "settings.txt"
    target.parent.mkdir(parents=True)
    target.write_text("alpha=1\nbeta=2\n", encoding="utf-8")

    with _pending_tool(
        workspace,
        patch_file_tool,
        {
            "path": str(target),
            "replacements": [
                {"search": "alpha=1", "replace": "alpha=3"},
                {"search": "beta=2", "replace": "beta=4"},
            ],
        },
    ) as (broker, turn_id, item, worker, _outcome):
        assert target.read_text(encoding="utf-8") == "alpha=1\nbeta=2\n"
        _assert_safe_workspace_review(
            item,
            workspace=workspace,
            label="src/settings.txt",
        )
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)

    assert target.read_text(encoding="utf-8") == "alpha=1\nbeta=2\n"


def test_real_write_file_canonical_alias_input_never_leaks_alias(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "alias.txt"
    canonical = str(target.resolve())
    alias = canonical.replace("/private/var/", "/var/", 1)
    if alias == canonical or Path(alias).resolve() != target.resolve():
        pytest.skip("platform does not expose a controllable canonical path alias")

    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": alias, "content": "alias-safe\n"},
    ) as (broker, turn_id, item, worker, _outcome):
        _assert_safe_workspace_review(
            item,
            workspace=workspace,
            label="alias.txt",
        )
        serialized = json.dumps(item, ensure_ascii=False)
        assert alias not in serialized
        assert "/var/" not in serialized
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)


def test_real_write_file_relative_path_remains_reviewable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": "relative.txt", "content": "relative-safe\n"},
    ) as (broker, turn_id, item, worker, _outcome):
        _assert_safe_workspace_review(
            item,
            workspace=workspace,
            label="relative.txt",
        )
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)


@pytest.mark.parametrize(
    ("input_path", "label"),
    [
        ("./nested/../code/dot.txt", "code/dot.txt"),
        ("src/../safe-parent.txt", "safe-parent.txt"),
        ("dir with spaces/hello world.txt", "dir with spaces/hello world.txt"),
        ("Unicode/naïve-λ.txt", "Unicode/naïve-λ.txt"),
        ("中文/说明.txt", "中文/说明.txt"),
        ("-leading.txt", "-leading.txt"),
        ("one/two/three/deep.txt", "one/two/three/deep.txt"),
    ],
)
def test_real_write_file_normalizes_dot_and_special_workspace_paths(
    tmp_path: Path,
    input_path: str,
    label: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": input_path, "content": "safe\n"},
    ) as (broker, turn_id, item, worker, _outcome):
        _assert_safe_workspace_review(item, workspace=workspace, label=label)
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)


def test_real_write_file_existing_file_uses_same_normalized_label(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "existing.txt"
    target.write_text("before\n", encoding="utf-8")

    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": str(target), "content": "after\n"},
    ) as (broker, turn_id, item, worker, _outcome):
        assert target.read_text(encoding="utf-8") == "before\n"
        _assert_safe_workspace_review(
            item,
            workspace=workspace,
            label="existing.txt",
        )
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)


@pytest.mark.parametrize("use_alias", [False, True])
def test_real_edit_file_relative_or_alias_replace_all_uses_one_safe_label(
    tmp_path: Path,
    use_alias: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "edit-all.txt"
    target.write_text("old old\n", encoding="utf-8")
    input_path = "edit-all.txt"
    if use_alias:
        input_path = str(target).replace("/private/var/", "/var/", 1)
        if input_path == str(target) or Path(input_path).resolve() != target.resolve():
            pytest.skip("platform does not expose a controllable canonical path alias")

    with _pending_tool(
        workspace,
        edit_file_tool,
        {
            "path": input_path,
            "old": "old",
            "new": "new",
            "replace_all": True,
        },
    ) as (broker, turn_id, item, worker, _outcome):
        _assert_safe_workspace_review(
            item,
            workspace=workspace,
            label="edit-all.txt",
        )
        assert item["review"]["diffPreview"].endswith("+new new")
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)


@pytest.mark.parametrize("use_alias", [False, True])
def test_real_patch_file_relative_or_alias_multiple_replacements_is_safe(
    tmp_path: Path,
    use_alias: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "patch-many.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")
    input_path = "patch-many.txt"
    if use_alias:
        input_path = str(target).replace("/private/var/", "/var/", 1)
        if input_path == str(target) or Path(input_path).resolve() != target.resolve():
            pytest.skip("platform does not expose a controllable canonical path alias")

    with _pending_tool(
        workspace,
        patch_file_tool,
        {
            "path": input_path,
            "replacements": [
                {"search": "one", "replace": "three"},
                {"search": "two", "replace": "four"},
            ],
        },
    ) as (broker, turn_id, item, worker, _outcome):
        _assert_safe_workspace_review(
            item,
            workspace=workspace,
            label="patch-many.txt",
        )
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)


@pytest.mark.parametrize(
    "content_factory",
    [
        lambda workspace: f"workspace={workspace}\n",
        lambda _workspace: f"home={Path.home()}\n",
        lambda _workspace: "other=/etc/minicode-private\n",
        lambda _workspace: "/etc/minicode-leading-private\n",
        lambda _workspace: "API_KEY=diff-body-secret-marker\n",
        lambda _workspace: "Authorization: Bearer diff-body-bearer-marker\n",
        lambda _workspace: (
            "url=https://user:diff-body-credential-marker@example.invalid/\n"
        ),
        lambda _workspace: (
            "-----BEGIN PRIVATE KEY-----\n"
            "diff-body-private-key-marker\n"
            "-----END PRIVATE KEY-----\n"
        ),
        lambda _workspace: "\x1b[31mansi-body-marker\x1b[0m\n",
        lambda _workspace: "control-body-marker\x01\n",
    ],
)
def test_real_write_file_sensitive_diff_body_remains_deny_only(
    tmp_path: Path,
    content_factory,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = content_factory(workspace)

    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": "sensitive.txt", "content": content},
    ) as (broker, turn_id, item, worker, _outcome):
        review = item["review"]
        serialized = json.dumps(item, ensure_ascii=False)
        assert item["reviewable"] is False
        assert item["choices"] == ["deny_once"]
        assert review["redacted"] is True
        assert review["diffPreview"] == "[REDACTED SENSITIVE REVIEW]"
        assert str(workspace) not in serialized
        assert str(Path.home()) not in serialized
        assert "diff-body-secret-marker" not in serialized
        assert "ansi-body-marker" not in serialized
        assert "control-body-marker" not in serialized
        assert "diff-body-bearer-marker" not in serialized
        assert "diff-body-credential-marker" not in serialized
        assert "diff-body-private-key-marker" not in serialized
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)


def test_real_write_file_truncated_diff_body_remains_deny_only(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": "large.txt", "content": "x" * (40 * 1024)},
    ) as (broker, turn_id, item, worker, _outcome):
        review = item["review"]
        assert item["reviewable"] is False
        assert item["choices"] == ["deny_once"]
        assert review["complete"] is False
        assert review["truncated"] is True
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)


@pytest.mark.parametrize(
    "input_factory",
    [
        lambda workspace: str(workspace.parent / "outside-absolute.txt"),
        lambda _workspace: "../outside-relative.txt",
    ],
)
def test_real_write_file_workspace_escape_stays_path_deny_only(
    tmp_path: Path,
    input_factory,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    input_path = input_factory(workspace)
    outside = Path(input_path)
    if not outside.is_absolute():
        outside = (workspace / outside).resolve()

    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": input_path, "content": "must-not-write\n"},
    ) as (broker, turn_id, item, worker, outcome):
        assert item["kind"] == "path"
        assert item["reviewable"] is False
        assert item["choices"] == ["deny_once"]
        assert item["review"] == {
            "intent": "write",
            "outsideWorkspace": True,
        }
        assert str(outside) not in json.dumps(item, ensure_ascii=False)
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)

    assert not outside.exists()
    assert isinstance(outcome.get("error"), RuntimeError)


def test_real_write_file_escaping_symlink_stays_path_deny_only(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    try:
        (workspace / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": "escape/leak.txt", "content": "must-not-write\n"},
    ) as (broker, turn_id, item, worker, _outcome):
        assert item["kind"] == "path"
        assert item["reviewable"] is False
        assert item["choices"] == ["deny_once"]
        assert item["review"] == {
            "intent": "write",
            "outsideWorkspace": True,
        }
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)

    assert not (outside / "leak.txt").exists()


def test_real_write_file_internal_symlink_uses_resolved_target_label(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    real = workspace / "real"
    real.mkdir(parents=True)
    try:
        (workspace / "alias-dir").symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": "alias-dir/safe.txt", "content": "safe\n"},
    ) as (broker, turn_id, item, worker, _outcome):
        _assert_safe_workspace_review(
            item,
            workspace=workspace,
            label="real/safe.txt",
        )
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)


@pytest.mark.parametrize("filename", ["line\nbreak.txt", "control\x01name.txt"])
def test_real_write_file_control_filename_fails_before_review(
    tmp_path: Path,
    filename: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompts: list[dict[str, object]] = []
    manager = PermissionManager(
        str(workspace),
        prompt=lambda request: prompts.append(request) or {"decision": "allow_once"},
    )

    with pytest.raises(
        PermissionError,
        match="File review target is not a safe workspace-local path",
    ):
        write_file_tool.run(
            write_file_tool.validator({"path": filename, "content": "unsafe\n"}),
            ToolContext(cwd=str(workspace), permissions=manager),
        )

    assert prompts == []
    assert not (workspace / filename).exists()


def test_real_write_file_nul_filename_fails_before_review(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompts: list[dict[str, object]] = []
    manager = PermissionManager(
        str(workspace),
        prompt=lambda request: prompts.append(request) or {"decision": "allow_once"},
    )

    with pytest.raises((OSError, ValueError, PermissionError)):
        write_file_tool.run(
            write_file_tool.validator(
                {"path": "nul\x00name.txt", "content": "unsafe\n"}
            ),
            ToolContext(cwd=str(workspace), permissions=manager),
        )

    assert prompts == []


def test_real_write_file_missing_workspace_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "missing-workspace"

    with pytest.raises(
        PermissionError,
        match="File review target is not a safe workspace-local path",
    ):
        write_file_tool.run(
            write_file_tool.validator({"path": "unsafe.txt", "content": "unsafe\n"}),
            ToolContext(cwd=str(workspace), permissions=None),
        )

    assert not workspace.exists()


def test_real_absolute_write_allow_writes_once_and_returns_relative_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "allowed" / "once.txt"
    writes: list[str] = []
    original_write_text = Path.write_text

    def counted_write_text(self: Path, data: str, *args, **kwargs):
        if self == target:
            writes.append(data)
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", counted_write_text)
    with _pending_tool(
        workspace,
        write_file_tool,
        {"path": str(target), "content": "written-once\n"},
    ) as (broker, turn_id, item, worker, outcome):
        assert not target.exists()
        _assert_safe_workspace_review(
            item,
            workspace=workspace,
            label="allowed/once.txt",
        )
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="allow_once",
        )
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert "error" not in outcome
    assert writes == ["written-once\n"]
    assert target.read_text(encoding="utf-8") == "written-once\n"
    assert outcome["result"].output == "Applied reviewed changes to allowed/once.txt"


@pytest.mark.parametrize("decision", ["allow_once", "deny_once"])
def test_real_gateway_absolute_write_exposes_safe_review_and_controls_effect(
    tmp_path: Path,
    decision: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "code" / "gateway.py"
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.permission_approval_broker = broker
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    turn_id = "turn_" + ("c" if decision == "allow_once" else "d") * 32
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id="run_" + "e" * 32,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    outcome: dict[str, object] = {}

    def run() -> None:
        session.tool_started("write_file")
        try:
            outcome["result"] = write_file_tool.run(
                write_file_tool.validator(
                    {"path": str(target), "content": "gateway-safe\n"}
                ),
                ToolContext(cwd=str(workspace), permissions=manager),
            )
        except BaseException as error:  # noqa: BLE001 - asserted control flow
            outcome["error"] = error
        finally:
            session.tool_finished("write_file")

    worker = threading.Thread(target=run)
    worker.start()
    try:
        status, headers, payload = _http_pending(server.server_address[1])
        assert status == 200
        assert headers["Cache-Control"] == "no-store"
        item = payload["items"][0]
        _assert_safe_workspace_review(
            item,
            workspace=workspace,
            label="code/gateway.py",
        )
        assert str(tmp_path) not in json.dumps(payload, ensure_ascii=False)
        status, response_headers, response = _http_request(
            server.server_address[1],
            "POST",
            f"/api/v1/permissions/{item['permissionId']}/decision",
            body=json.dumps({"turnId": turn_id, "decision": decision}).encode(),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Origin": (
                    f"http://127.0.0.1:{server.server_address[1]}"
                ),
            },
        )
        assert status == 200
        assert response_headers["Cache-Control"] == "no-store"
        assert "Access-Control-Allow-Origin" not in response_headers
        assert response["decision"] == decision
        assert response["decisionAccepted"] is True
        worker.join(timeout=2)
        assert target.exists() is (decision == "allow_once")
        if decision == "allow_once":
            assert target.read_text(encoding="utf-8") == "gateway-safe\n"
        else:
            assert isinstance(outcome.get("error"), RuntimeError)
    finally:
        broker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        worker.join(timeout=2)


def test_real_gateway_sensitive_diff_body_rejects_allow_without_disclosure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "secret.txt"
    marker = "gateway-diff-secret-marker"
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.permission_approval_broker = broker
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    turn_id = "turn_" + "f" * 32
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    outcome: dict[str, object] = {}

    def run() -> None:
        session.tool_started("write_file")
        try:
            write_file_tool.run(
                write_file_tool.validator(
                    {"path": str(target), "content": f"API_KEY={marker}\n"}
                ),
                ToolContext(cwd=str(workspace), permissions=manager),
            )
        except BaseException as error:  # noqa: BLE001 - asserted control flow
            outcome["error"] = error
        finally:
            session.tool_finished("write_file")

    worker = threading.Thread(target=run)
    worker.start()
    try:
        status, _, payload = _http_pending(server.server_address[1])
        assert status == 200
        item = payload["items"][0]
        serialized = json.dumps(payload, ensure_ascii=False)
        assert item["reviewable"] is False
        assert item["choices"] == ["deny_once"]
        assert item["review"]["redacted"] is True
        assert marker not in serialized
        assert str(workspace) not in serialized

        status, _, rejected = _http_request(
            server.server_address[1],
            "POST",
            f"/api/v1/permissions/{item['permissionId']}/decision",
            body=json.dumps(
                {"turnId": turn_id, "decision": "allow_once"}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert status == 409
        assert rejected["error"]["code"] == "permission_not_reviewable"

        status, _, denied = _http_request(
            server.server_address[1],
            "POST",
            f"/api/v1/permissions/{item['permissionId']}/decision",
            body=json.dumps(
                {"turnId": turn_id, "decision": "deny_once"}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        assert denied["decisionAccepted"] is True
        worker.join(timeout=2)
        assert not target.exists()
        assert isinstance(outcome.get("error"), RuntimeError)
    finally:
        broker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        worker.join(timeout=2)


@pytest.mark.parametrize(
    "dangerous_character",
    ["\x0b", "\x85", "\u2028", "\u202e", "\ufeff", "\ud800"],
    ids=["vt", "nel", "line-separator", "rlo", "bom", "surrogate"],
)
def test_real_gateway_invisible_control_is_safe_deny_only(
    tmp_path: Path,
    dangerous_character: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "unsafe.txt"
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.permission_approval_broker = broker
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    turn_id = "turn_" + "9" * 32
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    outcome: dict[str, object] = {}

    def run() -> None:
        session.tool_started("write_file")
        try:
            outcome["result"] = write_file_tool.run(
                write_file_tool.validator(
                    {
                        "path": str(target),
                        "content": f"safe{dangerous_character}hidden\n",
                    }
                ),
                ToolContext(cwd=str(workspace), permissions=manager),
            )
        except BaseException as error:  # noqa: BLE001 - asserted control flow
            outcome["error"] = error
        finally:
            session.tool_finished("write_file")

    worker = threading.Thread(target=run)
    worker.start()
    try:
        status, headers, payload = _http_pending(server.server_address[1])
        assert status == 200
        assert headers["Cache-Control"] == "no-store"
        item = payload["items"][0]
        _assert_fixed_deny_only_review(
            item,
            workspace=workspace,
            forbidden=dangerous_character,
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        assert str(target) not in serialized

        status, _, rejected = _http_request(
            server.server_address[1],
            "POST",
            f"/api/v1/permissions/{item['permissionId']}/decision",
            body=json.dumps(
                {"turnId": turn_id, "decision": "allow_once"}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert status == 409
        assert rejected["error"]["code"] == "permission_not_reviewable"
        assert not target.exists()

        status, _, denied = _http_request(
            server.server_address[1],
            "POST",
            f"/api/v1/permissions/{item['permissionId']}/decision",
            body=json.dumps(
                {"turnId": turn_id, "decision": "deny_once"}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        assert denied["decisionAccepted"] is True
        worker.join(timeout=2)
        assert not target.exists()
        assert "result" not in outcome
        assert isinstance(outcome.get("error"), RuntimeError)
    finally:
        broker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        worker.join(timeout=2)


@pytest.mark.parametrize(
    "dangerous_character",
    [*_DANGEROUS_REVIEW_CHARACTERS, "\udfff"],
    ids=lambda character: f"U+{ord(character):04X}",
)
def test_broker_rejects_raw_invisible_control_diff_before_serialization(
    tmp_path: Path,
    dangerous_character: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "7" * 32
    events: list[tuple[str, dict[str, object]]] = []
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )
    outcome: dict[str, object] = {}

    def prompt() -> None:
        session.tool_started("write_file")
        try:
            outcome["result"] = session.prompt(
                {
                    "schemaVersion": 1,
                    "kind": "edit",
                    "review": {
                        "targetPath": str(workspace / "unsafe.txt"),
                        "diffPreview": (
                            "--- a/unsafe.txt\n"
                            "+++ b/unsafe.txt\n"
                            "@@ -0,0 +1 @@\n"
                            f"+safe{dangerous_character}hidden"
                        ),
                    },
                }
            )
        finally:
            session.tool_finished("write_file")

    worker = threading.Thread(target=prompt)
    worker.start()
    try:
        item = _wait_pending(broker)
        _assert_fixed_deny_only_review(
            item,
            workspace=workspace,
            forbidden=dangerous_character,
        )
        with pytest.raises(PermissionApprovalError) as error:
            broker.decide(
                permission_id=str(item["permissionId"]),
                turn_id=turn_id,
                decision="allow_once",
            )
        assert error.value.code == "permission_not_reviewable"
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)
    finally:
        broker.close()
        worker.join(timeout=2)

    assert outcome == {"result": {"decision": "deny_operation"}}
    assert dangerous_character not in json.dumps(events, ensure_ascii=False)
    assert {event_type for event_type, _payload in events} == {
        "permission.requested",
        "permission.decided",
    }


@pytest.mark.parametrize("terminal", ["cancel", "timeout", "restart"])
def test_invisible_control_terminal_paths_never_write(
    tmp_path: Path,
    terminal: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "unsafe.txt"
    dangerous_character = "\u202e"
    turn_id = "turn_" + "6" * 32
    monotonic_time = [0.0]
    broker = PermissionApprovalBroker(
        workspace,
        timeout_seconds=0.08 if terminal == "timeout" else 2,
        poll_interval=0.005,
        monotonic=lambda: monotonic_time[0],
    )
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    outcome: dict[str, object] = {}

    def run() -> None:
        session.tool_started("write_file")
        try:
            outcome["result"] = write_file_tool.run(
                write_file_tool.validator(
                    {
                        "path": "unsafe.txt",
                        "content": f"safe{dangerous_character}hidden\n",
                    }
                ),
                ToolContext(cwd=str(workspace), permissions=manager),
            )
        except BaseException as error:  # noqa: BLE001 - asserted control flow
            outcome["error"] = error
        finally:
            session.tool_finished("write_file")

    worker = threading.Thread(target=run)
    worker.start()
    item = _wait_pending(broker)
    _assert_fixed_deny_only_review(
        item,
        workspace=workspace,
        forbidden=dangerous_character,
    )
    try:
        if terminal == "cancel":
            broker.cancel_turn(turn_id)
        elif terminal == "timeout":
            monotonic_time[0] = 1.0
            assert broker.snapshot()["items"] == []
        else:
            permission_id = str(item["permissionId"])
            broker.close()
            replacement = PermissionApprovalBroker(workspace, timeout_seconds=2)
            try:
                with pytest.raises(PermissionApprovalError) as error:
                    replacement.decide(
                        permission_id=permission_id,
                        turn_id=turn_id,
                        decision="allow_once",
                    )
                assert error.value.code == "permission_not_found"
            finally:
                replacement.close()
        worker.join(timeout=2)
    finally:
        broker.close()
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert not target.exists()
    assert "result" not in outcome
    assert isinstance(outcome.get("error"), RuntimeError)


def test_broker_fails_closed_when_edit_headers_do_not_match_resolved_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "1" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    outcome: dict[str, object] = {}

    def prompt() -> None:
        session.tool_started("write_file")
        try:
            outcome["result"] = session.prompt(
                {
                    "schemaVersion": 1,
                    "kind": "edit",
                    "review": {
                        "targetPath": str(workspace / "safe.txt"),
                        "diffPreview": (
                            "--- a//outside/leak.txt\n"
                            "+++ b//outside/leak.txt\n"
                            "@@ -0,0 +1 @@\n"
                            "+safe"
                        ),
                    },
                }
            )
        finally:
            session.tool_finished("write_file")

    worker = threading.Thread(target=prompt)
    worker.start()
    try:
        item = _wait_pending(broker)
        assert item["reviewable"] is False
        assert item["choices"] == ["deny_once"]
        assert item["review"]["redacted"] is True
        assert item["review"]["diffPreview"] == "[REDACTED SENSITIVE REVIEW]"
        assert "/outside/" not in json.dumps(item, ensure_ascii=False)
        broker.decide(
            permission_id=str(item["permissionId"]),
            turn_id=turn_id,
            decision="deny_once",
        )
        worker.join(timeout=2)
    finally:
        broker.close()
        worker.join(timeout=2)

    assert outcome == {"result": {"decision": "deny_operation"}}
