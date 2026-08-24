from __future__ import annotations

from pathlib import Path

from tests.node_harness import run_node


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "minicode/web/static/assets/app.js"
HTML = ROOT / "minicode/web/static/index.html"
STYLES = ROOT / "minicode/web/static/assets/styles.css"


def _permission_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    helpers = javascript[
        javascript.index("const CHANGE_RESOURCE_NAMES") : javascript.index(
            "\nfunction createResourceRefreshQueue"
        )
    ]
    return helpers


def _permission_action_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    return _permission_source() + "\n" + javascript[
        javascript.index("function fixedPermissionError") : javascript.index(
            "\nfunction renderSessionMenu"
        )
    ]


def _cancel_finish_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    return javascript[
        javascript.index("function finishCancelledTurn") : javascript.index(
            "\nfunction validCancelResponse"
        )
    ]


def _chat_terminal_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    return javascript[
        javascript.index("function validTurnStatus") : javascript.index(
            "\nasync function reconcileActiveTurnOnce"
        )
    ]


def _submit_chat_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    return javascript[
        javascript.index("async function submitChatTurn") : javascript.index(
            "\nfunction renderConversationDock"
        )
    ]


def test_formal_permission_store_panel_and_single_realtime_channel_exist() -> None:
    javascript = APP.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    stylesheet = STYLES.read_text(encoding="utf-8")

    assert "const permissionStore" in javascript
    assert "loadPendingPermissions" in javascript
    assert "decidePermission" in javascript
    assert "validatePermissionPendingPayload" in javascript
    assert "renderPermissionPanel" in javascript
    assert "permissions" in javascript[javascript.index("const CHANGE_RESOURCE_NAMES") :][:240]
    assert javascript.count("new EventSource('/api/v1/events')") == 1
    assert "setInterval(loadPendingPermissions" not in javascript
    assert "localStorage.setItem" not in _permission_source()
    assert "sessionStorage" not in _permission_source()
    action_source = _permission_action_source()
    assert "submitChatTurn" not in action_source
    assert "chatStore.draft =" not in action_source
    assert "sessionDetailStore" not in action_source
    assert "runDetailStore" not in action_source
    assert "console." not in action_source
    assert "setTimeout" not in action_source
    assert "setInterval" not in action_source
    assert 'id="permission-panel"' in html
    assert 'aria-live="polite"' in html
    assert ".permission-card" in stylesheet


