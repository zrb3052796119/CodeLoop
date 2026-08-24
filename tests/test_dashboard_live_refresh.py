from __future__ import annotations

from pathlib import Path

from tests.node_harness import run_node


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "minicode/web/static/assets/app.js"


def _controller_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    start = javascript.index("const CHANGE_RESOURCE_NAMES")
    end = javascript.index("\nfunction captureLiveRefreshInteractionState", start)
    return javascript[start:end]


def _interaction_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    start = javascript.index("function captureLiveRefreshInteractionState")
    end = javascript.index("\nasync function refreshRunsFromChangeFeed", start)
    return javascript[start:end]


def test_live_refresh_controller_has_one_sse_primary_and_bounded_poll_fallback() -> None:
    javascript = APP.read_text(encoding="utf-8")
    dispatcher = javascript[
        javascript.index("async function refreshChangedResources") : javascript.index(
            "\nconst esc =", javascript.index("async function refreshChangedResources")
        )
    ]

    assert "function createLiveRefreshController" in javascript
    assert "fetch('/api/v1/changes'" in javascript
    assert "LIVE_RETRY_DELAYS_MS = [2000, 4000, 8000, 16000, 30000]" in javascript
    assert "document.visibilityState" in javascript
    assert "visibilitychange" in javascript
    assert "AbortController" in javascript
    assert "setTimeout" in javascript
    assert javascript.count("new EventSource('/api/v1/events')") == 1
    assert "createRealtimeRefreshController" in javascript
    assert "new WebSocket" not in javascript
    assert "setInterval(load" not in javascript
    assert "setInterval(checkActiveTurnStatus" not in javascript
    assert "submitChatTurn" not in _controller_source()
    assert "checkActiveTurnStatus(true)" in dispatcher
    assert "submitChatTurn" not in dispatcher
    assert "loadSessions(false, true)" in dispatcher
    assert "loadPendingPermissions" in dispatcher
    assert "decidePermission" not in dispatcher
    assert "changed.has('permissions')" in dispatcher
    assert "changed.has('memory')" in dispatcher
    assert "loadMemoryApprovals" in dispatcher
    assert "memoryApprovalStore.phase !== 'idle'" in dispatcher
    assert "decideMemoryApproval" not in dispatcher
    assert "loadRunDetail(selectedRunId, false, true)" in javascript
    assert "captureLiveRefreshInteractionState" in dispatcher
    assert "restoreLiveRefreshInteractionState" in dispatcher
    assert "main?.scrollTop === 0" in javascript
    assert "document.activeElement === document.body" in javascript


def test_live_refresh_controller_scheduler_backoff_visibility_and_stale_guards() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const resources = ['runs', 'sessions', 'turns', 'memory', 'skills', 'connections', 'permissions'];
function snapshot(suffix = 'a', overrides = {}) {
  const values = Object.fromEntries(resources.map((name) => [name, {
    status: 'live', revision: `rev_${suffix.repeat(64)}`,
  }]));
  Object.assign(values, overrides);
  return {
    schemaVersion: 2,
    generatedAt: '2026-07-19T00:00:00.000Z',
    mode: 'read-only',
    pollAfterMs: 2000,
    resources: values,
    diagnostics: [],
  };
}
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture(fetchChanges) {
  let visible = true;
  let nextId = 0;
  const jobs = [];
  const states = [];
  const refreshes = [];
  const aborted = [];
  const controller = createLiveRefreshController({
    fetchChanges,
    refreshResources: async (changed) => { refreshes.push([...changed]); },
    isVisible: () => visible,
    schedule: (callback, delay) => {
      const job = { id: ++nextId, callback, delay, cancelled: false };
      jobs.push(job);
      return job.id;
    },
    cancelSchedule: (id) => {
      const job = jobs.find((item) => item.id === id);
      if (job) job.cancelled = true;
    },
    createAbortController: () => {
      const token = { aborted: false };
      aborted.push(token);
      return { signal: token, abort: () => { token.aborted = true; } };
    },
    onState: (state) => states.push({ ...state }),
  });
  return {
    controller, jobs, states, refreshes, aborted,
    setVisible(value) { visible = value; },
    async runNext() {
      const job = jobs.find((item) => !item.cancelled && !item.ran);
      assert.ok(job, 'expected a scheduled job');
      job.ran = true;
      await job.callback();
      return job;
    },
  };
}

