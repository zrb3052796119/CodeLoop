from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "minicode/web/static/assets/app.js"
STYLES = ROOT / "minicode/web/static/assets/styles.css"


def _deletion_helpers_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    session_pattern = javascript[
        javascript.index("const SESSION_ID_PATTERN") : javascript.index(
            "\nconst TURN_ID_PATTERN"
        )
    ]
    return session_pattern + "\n" + javascript[
        javascript.index("const CHANGE_RESOURCE_NAMES") : javascript.index(
            "\nfunction createResourceRefreshQueue"
        )
    ]


def _deletion_transport_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    return (
        _deletion_helpers_source()
        + "\n"
        + javascript[
            javascript.index("function fixedDeletionError") : javascript.index(
                "\nfunction ensureDeletionDialogHost"
            )
        ]
        + "\n"
        + javascript[
            javascript.index("async function loadDeletionPreview") : javascript.index(
                "\nfunction prepareDeletionConvergence"
            )
        ]
        + "\n"
        + javascript[
            javascript.index("async function submitDeletion") : javascript.index(
                "\nfunction wireDeletionDialog"
            )
        ]
    )


def _deletion_convergence_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    return javascript[
        javascript.index("function prepareDeletionConvergence") : javascript.index(
            "\nasync function reconcileDeletionCollections"
        )
    ]


def _deletion_focus_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    return javascript[
        javascript.index("function wireDeletionDialog") : javascript.index(
            "\nfunction fixedPermissionError"
        )
    ]


def test_deletion_preview_and_result_contracts_are_exact_and_bounded() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const revision = `delrev_${'a'.repeat(64)}`;
const conversation = {
  schemaVersion: 1,
  generatedAt: '2026-07-23T00:00:00.000Z',
  mode: 'read-write',
  kind: 'conversation',
  target: { sessionId: 'session-safe-1' },
  status: 'ready',
  deletionRevision: revision,
  affected: { sessions: 1, turns: 2, runs: 3 },
  blockers: [],
  diagnostics: [],
};
const memory = {
  ...conversation,
  kind: 'project-memory',
  target: {
    memoryId: 'project-safe-1',
    scope: 'project',
    category: 'architecture',
    tier: 'long_term',
    lifecycleStatus: 'active',
    approvalStatus: 'approved',
  },
  affected: { entries: 1, approvalAuditRecords: 2, backlinks: 3 },
};
assert.equal(validateDeletionPreview(conversation, 'conversation', 'session-safe-1'), conversation);
assert.equal(validateDeletionPreview(memory, 'project-memory', 'project-safe-1'), memory);
for (const bad of [
  { ...conversation, schemaVersion: true },
  { ...conversation, extra: true },
  { ...conversation, generatedAt: '2026-02-30T00:00:00.000Z' },
  { ...conversation, deletionRevision: 'delrev_bad' },
  { ...conversation, kind: 'project-memory' },
  { ...conversation, affected: { sessions: 1, turns: -1, runs: 0 } },
  { ...conversation, affected: { sessions: 1, turns: 1.5, runs: 0 } },
  { ...conversation, affected: { sessions: 1, turns: 1000001, runs: 0 } },
  { ...conversation, affected: { sessions: 1, turns: 0, runs: 0, extra: 1 } },
  { ...conversation, blockers: Array(9).fill({ code: 'active_turn' }) },
  { ...conversation, diagnostics: [{ code: 'server_secret' }] },
]) assert.equal(validateDeletionPreview(bad, 'conversation', 'session-safe-1'), null);
assert.equal(
  validateDeletionPreview({ ...conversation, target: { sessionId: 'session-other' } }, 'conversation', 'session-safe-1'),
  null,
);
for (const field of ['category', 'tier', 'lifecycleStatus', 'approvalStatus']) {
  const bad = structuredClone(memory);
  bad.target[field] = 'invented';
  assert.equal(validateDeletionPreview(bad, 'project-memory', 'project-safe-1'), null);
}
const result = {
  schemaVersion: 1,
  generatedAt: '2026-07-23T00:00:01.000Z',
  mode: 'read-write',
  kind: 'conversation',
  target: { sessionId: 'session-safe-1' },
  status: 'completed',
  deletionRevision: revision,
  deleted: { sessions: 1, turns: 2, runs: 3 },
  remaining: { sessions: 0, turns: 0, runs: 0 },
  diagnostics: [],
};
assert.equal(
  validateDeletionResult(result, 'conversation', 'session-safe-1', revision),
  result,
);
assert.equal(
  validateDeletionResult({ ...result, status: 'ready' }, 'conversation', 'session-safe-1', revision),
  null,
);
assert.equal(
  validateDeletionResult({ ...result, deletionRevision: `delrev_${'b'.repeat(64)}` }, 'conversation', 'session-safe-1', revision),
  null,
);
const partial = {
  ...result,
  status: 'partial',
  diagnostics: [{ code: 'deletion_retry_required' }],
  remaining: { sessions: 1, turns: 0, runs: 1 },
};
assert.equal(validateDeletionResult(partial, 'conversation', 'session-safe-1', revision), partial);
"""
    subprocess.run(
        ["node", "-e", _deletion_helpers_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_deletion_ui_contract_is_independent_accessible_and_transport_bounded() -> None:
    javascript = APP.read_text(encoding="utf-8")
    stylesheet = STYLES.read_text(encoding="utf-8")

    assert "const conversationDeletionStore" in javascript
    assert "const projectMemoryDeletionStore" in javascript
    assert "openConversationDeletion" in javascript
    assert "openProjectMemoryDeletion" in javascript
    assert "submitDeletion" in javascript
    assert "role=\"dialog\"" in javascript
    assert "aria-modal=\"true\"" in javascript
    assert "删除会话及关联记录" in javascript
    assert "删除这条 Project Memory" in javascript
    assert "window.confirm" not in javascript
    assert "window.alert" not in javascript
    assert javascript.count("new EventSource('/api/v1/events')") == 1
    assert "setInterval(loadDeletion" not in javascript
    assert ".deletion-dialog" in stylesheet
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet
    assert (
        ".deletion-dialog > footer { align-items: stretch; "
        "flex-direction: column; }"
    ) in stylesheet


def test_deletion_dialog_focus_loop_and_escape_are_executable() -> None:
    harness = r"""