def test_pending_validator_accepts_only_strict_safe_union_and_allow_gate() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const base = {
  schemaVersion: 1,
  generatedAt: '2026-07-20T00:00:00.000Z',
  mode: 'read-only',
  source: 'gateway-permission-broker',
  revision: `permissionrev_${'a'.repeat(32)}`,
  items: [{
    permissionId: `permission_${'1'.repeat(32)}`,
    turnId: `turn_${'2'.repeat(32)}`,
    runId: `run_${'3'.repeat(32)}`,
    toolOperationId: `permissiontool_${'4'.repeat(32)}`,
    toolName: 'write_file',
    kind: 'edit',
    summary: 'Review a file modification.',
    reviewable: true,
    review: {
      targetPath: 'safe/file.txt', diffPreview: '<script>alert(1)</script>',
      complete: true, truncated: false, redacted: false,
    },
    choices: ['allow_once', 'deny_once'],
    createdAt: '2026-07-20T00:00:00.000Z',
    expiresAt: '2026-07-20T00:05:00.000Z',
  }],
};
assert.ok(validatePermissionPendingPayload(base));
assert.equal(canAllowPermission(base.items[0]), true);
assert.equal(validatePermissionPendingPayload({ ...base, schemaVersion: true }), null);
assert.equal(validatePermissionPendingPayload({ ...base, extra: true }), null);
assert.equal(validatePermissionPendingPayload({ ...base, revision: 'permissionrev_bad' }), null);
assert.equal(validatePermissionPendingPayload({ ...base, generatedAt: '2026-02-30T00:00:00.000Z' }), null);
const missing = structuredClone(base);
delete missing.items[0].turnId;
assert.equal(validatePermissionPendingPayload(missing), null);
const invalidId = structuredClone(base);
invalidId.items[0].permissionId = 'permission_../secret';
assert.equal(validatePermissionPendingPayload(invalidId), null);
assert.equal(validatePermissionPendingPayload({ ...base, items: [...base.items, ...Array(16).fill(base.items[0])] }), null);
const oversizedDiff = structuredClone(base);
oversizedDiff.items[0].review.diffPreview = '界'.repeat(12000);
assert.equal(validatePermissionPendingPayload(oversizedDiff), null);
const wrongUnion = structuredClone(base);
wrongUnion.items[0].review.commandPreview = 'echo safe';
assert.equal(validatePermissionPendingPayload(wrongUnion), null);
const redacted = structuredClone(base);
redacted.items[0].review.redacted = true;
assert.equal(validatePermissionPendingPayload(redacted), null);
assert.equal(canAllowPermission(redacted.items[0]), false);
redacted.items[0].reviewable = false;
redacted.items[0].choices = ['deny_once'];
assert.ok(validatePermissionPendingPayload(redacted));
const hiddenForged = structuredClone(base);
hiddenForged.items[0].toolName = 'run_command';
hiddenForged.items[0].kind = 'command';
hiddenForged.items[0].review = {
  commandPreview: '[REDACTED SENSITIVE REVIEW]', cwd: '.', reason: 'controlled test',
  complete: true, truncated: false, redacted: false,
};
assert.equal(validatePermissionPendingPayload(hiddenForged), null);
assert.equal(canAllowPermission(hiddenForged.items[0]), false);
hiddenForged.items[0].reviewable = false;
hiddenForged.items[0].choices = ['deny_once'];
assert.ok(validatePermissionPendingPayload(hiddenForged));
const hiddenEditForged = structuredClone(base);
hiddenEditForged.items[0].review.diffPreview = '[REDACTED SENSITIVE REVIEW]';
assert.equal(validatePermissionPendingPayload(hiddenEditForged), null);
assert.equal(canAllowPermission(hiddenEditForged.items[0]), false);
hiddenEditForged.items[0].reviewable = false;
hiddenEditForged.items[0].choices = ['deny_once'];
assert.ok(validatePermissionPendingPayload(hiddenEditForged));
const contradictory = structuredClone(base);
contradictory.items[0].reviewable = true;
contradictory.items[0].choices = ['deny_once'];
assert.equal(validatePermissionPendingPayload(contradictory), null);
assert.equal(canAllowPermission(contradictory.items[0]), false);
const contradictoryDenyOnly = structuredClone(base);
contradictoryDenyOnly.items[0].reviewable = false;
assert.equal(validatePermissionPendingPayload(contradictoryDenyOnly), null);
assert.equal(canAllowPermission(contradictoryDenyOnly.items[0]), false);
for (const [field, value] of [['complete', false], ['truncated', true]]) {
  const unsafe = structuredClone(base);
  unsafe.items[0].review[field] = value;
  assert.equal(validatePermissionPendingPayload(unsafe), null);
  assert.equal(canAllowPermission(unsafe.items[0]), false);
  unsafe.items[0].reviewable = false;
  unsafe.items[0].choices = ['deny_once'];
  assert.ok(validatePermissionPendingPayload(unsafe));
}
const path = structuredClone(base);
path.items[0].kind = 'path';
path.items[0].reviewable = false;
path.items[0].review = { intent: 'read', outsideWorkspace: true };
path.items[0].choices = ['deny_once'];
assert.ok(validatePermissionPendingPayload(path));
assert.equal(canAllowPermission(path.items[0]), false);
const command = structuredClone(base);
command.items[0].toolName = 'run_command';
command.items[0].kind = 'command';
command.items[0].review = {
  commandPreview: 'echo safe', cwd: '.', reason: 'controlled test',
  complete: true, truncated: false, redacted: false,
};
assert.ok(validatePermissionPendingPayload(command));
assert.equal(canAllowPermission(command.items[0]), true);
assert.equal(canAllowPermission({ ...command.items[0], kind: 'unknown' }), false);
"""
    run_node(_permission_source() + "\n" + harness)


def test_network_permission_review_is_strict_allowable_or_deny_only() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const item = {
  permissionId: `permission_${'1'.repeat(32)}`,
  turnId: `turn_${'2'.repeat(32)}`,
  runId: null,
  toolOperationId: `permissiontool_${'3'.repeat(32)}`,
  toolName: 'http_request',
  kind: 'network',
  summary: 'Review a network request.',
  reviewable: true,
  review: {
    reviewVersion: 1,
    method: 'POST',
    scheme: 'https',
    hostname: 'api.public.example',
    port: 443,
    pathSummary: '/v1/items',
    hasBody: true,
    hasSensitiveHeaders: true,
    requestFingerprint: `networkreq_${'4'.repeat(64)}`,
  },
  choices: ['allow_once', 'deny_once'],
  createdAt: '2026-07-20T00:00:00.000Z',
  expiresAt: '2026-07-20T00:05:00.000Z',
};
const payload = {
  schemaVersion: 1,
  generatedAt: '2026-07-20T00:00:00.000Z',
  mode: 'read-only',
  source: 'gateway-permission-broker',
  revision: `permissionrev_${'5'.repeat(32)}`,
  items: [item],
};
assert.ok(validatePermissionPendingPayload(payload));
assert.equal(canAllowPermission(item), true);

for (const mutate of [
  (candidate) => { candidate.review.scheme = 'http'; },
  (candidate) => { candidate.review.method = 'GET'; },
  (candidate) => { candidate.review.pathSummary = '/v1/items?secret=hidden'; },
  (candidate) => { candidate.review.hostname = '127.0.0.1'; },
  (candidate) => { candidate.review.hostname = '192.0.2.1'; },
  (candidate) => { candidate.review.requestFingerprint = 'networkreq_bad'; },
  (candidate) => { candidate.review.query = 'secret=hidden'; },
]) {
  const unsafe = structuredClone(item);
  mutate(unsafe);
  assert.equal(validPermissionItem(unsafe), false);
  assert.equal(canAllowPermission(unsafe), false);
}

const publicIpv6 = structuredClone(item);
publicIpv6.review.hostname = '2001:4860:4860::8888';
assert.equal(validPermissionItem(publicIpv6), true);
assert.equal(canAllowPermission(publicIpv6), true);

const denyOnly = structuredClone(item);
denyOnly.reviewable = false;
denyOnly.review = {};
denyOnly.choices = ['deny_once'];
assert.ok(validPermissionItem(denyOnly));
assert.equal(canAllowPermission(denyOnly), false);
"""
    run_node(_permission_source() + "\n" + harness)