(async () => {
  const replies = [
    snapshot('a'),
    snapshot('a', { runs: { status: 'live', revision: `rev_${'b'.repeat(64)}` } }),
    snapshot('a', { runs: { status: 'live', revision: `rev_${'b'.repeat(64)}` } }),
  ];
  const normal = fixture(async () => replies.shift());
  normal.controller.start();
  assert.equal((await normal.runNext()).delay, 0);
  assert.deepEqual(normal.refreshes, []); // first success establishes a baseline
  assert.equal(normal.jobs.at(-1).delay, 2000);
  await normal.runNext();
  assert.deepEqual(normal.refreshes, [['runs']]);
  await normal.runNext();
  assert.deepEqual(normal.refreshes, [['runs']]);

  normal.setVisible(false);
  normal.controller.visibilityChanged();
  assert.equal(normal.states.at(-1).phase, 'paused');
  assert.equal(normal.jobs.at(-1).cancelled, true);
  normal.setVisible(true);
  normal.controller.visibilityChanged();
  assert.equal(normal.jobs.at(-1).delay, 0);

  let attempts = 0;
  const retry = fixture(async () => {
    attempts += 1;
    if (attempts <= 5) throw new Error('offline');
    return snapshot('c');
  });
  retry.controller.start();
  const delays = [];
  for (let index = 0; index < 5; index += 1) {
    await retry.runNext();
    delays.push(retry.jobs.at(-1).delay);
  }
  assert.deepEqual(delays, [2000, 4000, 8000, 16000, 30000]);
  assert.equal(retry.states.at(-1).phase, 'stale');
  await retry.runNext();
  assert.equal(retry.states.at(-1).phase, 'live');
  assert.equal(retry.jobs.at(-1).delay, 2000); // success resets backoff

  const first = deferred();
  let fetchCount = 0;
  const stale = fixture(async () => {
    fetchCount += 1;
    return fetchCount === 1 ? first.promise : snapshot('d');
  });
  stale.controller.start();
  const pendingJob = stale.jobs.find((item) => !item.cancelled);
  pendingJob.ran = true;
  const pending = pendingJob.callback();
  await Promise.resolve();
  await stale.controller.pollNow();
  assert.equal(fetchCount, 1); // max one active request
  stale.setVisible(false);
  stale.controller.visibilityChanged();
  assert.equal(stale.aborted[0].aborted, true);
  stale.setVisible(true);
  stale.controller.visibilityChanged();
  await stale.runNext();
  assert.equal(fetchCount, 2);
  first.resolve(snapshot('e', { memory: { status: 'live', revision: `rev_${'f'.repeat(64)}` } }));
  await pending;
  assert.deepEqual(stale.refreshes, []); // aborted generation cannot refresh

  const permissionReplies = [
    snapshot('a'),
    snapshot('a', { permissions: { status: 'live', revision: `rev_${'9'.repeat(64)}` } }),
  ];
  const permissionPoll = fixture(async () => permissionReplies.shift());
  permissionPoll.controller.start();
  await permissionPoll.runNext();
  await permissionPoll.runNext();
  assert.deepEqual(permissionPoll.refreshes, [['permissions']]);

  const malformed = snapshot('a');
  malformed.secret = '/Users/private';
  assert.equal(validChangeSnapshot(malformed), false);
  assert.equal(validChangeSnapshot({ ...snapshot('a'), schemaVersion: 1 }), false);
})().catch((error) => { console.error(error); process.exit(1); });
"""

    run_node(_controller_source() + "\n" + harness)


def test_live_refresh_restores_only_state_reset_by_rendering() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const body = {};
const documentElement = {};
const input = {
  disabled: false,
  value: 'draft',
  focused: 0,
  selection: null,
  focus() { this.focused += 1; },
  setSelectionRange(start, end, direction) { this.selection = [start, end, direction]; },
};
const main = { scrollTop: 0 };
const view = { scrollTop: 0 };
const chatLog = { scrollTop: 0 };
const elements = { '#message': input, main, '#view': view, '#chat-log': chatLog };
global.document = {
  activeElement: body,
  body,
  documentElement,
  querySelector(selector) { return elements[selector] || null; },
};
const saved = {
  messageFocused: true,
  selectionStart: 2,
  selectionEnd: 4,
  selectionDirection: 'forward',
  mainScrollTop: 11,
  viewScrollTop: 22,
  chatScrollTop: 33,
};
restoreLiveRefreshInteractionState(saved);
assert.equal(main.scrollTop, 11);
assert.equal(view.scrollTop, 22);
assert.equal(chatLog.scrollTop, 33);
assert.equal(input.focused, 1);
assert.deepEqual(input.selection, [2, 4, 'forward']);

const userControl = {};
document.activeElement = userControl;
main.scrollTop = 101;
view.scrollTop = 102;
chatLog.scrollTop = 103;
restoreLiveRefreshInteractionState(saved);
assert.equal(main.scrollTop, 101);
assert.equal(view.scrollTop, 102);
assert.equal(chatLog.scrollTop, 103);
assert.equal(input.focused, 1);
assert.deepEqual(input.selection, [2, 4, 'forward']);
"""

    run_node(_interaction_source() + "\n" + harness)


def test_live_refresh_ui_is_restrained_and_honest() -> None:
    html = (ROOT / "minicode/web/static/index.html").read_text(encoding="utf-8")
    stylesheet = (ROOT / "minicode/web/static/assets/styles.css").read_text(
        encoding="utf-8"
    )
    javascript = APP.read_text(encoding="utf-8")

    assert 'id="live-refresh-status"' in html
    assert "live-refresh-status" in stylesheet
    for label in ("实时", "已暂停（页面不可见）", "正在重连", "数据可能过期"):
        assert label in javascript
    assert "live refresh" in html
    assert "connection-scoped Assistant/Tool stream" in html
    assert "final Session authority" in html
    assert "no token streaming" not in html
