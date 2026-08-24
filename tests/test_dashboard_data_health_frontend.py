from __future__ import annotations

from pathlib import Path

from tests.node_harness import run_node


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "minicode/web/static/assets/app.js"
STYLES = ROOT / "minicode/web/static/assets/styles.css"


def _contract_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    return (
        javascript[
            javascript.index("const DATA_HEALTH_SCHEMA_VERSION") : javascript.index(
                "\nfunction validateDataHealthPayload"
            )
        ]
        + "\n"
        + javascript[
            javascript.index("function validateDataHealthPayload") : javascript.index(
                "\nfunction validPermissionTimestamp"
            )
        ]
    )


def _transport_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    return (
        javascript[
            javascript.index("const dataHealthStore") : javascript.index(
                "\nconst opsStore"
            )
        ]
        + "\n"
        + _contract_source()
        + "\n"
        + javascript[
            javascript.index("async function loadDataHealth") : javascript.index(
                "\nasync function loadOps"
            )
        ]
    )


def _payload() -> str:
    return r"""
const specs = DATA_HEALTH_STORE_SPECS;
const stores = specs.map(([id, scope, durability, resetDisposition]) => ({
  id, scope, durability, status: 'live',
  recordCount: durability === 'process-local' ? null : 0,
  byteCount: durability === 'process-local' ? null : 0,
  updatedAt: null,
  resetDisposition,
  message: durability === 'process-local'
    ? 'Process-local state is not a disk persistence fact.'
    : 'The bounded read-only scan completed.',
}));
const payload = {
  schemaVersion: 1,
  generatedAt: '2026-07-23T00:00:00.000Z',
  mode: 'read-only',
  status: 'live',
  workspace: { id: 'ws_0123456789abcdef', name: 'fixture-workspace' },
  summary: {
    storeCount: stores.length, knownRecordCount: 0, knownByteCount: 0, issueCount: 0,
  },
  stores,
  maintenancePlan: {
    status: 'planning',
    destructiveActionsAvailable: false,
    eligibleStoreIds: specs.filter((item) => item[3] === 'planned').map((item) => item[0]),
    excludedStoreIds: specs.filter((item) => item[3] === 'excluded').map((item) => item[0]),
    blockers: [],
  },
  diagnostics: [],
};
"""


def test_data_health_payload_validator_is_exact_bounded_and_safe() -> None:
    harness = (
        r"""
const assert = require('node:assert/strict');
"""
        + _payload()
        + r"""
assert.equal(validateDataHealthPayload(payload), payload);
for (const bad of [
  { ...payload, schemaVersion: true },
  { ...payload, extra: true },
  { ...payload, generatedAt: '2026-02-30T00:00:00.000Z' },
  { ...payload, mode: 'read-write' },
  { ...payload, workspace: { ...payload.workspace, id: '/Users/private' } },
  { ...payload, workspace: { ...payload.workspace, name: '../private' } },
  { ...payload, summary: { ...payload.summary, knownByteCount: true } },
  { ...payload, summary: { ...payload.summary, knownByteCount: -1 } },
  { ...payload, summary: { ...payload.summary, knownByteCount: 2 ** 53 } },
  { ...payload, stores: payload.stores.slice(1) },
  { ...payload, diagnostics: [{ storeId: 'sessions', code: 'read_failed', message: '/Users/private' }] },
  { ...payload, maintenancePlan: { ...payload.maintenancePlan, destructiveActionsAvailable: true } },
]) assert.equal(validateDataHealthPayload(bad), null);

const badStore = structuredClone(payload);
badStore.stores[0].recordCount = false;
assert.equal(validateDataHealthPayload(badStore), null);
const badStoreTime = structuredClone(payload);
badStoreTime.stores[0].updatedAt = 'yesterday';
assert.equal(validateDataHealthPayload(badStoreTime), null);
const extraStoreField = structuredClone(payload);
extraStoreField.stores[0].path = '/private';
assert.equal(validateDataHealthPayload(extraStoreField), null);
const wrongScope = structuredClone(payload);
wrongScope.stores[0].scope = 'user';
assert.equal(validateDataHealthPayload(wrongScope), null);
const oversized = structuredClone(payload);
oversized.workspace.name = 'x'.repeat(DATA_HEALTH_MAX_BYTES);
assert.equal(validateDataHealthPayload(oversized), null);
"""
    )
    run_node(_contract_source() + "\n" + harness)