def test_network_permission_dom_contains_only_safe_review_fields() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
})[char]);
const item = {
  kind: 'network',
  reviewable: true,
  review: {
    reviewVersion: 1,
    method: 'POST',
    scheme: 'https',
    hostname: 'api.public.example',
    port: 443,
    pathSummary: '/v1/items',
    hasBody: true,
    hasSensitiveHeaders: true,
    requestFingerprint: `networkreq_${'4'.repeat(64)}`,
  },
};
const html = permissionReviewHtml(item);
assert.match(html, /POST/);
assert.match(html, /https:\/\/api\.public\.example:443/);
assert.match(html, /\/v1\/items/);
assert.doesNotMatch(html, /networkreq_/);
assert.doesNotMatch(html, /\?/);
assert.doesNotMatch(html, /\[object Object\]/);
const denyOnly = permissionReviewHtml({ kind: 'network', reviewable: false, review: {} });
assert.match(denyOnly, /只能拒绝/);
"""
    run_node(_permission_action_source() + "\n" + harness)


def test_schema_v2_events_accept_permissions_and_reject_old_or_wrong_order() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const epoch = 'a'.repeat(32);
const eventId = `evt_${epoch}_${'0'.repeat(15)}1`;
const changed = {
  schemaVersion: 2,
  type: 'resources.changed',
  generatedAt: '2026-07-20T00:00:00.000Z',
  resources: [{ name: 'permissions', status: 'live', revision: `rev_${'b'.repeat(64)}` }],
};
assert.deepEqual(
  parseDashboardEvent('resources.changed', eventId, JSON.stringify(changed)).resources,
  ['permissions'],
);
assert.equal(
  parseDashboardEvent('resources.changed', eventId, JSON.stringify({ ...changed, schemaVersion: 1 })),
  null,
);
const reset = {
  schemaVersion: 2,
  type: 'stream.reset',
  generatedAt: '2026-07-20T00:00:00.000Z',
  reason: 'stream_restarted',
  resources: [...CHANGE_RESOURCE_NAMES],
};
assert.equal(parseDashboardEvent('stream.reset', eventId, JSON.stringify(reset)).kind, 'reset');
assert.equal(
  parseDashboardEvent('stream.reset', eventId, JSON.stringify({ ...reset, resources: reset.resources.slice(0, -1) })),
  null,
);
const resources = Object.fromEntries(CHANGE_RESOURCE_NAMES.map((name) => [name, {
  status: name === 'permissions' ? 'unavailable' : 'live', revision: `rev_${'c'.repeat(64)}`,
}]));
const change = {
  schemaVersion: 2, generatedAt: '2026-07-20T00:00:00.000Z', mode: 'read-only',
  pollAfterMs: 2000, resources, diagnostics: [],
};
assert.equal(validChangeSnapshot(change), true);
const wrongOrder = { ...change, resources: { permissions: resources.permissions, ...resources } };
assert.equal(validChangeSnapshot(wrongOrder), false);
"""
    run_node(_permission_source() + "\n" + harness)


