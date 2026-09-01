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
 * Multi-run selector: order runs active-first, latest active on top,
 * archived runs dropped from the selector.
 * ------------------------------------------------------------------ */
function sampleRuns() {
  return [
    { run_id: 'run-1', task: '面板', status: 'complete', started_at: '2026-08-29T08:00:00Z' },
    { run_id: 'run-2', task: '桥', status: 'active', started_at: '2026-08-29T10:00:00Z' },
    { run_id: 'run-3', task: '管道', status: 'active', started_at: '2026-08-29T09:00:00Z' },
    { run_id: 'run-4', task: '归档旧', status: 'archived', started_at: '2026-08-29T07:00:00Z' }
  ];
}

test('logic: normalizeRun maps contract columns and collapses unknown status to active', () => {
  const r = LOGIC.normalizeRun({ run_id: 'r1', task: 'x', status: 'active', started_at: '2026-08-29T10:00:00Z' });
  assert.equal(r.run_id, 'r1');
  assert.equal(r.task, 'x');
  assert.equal(r.status, 'active');
  // camelCase run_id + unknown status → active
  const c = LOGIC.normalizeRun({ runId: 'r2', status: 'bogus' });
  assert.equal(c.run_id, 'r2');
  assert.equal(c.status, 'active');
  // missing → default active
  assert.equal(LOGIC.normalizeRun({}).status, 'active');
});

test('logic: orderRunsActiveFirst lists active runs first (latest active on top) and drops archived', () => {
  const ordered = LOGIC.orderRunsActiveFirst(sampleRuns());
  const ids = ordered.map((r) => r.run_id);
  // active first: run-2 (newest) then run-3, then complete run-1; archived run-4 gone
  assert.deepEqual(ids, ['run-2', 'run-3', 'run-1']);
  assert.ok(ordered.every((r) => r.status !== 'archived'));
});

test('logic: orderRunsActiveFirst sorts newest-first within each status group', () => {
  const ordered = LOGIC.orderRunsActiveFirst([
    { run_id: 'a', status: 'active', started_at: '2026-08-29T08:00:00Z' },
    { run_id: 'b', status: 'active', started_at: '2026-08-29T11:00:00Z' },
    { run_id: 'c', status: 'complete', started_at: '2026-08-29T12:00:00Z' }
  ]);
  assert.deepEqual(ordered.map((r) => r.run_id), ['b', 'a', 'c']);
});

test('logic: pickLatestActive returns the active run with the newest started_at', () => {
  const latest = LOGIC.pickLatestActive(sampleRuns());
  assert.equal(latest.run_id, 'run-2');
});

test('logic: pickLatestActive returns null when no active run exists', () => {
  assert.equal(LOGIC.pickLatestActive([{ run_id: 'x', status: 'complete' }]), null);
  assert.equal(LOGIC.pickLatestActive([]), null);
  assert.equal(LOGIC.pickLatestActive([{ run_id: 'y', status: 'archived' }]), null);
});

test('logic: pickLatestActive ignores runs missing started_at (sorts last)', () => {
  const latest = LOGIC.pickLatestActive([
    { run_id: 'old', status: 'active', started_at: '2026-08-29T08:00:00Z' },
    { run_id: 'noda', status: 'active' }
  ]);
  assert.equal(latest.run_id, 'old');
});

/* ------------------------------------------------------------------ *
 * Archive reducer: remove the run from the selector, clear selection
 * when the archived run was the one being viewed. Fully immutable.
 * ------------------------------------------------------------------ */
test('logic: reduceArchiveRun removes a run from the selector list', () => {
  const next = LOGIC.reduceArchiveRun(
    { runs: sampleRuns(), selected: 'run-3' },
    'run-3'
  );
  assert.ok(!next.runs.some((r) => r.run_id === 'run-3'));
  assert.equal(next.runs.length, sampleRuns().length - 1);
  // unrelated runs kept
  assert.ok(next.runs.some((r) => r.run_id === 'run-2'));
});

test('logic: reduceArchiveRun clears selected when the archived run was selected', () => {
  const next = LOGIC.reduceArchiveRun({ runs: sampleRuns(), selected: 'run-2' }, 'run-2');
  assert.equal(next.selected, null);
});

test('logic: reduceArchiveRun keeps selected when archiving an unrelated run', () => {
  const next = LOGIC.reduceArchiveRun({ runs: sampleRuns(), selected: 'run-2' }, 'run-1');
  assert.equal(next.selected, 'run-2');
  assert.ok(!next.runs.some((r) => r.run_id === 'run-1'));
});

test('logic: reduceArchiveRun is immutable (source runs array untouched)', () => {
  const src = sampleRuns();
  const before = JSON.stringify(src);
  LOGIC.reduceArchiveRun({ runs: src, selected: 'run-2' }, 'run-2');
  assert.equal(JSON.stringify(src), before);
});

/* ------------------------------------------------------------------ *
 * client.js source markers for the multi-run selector + archive button.
 * ------------------------------------------------------------------ */
test('client.js renders multi-run selector (active-first) with an archive button per active run', () => {
  const client = read('lib/client.js');
  for (const marker of [
    'logic.orderRunsActiveFirst',
    'logic.pickLatestActive',
    'logic.reduceArchiveRun',
    'handleArchive',
    'data-ak-archive',
    'ak-run-select',
    'ak-run-archive',
    'client.archive('
  ]) {
    assert.ok(client.includes(marker), `client.js must contain ${marker}`);
  }
});

test('styles: multi-run selector + archive button present in both style sources', () => {
  const styleJs = read('lib/style.js');
  const css = read('lib/styles.css');
  for (const sel of ['.ak-run-pill', '.ak-run-select', '.ak-run-archive', '.ak-run-pill-active']) {
    assert.ok(styleJs.includes(sel), `style.js must contain ${sel}`);
    assert.ok(css.includes(sel), `styles.css must contain ${sel}`);
  }
});
