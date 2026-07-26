from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "minicode/web/static/assets/app.js"
STYLES = ROOT / "minicode/web/static/assets/styles.css"


def _approval_helpers_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    return javascript[
        javascript.index("const MEMORY_APPROVAL_PENDING_SCHEMA_VERSION") : javascript.index(
            "\nfunction createResourceRefreshQueue"
        )
    ]


def _approval_action_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    return _approval_helpers_source() + "\n" + javascript[
        javascript.index("function fixedMemoryApprovalError") : javascript.index(
            "\nfunction deletionStoreFor"
        )
    ]


def test_memory_approval_store_route_and_single_transport_are_formal() -> None:
    javascript = APP.read_text(encoding="utf-8")
    stylesheet = STYLES.read_text(encoding="utf-8")

    assert "const memoryApprovalStore" in javascript
    assert "validateMemoryApprovalPendingPayload" in javascript
    assert "validMemoryApprovalItem" in javascript
    assert "memoryApprovalReviewConsistent" in javascript
    assert "canApproveMemory" in javascript
    assert "validMemoryApprovalDecisionPayload" in javascript
    assert "loadMemoryApprovals" in javascript
    assert "decideMemoryApproval" in javascript
    assert "renderMemoryApprovals" in javascript
    assert "['approvals', '待审批'" in javascript
    assert "只有 Project 条目提供严格确认删除；User / Local 无删除入口" in javascript
    assert "read-write · persistent approval" in javascript
    assert javascript.count("new EventSource('/api/v1/events')") == 1
    assert "new WebSocket" not in javascript
    assert "setInterval(loadMemoryApprovals" not in javascript
    approval_source = _approval_action_source()
    assert "localStorage" not in approval_source
    assert "sessionStorage" not in approval_source
    assert "setInterval" not in approval_source
    assert "new EventSource" not in approval_source
    assert ".memory-approval-workspace" in stylesheet
    assert ".memory-approval-preview" in stylesheet