const assert = require('node:assert/strict');
let keydown = null;
let closed = 0;
const first = { focus() { document.activeElement = first; } };
const middle = { focus() { document.activeElement = middle; } };
const last = { focus() { document.activeElement = last; } };
const dialog = { querySelectorAll() { return [first, middle, last]; } };
global.document = {
  activeElement: first,
  addEventListener(type, callback) {
    if (type === 'keydown') keydown = callback;
  },
  querySelector(selector) {
    return selector === '.deletion-dialog' ? dialog : null;
  },
};
function ensureDeletionDialogHost() {}
function activeDeletionStore() { return { phase: 'review', kind: 'conversation' }; }
function closeDeletionDialog() { closed += 1; }
wireDeletionDialog();
let prevented = 0;
keydown({ key: 'Tab', shiftKey: true, preventDefault() { prevented += 1; } });
assert.equal(document.activeElement, last);
assert.equal(prevented, 1);
keydown({ key: 'Tab', shiftKey: false, preventDefault() { prevented += 1; } });
assert.equal(document.activeElement, first);
assert.equal(prevented, 2);
keydown({ key: 'Escape', shiftKey: false, preventDefault() { prevented += 1; } });
assert.equal(closed, 1);
assert.equal(prevented, 3);
"""
    subprocess.run(
        ["node", "-e", _deletion_focus_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_preview_target_switch_and_close_generation_drop_old_responses() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const revision = (char) => `delrev_${char.repeat(64)}`;
const preview = (id, char) => ({
  schemaVersion: 1, generatedAt: '2026-07-23T00:00:00.000Z', mode: 'read-write',
  kind: 'conversation', target: { sessionId: id }, status: 'ready',
  deletionRevision: revision(char), affected: { sessions: 1, turns: 0, runs: 0 },
  blockers: [], diagnostics: [],
});
const store = {
  phase: 'review', kind: 'conversation', targetId: 'session-A', preview: null,
  result: null, errorCode: null, errorMessage: null, requestGeneration: 0,
  actionGeneration: 0, outcomeUnconfirmed: false, staleNotice: false,
  localBusy: false, convergence: null,
};
let resolveA;
const response = (payload) => ({ ok: true, async text() { return JSON.stringify(payload); } });
global.fetch = async (url) => {
  if (url.includes('session-A')) return new Promise((resolve) => { resolveA = () => resolve(response(preview('session-A', 'a'))); });
  return response(preview('session-B', 'b'));
};
function renderDeletionDialog() {}
function deletionPath(value) { return `/api/v1/sessions/${value.targetId}/deletion`; }
async function beginDeletionReconciliation() {}
async function reconcileDeletionCollections() {}
(async () => {
  const old = loadDeletionPreview(store, 'open');
  store.targetId = 'session-B';
  const current = loadDeletionPreview(store, 'open');
  await current;
  assert.equal(store.preview.target.sessionId, 'session-B');
  assert.equal(store.phase, 'review');
  resolveA();
  await old;
  assert.equal(store.preview.target.sessionId, 'session-B');
  store.requestGeneration += 1;
  store.targetId = null;
  assert.equal(await loadDeletionPreview(store, 'manual'), false);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", _deletion_transport_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_stale_partial_and_lost_post_never_auto_repost() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const revision = `delrev_${'a'.repeat(64)}`;
const ready = {
  schemaVersion: 1, generatedAt: '2026-07-23T00:00:00.000Z', mode: 'read-write',
  kind: 'conversation', target: { sessionId: 'session-safe' }, status: 'ready',
  deletionRevision: revision, affected: { sessions: 1, turns: 1, runs: 1 },
  blockers: [], diagnostics: [],
};
const result = (status) => ({
  schemaVersion: 1, generatedAt: '2026-07-23T00:00:01.000Z', mode: 'read-write',
  kind: 'conversation', target: { sessionId: 'session-safe' }, status,
  deletionRevision: revision, deleted: { sessions: 0, turns: 1, runs: 0 },
  remaining: { sessions: 1, turns: 0, runs: 1 },
  diagnostics: status === 'partial' ? [{ code: 'deletion_retry_required' }] : [],
});
const store = {
  phase: 'review', kind: 'conversation', targetId: 'session-safe', preview: ready,
  result: null, errorCode: null, errorMessage: null, requestGeneration: 0,
  actionGeneration: 0, outcomeUnconfirmed: false, staleNotice: false,
  localBusy: false, convergence: null,
};
const response = (ok, payload, status = ok ? 200 : 409) => ({
  ok, status, async text() { return JSON.stringify(payload); },
});
function deletionStoreFor() { return store; }
function activeDeletionStore() { return store; }
function deletionPath() { return '/api/v1/sessions/session-safe/deletion'; }
function deletionCanSubmit(value) { return value.phase === 'review' && value.preview === ready; }
function renderDeletionDialog() {}
async function beginDeletionReconciliation() { throw new Error('unexpected reconciliation'); }
async function reconcileDeletionCollections() {}
let previewGets = 0;
async function loadDeletionPreview(value, reason) {
  previewGets += 1;
  assert.equal(reason, 'stale');
  value.preview = { ...ready, deletionRevision: `delrev_${'b'.repeat(64)}` };
  value.phase = 'stale';
  return true;
}
(async () => {
  let calls = [];
  global.fetch = async (url, options) => {
    calls.push(options);
    return response(false, { ok: false, error: { code: 'deletion_revision_stale', message: 'raw secret' } });
  };
  assert.equal(await submitDeletion('conversation'), false);
  assert.equal(calls.filter((item) => item.method === 'POST').length, 1);
  assert.equal(previewGets, 1);
  assert.equal(store.phase, 'stale');
  assert.equal(store.errorMessage.includes('raw secret'), false);

  Object.assign(store, {
    phase: 'review', preview: ready, result: null, outcomeUnconfirmed: false,
    errorCode: null, errorMessage: null,
  });
  calls = [];
  global.fetch = async (url, options) => {
    calls.push(options);
    return response(true, result('partial'));
  };
  assert.equal(await submitDeletion('conversation'), false);
  assert.equal(calls.filter((item) => item.method === 'POST').length, 1);
  assert.equal(store.phase, 'partial');
  assert.equal(store.preview, null);

  Object.assign(store, {
    phase: 'review', preview: ready, result: null, outcomeUnconfirmed: false,
    errorCode: null, errorMessage: null,
  });
  calls = [];
  global.fetch = async (url, options) => { calls.push(options); throw new Error('offline'); };
  assert.equal(await submitDeletion('conversation'), false);
  assert.equal(calls.filter((item) => item.method === 'POST').length, 1);
  assert.equal(store.phase, 'unconfirmed');
  assert.equal(store.outcomeUnconfirmed, true);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", _deletion_helpers_source() + "\n" + APP.read_text(encoding="utf-8")[
            APP.read_text(encoding="utf-8").index("function fixedDeletionError") :
            APP.read_text(encoding="utf-8").index("\nfunction ensureDeletionDialogHost")
        ] + "\n" + APP.read_text(encoding="utf-8")[
            APP.read_text(encoding="utf-8").index("async function submitDeletion") :
            APP.read_text(encoding="utf-8").index("\nfunction wireDeletionDialog")
        ] + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_convergence_fences_stores_preserves_draft_and_memory_filters() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const conversationDeletionTombstones = new Set();
const projectMemoryDeletionTombstones = new Set();
const sessionsStore = { requestId: 2, items: [{ id: 'gone' }, { id: 'keep' }] };
const sessionDetailStore = {
  requestId: 3, selectionVersion: 4, sessionId: 'gone', data: { session: { id: 'gone' } }, phase: 'loaded',
};
const runsStore = { requestId: 5, items: [{ id: 'run-gone', sessionId: 'gone' }, { id: 'run-keep', sessionId: 'keep' }] };
const runDetailStore = { requestId: 6, runId: 'run-gone', data: { run: { sessionId: 'gone' } } };
const runtimeTraceStore = { listRequestId: 7, detailRequestId: 8 };
const chatStore = {
  targetMode: 'existing', draft: '保留这个草稿', activeTurnId: null, activeTargetSessionId: null,
};
let cleared = null;
let resetRuns = 0;
function clearStoredSessionSelection(id) { cleared = id; }
function resetRunDetail() { resetRuns += 1; runDetailStore.runId = null; runDetailStore.data = null; }
function renderSessionSurfaces() {}
function renderRouteOnly() {}
const memoryStore = {
  requestId: 10, filters: { scope: 'project', tier: 'long_term', category: 'testing' },
  data: { items: [{ id: 'mem-gone' }, { id: 'mem-keep' }] },
};
const memoryApprovalStore = {
  requestId: 11, actionGeneration: 12,
  items: [{ memoryId: 'mem-gone' }, { memoryId: 'mem-keep' }],
  selectedMemoryId: 'mem-gone', actingMemoryId: 'mem-gone',
};
let memoryApprovalRefreshQueued = true;
const conversation = { kind: 'conversation', targetId: 'gone' };
prepareDeletionConvergence(conversation);
assert.deepEqual(sessionsStore.items.map((item) => item.id), ['keep']);
assert.deepEqual(runsStore.items.map((item) => item.id), ['run-keep']);
assert.equal(sessionDetailStore.sessionId, null);
assert.equal(chatStore.targetMode, 'new');
assert.equal(chatStore.draft, '保留这个草稿');
assert.equal(chatStore.activeTurnId, null);
assert.equal(cleared, 'gone');
assert.equal(resetRuns, 1);
assert.equal(conversationDeletionTombstones.has('gone'), true);
assert.deepEqual(conversation.convergence, { sessions: false, runs: false });

const filters = JSON.stringify(memoryStore.filters);
const memory = { kind: 'project-memory', targetId: 'mem-gone' };
prepareDeletionConvergence(memory);
assert.deepEqual(memoryStore.data.items.map((item) => item.id), ['mem-keep']);
assert.deepEqual(memoryApprovalStore.items.map((item) => item.memoryId), ['mem-keep']);
assert.equal(memoryApprovalStore.selectedMemoryId, null);
assert.equal(memoryApprovalStore.actingMemoryId, null);
assert.equal(memoryApprovalStore.actionGeneration, 13);
assert.equal(JSON.stringify(memoryStore.filters), filters);
assert.equal(projectMemoryDeletionTombstones.has('mem-gone'), true);
"""
    subprocess.run(
        ["node", "-e", _deletion_convergence_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_deletion_source_never_persists_revisions_or_renders_sensitive_copies() -> None:
    javascript = APP.read_text(encoding="utf-8")
    action_source = javascript[
        javascript.index("function deletionStoreFor") : javascript.index(
            "\nfunction fixedPermissionError"
        )
    ]
    preview_source = javascript[
        javascript.index("function deletionPreviewDetails") : javascript.index(
            "\nfunction deletionDialogActions"
        )
    ]

    assert "localStorage" not in action_source
    assert "sessionStorage" not in action_source
    assert "new EventSource" not in action_source
    assert "setInterval" not in action_source
    assert "console." not in action_source
    for sensitive in (
        "lastMessagePreview",
        "contentHash",
        "approvalReason",
        "provenance",
        "toolInput",
        "toolOutput",
    ):
        assert sensitive not in preview_source