def test_permission_actions_are_single_flight_fenced_and_never_auto_retry() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
})[char]);
const panel = {
  hidden: true,
  innerHTML: '',
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
global.document = { querySelector(selector) { return selector === '#permission-panel' ? panel : null; } };
const itemA = {
  permissionId: `permission_${'1'.repeat(32)}`,
  turnId: `turn_${'2'.repeat(32)}`,
  runId: null,
  toolOperationId: `permissiontool_${'3'.repeat(32)}`,
  toolName: 'write_file', kind: 'edit', summary: 'Review a file modification.',
  reviewable: true,
  review: { targetPath: 'safe.txt', diffPreview: '+safe', complete: true, truncated: false, redacted: false },
  choices: ['allow_once', 'deny_once'],
  createdAt: '2026-07-20T00:00:00.000Z', expiresAt: '2026-07-20T00:05:00.000Z',
};
const itemB = { ...itemA, permissionId: `permission_${'4'.repeat(32)}` };
const permissionStore = {
  phase: 'live', items: [itemA], revision: `permissionrev_${'a'.repeat(32)}`,
  error: null, requestId: 0, actionGeneration: 0, actingPermissionId: null, lastUpdatedAt: null,
};
const chatStore = { activeTurnId: itemA.turnId, phase: 'in_progress' };
const response = (ok, payload, status = ok ? 200 : 409) => ({
  ok, status, async text() { return JSON.stringify(payload); },
});
const emptyPending = {
  schemaVersion: 1, generatedAt: '2026-07-20T00:00:01.000Z', mode: 'read-only',
  source: 'gateway-permission-broker', revision: `permissionrev_${'b'.repeat(32)}`, items: [],
};
let calls = [];
let fetchImpl = async (url, options = {}) => {
  calls.push([url, options]);
  if (options.method === 'POST') return response(true, {
    schemaVersion: 1, mode: 'read-write', permissionId: itemA.permissionId,
    turnId: itemA.turnId, status: 'allowed', decision: 'allow_once',
    decisionAccepted: false, updatedAt: '2026-07-20T00:00:01.000Z',
  });
  return response(true, emptyPending);
};
global.fetch = (...args) => fetchImpl(...args);

(async () => {
  const first = decidePermission(itemA.permissionId, itemA.turnId, 'allow_once');
  const duplicate = decidePermission(itemA.permissionId, itemA.turnId, 'allow_once');
  assert.equal(await duplicate, false);
  assert.equal(await first, true);
  assert.equal(calls.filter(([, options]) => options.method === 'POST').length, 1);
  assert.equal(calls.filter(([, options]) => options.method !== 'POST').length, 1);
  const posted = JSON.parse(calls[0][1].body);
  assert.deepEqual(posted, { turnId: itemA.turnId, decision: 'allow_once' });
  assert.equal(permissionStore.phase, 'empty');

  Object.assign(permissionStore, {
    phase: 'live', items: [itemA], requestId: 0, actionGeneration: 10,
    actingPermissionId: null, error: null,
  });
  calls = [];
  let release;
  fetchImpl = (url, options = {}) => {
    calls.push([url, options]);
    return new Promise((resolve) => { release = resolve; });
  };
  const stale = decidePermission(itemA.permissionId, itemA.turnId, 'deny_once');
  await Promise.resolve();
  permissionStore.actionGeneration += 1;
  permissionStore.items = [itemB];
  release(response(true, {
    schemaVersion: 1, mode: 'read-write', permissionId: itemA.permissionId,
    turnId: itemA.turnId, status: 'denied', decision: 'deny_once',
    decisionAccepted: true, updatedAt: '2026-07-20T00:00:02.000Z',
  }));
  assert.equal(await stale, false);
  assert.equal(permissionStore.items[0].permissionId, itemB.permissionId);
  assert.equal(calls.length, 1); // stale response cannot start a GET

  Object.assign(permissionStore, {
    phase: 'live', items: [itemA], requestId: 0, actionGeneration: 20,
    actingPermissionId: null, error: null,
  });
  calls = [];
  fetchImpl = async (url, options = {}) => {
    calls.push([url, options]);
    throw new Error('connection dropped');
  };
  assert.equal(await decidePermission(itemA.permissionId, itemA.turnId, 'deny_once'), false);
  assert.equal(calls.length, 1); // response loss is never automatically retried or reconciled
  assert.equal(permissionStore.phase, 'error');
  assert.match(permissionStore.error, /不会自动重试决定/);

  fetchImpl = async (url, options = {}) => {
    calls.push([url, options]);
    return response(true, emptyPending);
  };
  assert.equal(await loadPendingPermissions(), true);
  assert.equal(calls.filter(([, options]) => options.method === 'POST').length, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    run_node(_permission_action_source() + "\n" + harness)


def test_terminal_cancel_retires_permissions_before_identity_clear_and_fences_stale_get() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const esc = (value) => String(value ?? '');
const panel = {
  hidden: true, innerHTML: '',
  querySelector() { return null; }, querySelectorAll() { return []; },
};
global.document = { querySelector(selector) { return selector === '#permission-panel' ? panel : null; } };
const item = {
  permissionId: `permission_${'1'.repeat(32)}`,
  turnId: `turn_${'2'.repeat(32)}`,
  runId: null,
  toolOperationId: `permissiontool_${'3'.repeat(32)}`,
  toolName: 'write_file', kind: 'edit', summary: 'Review a file modification.',
  reviewable: true,
  review: { targetPath: 'safe.txt', diffPreview: '+safe', complete: true, truncated: false, redacted: false },
  choices: ['allow_once', 'deny_once'],
  createdAt: '2026-07-20T00:00:00.000Z', expiresAt: '2026-07-20T00:05:00.000Z',
};
const other = {
  ...item,
  permissionId: `permission_${'4'.repeat(32)}`,
  turnId: `turn_${'5'.repeat(32)}`,
  toolOperationId: `permissiontool_${'6'.repeat(32)}`,
};
const permissionStore = {
  phase: 'live', items: [item], revision: `permissionrev_${'a'.repeat(32)}`,
  error: null, requestId: 0, actionGeneration: 0, actingPermissionId: null, lastUpdatedAt: null,
};
const chatStore = {
  phase: 'cancelling', activeTurnId: item.turnId, operationGeneration: 0,
  terminalTurnId: null, terminalPromise: null,
};
function clearActiveTurn(turnId) {
  assert.equal(chatStore.activeTurnId, turnId);
  chatStore.activeTurnId = null;
}
const pending = {
  schemaVersion: 1, generatedAt: '2026-07-20T00:00:01.000Z', mode: 'read-only',
  source: 'gateway-permission-broker', revision: `permissionrev_${'b'.repeat(32)}`, items: [item],
};
const freshPending = {
  ...pending, revision: `permissionrev_${'c'.repeat(32)}`, items: [item, other],
};
const gets = [];
global.fetch = (url, options = {}) => new Promise((resolve) => { gets.push({ url, options, resolve }); });
const response = (payload) => ({ ok: true, status: 200, async text() { return JSON.stringify(payload); } });

(async () => {
  assert.equal(permissionActionAvailable(item, 'allow_once'), false);
  const staleGet = loadPendingPermissions();
  await Promise.resolve();
  finishCancelledTurn(item.turnId);
  assert.equal(gets.length, 2);
  assert.equal(chatStore.activeTurnId, null);
  assert.equal(permissionActionAvailable(item, 'allow_once'), false);
  assert.equal(permissionActionAvailable(item, 'deny_once'), false);
  assert.equal(permissionStore.phase, 'loading');
  assert.equal(permissionStore.items.some((candidate) => candidate.turnId === item.turnId), false);
  gets[0].resolve(response(pending));
  assert.equal(await staleGet, false);
  assert.equal(permissionStore.phase, 'loading');
  const reconciliation = permissionStore.reconciliationPromise;
  gets[1].resolve(response(freshPending));
  assert.equal(await reconciliation, true);
  assert.equal(permissionStore.items.some((candidate) => candidate.turnId === item.turnId), false);
  assert.deepEqual(permissionStore.items.map((candidate) => candidate.turnId), [other.turnId]);
  assert.equal(permissionActionAvailable(permissionStore.items[0], 'allow_once'), true);
  assert.equal(gets.some(({ options }) => options.method === 'POST'), false);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    run_node(
        _permission_action_source() + "\n" + _cancel_finish_source() + "\n" + harness
    )


def test_terminal_cancel_fences_stale_decision_post_without_retrying_actions() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const esc = (value) => String(value ?? '');
const panel = {
  hidden: true, innerHTML: '',
  querySelector() { return null; }, querySelectorAll() { return []; },
};
global.document = { querySelector(selector) { return selector === '#permission-panel' ? panel : null; } };
const item = {
  permissionId: `permission_${'1'.repeat(32)}`,
  turnId: `turn_${'2'.repeat(32)}`,
  runId: null,
  toolOperationId: `permissiontool_${'3'.repeat(32)}`,
  toolName: 'run_command', kind: 'command', summary: 'Review a command.',
  reviewable: true,
  review: { commandPreview: 'echo safe', cwd: '.', reason: 'test', complete: true, truncated: false, redacted: false },
  choices: ['allow_once', 'deny_once'],
  createdAt: '2026-07-20T00:00:00.000Z', expiresAt: '2026-07-20T00:05:00.000Z',
};
const other = {
  ...item,
  permissionId: `permission_${'4'.repeat(32)}`,
  turnId: `turn_${'5'.repeat(32)}`,
  toolOperationId: `permissiontool_${'6'.repeat(32)}`,
};
const permissionStore = {
  phase: 'live', items: [item], revision: `permissionrev_${'a'.repeat(32)}`,
  error: null, requestId: 0, actionGeneration: 0, actingPermissionId: null,
  lastUpdatedAt: null, reconciliationPromise: null,
};
const chatStore = {
  phase: 'in_progress', activeTurnId: item.turnId, operationGeneration: 0,
  terminalTurnId: null, terminalPromise: null,
};
function clearActiveTurn(turnId) {
  assert.equal(chatStore.activeTurnId, turnId);
  chatStore.activeTurnId = null;
}
let releasePost;
let releaseGet;
const calls = [];
global.fetch = (url, options = {}) => {
  calls.push({ url, options });
  return new Promise((resolve) => {
    if (options.method === 'POST') releasePost = resolve;
    else releaseGet = resolve;
  });
};
const response = (payload) => ({ ok: true, status: 200, async text() { return JSON.stringify(payload); } });
const pendingOther = {
  schemaVersion: 1, generatedAt: '2026-07-20T00:00:02.000Z', mode: 'read-only',
  source: 'gateway-permission-broker', revision: `permissionrev_${'b'.repeat(32)}`, items: [other],
};

(async () => {
  const stalePost = decidePermission(item.permissionId, item.turnId, 'allow_once');
  await Promise.resolve();
  assert.equal(calls.filter(({ options }) => options.method === 'POST').length, 1);
  chatStore.phase = 'cancelling';
  finishCancelledTurn(item.turnId);
  assert.equal(chatStore.activeTurnId, null);
  assert.equal(permissionStore.phase, 'loading');
  assert.equal(calls.filter(({ options }) => options.method !== 'POST').length, 1);
  releasePost(response({
    schemaVersion: 1, mode: 'read-write', permissionId: item.permissionId,
    turnId: item.turnId, status: 'allowed', decision: 'allow_once',
    decisionAccepted: true, updatedAt: '2026-07-20T00:00:01.000Z',
  }));
  assert.equal(await stalePost, false);
  assert.equal(permissionStore.phase, 'loading');
  assert.deepEqual(permissionStore.items, []);
  const reconciliation = permissionStore.reconciliationPromise;
  releaseGet(response(pendingOther));
  assert.equal(await reconciliation, true);
  assert.equal(permissionStore.items[0].turnId, other.turnId);
  assert.equal(permissionActionAvailable(permissionStore.items[0], 'allow_once'), true);
  assert.equal(calls.filter(({ options }) => options.method === 'POST').length, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    run_node(
        _permission_action_source() + "\n" + _cancel_finish_source() + "\n" + harness
    )


def test_cancel_and_status_terminal_paths_share_fail_closed_permission_retirement() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const esc = (value) => String(value ?? '');
const panel = {
  hidden: true, innerHTML: '',
  querySelector() { return null; }, querySelectorAll() { return []; },
};
global.document = { querySelector(selector) { return selector === '#permission-panel' ? panel : null; } };
const turnId = `turn_${'2'.repeat(32)}`;
const item = {
  permissionId: `permission_${'1'.repeat(32)}`, turnId, runId: null,
  toolOperationId: `permissiontool_${'3'.repeat(32)}`,
  toolName: 'write_file', kind: 'edit', summary: 'Review a file modification.',
  reviewable: true,
  review: { targetPath: 'safe.txt', diffPreview: '+safe', complete: true, truncated: false, redacted: false },
  choices: ['allow_once', 'deny_once'],
  createdAt: '2026-07-20T00:00:00.000Z', expiresAt: '2026-07-20T00:05:00.000Z',
};
const permissionStore = {
  phase: 'live', items: [item], revision: `permissionrev_${'a'.repeat(32)}`,
  error: null, requestId: 0, actionGeneration: 0, actingPermissionId: null,
  lastUpdatedAt: null, reconciliationPromise: null,
};
const chatStore = {
  activeTurnId: turnId, activeTargetSessionId: null, operationGeneration: 0,
  requestGeneration: 0, phase: 'in_progress', error: null, draft: 'retained',
  targetMode: 'new', lastSessionId: null, terminalTurnId: null, terminalPromise: null,
};
const chatStreamStore = { turnId: null };
const sessionDetailStore = { selectionVersion: 0, sessionId: null, data: null };
let permissionGets = 0;
let primaryResponse = null;
let clearChecks = 0;
function renderConversationDock() {}
function fixedChatError(code) { return `safe:${code}`; }
function clearActiveTurn(expected = null) {
  if (expected === null || chatStore.activeTurnId === expected) {
    assert.equal(retiredPermissionTurnIds.has(chatStore.activeTurnId), true);
    clearChecks += 1;
    chatStore.activeTurnId = null;
    chatStore.activeTargetSessionId = null;
  }
}
function persistSessionSelection() {}
async function refreshSessions() {}
async function refreshRuns() {}
async function refreshRunsFromChangeFeed() {}
async function refreshDashboardSnapshot() {}
async function refreshOps() {}
async function loadOps() {}
async function loadSessionDetail(sessionId) {
  sessionDetailStore.sessionId = sessionId;
  sessionDetailStore.data = { session: { id: sessionId } };
  return 'loaded';
}
function resetChatStreamState() {}
function setCompletedFeedbackTarget() {}
const pendingEmpty = {
  schemaVersion: 1, generatedAt: '2026-07-20T00:00:03.000Z', mode: 'read-only',
  source: 'gateway-permission-broker', revision: `permissionrev_${'b'.repeat(32)}`, items: [],
};
function response(payload, status = 200) {
  return {
    status, ok: status >= 200 && status < 300,
    async json() { return payload; },
    async text() { return JSON.stringify(payload); },
  };
}
global.fetch = async (url) => {
  if (url === '/api/v1/permissions/pending') {
    permissionGets += 1;
    return response(pendingEmpty);
  }
  return primaryResponse;
};
function resetCase(phase = 'in_progress') {
  retiredPermissionTurnIds.clear();
  Object.assign(permissionStore, {
    phase: 'live', items: [item], error: null, requestId: 0,
    actionGeneration: 0, actingPermissionId: null, reconciliationPromise: null,
  });
  Object.assign(chatStore, {
    activeTurnId: turnId, activeTargetSessionId: null, operationGeneration: 0,
    requestGeneration: 0, phase, error: null, terminalTurnId: null, terminalPromise: null,
  });
  sessionDetailStore.sessionId = null;
  sessionDetailStore.data = null;
  permissionGets = 0;
  clearChecks = 0;
}
async function assertRetired() {
  await Promise.resolve();
  assert.equal(chatStore.activeTurnId, null);
  assert.equal(retiredPermissionTurnIds.has(turnId), true);
  assert.equal(permissionStore.items.some((candidate) => candidate.turnId === turnId), false);
  assert.equal(permissionActionAvailable(item, 'allow_once'), false);
  assert.equal(permissionActionAvailable(item, 'deny_once'), false);
  assert.equal(permissionGets, 1);
  assert.equal(clearChecks, 1);
}
const cancelPayload = (status) => ({
  ok: true, schemaVersion: 1, mode: 'read-write', turnId, status,
  cancellationAccepted: status === 'cancel_requested', sessionId: null, runId: null,
  updatedAt: '2026-07-20T00:00:01.000Z',
});
const statusPayload = (status, extra = {}) => ({
  ok: true, schemaVersion: 1, mode: 'read-only', turnId, status,
  sessionId: null, created: null, runId: null,
  createdAt: '2026-07-20T00:00:00.000Z', updatedAt: '2026-07-20T00:00:01.000Z',
  completedAt: '2026-07-20T00:00:01.000Z', errorCode: null,
  resultAvailable: false, ...extra,
});

(async () => {
  resetCase('in_progress');
  primaryResponse = response(null, 404);
  await cancelActiveTurn();
  await assertRetired();

  for (const status of ['cancelled', 'failed', 'interrupted']) {
    resetCase('in_progress');
    primaryResponse = response(cancelPayload(status));
    await cancelActiveTurn();
    await assertRetired();
  }

  resetCase('cancel_requested');
  primaryResponse = response(null, 404);
  await checkActiveTurnStatus();
  await assertRetired();

  for (const status of ['cancelled', 'failed', 'interrupted']) {
    resetCase(status === 'cancelled' ? 'cancel_requested' : 'in_progress');
    primaryResponse = response(statusPayload(status, {
      errorCode: status === 'failed' ? 'turn_failed' : status === 'interrupted' ? 'turn_interrupted' : 'turn_cancelled',
    }));
    await checkActiveTurnStatus();
    await assertRetired();
  }

  resetCase('committing');
  primaryResponse = response(statusPayload('completed'));
  await checkActiveTurnStatus();
  await assertRetired();
  assert.equal(chatStore.phase, 'completed_unavailable');

  resetCase('committing');
  primaryResponse = response(statusPayload('completed', {
    sessionId: 'session_completed', created: true, resultAvailable: true,
  }));
  await checkActiveTurnStatus();
  await assertRetired();
  assert.equal(chatStore.phase, 'success');
})().catch((error) => { console.error(error); process.exit(1); });
"""
    run_node(
        _permission_action_source() + "\n" + _chat_terminal_source() + "\n" + harness
    )


def test_ndjson_and_json_terminal_chat_paths_retire_without_automatic_replay() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const esc = (value) => String(value ?? '');
const panel = {
  hidden: true, innerHTML: '',
  querySelector() { return null; }, querySelectorAll() { return []; },
};
global.document = { querySelector(selector) { return selector === '#permission-panel' ? panel : null; } };
const turnA = `turn_${'a'.repeat(32)}`;
const turnB = `turn_${'b'.repeat(32)}`;
const turnC = `turn_${'c'.repeat(32)}`;
const makeItem = (turnId, suffix) => ({
  permissionId: `permission_${suffix.repeat(32)}`, turnId, runId: null,
  toolOperationId: `permissiontool_${suffix.repeat(32)}`,
  toolName: 'write_file', kind: 'edit', summary: 'Review a file modification.',
  reviewable: true,
  review: { targetPath: `safe-${suffix}.txt`, diffPreview: '+safe', complete: true, truncated: false, redacted: false },
  choices: ['allow_once', 'deny_once'],
  createdAt: '2026-07-20T00:00:00.000Z', expiresAt: '2026-07-20T00:05:00.000Z',
});
const permissionStore = {
  phase: 'idle', items: [], revision: null, error: null, requestId: 0,
  actionGeneration: 0, actingPermissionId: null, lastUpdatedAt: null,
  reconciliationPromise: null,
};
const chatStore = {
  phase: 'idle', requestGeneration: 0, operationGeneration: 0, draft: '',
  targetMode: 'new', error: null, lastSessionId: null, activeTurnId: null,
  activeTargetSessionId: null, terminalTurnId: null, terminalPromise: null,
};
const chatStreamStore = {
  turnId: null, generation: 0, phase: 'idle', lastSequence: -1,
  provisionalText: '', tools: [], incomplete: false,
};
const sessionDetailStore = { selectionVersion: 0, sessionId: null, data: null };
const sessionsStore = { items: [] };
let generatedTurns = [];
let chatResponse = null;
let streamTerminalCode = null;
let calls = [];
let clearChecks = 0;
function createTurnId() { return generatedTurns.shift(); }
function chatTargetSessionId() { return null; }
function resetChatStreamState(turnId = null, generation = 0) {
  Object.assign(chatStreamStore, { turnId, generation, phase: turnId ? 'connecting' : 'idle' });
}
function resetChatFeedbackTarget() {}
function setCompletedFeedbackTarget() {}
function persistActiveTurn() {}
function persistSessionSelection() {}
function renderConversationDock() {}
function fixedChatError(code) { return `safe:${code}`; }
function clearActiveTurn(expected = null) {
  if (expected === null || chatStore.activeTurnId === expected) {
    assert.equal(retiredPermissionTurnIds.has(chatStore.activeTurnId), true);
    clearChecks += 1;
    chatStore.activeTurnId = null;
    chatStore.activeTargetSessionId = null;
  }
}
async function consumeChatNdjson(_body, options) {
  options.onFrame({
    schemaVersion: 1, type: 'chat.turn.error', turnId: chatStore.activeTurnId,
    sequence: 0, emittedAt: '2026-07-20T00:00:01.000Z', code: streamTerminalCode,
  });
}
function scheduleChatStreamRender() {}
function detachChatStreamState() {}
async function refreshSessions() {}
async function refreshRuns() {}
async function refreshDashboardSnapshot() {}
async function refreshOps() {}
async function loadSessionDetail(sessionId) {
  sessionDetailStore.sessionId = sessionId;
  sessionDetailStore.data = { session: { id: sessionId } };
  return 'loaded';
}
const pendingEmpty = {
  schemaVersion: 1, generatedAt: '2026-07-20T00:00:03.000Z', mode: 'read-only',
  source: 'gateway-permission-broker', revision: `permissionrev_${'d'.repeat(32)}`, items: [],
};
const response = (payload, contentType = 'application/json', status = 200) => ({
  ok: status >= 200 && status < 300, status, body: {},
  headers: { get(name) { return name.toLowerCase() === 'content-type' ? contentType : null; } },
  async json() { return payload; },
  async text() { return JSON.stringify(payload); },
});
global.fetch = async (url, options = {}) => {
  calls.push({ url, options });
  if (url === '/api/v1/permissions/pending') return response(pendingEmpty);
  return chatResponse;
};
function resetCase(turnId, suffix) {
  retiredPermissionTurnIds.clear();
  const item = makeItem(turnId, suffix);
  Object.assign(permissionStore, {
    phase: 'live', items: [item], error: null, requestId: 0,
    actionGeneration: 0, actingPermissionId: null, reconciliationPromise: null,
  });
  Object.assign(chatStore, {
    phase: 'idle', requestGeneration: 0, operationGeneration: 0,
    draft: 'run exactly once', targetMode: 'new', error: null,
    activeTurnId: null, activeTargetSessionId: null,
    terminalTurnId: null, terminalPromise: null,
  });
  resetChatStreamState();
  generatedTurns = [turnId];
  calls = [];
  clearChecks = 0;
  return item;
}
async function assertRetired(turnId, item) {
  await Promise.resolve();
  assert.equal(chatStore.activeTurnId, null);
  assert.equal(retiredPermissionTurnIds.has(turnId), true);
  assert.equal(permissionStore.items.some((candidate) => candidate.turnId === turnId), false);
  assert.equal(permissionActionAvailable(item, 'allow_once'), false);
  assert.equal(clearChecks, 1);
  assert.equal(calls.filter(({ url }) => url === '/api/v1/chat/turns').length, 1);
  assert.equal(calls.filter(({ url }) => url === '/api/v1/permissions/pending').length, 1);
  assert.equal(calls.filter(({ url }) => url.includes('/decision')).length, 0);
}

(async () => {
  let item = resetCase(turnA, '1');
  streamTerminalCode = 'turn_cancelled';
  chatResponse = response(null, 'application/x-ndjson');
  await submitChatTurn();
  await assertRetired(turnA, item);
  assert.equal(chatStore.phase, 'cancelled');

  item = resetCase(turnB, '2');
  streamTerminalCode = null;
  chatResponse = response({
    ok: true, schemaVersion: 1, mode: 'read-write', turnId: turnB,
    sessionId: 'session_json_success', created: true,
    assistant: { role: 'assistant', content: 'done' },
    updatedAt: '2026-07-20T00:00:02.000Z', runId: null,
  });
  await submitChatTurn();
  await assertRetired(turnB, item);
  assert.equal(chatStore.phase, 'success');

  item = resetCase(turnC, '3');
  chatResponse = response({ error: { code: 'turn_interrupted' } }, 'application/json', 409);
  await submitChatTurn();
  await assertRetired(turnC, item);
  assert.equal(chatStore.phase, 'interrupted');
})().catch((error) => { console.error(error); process.exit(1); });
"""
    run_node(
        _permission_action_source()
        + "\n"
        + _cancel_finish_source()
        + "\n"
        + _submit_chat_source()
        + "\n"
        + harness
    )


def test_permission_actions_reconcile_conflicts_and_advance_oldest_queue_item() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
})[char]);
const panel = {
  hidden: true, innerHTML: '',
  querySelector() { return null; }, querySelectorAll() { return []; },
};
global.document = { querySelector(selector) { return selector === '#permission-panel' ? panel : null; } };
const makeItem = (suffix, turnSuffix = '2') => ({
  permissionId: `permission_${suffix.repeat(32)}`,
  turnId: `turn_${turnSuffix.repeat(32)}`,
  runId: null,
  toolOperationId: `permissiontool_${'3'.repeat(32)}`,
  toolName: 'write_file', kind: 'edit', summary: 'Review a file modification.',
  reviewable: true,
  review: { targetPath: `safe-${suffix}.txt`, diffPreview: `+${suffix}`, complete: true, truncated: false, redacted: false },
  choices: ['allow_once', 'deny_once'],
  createdAt: '2026-07-20T00:00:00.000Z', expiresAt: '2026-07-20T00:05:00.000Z',
});
const itemA = makeItem('1', '2');
const itemB = makeItem('4', '5');
const permissionStore = {
  phase: 'live', items: [itemA, itemB], revision: `permissionrev_${'a'.repeat(32)}`,
  error: null, requestId: 0, actionGeneration: 0, actingPermissionId: null, lastUpdatedAt: null,
};
const chatStore = { activeTurnId: itemA.turnId, phase: 'in_progress' };
const response = (ok, payload, status = ok ? 200 : 409) => ({
  ok, status, async text() { return JSON.stringify(payload); },
});
const pending = (items, suffix = 'b') => ({
  schemaVersion: 1, generatedAt: '2026-07-20T00:00:01.000Z', mode: 'read-only',
  source: 'gateway-permission-broker', revision: `permissionrev_${suffix.repeat(32)}`, items,
});
let calls = [];
let replies = [
  response(false, { error: { code: 'permission_already_decided' } }),
  response(true, pending([itemB])),
];
global.fetch = async (url, options = {}) => {
  calls.push([url, options]);
  return replies.shift();
};

(async () => {
  assert.equal(await decidePermission(itemA.permissionId, itemA.turnId, 'allow_once'), false);
  assert.equal(calls.length, 2);
  assert.equal(calls[0][1].method, 'POST');
  assert.equal(calls[1][1].method, undefined);
  assert.equal(permissionStore.phase, 'live');
  assert.equal(permissionStore.items[0].permissionId, itemB.permissionId);
  assert.ok(panel.innerHTML.includes('1 / 1'));

  calls = [];
  replies = [
    response(true, { malformed: '<script>secret</script>' }),
    response(true, pending([], 'c')),
  ];
  assert.equal(await decidePermission(itemB.permissionId, itemB.turnId, 'deny_once'), false);
  assert.equal(calls.length, 2);
  assert.equal(permissionStore.phase, 'empty');
  assert.deepEqual(permissionStore.items, []);
  assert.equal(panel.hidden, true);

  Object.assign(permissionStore, {
    phase: 'live', items: [itemA], error: null, actingPermissionId: null,
  });
  calls = [];
  replies = [response(true, { schemaVersion: 1, injected: '<img onerror=secret>' })];
  assert.equal(await loadPendingPermissions(), false);
  assert.equal(permissionStore.phase, 'error');
  assert.equal(permissionStore.items[0].permissionId, itemA.permissionId);
  assert.equal(permissionActionAvailable(itemA, 'allow_once'), false);
  assert.equal(panel.innerHTML.includes('[object Object]'), false);
  assert.equal(panel.innerHTML.includes('<img onerror='), false);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    run_node(_permission_action_source() + "\n" + harness)


def test_permission_ui_escapes_reviews_is_deny_only_and_never_steals_focus() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
})[char]);
const panel = {
  hidden: true, innerHTML: '',
  querySelector() { return null; }, querySelectorAll() { return []; },
};
global.document = { querySelector(selector) { return selector === '#permission-panel' ? panel : null; } };
const edit = {
  permissionId: `permission_${'1'.repeat(32)}`, turnId: `turn_${'2'.repeat(32)}`, runId: null,
  toolOperationId: `permissiontool_${'3'.repeat(32)}`, toolName: 'write_file', kind: 'edit',
  summary: '<img onerror=alert(1)> " & ` ${value} </p>', reviewable: true,
  review: {
    targetPath: 'safe/<script>.txt',
    diffPreview: '<script>alert(1)</script>\n<img onerror=alert(2)> & " \' ` ${value} </code>',
    complete: true, truncated: false, redacted: false,
  },
  choices: ['allow_once', 'deny_once'],
  createdAt: '2026-07-20T00:00:00.000Z', expiresAt: '2026-07-20T00:05:00.000Z',
};
const permissionStore = {
  phase: 'live', items: [edit], revision: null, error: null, requestId: 0,
  actionGeneration: 0, actingPermissionId: null, lastUpdatedAt: null,
};
const chatStore = { activeTurnId: edit.turnId, phase: 'in_progress' };
renderPermissionPanel();
assert.equal(panel.hidden, false);
assert.ok(panel.innerHTML.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
assert.ok(panel.innerHTML.includes('&lt;img onerror=alert(2)&gt;'));
assert.equal(panel.innerHTML.includes('<script>'), false);
assert.equal(panel.innerHTML.includes('<img onerror='), false);
assert.equal(panel.innerHTML.includes('[object Object]'), false);
assert.ok(panel.innerHTML.includes('仅允许这一次'));
assert.ok(panel.innerHTML.includes('拒绝这一次'));
assert.ok(panel.innerHTML.includes('type="button"'));
chatStore.phase = 'cancelling';
assert.equal(permissionActionAvailable(edit, 'allow_once'), false);
assert.equal(permissionActionAvailable(edit, 'deny_once'), false);
chatStore.phase = 'in_progress';

const redacted = structuredClone(edit);
redacted.kind = 'command';
redacted.toolName = 'run_command';
redacted.reviewable = false;
redacted.choices = ['deny_once'];
redacted.review = {
  commandPreview: '[REDACTED SENSITIVE REVIEW]', cwd: '.',
  reason: 'Command review is unavailable.', complete: false, truncated: false, redacted: true,
};
permissionStore.items = [redacted];
renderPermissionPanel();
assert.ok(panel.innerHTML.includes('内容因安全原因隐藏，只能拒绝。'));
assert.equal(panel.innerHTML.includes('仅允许这一次'), false);
assert.ok(panel.innerHTML.includes('拒绝这一次'));

const path = structuredClone(edit);
path.kind = 'path';
path.reviewable = false;
path.choices = ['deny_once'];
path.review = { intent: 'read', outsideWorkspace: true };
permissionStore.items = [path];
renderPermissionPanel();
assert.ok(panel.innerHTML.includes('绝对路径不会显示'));
assert.equal(panel.innerHTML.includes('仅允许这一次'), false);
"""
    run_node(_permission_action_source() + "\n" + harness)

    javascript = APP.read_text(encoding="utf-8")
    render_body = javascript[
        javascript.index("function renderPermissionPanel") : javascript.index(
            "\nfunction renderSessionMenu"
        )
    ]
    html = HTML.read_text(encoding="utf-8")
    assert ".focus(" not in render_body
    assert html.index('id="permission-panel"') < html.index('id="chat-log"')
    assert html.index('id="permission-panel"') < html.index('id="chat-form"')