def test_data_health_loading_empty_live_partial_error_retry_and_stale_transport() -> (
    None
):
    harness = (
        r"""
const assert = require('node:assert/strict');
(async () => {
"""
        + _payload()
        + r"""
let mode = 'live';
let methods = [];
global.renderRouteOnly = () => {};
global.fetch = async (_url, options) => {
  methods.push(options);
  if (mode === 'error') throw new Error('private failure');
  const next = structuredClone(payload);
  if (mode === 'partial') {
    next.status = 'partial';
    next.stores[0].status = 'partial';
    next.stores[0].message = 'Some persisted facts could not be verified safely.';
    next.summary.issueCount = 1;
    next.maintenancePlan.blockers = [{ code: 'store_not_live', storeId: 'sessions' }];
    next.diagnostics = [{
      storeId: 'sessions',
      code: 'invalid_json',
      message: 'A persisted JSON document was malformed.',
    }];
  }
  if (mode === 'nonempty') {
    next.stores[0].recordCount = 2;
    next.stores[0].byteCount = 10;
    next.summary.knownRecordCount = 2;
    next.summary.knownByteCount = 10;
  }
  const body = JSON.stringify(next);
  return {
    ok: true,
    headers: { get: () => 'application/json; charset=utf-8' },
    text: async () => body,
  };
};

assert.equal(dataHealthStore.phase, 'idle');
await loadDataHealth();
assert.equal(dataHealthStore.phase, 'empty');
mode = 'nonempty';
await loadDataHealth();
assert.equal(dataHealthStore.phase, 'loaded');
mode = 'partial';
await loadDataHealth();
assert.equal(dataHealthStore.phase, 'partial');
const stale = dataHealthStore.data;
mode = 'error';
await refreshDataHealth();
assert.equal(dataHealthStore.phase, 'partial');
assert.equal(dataHealthStore.data, stale);
assert.equal(dataHealthStore.error, '无法读取安全的数据健康快照。');
dataHealthStore.data = null;
await refreshDataHealth();
assert.equal(dataHealthStore.phase, 'error');
assert.equal(methods.every((item) => item.method === 'GET'), true);
assert.equal(methods.every((item) => item.cache === 'no-store'), true);
assert.equal(methods.every((item) => item.headers.Accept === 'application/json'), true);
})();
"""
    )
    run_node(_transport_source() + "\n" + harness)


def test_data_health_ui_is_read_only_and_reuses_existing_refresh_transport() -> None:
    javascript = APP.read_text(encoding="utf-8")
    stylesheet = STYLES.read_text(encoding="utf-8")

    assert "const dataHealthStore" in javascript
    assert "validateDataHealthPayload" in javascript
    assert "async function loadDataHealth" in javascript
    assert "function refreshDataHealth" in javascript
    assert "Data Health / 数据健康" in javascript
    assert "本阶段没有删除、清理、修复或重置功能" in javascript
    assert "planned 不代表现在已经能删除" in javascript
    assert "process-local 状态不属于磁盘持久化事实" in javascript
    assert javascript.count("new EventSource('/api/v1/events')") == 1
    assert "setInterval(loadDataHealth" not in javascript
    assert "fetch('/api/v1/data-health'" in javascript
    assert "method: 'GET'" in javascript
    assert "method: 'POST', url: '/api/v1/data-health" not in javascript
    assert "method: 'DELETE', url: '/api/v1/data-health" not in javascript
    assert "Reset Data" not in javascript
    assert "Cleanup Data" not in javascript
    assert ".data-health-grid" in stylesheet
    assert ".data-health-store" in stylesheet
    assert "data-health-store status-${esc(store.status)}" in javascript
    assert 'data-health-store ${esc(store.status)}' not in javascript
    assert ".data-health-store.status-partial" in stylesheet
    assert "@media (max-width: 760px)" in stylesheet
