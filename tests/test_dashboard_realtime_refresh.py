from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "minicode/web/static/assets/app.js"


def _realtime_source() -> str:
    javascript = APP.read_text(encoding="utf-8")
    start = javascript.index("const CHANGE_RESOURCE_NAMES")
    end = javascript.index("\nfunction captureLiveRefreshInteractionState", start)
    return javascript[start:end]


def test_formal_dashboard_has_one_sse_owner_and_polling_only_as_fallback() -> None:
    javascript = APP.read_text(encoding="utf-8")

    assert javascript.count("new EventSource('/api/v1/events')") == 1
    assert "function parseDashboardEvent" in javascript
    assert "function createResourceRefreshQueue" in javascript
    assert "function createRealtimeRefreshController" in javascript
    assert "refreshQueue.enqueue" in javascript
    assert "refreshQueue.full" in javascript
    assert "pollingController" in javascript
    assert "SSE 重连中（轮询备用）" in javascript
    assert "publish('polling', '轮询备用'" in javascript
    assert "publish('stale', '轮询备用 · 数据可能过期'" in javascript
    assert "Last-Event-ID" not in javascript
    assert "localStorage.setItem('minicode-dashboard-event" not in javascript
    assert "sessionStorage.setItem('minicode-dashboard-event" not in javascript
    assert "new WebSocket" not in javascript


