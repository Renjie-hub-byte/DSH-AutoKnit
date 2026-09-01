import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const LOGIC = require(path.join(__dirname, '..', 'lib', 'logic.js'));

const read = (p) => readFileSync(path.join(__dirname, '..', p), 'utf8');

/* ------------------------------------------------------------------ *
 * buildBlockSummary — block-bottom aggregate metrics
 * ------------------------------------------------------------------ */
test('logic: buildBlockSummary derives token/totalMs/rounds from a module + timeline', () => {
  const m = { token_used: 150 };
  const s = LOGIC.buildBlockSummary(m, { rounds: [1, 2, 3], total: 3, totalMs: 25000 });
  assert.equal(s.token_used, 150);
  assert.equal(s.totalMs, 25000);
  assert.equal(s.rounds, 3);
});

test('logic: buildBlockSummary accepts a raw timeline payload (parses internally)', () => {
  const m = { token_used: 0 };
  const raw = {
    rounds: [
      { role: 'executor', duration_ms: 12000 },
      { role: 'auditor', duration_ms: 8000 }
    ]
  };
  const s = LOGIC.buildBlockSummary(m, raw);
  assert.equal(s.totalMs, 20000);
  assert.equal(s.rounds, 2);
});

test('logic: buildBlockSummary is safe with missing/empty inputs', () => {
  const empty = LOGIC.buildBlockSummary(null, null);
  assert.deepEqual(empty, { token_used: 0, totalMs: 0, rounds: 0 });
  assert.deepEqual(LOGIC.buildBlockSummary({}, []), { token_used: 0, totalMs: 0, rounds: 0 });
});

/* ------------------------------------------------------------------ *
 * buildModuleDetail — popover payload (reason / rounds / token / timing / split)
 * ------------------------------------------------------------------ */
test('logic: buildModuleDetail carries reason, round chain, token split, timing and split info', () => {
  const m = LOGIC.buildModuleView({
    id: 'm1',
    name: 'panel',
    status: 'needs_human',
    reason: '审计不通过，需人工决定',
    token_used: 120,
    started_at: '2026-08-29T10:00:00Z',
    ended_at: '2026-08-29T10:12:00Z',
    split: [
      { id: 'm1a', name: 'panel-a', status: 'done' },
      { id: 'm1b', name: 'panel-b', status: 'pending' }
    ]
  });
  const timeline = {
    rounds: [
      { role: 'executor', round: 1, verdict: 'ok', duration_ms: 5000 },
      { role: 'auditor', round: 1, verdict: 'revise', duration_ms: 4000 }
    ]
  };
  const usage = { total: 120, input: 60, output: 40, cache: 20 };
  const d = LOGIC.buildModuleDetail(m, timeline, usage);

  assert.equal(d.id, 'm1');
  assert.equal(d.status, 'needs_human');
  assert.equal(d.reason, '审计不通过，需人工决定');
  assert.equal(d.roundsTotal, 2);
  assert.equal(d.rounds[1].verdict, 'revise');
  assert.equal(d.durationMs, 9000);
  assert.equal(d.started_at, '2026-08-29T10:00:00Z');
  assert.equal(d.ended_at, '2026-08-29T10:12:00Z');
  assert.equal(d.splitCount, 2);
  assert.equal(d.split[0].id, 'm1a');
  assert.equal(d.split[1].status, 'pending');
  assert.equal(d.usage.total, 120);
  // 口径：输入含缓存 = 60 + 20
  assert.equal(d.usage.input, 80);
  assert.equal(d.usage.output, 40);
  assert.equal(d.usage.cache, 20);
  assert.equal(d.usage.hasSplit, true);
});

test('logic: buildModuleDetail handles empty timeline/usage and splitless modules', () => {
  const m = LOGIC.buildModuleView({ id: 'x', name: 'x', status: 'done' });
  const d = LOGIC.buildModuleDetail(m, null, {});
  assert.equal(d.reason, '');
  assert.equal(d.roundsTotal, 0);
  assert.equal(d.splitCount, 0);
  assert.equal(d.usage.hasSplit, false);
  assert.equal(d.usage.total, 0);
});

/* ------------------------------------------------------------------ *
 * normalizePoll / normalizeEvent — lightweight poll normalization
 * ------------------------------------------------------------------ */
test('logic: normalizePoll classifies a full tree payload as kind=full', () => {
  const p = LOGIC.normalizePoll({ run_id: 'r', modules: [{ id: 'a', status: 'done' }] });
  assert.equal(p.kind, 'full');
  assert.ok(p.tree);
  assert.deepEqual(p.events, []);
});

test('logic: normalizePoll classifies bare-array / {events} as kind=events', () => {
  const p1 = LOGIC.normalizePoll([{ module_id: 'a', status: 'running' }]);
  assert.equal(p1.kind, 'events');
  assert.equal(p1.events.length, 1);
  const p2 = LOGIC.normalizePoll({ events: [{ module_id: 'b' }] });
  assert.equal(p2.kind, 'events');
  assert.equal(p2.events[0].module_id, 'b');
});

test('logic: normalizePoll classifies anything else as kind=empty', () => {
  for (const v of [null, undefined, {}, { foo: 1 }]) {
    const p = LOGIC.normalizePoll(v);
    assert.equal(p.kind, 'empty');
    assert.equal(p.events.length, 0);
  }
});

