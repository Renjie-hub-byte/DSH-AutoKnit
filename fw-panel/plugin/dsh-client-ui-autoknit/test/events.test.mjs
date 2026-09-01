import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const LOGIC = require(path.join(__dirname, '..', 'lib', 'logic.js'));
const BRIDGE = require(path.join(__dirname, '..', 'lib', 'data-bridge.js'));

const read = (p) => readFileSync(path.join(__dirname, '..', p), 'utf8');

function sampleSelector() {
  return {
    runs: [
      { run_id: 'run-1', status: 'active', started_at: '2026-08-29T09:00:00Z' },
      { run_id: 'run-2', status: 'complete', started_at: '2026-08-29T08:00:00Z' }
    ],
    selected: 'run-1'
  };
}

/* ------------------------------------------------------------------ *
 * normalizeRunEvent — snake_case/camelCase + carried run record
 * ------------------------------------------------------------------ */
test('logic: normalizeRunEvent maps snake/camel run_id + carries the run record', () => {
  const ev = LOGIC.normalizeRunEvent({
    type: 'run.start',
    run_id: 'run-9',
    run: { runId: 'run-9', status: 'active', started_at: '2026-08-29T12:00:00Z' },
    at: '2026-08-29T12:00:00Z'
  });
  assert.equal(ev.type, 'run.start');
  assert.equal(ev.run_id, 'run-9');
  assert.equal(ev.run.run_id, 'run-9');
  assert.equal(ev.run.status, 'active');

  const camel = LOGIC.normalizeRunEvent({ type: 'task.update', runId: 'r-x' });
  assert.equal(camel.run_id, 'r-x');
  assert.equal(camel.run.run_id, '');
  // missing → empty patch, no throw
  assert.deepEqual(LOGIC.normalizeRunEvent(null).run_id, '');
});

/* ------------------------------------------------------------------ *
 * extractRunEvents — bare array / {events} / cursor plumbing
 * ------------------------------------------------------------------ */
test('logic: extractRunEvents handles bare array and {events} forms', () => {
  const bare = LOGIC.extractRunEvents([{ type: 'run.start', run_id: 'a' }]);
  assert.equal(bare.events.length, 1);
  assert.equal(bare.events[0].run_id, 'a');
  assert.equal(bare.cursor, null);

  const wrapped = LOGIC.extractRunEvents({ events: [{ type: 'task.update', run_id: 'b' }], next: 42 });
  assert.equal(wrapped.events.length, 1);
  assert.equal(wrapped.events[0].run_id, 'b');
  assert.equal(wrapped.cursor, 42);
});

test('logic: extractRunEvents prefers next > cursor > since for the cursor', () => {
  assert.equal(LOGIC.extractRunEvents({ events: [], next: 3, since: 1 }).cursor, 3);
  assert.equal(LOGIC.extractRunEvents({ events: [], cursor: 2, since: 1 }).cursor, 2);
  assert.equal(LOGIC.extractRunEvents({ events: [], since: 7 }).cursor, 7);
  // non-array/non-events payload → empty events, null cursor
  const empty = LOGIC.extractRunEvents({ foo: 1 });
  assert.deepEqual(empty.events, []);
  assert.equal(empty.cursor, null);
});

/* ------------------------------------------------------------------ *
 * followRunIdFromEvents — auto-follow switch decision
 * ------------------------------------------------------------------ */
test('logic: followRunIdFromEvents returns the first run.start run_id', () => {
  const id = LOGIC.followRunIdFromEvents([
    { type: 'task.update', run_id: 'r1' },
    { type: 'run.start', run_id: 'r2' }
  ]);
  assert.equal(id, 'r2');
});

test('logic: followRunIdFromEvents returns null when no run.start present', () => {
  assert.equal(LOGIC.followRunIdFromEvents([{ type: 'task.update', run_id: 'r1' }]), null);
  assert.equal(LOGIC.followRunIdFromEvents([]), null);
  assert.equal(LOGIC.followRunIdFromEvents(null), null);
});

/* ------------------------------------------------------------------ *
 * reduceRunEvents — the pure event→selector reducer
 * ------------------------------------------------------------------ */
test('logic: reduceRunEvents auto-follows a run.start, upserts it active-first and marks refresh', () => {
  const next = LOGIC.reduceRunEvents(sampleSelector(), [
    { type: 'run.start', run_id: 'run-3', run: { run_id: 'run-3', status: 'active', started_at: '2026-08-29T13:00:00Z' } }
  ]);
  assert.equal(next.selected, 'run-3');
  assert.equal(next.followedNewRun, true);
  // run-3 is active + newest → first in the selector
  assert.deepEqual(next.runs.map((r) => r.run_id), ['run-3', 'run-1', 'run-2']);
  assert.deepEqual(next.refreshRunIds, ['run-3']);
});

test('logic: reduceRunEvents synthesizes a minimal active run when the record is absent', () => {
  const next = LOGIC.reduceRunEvents(sampleSelector(), [
    { type: 'run.start', run_id: 'run-9', at: '2026-08-29T14:00:00Z' }
  ]);
  assert.equal(next.selected, 'run-9');
  const r9 = next.runs.find((r) => r.run_id === 'run-9');
  assert.equal(r9.status, 'active');
  assert.equal(r9.started_at, '2026-08-29T14:00:00Z');
});

test('logic: reduceRunEvents refreshes the followed run on task.update, leaves selector unchanged', () => {
  const next = LOGIC.reduceRunEvents(sampleSelector(), [
    { type: 'task.update', run_id: 'run-1', module_id: 'm1', status: 'running' }
  ]);
  assert.equal(next.selected, 'run-1');
  assert.equal(next.followedNewRun, false);
  assert.deepEqual(next.refreshRunIds, ['run-1']);
  assert.deepEqual(next.runs.map((r) => r.run_id), ['run-1', 'run-2']);
});

test('logic: reduceRunEvents ignores task.update for a non-followed run', () => {
  const next = LOGIC.reduceRunEvents(sampleSelector(), [
    { type: 'task.update', run_id: 'run-2', status: 'done' }
  ]);
  assert.equal(next.selected, 'run-1');
  assert.deepEqual(next.refreshRunIds, []);
});

test('logic: reduceRunEvents is immutable (source runs/events untouched)', () => {
  const src = sampleSelector();
  const before = JSON.stringify(src);
  const events = [{ type: 'run.start', run_id: 'run-9' }];
  const eventsBefore = JSON.stringify(events);
  LOGIC.reduceRunEvents(src, events);
  assert.equal(JSON.stringify(src), before);
  assert.equal(JSON.stringify(events), eventsBefore);
});

/* ------------------------------------------------------------------ *
 * data-bridge events(since) — GET /api/events (+ since cursor query)
 * ------------------------------------------------------------------ */
function fakeFetch(router) {
  return function (url, init) {
    const method = (init && init.method) || 'GET';
    const entry = router[method + ' ' + url];
    if (!entry) {
      return Promise.resolve({ ok: false, status: 404, text: () => Promise.resolve('not found') });
    }
    return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(entry)) });
  };
}

test('data-bridge: events() hits GET /api/events and appends ?since= cursor', async () => {
  const seen = [];
  const client = BRIDGE.createClient({
    fetch: function (url, init) {
      seen.push((init && init.method || 'GET') + ' ' + url);
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ events: [], next: 5 })) });
    }
  });
  await client.events();
  await client.events(7);
  assert.deepEqual(seen, ['GET /api/events', 'GET /api/events?since=7']);
});

test('data-bridge: events() returns the parsed events payload', async () => {
  const payload = { events: [{ type: 'run.start', run_id: 'r1' }], next: 9 };
  const client = BRIDGE.createClient({ fetch: fakeFetch({ 'GET /api/events?since=1': payload }) });
  const res = await client.events(1);
  assert.equal(res.next, 9);
  assert.equal(res.events[0].run_id, 'r1');
});

/* ------------------------------------------------------------------ *
 * client.js source markers for the event-driven refresh
 * ------------------------------------------------------------------ */
test('client.js wires event-driven refresh (events/extract/reduce/fallback polling)', () => {
  const client = read('lib/client.js');
  for (const marker of [
    'client.events(',
    'logic.extractRunEvents',
    'logic.reduceRunEvents',
    'logic.normalizePoll',
    'logic.applyPoll',
    'eventCursor',
    'eventsEnabled',
    'POLL_INTERVAL_MS',
    'setInterval',
    'refreshTree',
    'pollTree'
  ]) {
    assert.ok(client.includes(marker), `client.js must contain ${marker}`);
  }
});

test('logic: reduceRunEvents does not steal follow from a known (historical) run.start', () => {
  // run-1 is already in the selector (selected). A run.start for it (arriving on
  // the first full /api/events pull) must NOT switch follow away / re-trigger
  // refresh — the panel keeps the default selection.
  const next = LOGIC.reduceRunEvents(sampleSelector(), [
    { type: 'run.start', run_id: 'run-1', run: { run_id: 'run-1', status: 'active', started_at: '2026-08-29T09:00:00Z' } }
  ]);
  assert.equal(next.selected, 'run-1');
  assert.equal(next.followedNewRun, false);
  assert.deepEqual(next.refreshRunIds, []);
  // re-upserted: run-1 still present active-first
  assert.equal(next.runs[0].run_id, 'run-1');
});

test('logic: reduceRunEvents ignores a run.start for a known run even when another is selected', () => {
  const state = { runs: [
    { run_id: 'run-1', status: 'active', started_at: '2026-08-29T09:00:00Z' },
    { run_id: 'run-2', status: 'complete', started_at: '2026-08-29T08:00:00Z' }
  ], selected: 'run-2' };
  const next = LOGIC.reduceRunEvents(state, [
    { type: 'run.start', run_id: 'run-1', run: { run_id: 'run-1', status: 'active', started_at: '2026-08-29T09:00:00Z' } }
  ]);
  // selection preserved (run-2), no follow switch, no refresh
  assert.equal(next.selected, 'run-2');
  assert.equal(next.followedNewRun, false);
  assert.deepEqual(next.refreshRunIds, []);
});

test('logic: reduceRunEvents removes an archived run and clears its selection', () => {
  const next = LOGIC.reduceRunEvents(sampleSelector(), [
    { type: 'run.archived', run_id: 'run-1', status: 'archived' }
  ]);
  // run-1 从列表移除，selected 清空（归档不该残留）
  assert.deepEqual(next.runs.map((r) => r.run_id), ['run-2']);
  assert.equal(next.selected, null);
  assert.equal(next.followedNewRun, false);
});

test('logic: reduceRunEvents archived-run followed by run.start does not resurrect it', () => {
  // 归档后的首次全量：run.start（历史 complete，knownBefore=false 会 upsert）
  // + run.archived 紧跟 → 净效果仍移除（回归：归档完还会出来）。
  const next = LOGIC.reduceRunEvents(sampleSelector(), [
    { type: 'run.start', run_id: 'run-1', run: { run_id: 'run-1', status: 'complete', started_at: '2026-08-29T09:00:00Z' } },
    { type: 'run.archived', run_id: 'run-1', status: 'archived' }
  ]);
  assert.deepEqual(next.runs.map((r) => r.run_id), ['run-2']);
});