def test_memory_approval_pending_validator_is_exact_bounded_and_fail_closed() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const item = {
  memoryId: 'project-safe-1', scope: 'project', scopeKind: 'workspace',
  category: 'convention', tier: 'short_term', source: 'reflection',
  createdAt: '2026-07-21T00:00:00.000Z', risk: 'low', safetyStatus: 'safe',
  reviewable: true,
  review: { contentPreview: '<script>alert(1)</script>', complete: true, truncated: false, redacted: false },
  reviewRevision: `memoryreviewrev_${'a'.repeat(64)}`,
  choices: ['approve', 'reject'],
};
const payload = {
  schemaVersion: 1, generatedAt: '2026-07-21T00:00:01.000Z', mode: 'read-only',
  source: { status: 'live', updatedAt: '2026-07-21T00:00:00.000Z', message: null },
  revision: `memoryapprovalrev_${'b'.repeat(64)}`, items: [item], diagnostics: [],
};
assert.ok(validateMemoryApprovalPendingPayload(payload));
assert.equal(validMemoryApprovalItem(item), true);
assert.equal(memoryApprovalReviewConsistent(item), true);
assert.equal(canApproveMemory(item), true);
for (const candidate of [
  { ...payload, schemaVersion: true },
  { ...payload, extra: true },
  { ...payload, revision: 'memoryapprovalrev_bad' },
  { ...payload, generatedAt: '2026-02-30T00:00:00.000Z' },
  { ...payload, items: Array(21).fill(item) },
]) assert.equal(validateMemoryApprovalPendingPayload(candidate), null);
const badScope = structuredClone(payload);
badScope.items[0].scope = 'user';
assert.equal(validateMemoryApprovalPendingPayload(badScope), null);
const badScopeKind = structuredClone(payload);
badScopeKind.items[0].scopeKind = 'user/global';
assert.equal(validateMemoryApprovalPendingPayload(badScopeKind), null);
const badRisk = structuredClone(payload);
badRisk.items[0].risk = 'medium';
assert.equal(validateMemoryApprovalPendingPayload(badRisk), null);
const badBool = structuredClone(payload);
badBool.items[0].review.complete = 1;
assert.equal(validateMemoryApprovalPendingPayload(badBool), null);
const badId = structuredClone(payload);
badId.items[0].memoryId = '../secret';
assert.equal(validateMemoryApprovalPendingPayload(badId), null);
const badPreview = structuredClone(payload);
badPreview.items[0].review.contentPreview = '界'.repeat(3000);
assert.equal(validateMemoryApprovalPendingPayload(badPreview), null);
const hiddenApprove = structuredClone(payload);
hiddenApprove.items[0].review.contentPreview = '[REDACTED SENSITIVE MEMORY]';
assert.equal(validateMemoryApprovalPendingPayload(hiddenApprove), null);
assert.equal(canApproveMemory(hiddenApprove.items[0]), false);
const denyOnly = structuredClone(payload);
denyOnly.items[0].reviewable = false;
denyOnly.items[0].review = { contentPreview: '[REDACTED SENSITIVE MEMORY]', complete: false, truncated: false, redacted: true };
denyOnly.items[0].choices = ['reject'];
assert.ok(validateMemoryApprovalPendingPayload(denyOnly));
assert.equal(canApproveMemory(denyOnly.items[0]), false);
const forgedDeny = structuredClone(denyOnly);
forgedDeny.items[0].choices = ['approve', 'reject'];
assert.equal(validateMemoryApprovalPendingPayload(forgedDeny), null);
const oneBadItem = structuredClone(payload);
oneBadItem.items.push({ ...item, memoryId: '../bad' });
assert.equal(validateMemoryApprovalPendingPayload(oneBadItem), null);
const missingField = structuredClone(payload);
delete missingField.items[0].source;
assert.equal(validateMemoryApprovalPendingPayload(missingField), null);
const suspicious = structuredClone(payload);
Object.assign(suspicious.items[0], { risk: 'medium', safetyStatus: 'suspicious' });
assert.ok(validateMemoryApprovalPendingPayload(suspicious));
assert.equal(canApproveMemory(suspicious.items[0]), true);
for (const review of [
  { contentPreview: '[TRUNCATED MEMORY PREVIEW]', complete: false, truncated: true, redacted: false },
  { contentPreview: '[UNSAFE MEMORY CONTENT HIDDEN]', complete: false, truncated: false, redacted: true },
]) {
  const boundedDeny = structuredClone(payload);
  Object.assign(boundedDeny.items[0], {
    reviewable: false, risk: review.redacted ? 'high' : 'low',
    safetyStatus: review.redacted ? 'unsafe' : 'safe', review, choices: ['reject'],
  });
  assert.ok(validateMemoryApprovalPendingPayload(boundedDeny));
  assert.equal(canApproveMemory(boundedDeny.items[0]), false);
}
"""
    subprocess.run(
        ["node", "-e", _approval_helpers_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_memory_approval_decision_validator_binds_identity_revision_and_result() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const expected = {
  memoryId: 'project-safe-1', reviewRevision: `memoryreviewrev_${'a'.repeat(64)}`,
  decision: 'approve',
};
const result = {
  schemaVersion: 1, generatedAt: '2026-07-21T00:00:02.000Z', mode: 'read-write',
  memoryId: expected.memoryId, status: 'approved', decision: 'approve',
  decisionAccepted: true, updatedAt: '2026-07-21T00:00:02.000Z',
};
assert.equal(validMemoryApprovalDecisionPayload(result, expected), true);
assert.equal(validMemoryApprovalDecisionPayload({ ...result, memoryId: 'project-other' }, expected), false);
assert.equal(validMemoryApprovalDecisionPayload({ ...result, status: 'rejected' }, expected), false);
assert.equal(validMemoryApprovalDecisionPayload({ ...result, decisionAccepted: 1 }, expected), false);
assert.equal(validMemoryApprovalDecisionPayload({ ...result, extra: true }, expected), false);
assert.equal(validMemoryApprovalDecisionPayload(result, { ...expected, reviewRevision: 'bad' }), false);
"""
    subprocess.run(
        ["node", "-e", _approval_helpers_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_memory_approval_actions_are_single_flight_authoritative_and_never_reposted() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const item = {
  memoryId: 'project-safe-1', scope: 'project', scopeKind: 'workspace', category: 'convention',
  tier: 'short_term', source: 'reflection', createdAt: '2026-07-21T00:00:00.000Z',
  risk: 'low', safetyStatus: 'safe', reviewable: true,
  review: { contentPreview: '<script>safe</script>', complete: true, truncated: false, redacted: false },
  reviewRevision: `memoryreviewrev_${'a'.repeat(64)}`, choices: ['approve', 'reject'],
};
const pending = (items = [], suffix = 'b') => ({
  schemaVersion: 1, generatedAt: '2026-07-21T00:00:03.000Z', mode: 'read-only',
  source: { status: 'live', updatedAt: '2026-07-21T00:00:03.000Z', message: null },
  revision: `memoryapprovalrev_${suffix.repeat(64)}`, items, diagnostics: [],
});
const memoryApprovalStore = {
  phase: 'live', items: [item], revision: `memoryapprovalrev_${'c'.repeat(64)}`,
  diagnostics: [], error: null, requestId: 0, actionGeneration: 0,
  actingMemoryId: null, selectedMemoryId: item.memoryId, lastUpdatedAt: null,
};
const memoryStore = { data: {}, phase: 'loaded', requestId: 0 };
let memoryApprovalReadPromise = null;
let memoryApprovalRefreshQueued = false;
let renders = 0;
let memoryRefreshes = 0;
let snapshotRefreshes = 0;
function renderRouteOnly() { renders += 1; }
async function loadMemory() { memoryRefreshes += 1; }
async function loadDashboardSnapshot() { snapshotRefreshes += 1; }
const response = (ok, payload, status = ok ? 200 : 409) => ({
  ok, status, async text() { return JSON.stringify(payload); },
});
let calls = [];
let replies = [
  response(true, {
    schemaVersion: 1, generatedAt: '2026-07-21T00:00:02.000Z', mode: 'read-write',
    memoryId: item.memoryId, status: 'approved', decision: 'approve',
    decisionAccepted: false, updatedAt: '2026-07-21T00:00:02.000Z',
  }),
  response(true, pending([])),
];
global.fetch = async (url, options = {}) => { calls.push([url, options]); return replies.shift(); };
(async () => {
  const first = decideMemoryApproval(item.memoryId, item.reviewRevision, 'approve');
  const duplicate = decideMemoryApproval(item.memoryId, item.reviewRevision, 'approve');
  assert.equal(await duplicate, false);
  assert.equal(await first, true);
  assert.equal(calls.filter(([, options]) => options.method === 'POST').length, 1);
  assert.equal(calls.filter(([, options]) => options.method !== 'POST').length, 1);
  assert.deepEqual(JSON.parse(calls[0][1].body), { decision: 'approve', reviewRevision: item.reviewRevision });
  assert.equal(memoryApprovalStore.phase, 'empty');
  assert.equal(memoryRefreshes, 1);
  assert.equal(snapshotRefreshes, 1);

  Object.assign(memoryApprovalStore, { phase: 'live', items: [item], error: null, actingMemoryId: null });
  calls = [];
  global.fetch = async (url, options = {}) => { calls.push([url, options]); throw new Error('offline'); };
  assert.equal(await decideMemoryApproval(item.memoryId, item.reviewRevision, 'reject'), false);
  assert.equal(calls.length, 1);
  assert.equal(memoryApprovalStore.phase, 'error');
  assert.match(memoryApprovalStore.error, /结果尚未确认/);
  assert.equal(calls.filter(([, options]) => options.method === 'POST').length, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", _approval_action_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_memory_approval_get_renders_after_read_single_flight_is_released() -> None:
    javascript = APP.read_text(encoding="utf-8")
    loader = javascript[
        javascript.index("async function loadMemoryApprovals") : javascript.index(
            "\nasync function decideMemoryApproval"
        )
    ]

    release = "if (memoryApprovalReadPromise === operation) memoryApprovalReadPromise = null;"
    final_render = (
        "if (requestId === memoryApprovalStore.requestId) renderRouteOnly('memory');"
    )
    assert release in loader
    assert final_render in loader
    assert loader.rindex(release) < loader.rindex(final_render)


def test_memory_sse_refreshes_existing_stores_without_a_second_channel() -> None:
    javascript = APP.read_text(encoding="utf-8")
    dispatcher = javascript[
        javascript.index("async function refreshChangedResources") : javascript.index(
            "\nconst esc =", javascript.index("async function refreshChangedResources")
        )
    ]
    route_loader = javascript[
        javascript.index("function loadRouteData") : javascript.index(
            "\nfunction handleRouteChange"
        )
    ]

    assert "loadMemoryApprovals" in dispatcher
    assert "memoryApprovalStore" in dispatcher
    assert "decideMemoryApproval" not in dispatcher
    assert "loadMemoryApprovals" in route_loader
    assert "sub === 'approvals'" in route_loader
    assert javascript.count("new EventSource('/api/v1/events')") == 1


def test_memory_approval_ui_is_bounded_escaped_accessible_and_responsive() -> None:
    javascript = APP.read_text(encoding="utf-8")
    stylesheet = STYLES.read_text(encoding="utf-8")
    render_source = javascript[
        javascript.index("function renderMemoryApprovals") : javascript.index(
            "\nconst VIEWS ="
        )
    ]

    assert "esc(selected.review.contentPreview)" in render_source
    assert 'aria-label="待审批 Memory"' in render_source
    assert 'aria-live="polite"' in render_source
    assert 'aria-busy="${acting ? \'true\' : \'false\'}"' in render_source
    assert "data-memory-approval-select" in render_source
    assert "data-memory-approval-decision" in render_source
    assert "审批" in render_source and "拒绝" in render_source
    assert "批准并启用" in render_source
    assert "批准后才会进入 Retrieval / Injection" in render_source
    assert "当前没有待审批的持久记忆" in render_source
    assert "只能拒绝" in render_source
    assert "手动刷新" in render_source
    assert "overflow-y: auto" in stylesheet
    assert "grid-template-columns: minmax(220px" in stylesheet
    assert "@media (max-width: 760px)" in stylesheet
    assert ".memory-approval-workspace { grid-template-columns: 1fr; }" in stylesheet


def test_invalid_get_preserves_only_previous_safe_items_and_fences_stale_completion() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const item = {
  memoryId: 'project-safe-1', scope: 'project', scopeKind: 'workspace', category: 'testing',
  tier: 'short_term', source: 'reflection', createdAt: '2026-07-21T00:00:00.000Z',
  risk: 'low', safetyStatus: 'safe', reviewable: true,
  review: { contentPreview: 'safe prior preview', complete: true, truncated: false, redacted: false },
  reviewRevision: `memoryreviewrev_${'a'.repeat(64)}`, choices: ['approve', 'reject'],
};
const memoryApprovalStore = {
  phase: 'live', items: [item], revision: `memoryapprovalrev_${'b'.repeat(64)}`,
  diagnostics: [], error: null, requestId: 0, actionGeneration: 0,
  actingMemoryId: null, selectedMemoryId: item.memoryId, lastUpdatedAt: null,
};
const memoryStore = {};
let memoryApprovalReadPromise = null;
let memoryApprovalRefreshQueued = false;
function renderRouteOnly() {}
async function loadMemory() {}
async function loadDashboardSnapshot() {}
const malicious = { schemaVersion: 1, injected: '<img src=x onerror=secret>' };
global.fetch = async () => ({ ok: true, async text() { return JSON.stringify(malicious); } });
(async () => {
  assert.equal(await loadMemoryApprovals(), false);
  assert.equal(memoryApprovalStore.phase, 'error');
  assert.deepEqual(memoryApprovalStore.items, [item]);
  assert.equal(memoryApprovalStore.error.includes('onerror'), false);
  assert.equal(memoryApprovalActionAvailable(item, 'approve'), false);

  let release;
  global.fetch = () => new Promise((resolve) => { release = resolve; });
  memoryApprovalStore.phase = 'live';
  memoryApprovalStore.error = null;
  const stale = loadMemoryApprovals();
  await Promise.resolve();
  memoryApprovalStore.requestId += 1;
  memoryApprovalStore.items = [item];
  release({ ok: true, async text() { return JSON.stringify({
    schemaVersion: 1, generatedAt: '2026-07-21T00:00:01.000Z', mode: 'read-only',
    source: { status: 'live', updatedAt: '2026-07-21T00:00:01.000Z', message: null },
    revision: `memoryapprovalrev_${'c'.repeat(64)}`, items: [], diagnostics: [],
  }); } });
  assert.equal(await stale, false);
  assert.deepEqual(memoryApprovalStore.items, [item]);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", _approval_action_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_conflicts_reload_authority_while_busy_and_network_never_retry_post() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const safe = {
  memoryId: 'project-safe-1', scope: 'project', scopeKind: 'workspace', category: 'testing',
  tier: 'short_term', source: 'reflection', createdAt: '2026-07-21T00:00:00.000Z',
  risk: 'low', safetyStatus: 'safe', reviewable: true,
  review: { contentPreview: 'safe preview', complete: true, truncated: false, redacted: false },
  reviewRevision: `memoryreviewrev_${'a'.repeat(64)}`, choices: ['approve', 'reject'],
};
const denyOnly = {
  ...safe, reviewable: false, risk: 'high', safetyStatus: 'unsafe',
  review: { contentPreview: '[UNSAFE MEMORY CONTENT HIDDEN]', complete: false, truncated: false, redacted: true },
  reviewRevision: `memoryreviewrev_${'d'.repeat(64)}`, choices: ['reject'],
};
const pending = {
  schemaVersion: 1, generatedAt: '2026-07-21T00:00:03.000Z', mode: 'read-only',
  source: { status: 'live', updatedAt: '2026-07-21T00:00:03.000Z', message: null },
  revision: `memoryapprovalrev_${'b'.repeat(64)}`, items: [denyOnly], diagnostics: [],
};
const memoryApprovalStore = {
  phase: 'live', items: [safe], revision: `memoryapprovalrev_${'c'.repeat(64)}`,
  diagnostics: [], error: null, requestId: 0, actionGeneration: 0,
  actingMemoryId: null, selectedMemoryId: safe.memoryId, lastUpdatedAt: null,
};
const memoryStore = {};
let memoryApprovalReadPromise = null;
let memoryApprovalRefreshQueued = false;
function renderRouteOnly() {}
async function loadMemory() {}
async function loadDashboardSnapshot() {}
const response = (ok, payload, status = ok ? 200 : 409) => ({
  ok, status, async text() { return JSON.stringify(payload); },
});
const conflictCodes = [
  'memory_review_stale', 'memory_approval_not_found', 'memory_already_decided',
  'memory_not_reviewable', 'memory_write_conflict', 'memory_approval_failed',
  'memory_approval_unavailable', 'invalid_request', 'invalid_memory_id',
  'invalid_decision', 'invalid_review_revision',
];
(async () => {
  for (const code of conflictCodes) {
    Object.assign(memoryApprovalStore, {
      phase: 'live', items: [safe], error: null, actingMemoryId: null,
      selectedMemoryId: safe.memoryId,
    });
    memoryApprovalReadPromise = null;
    memoryApprovalRefreshQueued = false;
    const calls = [];
    const replies = [response(false, { ok: false, error: { code, message: 'raw secret' } }), response(true, pending)];
    global.fetch = async (url, options = {}) => { calls.push([url, options]); return replies.shift(); };
    assert.equal(await decideMemoryApproval(safe.memoryId, safe.reviewRevision, 'approve'), false);
    assert.equal(calls.length, 2);
    assert.equal(calls.filter(([, options]) => options.method === 'POST').length, 1);
    assert.equal(memoryApprovalStore.items[0].reviewRevision, denyOnly.reviewRevision);
    assert.equal(memoryApprovalActionAvailable(memoryApprovalStore.items[0], 'approve'), false);
    assert.equal(memoryApprovalActionAvailable(memoryApprovalStore.items[0], 'reject'), true);
    assert.equal(memoryApprovalStore.error.includes('raw secret'), false);
  }

  Object.assign(memoryApprovalStore, { phase: 'live', items: [safe], error: null, actingMemoryId: null });
  memoryApprovalReadPromise = null;
  let calls = [];
  global.fetch = async (url, options = {}) => {
    calls.push([url, options]);
    return response(false, { ok: false, error: { code: 'memory_store_busy', message: 'secret' } }, 423);
  };
  assert.equal(await decideMemoryApproval(safe.memoryId, safe.reviewRevision, 'reject'), false);
  assert.equal(calls.length, 1);
  assert.equal(memoryApprovalStore.items[0], safe);
  assert.equal(memoryApprovalStore.phase, 'error');

  Object.assign(memoryApprovalStore, { phase: 'live', items: [safe], error: null, actingMemoryId: null });
  memoryApprovalReadPromise = null;
  calls = [];
  global.fetch = async (url, options = {}) => { calls.push([url, options]); throw new Error('offline'); };
  assert.equal(await decideMemoryApproval(safe.memoryId, safe.reviewRevision, 'reject'), false);
  assert.equal(calls.length, 1);
  assert.match(memoryApprovalStore.error, /不会自动重发/);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", _approval_action_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )
