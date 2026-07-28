from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "minicode/web/static/assets/app.js"


def _stream_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    start = javascript.index("const SESSION_ID_PATTERN")
    end = javascript.index("\nconst memoryStore", start)
    return javascript[start:end]


def _presentation_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    start = javascript.index("function chatLogIsNearBottom")
    end = javascript.index("\nfunction validTurnStatus", start)
    return javascript[start:end]


def _terminal_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    start = javascript.index("async function refreshCompletedTurn")
    end = javascript.index("\nasync function checkActiveTurnStatus", start)
    return javascript[start:end]


def _submit_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    start = javascript.index("async function submitChatTurn")
    end = javascript.index("\nfunction renderConversationDock", start)
    return javascript[start:end]


def test_chat_ndjson_parser_is_incremental_strict_and_generation_scoped() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const turn = 'turn_' + '7'.repeat(32);
const otherTurn = 'turn_' + '8'.repeat(32);
const toolId = 'toolstream_' + '9'.repeat(32);
const base = (type, sequence, fields = {}) => ({schemaVersion: 1, type, turnId: turn, sequence, ...fields});
const line = (value) => `${JSON.stringify(value)}\n`;
const bytes = (value) => new TextEncoder().encode(value);

function fixture(generation = 4) {
  const store = createChatStreamState(turn, generation);
  const frames = [];
  let invalid = 0;
  const parser = createChatNdjsonParser({
    turnId: turn,
    generation,
    store,
    onFrame: (frame, outcome) => frames.push([frame.type, outcome]),
    onInvalid: () => { invalid += 1; },
  });
  return {store, frames, parser, invalid: () => invalid};
}

// One line split across chunks and one chunk containing several lines.
const fragmented = fixture();
const ready = line(base('chat.stream.ready', 0));
fragmented.parser.feed(bytes(ready.slice(0, 7)));
fragmented.parser.feed(bytes(ready.slice(7)));
fragmented.parser.feed(bytes(
  line(base('chat.assistant.delta', 1, {text: '一'}))
  + line(base('chat.assistant.delta', 2, {text: '二'}))
));
assert.equal(fragmented.parser.finish(), true);
assert.equal(fragmented.store.provisionalText, '一二');
assert.equal(fragmented.store.lastSequence, 2);
assert.equal(fragmented.invalid(), 0);

// UTF-8 code points may cross network chunks.
const unicode = fixture();
const unicodePayload = bytes(ready + line(base('chat.assistant.delta', 1, {text: '汉🙂'})));
const emojiLead = unicodePayload.indexOf(0xf0);
unicode.parser.feed(unicodePayload.slice(0, emojiLead + 2));
unicode.parser.feed(unicodePayload.slice(emojiLead + 2));
unicode.parser.finish();
assert.equal(unicode.store.provisionalText, '汉🙂');

// Exact allowlists reject malformed JSON, extras, booleans, foreign turns and unsafe tool data.
const invalidLines = [
  '{',
  JSON.stringify({...base('chat.stream.ready', 0), secret: 'x'}),
  JSON.stringify({...base('chat.stream.ready', 0), schemaVersion: true}),
  JSON.stringify({...base('chat.stream.ready', 0), sequence: true}),
  JSON.stringify({...base('chat.stream.ready', 0), turnId: otherTurn}),
  JSON.stringify(base('chat.tool.started', 0, {toolStreamId: toolId, toolName: '../secret'})),
  JSON.stringify(base('chat.turn.error', 0, {code: 'private_exception'})),
];
for (const invalidLine of invalidLines) {
  assert.equal(parseChatStreamFrame(invalidLine, turn), null, invalidLine);
}

// Duplicate/backward are ignored; a gap stops trusting provisional content but terminal remains usable.
const ordered = createChatStreamState(turn, 3);
assert.equal(applyChatStreamFrame(ordered, base('chat.stream.ready', 0), 3), 'chat.stream.ready');
assert.equal(applyChatStreamFrame(ordered, base('chat.assistant.delta', 1, {text: 'kept'}), 3), 'chat.assistant.delta');
assert.equal(applyChatStreamFrame(ordered, base('chat.assistant.delta', 1, {text: 'duplicate'}), 3), 'ignored');
assert.equal(applyChatStreamFrame(ordered, base('chat.assistant.delta', 0, {text: 'backward'}), 3), 'ignored');
assert.equal(applyChatStreamFrame(ordered, base('chat.assistant.delta', 3, {text: 'missing'}), 3), 'gap');
assert.equal(ordered.provisionalText, 'kept');
assert.equal(ordered.incomplete, true);
assert.equal(ordered.trusted, false);
const completed = base('chat.turn.completed', 4, {
  status: 'completed', sessionId: 'session_01', created: true,
  updatedAt: '2026-07-20T12:00:00.000Z', runId: null,
});
assert.equal(applyChatStreamFrame(ordered, completed, 3), 'chat.turn.completed');
assert.equal(ordered.terminal, true);

// A stale stream/generation cannot mutate the active presentation.
const stale = createChatStreamState(turn, 8);
assert.equal(applyChatStreamFrame(stale, base('chat.stream.ready', 0), 7), 'stale');
assert.equal(stale.lastSequence, -1);
assert.equal(applyChatStreamFrame(stale, {...base('chat.stream.ready', 0), turnId: otherTurn}, 8), 'stale');
assert.equal(stale.lastSequence, -1);

// Tool projection contains only safe identity/status and honors pairing.
const tools = createChatStreamState(turn, 1);
applyChatStreamFrame(tools, base('chat.stream.ready', 0), 1);
applyChatStreamFrame(tools, base('chat.tool.started', 1, {toolStreamId: toolId, toolName: 'read_file'}), 1);
applyChatStreamFrame(tools, base('chat.tool.finished', 2, {toolStreamId: toolId, toolName: 'read_file', outcome: 'success', paired: true}), 1);
assert.deepEqual(tools.tools, [{toolStreamId: toolId, toolName: 'read_file', status: 'success', paired: true}]);

// Invalid transport detaches once and keeps the Turn identity for recovery.
const malformed = fixture();
assert.equal(malformed.parser.feed(bytes('{\n')), false);
assert.equal(malformed.parser.feed(bytes(ready)), false);
assert.equal(malformed.invalid(), 1);
assert.equal(malformed.store.turnId, turn);
assert.equal(malformed.store.detached, true);
assert.equal(malformed.store.incomplete, true);
"""
    subprocess.run(
        ["node", "-e", _stream_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_formal_chat_stream_is_connection_scoped_and_never_persisted() -> None:
    javascript = APP.read_text(encoding="utf-8")
    storage_writes = [line for line in javascript.splitlines() if ".setItem(" in line]

    assert "Accept: 'application/x-ndjson'" in javascript
    assert "ReadableStream" not in javascript  # consumes the fetch body; creates no side channel
    assert "body.getReader" in javascript
    assert "new TextDecoder('utf-8', { fatal: true })" in javascript
    assert javascript.count("new EventSource('/api/v1/events')") == 1
    assert "new WebSocket" not in javascript
    assert all(
        forbidden not in line
        for line in storage_writes
        for forbidden in (
            "provisionalText",
            "chatStreamStore",
            "partial frame",
            "toolStreamId",
            "terminal",
        )
    )


def test_chat_stream_rendering_coalesces_frames_respects_scroll_and_escapes() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
})[char]);
const turn = 'turn_' + '7'.repeat(32);
const chatStore = {activeTurnId: turn};
const chatStreamStore = {
  turnId: turn, generation: 3, phase: 'generating', provisionalText: '<img src=x onerror=alert(1)>',
  tools: [{toolName: '<script>secret</script>', status: 'running'}],
  incomplete: false, truncatedAssistant: false, truncatedTools: false,
};
let chatStreamRenderPending = false;
let activeChatStreamReader = null;
const log = {scrollHeight: 500, scrollTop: 100, clientHeight: 200};
global.document = {querySelector: () => log};
const jobs = [];
global.requestAnimationFrame = (callback) => { jobs.push(callback); return jobs.length; };
const renders = [];
function renderConversationDock(follow) { renders.push(follow); }

const html = chatStreamPresentationHtml();
assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
assert.ok(html.includes('&lt;script&gt;secret&lt;/script&gt;'));
assert.equal(html.includes('<img src=x'), false);
assert.equal(html.includes('<script>secret'), false);
assert.equal((html.match(/aria-live=/g) || []).length, 1);

scheduleChatStreamRender(3);
scheduleChatStreamRender(3);
scheduleChatStreamRender(3);
assert.equal(jobs.length, 1);
jobs[0]();
assert.deepEqual(renders, [false]); // the user had scrolled up

log.scrollTop = 300;
scheduleChatStreamRender(3);
jobs[1]();
assert.deepEqual(renders, [false, true]);

scheduleChatStreamRender(2);
jobs[2]();
assert.deepEqual(renders, [false, true]); // stale generation cannot render
"""
    subprocess.run(
        ["node", "-e", _presentation_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_terminal_refresh_is_deduplicated_and_removes_partial_only_after_rest() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const turn = 'turn_' + '7'.repeat(32);
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const chatStore = {
  activeTurnId: turn, activeTargetSessionId: null, operationGeneration: 0,
  terminalTurnId: null, terminalPromise: null, draft: 'draft', targetMode: 'new',
  lastSessionId: null, phase: 'submitting', error: null,
};
const chatStreamStore = {turnId: turn};
const sessionDetailStore = {selectionVersion: 0, sessionId: null, data: null};
let refreshCalls = 0;
let clearCalls = 0;
let resetCalls = 0;
function persistSessionSelection() {}
async function refreshSessions() { refreshCalls += 1; }
async function refreshRuns() { refreshCalls += 1; }
async function refreshDashboardSnapshot() { refreshCalls += 1; }
async function refreshOps() { refreshCalls += 1; }
async function loadSessionDetail(sessionId) {
  refreshCalls += 1;
  await Promise.resolve();
  sessionDetailStore.sessionId = sessionId;
  sessionDetailStore.data = {session: {id: sessionId}};
  return 'loaded';
}
function clearActiveTurn(turnId) {
  assert.equal(turnId, turn);
  clearCalls += 1;
  chatStore.activeTurnId = null;
}
function retirePermissionTurn() {}
function resetChatStreamState() { resetCalls += 1; chatStreamStore.turnId = null; }
function setCompletedFeedbackTarget(payload) {
  assert.equal(payload.turnId, turn);
}
function renderConversationDock() {}
const completed = {
  turnId: turn, sessionId: 'session_01', created: true,
  updatedAt: '2026-07-20T12:00:00.000Z', runId: null,
};

(async () => {
  const first = finalizeCompletedTurn(completed);
  const second = finalizeCompletedTurn(completed);
  assert.equal(await first, true);
  assert.equal(await second, true);
  assert.equal(refreshCalls, 5);
  assert.equal(clearCalls, 1);
  assert.equal(resetCalls, 1);
  assert.equal(chatStore.phase, 'success');
  assert.equal(chatStore.activeTurnId, null);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", _terminal_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_failed_final_rest_refresh_keeps_partial_and_recovery_identity() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const turn = 'turn_' + '7'.repeat(32);
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const chatStore = {
  activeTurnId: turn, activeTargetSessionId: null, operationGeneration: 0,
  terminalTurnId: null, terminalPromise: null, draft: 'draft', targetMode: 'new',
  lastSessionId: null, phase: 'submitting', error: null,
};
const chatStreamStore = {turnId: turn, provisionalText: 'temporary only'};
const sessionDetailStore = {selectionVersion: 0, sessionId: null, data: null};
let clearCalls = 0;
function persistSessionSelection() {}
async function refreshSessions() {}
async function refreshRuns() {}
async function refreshDashboardSnapshot() {}
async function refreshOps() {}
async function loadSessionDetail(sessionId) {
  sessionDetailStore.sessionId = sessionId;
  sessionDetailStore.data = null;
  return 'error';
}
function clearActiveTurn() { clearCalls += 1; }
function retirePermissionTurn() {}
function resetChatStreamState() { throw new Error('partial must be retained'); }
function renderConversationDock() {}

(async () => {
  const completed = await finalizeCompletedTurn({turnId: turn, sessionId: 'session_01'});
  assert.equal(completed, false);
  assert.equal(clearCalls, 0);
  assert.equal(chatStore.activeTurnId, turn);
  assert.equal(chatStore.phase, 'completed_unavailable');
  assert.ok(chatStore.error.includes('最终 Session 尚未重新读取'));
  assert.equal(chatStreamStore.provisionalText, 'temporary only');
  assert.equal(chatStore.terminalTurnId, null); // a manual status check may retry REST
})().catch((error) => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", _terminal_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )


def test_chat_stream_ui_copy_and_global_transport_count_are_current() -> None:
    javascript = APP.read_text(encoding="utf-8")
    html = (ROOT / "minicode/web/static/index.html").read_text(encoding="utf-8")

    assert "connection-scoped Assistant/Tool stream" in html
    assert "final Session authority" in html
    assert "loopback permission approval" in html
    assert "no token streaming" not in html
    assert javascript.count("new EventSource('/api/v1/events')") == 1
    assert "fetch('/api/v1/changes'" in javascript
    assert "fetch('/api/v1/chat/turns'" in javascript
    assert "不会自动重发" in javascript


def test_submit_supports_json_fallback_and_disconnect_never_reposts() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const turn = 'turn_' + '7'.repeat(32);
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const chatStore = {
  phase: 'idle', requestGeneration: 0, operationGeneration: 0,
  draft: 'hello', targetMode: 'new', error: null, lastSessionId: null,
  activeTurnId: null, activeTargetSessionId: null,
  terminalTurnId: null, terminalPromise: null,
};
const chatStreamStore = {turnId: null, generation: 0, provisionalText: '', detached: false};
const sessionsStore = {items: []};
const sessionDetailStore = {selectionVersion: 0, sessionId: null, data: null};
let fetchCalls = 0;
let lastHeaders = null;
let responseMode = 'json';
function chatTargetSessionId() { return null; }
function createTurnId() { return turn; }
function persistActiveTurn() { return true; }
function persistSessionSelection() {}
function renderConversationDock() {}
function fixedChatError(code) { return `safe:${code}`; }
function resetChatFeedbackTarget() {}
function setCompletedFeedbackTarget() {}
function resetChatStreamState(turnId = null, generation = 0) {
  Object.assign(chatStreamStore, {turnId, generation, provisionalText: '', detached: false, terminal: false});
}
function clearActiveTurn(turnId) { if (chatStore.activeTurnId === turnId) chatStore.activeTurnId = null; }
function retirePermissionTurn() {}
function detachChatStreamState(store) { store.detached = true; }
function scheduleChatStreamRender() {}
function finishCancelledTurn() {}
async function finalizeCompletedTurn() {}
async function refreshSessions() { sessionDetailStore.data = {session: {id: 'session_01'}}; }
async function refreshRuns() {}
async function refreshDashboardSnapshot() {}
async function refreshOps() {}
async function loadSessionDetail() { return 'loaded'; }
async function checkActiveTurnStatus() {}
async function consumeChatNdjson(_body, options) {
  options.store.provisionalText = 'partial before disconnect';
  throw new Error('connection reset');
}
function response() {
  if (responseMode === 'json') {
    return {
      ok: true,
      headers: {get: () => 'application/json; charset=utf-8'},
      json: async () => ({
        ok: true, schemaVersion: 1, mode: 'read-write', turnId: turn,
        sessionId: 'session_01', created: true,
        assistant: {role: 'assistant', content: 'committed'},
        updatedAt: '2026-07-20T12:00:00.000Z', runId: null,
      }),
    };
  }
  return {
    ok: true,
    headers: {get: () => 'application/x-ndjson; charset=utf-8'},
    body: {},
  };
}
global.fetch = async (_url, options) => {
  fetchCalls += 1;
  lastHeaders = options.headers;
  return response();
};

(async () => {
  await submitChatTurn();
  assert.equal(fetchCalls, 1);
  assert.equal(lastHeaders.Accept, 'application/x-ndjson');
  assert.equal(chatStore.phase, 'success');
  assert.equal(chatStore.activeTurnId, null);
  assert.equal(chatStore.draft, '');

  responseMode = 'stream';
  chatStore.phase = 'idle';
  chatStore.draft = 'second';
  chatStore.activeTurnId = null;
  await submitChatTurn();
  assert.equal(fetchCalls, 2); // no automatic retry POST
  assert.equal(chatStore.phase, 'recovery_error');
  assert.equal(chatStore.activeTurnId, turn);
  assert.equal(chatStreamStore.provisionalText, 'partial before disconnect');
  assert.equal(chatStreamStore.detached, true);
  assert.ok(chatStore.error.includes('不会自动重发'));
})().catch((error) => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", _submit_source() + "\n" + harness],
        check=True,
        capture_output=True,
        text=True,
    )