test('logic: normalizeEvent accepts camelCase and snake_case, coerces numbers', () => {
  const snake = LOGIC.normalizeEvent({ module_id: 'm1', status: 'needs_human', token_used: '42' });
  assert.equal(snake.module_id, 'm1');
  assert.equal(snake.status, 'needs_human');
  assert.equal(snake.token_used, 42);
  const camel = LOGIC.normalizeEvent({ moduleId: 'm2', status: 'running' });
  assert.equal(camel.module_id, 'm2');
  const none = LOGIC.normalizeEvent(null);
  assert.equal(none.module_id, '');
});

/* ------------------------------------------------------------------ *
 * findModuleView / patchModule — immutable module lookup + patch
 * ------------------------------------------------------------------ */
function sampleRouteMap() {
  return LOGIC.buildRouteMap({
    modules: [
      {
        id: 'm0', status: 'done',
        split: [
          { id: 'm0a', status: 'running' },
          { id: 'm0b', status: 'pending' }
        ]
      },
      { id: 'm1', status: 'needs_human' }
    ]
  }, { total: 100 });
}

test('logic: findModuleView finds a top-level and a split child', () => {
  const rm = sampleRouteMap();
  assert.equal(LOGIC.findModuleView(rm, 'm1').status, 'needs_human');
  assert.equal(LOGIC.findModuleView(rm, 'm0a').status, 'running');
  assert.equal(LOGIC.findModuleView(rm, 'missing'), null);
});

test('logic: patchModule patches a top-level module and is immutable', () => {
  const rm = sampleRouteMap();
  const next = LOGIC.patchModule(rm, 'm1', { status: 'running', reason: '决策提交' });
  assert.equal(next.layers.flat().find((m) => m.id === 'm1').status, 'running');
  assert.equal(next.layers.flat().find((m) => m.id === 'm1').reason, '决策提交');
  // source untouched
  assert.equal(rm.layers.flat().find((m) => m.id === 'm1').status, 'needs_human');
  assert.ok(next !== rm);
});

test('logic: patchModule patches a split child without breaking siblings', () => {
  const rm = sampleRouteMap();
  const next = LOGIC.patchModule(rm, 'm0b', { status: 'running' });
  const m0 = next.layers.flat().find((m) => m.id === 'm0');
  assert.equal(m0.split.find((c) => c.id === 'm0b').status, 'running');
  assert.equal(m0.split.find((c) => c.id === 'm0a').status, 'running');
  // source intact
  assert.equal(rm.layers.flat().find((m) => m.id === 'm0').split.find((c) => c.id === 'm0b').status, 'pending');
});

test('logic: patchModule returns the same object for an unknown id', () => {
  const rm = sampleRouteMap();
  assert.equal(LOGIC.patchModule(rm, 'nope', { status: 'done' }), rm);
});

/* ------------------------------------------------------------------ *
 * applyPoll — full rebuild / incremental events / empty no-op
 * ------------------------------------------------------------------ */
test('logic: applyPoll rebuilds the route map for a full-tree poll', () => {
  const prev = sampleRouteMap();
  const tree = { modules: [{ id: 'only', status: 'done' }] };
  const next = LOGIC.applyPoll(prev, tree, { usage: { total: 7 } });
  assert.equal(next.layers.flat().length, 1);
  assert.equal(next.layers.flat()[0].id, 'only');
  assert.equal(next.usage.total, 7);
});

test('logic: applyPoll applies incremental events as patches', () => {
  const rm = sampleRouteMap();
  const next = LOGIC.applyPoll(rm, {
    events: [
      { module_id: 'm1', status: 'running' },
      { module_id: 'm0a', status: 'done' }
    ]
  });
  assert.equal(next.layers.flat().find((m) => m.id === 'm1').status, 'running');
  const m0 = next.layers.flat().find((m) => m.id === 'm0');
  assert.equal(m0.split.find((c) => c.id === 'm0a').status, 'done');
  // source untouched
  assert.equal(rm.layers.flat().find((m) => m.id === 'm1').status, 'needs_human');
});

test('logic: applyPoll is a no-op for an empty poll', () => {
  const rm = sampleRouteMap();
  assert.equal(LOGIC.applyPoll(rm, null), rm);
  assert.equal(LOGIC.applyPoll(rm, {}), rm);
});

/* ------------------------------------------------------------------ *
 * client.js source markers for the detail popover + polling
 * ------------------------------------------------------------------ */
test('client.js renders detail popover + block summary + lightweight polling', () => {
  const client = read('lib/client.js');
  for (const marker of [
    "logic.buildBlockSummary",
    "logic.buildModuleDetail",
    "logic.findModuleView",
    "data-ak-popover",
    "data-ak-detail-open",
    "data-ak-summary",
    "logic.normalizePoll",
    "logic.applyPoll",
    "setInterval",
    "POLL_INTERVAL_MS",
    "client.timeline("
  ]) {
    assert.ok(client.includes(marker), `client.js must contain ${marker}`);
  }
});

test('styles: detail popover + block summary + detail button present in both style sources', () => {
  const styleJs = read('lib/style.js');
  const css = read('lib/styles.css');
  for (const sel of ['.ak-popover', '.ak-block-summary', '.ak-detail-btn', '.ak-module-head-actions']) {
    assert.ok(styleJs.includes(sel), `style.js must contain ${sel}`);
    assert.ok(css.includes(sel), `styles.css must contain ${sel}`);
  }
  // CSS animation requirement stays intact (no JS polling animation introduced).
  assert.ok(styleJs.includes('@keyframes ak-border-spin'), 'style.js must keep ak-border-spin');
  assert.ok(css.includes('@keyframes ak-border-spin'), 'styles.css must keep ak-border-spin');
});