def test_realtime_validation_queue_and_controller_contracts() -> None:
    harness = r"""
const assert = require('node:assert/strict');

const epoch = 'a'.repeat(32);
const otherEpoch = 'b'.repeat(32);
const eventId = (sequence, selectedEpoch = epoch) =>
  `evt_${selectedEpoch}_${BigInt(sequence).toString(16).padStart(16, '0')}`;
const ready = (selectedEpoch = epoch) => ({
  schemaVersion: 2,
  type: 'stream.ready',
  streamId: `stream_${selectedEpoch}`,
  generatedAt: '2026-07-20T00:00:00.000Z',
  retryMs: 2000,
});
const resource = (name, suffix = 'c', status = 'live') => ({
  name, status, revision: `rev_${suffix.repeat(64)}`,
});
const changed = (items = [resource('runs')]) => ({
  schemaVersion: 2,
  type: 'resources.changed',
  generatedAt: '2026-07-20T00:00:01.000Z',
  resources: items,
});
const reset = (reason = 'replay_unavailable') => ({
  schemaVersion: 2,
  type: 'stream.reset',
  generatedAt: '2026-07-20T00:00:02.000Z',
  reason,
  resources: [...CHANGE_RESOURCE_NAMES],
});
const encoded = (value) => JSON.stringify(value);

let validationChecks = 0;
const valid = (type, id, payload) => parseDashboardEvent(type, id, encoded(payload));
const invalidCases = [
  ['malformed JSON', 'stream.ready', eventId(0), '{'],
  ['array payload', 'stream.ready', eventId(0), '[]'],
  ['over 4 KiB before parse', 'stream.ready', eventId(0), ' '.repeat(4097)],
  ['boolean schema', 'stream.ready', eventId(0), encoded({...ready(), schemaVersion: true})],
  ['extra ready field', 'stream.ready', eventId(0), encoded({...ready(), secret: 'x'})],
  ['invalid timestamp', 'stream.ready', eventId(0), encoded({...ready(), generatedAt: 'not-time'})],
  ['non-canonical timestamp', 'stream.ready', eventId(0), encoded({...ready(), generatedAt: '2026-07-20'})],
  ['overlong timestamp', 'stream.ready', eventId(0), encoded({...ready(), generatedAt: '2'.repeat(65)})],
  ['malformed event id', 'stream.ready', 'evt_bad', encoded(ready())],
  ['stream epoch mismatch', 'stream.ready', eventId(0), encoded(ready(otherEpoch))],
  ['boolean retry', 'stream.ready', eventId(0), encoded({...ready(), retryMs: true})],
  ['empty change resources', 'resources.changed', eventId(1), encoded(changed([]))],
  ['too many change resources', 'resources.changed', eventId(1), encoded(changed([...CHANGE_RESOURCE_NAMES.map((name) => resource(name)), resource('runs')]))],
  ['duplicate resources', 'resources.changed', eventId(1), encoded(changed([resource('runs'), resource('runs')]))],
  ['wrong resource order', 'resources.changed', eventId(1), encoded(changed([resource('sessions'), resource('runs')]))],
  ['unknown resource', 'resources.changed', eventId(1), encoded(changed([resource('other')]))],
  ['unknown status', 'resources.changed', eventId(1), encoded(changed([resource('runs', 'c', 'online')]))],
  ['invalid revision', 'resources.changed', eventId(1), encoded(changed([{...resource('runs'), revision: 'rev_bad'}]))],
  ['extra resource field', 'resources.changed', eventId(1), encoded(changed([{...resource('runs'), value: 1}]))],
  ['unknown reset reason', 'stream.reset', eventId(1), encoded(reset('other'))],
  ['reset resource order', 'stream.reset', eventId(1), encoded({...reset(), resources: [...CHANGE_RESOURCE_NAMES].reverse()})],
  ['event/payload type mismatch', 'stream.ready', eventId(1), encoded(changed())],
];
for (const [name, type, id, data] of invalidCases) {
  assert.equal(parseDashboardEvent(type, id, data), null, name);
  validationChecks += 1;
}
assert.equal(valid('stream.ready', eventId(0), ready()).kind, 'ready');
validationChecks += 1;
assert.deepEqual(valid('resources.changed', eventId(1), changed()).resources, ['runs']);
validationChecks += 1;
assert.equal(valid('stream.reset', eventId(2), reset()).kind, 'reset');
validationChecks += 1;
assert.equal(validationChecks, 25);

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
let queueChecks = 0;
async function queueCheck(name, callback) {
  await callback();
  queueChecks += 1;
}

(async () => {
  await queueCheck('deduplicates one resource', async () => {
    const calls = [];
    const queue = createResourceRefreshQueue({refreshResources: async (names) => calls.push(names)});
    queue.enqueue(['runs', 'runs']);
    await queue.idle();
    assert.deepEqual(calls, [['runs']]);
  });
  await queueCheck('uses fixed resource order', async () => {
    const calls = [];
    const queue = createResourceRefreshQueue({refreshResources: async (names) => calls.push(names)});
    queue.enqueue(['connections', 'runs', 'memory']);
    await queue.idle();
    assert.deepEqual(calls, [['runs', 'memory', 'connections']]);
  });
  await queueCheck('ignores unknown resources', async () => {
    const calls = [];
    const queue = createResourceRefreshQueue({refreshResources: async (names) => calls.push(names)});
    queue.enqueue(['other']);
    await queue.idle();
    assert.deepEqual(calls, []);
  });
  await queueCheck('full overrides targeted pending work', async () => {
    const calls = [];
    const queue = createResourceRefreshQueue({refreshResources: async (names) => calls.push(names)});
    queue.enqueue(['runs']);
    queue.full();
    await queue.idle();
    assert.deepEqual(calls, [CHANGE_RESOURCE_NAMES]);
  });
  await queueCheck('keeps at most one refresh in flight', async () => {
    const gate = deferred();
    let active = 0;
    let maximum = 0;
    const queue = createResourceRefreshQueue({refreshResources: async () => {
      active += 1; maximum = Math.max(maximum, active); await gate.promise; active -= 1;
    }});
    queue.enqueue(['runs']);
    await flush();
    queue.enqueue(['sessions']);
    await flush();
    assert.equal(maximum, 1);
    gate.resolve();
    await queue.idle();
  });
  await queueCheck('retains events arriving during refresh', async () => {
    const gate = deferred();
    const calls = [];
    const queue = createResourceRefreshQueue({refreshResources: async (names) => {
      calls.push(names); if (calls.length === 1) await gate.promise;
    }});
    queue.enqueue(['runs']);
    await flush();
    queue.enqueue(['sessions']);
    gate.resolve();
    await queue.idle();
    assert.deepEqual(calls, [['runs'], ['sessions']]);
  });
  await queueCheck('failure does not brick later drains', async () => {
    let attempts = 0;
    let errors = 0;
    const queue = createResourceRefreshQueue({
      refreshResources: async () => { attempts += 1; if (attempts === 1) throw new Error('fail'); },
      onError: () => { errors += 1; },
    });
    queue.enqueue(['runs']);
    await queue.idle();
    queue.enqueue(['sessions']);
    await queue.idle();
    assert.equal(attempts, 2);
    assert.equal(errors, 1);
  });
  await queueCheck('stop drops scheduled work', async () => {
    const calls = [];
    const queue = createResourceRefreshQueue({refreshResources: async (names) => calls.push(names)});
    queue.enqueue(['runs']);
    queue.stop();
    await flush();
    assert.deepEqual(calls, []);
  });
  await queueCheck('stop fences work queued during an in-flight refresh', async () => {
    const gate = deferred();
    const calls = [];
    const queue = createResourceRefreshQueue({refreshResources: async (names) => {
      calls.push(names); await gate.promise;
    }});
    queue.enqueue(['runs']);
    await flush();
    queue.enqueue(['sessions']);
    queue.stop();
    gate.resolve();
    await queue.idle();
    assert.deepEqual(calls, [['runs']]);
  });
  await queueCheck('full means all seven authorities', async () => {
    const calls = [];
    const queue = createResourceRefreshQueue({refreshResources: async (names) => calls.push(names)});
    queue.full();
    await queue.idle();
    assert.deepEqual(calls[0], ['runs', 'sessions', 'turns', 'memory', 'skills', 'connections', 'permissions']);
  });
  assert.equal(queueChecks, 10);

  class FakeSource {
    constructor(url) { this.url = url; this.listeners = new Map(); this.closed = 0; this.readyState = 0; }
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    emit(name, payload, id) {
      const callback = this.listeners.get(name);
      if (callback) callback({data: encoded(payload), lastEventId: id});
    }
    open() { if (this.onopen) this.onopen({}); }
    error(readyState = 0) { this.readyState = readyState; if (this.onerror) this.onerror({}); }
    close() { this.readyState = 2; this.closed += 1; }
  }
  function controllerFixture({eventSourceAvailable = true} = {}) {
    let visible = true;
    let nextTimer = 0;
    const sources = [];
    const states = [];
    const timers = [];
    const queue = {
      enqueues: [], fulls: 0, stops: 0,
      enqueue(names) { this.enqueues.push([...names]); },
      full() { this.fulls += 1; },
      stop() { this.stops += 1; },
    };
    const poll = {
      starts: 0, stops: 0,
      start() { this.starts += 1; },
      stop() { this.stops += 1; },
    };
    const controller = createRealtimeRefreshController({
      createEventSource: () => {
        if (!eventSourceAvailable) throw new Error('unsupported');
        const source = new FakeSource('/api/v1/events');
        sources.push(source);
        return source;
      },
      pollingController: poll,
      refreshQueue: queue,
      isVisible: () => visible,
      schedule: (callback, delay) => {
        const timer = {id: ++nextTimer, callback, delay, cancelled: false, ran: false};
        timers.push(timer);
        return timer.id;
      },
      cancelSchedule: (id) => {
        const timer = timers.find((item) => item.id === id);
        if (timer) timer.cancelled = true;
      },
      onState: (value) => states.push({...value}),
      graceMs: 3000,
      rebuildDelayMs: 2000,
    });
    return {
      controller, sources, states, timers, queue, poll,
      setVisible(value) { visible = value; },
      runTimer(delay) {
        const timer = timers.find((item) => !item.cancelled && !item.ran && (delay === undefined || item.delay === delay));
        assert.ok(timer, `missing timer ${delay}`);
        timer.ran = true;
        timer.callback();
        return timer;
      },
    };
  }
  let controllerChecks = 0;
  function controllerCheck(name, callback) { callback(); controllerChecks += 1; }

  const startup = controllerFixture();
  startup.controller.start();
  controllerCheck('one initial EventSource', () => assert.equal(startup.sources.length, 1));
  controllerCheck('exact event URL', () => assert.equal(startup.sources[0].url, '/api/v1/events'));
  controllerCheck('startup connecting state', () => assert.equal(startup.states.at(-1).phase, 'connecting'));
  startup.sources[0].open();
  controllerCheck('first open waits for handshake', () => assert.equal(startup.poll.stops, 0));
  startup.runTimer(3000);
  controllerCheck('grace starts polling fallback', () => assert.equal(startup.poll.starts, 1));
  controllerCheck('fallback begins with full resync', () => assert.equal(startup.queue.fulls, 1));
  controllerCheck('grace retains same source', () => assert.equal(startup.sources[0].closed, 0));
  controllerCheck('grace publishes fallback', () => assert.equal(startup.states.at(-1).phase, 'polling'));

  startup.sources[0].emit('stream.ready', ready(), eventId(0));
  controllerCheck('ready stops polling', () => assert.equal(startup.poll.stops, 1));
  controllerCheck('ready performs full REST resync', () => assert.equal(startup.queue.fulls, 2));
  controllerCheck('ready publishes realtime', () => assert.equal(startup.states.at(-1).phase, 'realtime'));
  startup.sources[0].emit('resources.changed', changed([resource('runs')]), eventId(1));
  controllerCheck('changed targets REST authority', () => assert.deepEqual(startup.queue.enqueues, [['runs']]));
  startup.sources[0].emit('resources.changed', changed([resource('sessions')]), eventId(1));
  controllerCheck('duplicate id is ignored', () => assert.equal(startup.queue.enqueues.length, 1));
  startup.sources[0].emit('resources.changed', changed([resource('sessions')]), eventId(0));
  controllerCheck('stale id is ignored', () => assert.equal(startup.queue.enqueues.length, 1));
  startup.sources[0].emit('resources.changed', changed([resource('permissions')]), eventId(2));
  controllerCheck('permission invalidation targets the existing REST queue', () => assert.deepEqual(startup.queue.enqueues, [['runs'], ['permissions']]));
  startup.sources[0].emit('resources.changed', changed([resource('memory')]), eventId(4));
  controllerCheck('sequence gap becomes full resync', () => assert.equal(startup.queue.fulls, 3));
  startup.sources[0].emit('stream.reset', reset(), eventId(5, otherEpoch));
  controllerCheck('reset accepts new epoch and full resyncs', () => assert.equal(startup.queue.fulls, 4));
  controllerCheck('reset keeps EventSource', () => assert.equal(startup.sources[0].closed, 0));

  startup.sources[0].error();
  controllerCheck('native error starts polling', () => assert.equal(startup.poll.starts, 2));
  controllerCheck('native error retains one source', () => assert.equal(startup.sources.length, 1));
  controllerCheck('native error shows reconnecting', () => assert.equal(startup.states.at(-1).phase, 'reconnecting'));
  startup.sources[0].open();
  controllerCheck('reopen after handshake stops polling', () => assert.equal(startup.poll.stops, 2));
  controllerCheck('reopen returns realtime state', () => assert.equal(startup.states.at(-1).phase, 'realtime'));

  const closedNative = controllerFixture();
  closedNative.controller.start();
  const permanentlyClosed = closedNative.sources[0];
  permanentlyClosed.error(2);
  controllerCheck('permanently closed native source is released', () => assert.equal(permanentlyClosed.closed, 1));
  controllerCheck('permanently closed native source gets one bounded rebuild', () => {
    assert.equal(closedNative.timers.filter((item) => item.delay === 2000 && !item.cancelled).length, 1);
  });
  closedNative.runTimer(2000);
  controllerCheck('permanently closed source has one replacement', () => assert.equal(closedNative.sources.length, 2));

  const malformed = controllerFixture();
  malformed.controller.start();
  const old = malformed.sources[0];
  old.emit('stream.ready', {...ready(), secret: true}, eventId(0));
  controllerCheck('malformed event closes current source', () => assert.equal(old.closed, 1));
  controllerCheck('malformed event starts fallback', () => assert.equal(malformed.poll.starts, 1));
  controllerCheck('malformed event schedules bounded rebuild', () => assert.equal(malformed.timers.filter((item) => item.delay === 2000 && !item.cancelled).length, 1));
  malformed.runTimer(2000);
  controllerCheck('rebuild creates exactly one replacement', () => assert.equal(malformed.sources.length, 2));
  old.emit('resources.changed', changed([resource('skills')]), eventId(1));
  controllerCheck('stale source callbacks are fenced', () => assert.deepEqual(malformed.queue.enqueues, []));

  malformed.setVisible(false);
  malformed.controller.visibilityChanged();
  controllerCheck('hidden closes source', () => assert.equal(malformed.sources[1].closed, 1));
  controllerCheck('hidden stops polling', () => assert.ok(malformed.poll.stops >= 1));
  controllerCheck('hidden publishes paused', () => assert.equal(malformed.states.at(-1).phase, 'paused'));
  malformed.setVisible(true);
  malformed.controller.visibilityChanged();
  controllerCheck('visible creates one fresh source', () => assert.equal(malformed.sources.length, 3));

  const unsupported = controllerFixture({eventSourceAvailable: false});
  unsupported.controller.start();
  controllerCheck('missing EventSource falls back without throwing', () => assert.equal(unsupported.poll.starts, 1));

  const stopped = controllerFixture();
  stopped.controller.start();
  const stoppedSource = stopped.sources[0];
  stopped.controller.stop();
  controllerCheck('stop closes transports and queue', () => {
    assert.equal(stoppedSource.closed, 1); assert.equal(stopped.queue.stops, 1); assert.equal(stopped.poll.stops, 1);
  });
  stoppedSource.emit('stream.ready', ready(), eventId(0));
  controllerCheck('callbacks after stop are ignored', () => assert.equal(stopped.queue.fulls, 0));

  assert.ok(controllerChecks >= 30, `expected >=30 controller checks, got ${controllerChecks}`);
  console.log(`validation=${validationChecks} queue=${queueChecks} controller=${controllerChecks}`);
})().catch((error) => { console.error(error); process.exit(1); });
"""

    completed = subprocess.run(
        ["node", "-e", _realtime_source() + "\n" + harness],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "validation=25 queue=10 controller=" in completed.stdout
