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
 * parseDispatch — normalize dispatch.jsonl (by seq) with E/A order kept
 * ------------------------------------------------------------------ */
test('logic: parseDispatch orders records by seq and preserves E→A order', () => {
  const raw = {
    rounds: [
      { seq: 3, role: 'auditor', module_id: 'm1', round: 1, verdict: 'ok', duration_ms: 8000 },
      { seq: 1, role: 'executor', module_id: 'm1', round: 1, verdict: 'ok', duration_ms: 12000 },
      { seq: 2, role: 'auditor', module_id: 'root', round: 1, verdict: 'ok' }
    ]
  };
  const d = LOGIC.parseDispatch(raw);
  assert.equal(d.length, 3);
  assert.deepEqual(d.map((r) => r.seq), [1, 2, 3], 'must be ordered by seq');
  assert.equal(d[0].role, 'executor');
  assert.equal(d[0].module_id, 'm1');
  assert.equal(d[2].role, 'auditor');
  assert.equal(d[2].module_id, 'm1');
});

test('logic: parseDispatch accepts bare arrays and alternative object fields', () => {
  const bare = LOGIC.parseDispatch([
    { role: 'executor', module: 'a' },
    { role: 'auditor', moduleId: 'a' }
  ]);
  assert.equal(bare.length, 2);
  assert.equal(bare[0].module_id, 'a');
  assert.equal(bare[1].module_id, 'a');
  const viaEvents = LOGIC.parseDispatch({ events: [{ seq: 5, role: 'split', module_id: 'm' }] });
  assert.equal(viaEvents.length, 1);
  assert.equal(viaEvents[0].role, 'split');
  assert.equal(LOGIC.parseDispatch(null).length, 0);
  assert.equal(LOGIC.parseDispatch({}).length, 0);
});

test('logic: parseDispatch normalizes round numbers and guards durations', () => {
  const d = LOGIC.parseDispatch([{ round: 'abc', duration_ms: -5, role: 'executor' }]);
  assert.equal(d[0].round, null);
  assert.equal(d[0].duration_ms, 0);
});

/* ------------------------------------------------------------------ *
 * fork detection
 * ------------------------------------------------------------------ */
test('logic: isSplitDispatch detects split/fork markers', () => {
  assert.equal(LOGIC.isSplitDispatch({ role: 'split' }), true);
  assert.equal(LOGIC.isSplitDispatch({ role: 'fork' }), true);
  assert.equal(LOGIC.isSplitDispatch({ role: 'executor' }), false);
  assert.equal(LOGIC.isSplitDispatch({ role: 'SPLIT' }), true);
  assert.equal(LOGIC.isSplitDispatch(null), false);
});

/* ------------------------------------------------------------------ *
 * buildPipelineChain — planner/root → module blocks → fork + recursion
 * ------------------------------------------------------------------ */
function sampleDispatch() {
  // seq order mirrors dispatch.jsonl: root E/A, then m1 E/A, split, sub E/A.
  return [
    { seq: 1, module_id: 'root', role: 'executor', round: 1, verdict: 'ok', duration_ms: 1000 },
    { seq: 2, module_id: 'root', role: 'auditor', round: 1, verdict: 'ok', duration_ms: 900 },
    { seq: 3, module_id: 'm1', role: 'executor', round: 1, verdict: 'ok', duration_ms: 5000 },
    { seq: 4, module_id: 'm1', role: 'auditor', round: 1, verdict: 'revise', duration_ms: 4000 },
    { seq: 5, module_id: 'm1', role: 'split' },
    { seq: 6, module_id: 'm1a', role: 'executor', round: 1, verdict: 'ok', duration_ms: 3000 }
  ];
}

function sampleTree() {
  return {
    run: { run_id: 'run-1' },
    modules: [
      { id: 'root', name: 'orchestrator', status: 'done', dependencies: [] },
      {
        id: 'm1', name: 'panel', status: 'needs_human', dependencies: ['root'], token_used: 120,
        split: [
          { id: 'm1a', name: 'panel-a', status: 'done' },
          { id: 'm1b', name: 'panel-b', status: 'pending' }
        ]
      }
    ]
  };
}

test('logic: buildPipelineChain places root on the left and chains after it', () => {
  const p = LOGIC.buildPipelineChain(sampleTree(), sampleDispatch());
  assert.equal(p.run_id, 'run-1');
  assert.ok(p.root, 'root must exist');
  assert.equal(p.root.id, 'root');
  assert.equal(p.root.status, 'done');
  // m1 is the top-level chain (root not repeated in chains).
  assert.equal(p.chains.length, 1);
  assert.equal(p.chains[0].id, 'm1');
});

test('logic: buildPipelineChain keeps E/A round-card order per module', () => {
  const p = LOGIC.buildPipelineChain(sampleTree(), sampleDispatch());
  const m1 = p.chains[0];
  assert.deepEqual(m1.rounds.map((r) => r.role), ['executor', 'auditor']);
  assert.equal(m1.rounds[0].verdict, 'ok');
  assert.equal(m1.rounds[1].verdict, 'revise');
  assert.equal(m1.rounds[1].duration_ms, 4000);
  // the split record is not surfaced as a round card.
  assert.equal(m1.rounds.some((r) => r.role === 'split'), false);
});

test('logic: buildPipelineChain detects the split fork and recurses into submodules', () => {
  const p = LOGIC.buildPipelineChain(sampleTree(), sampleDispatch());
  const m1 = p.chains[0];
  assert.ok(m1.fork, 'm1 must have a fork (split)');
  assert.equal(m1.fork.splitCount, 2);
  assert.equal(m1.fork.children.length, 2);
  const a = m1.fork.children[0];
  const b = m1.fork.children[1];
  assert.equal(a.id, 'm1a');
  assert.equal(a.status, 'done');
  // recursion attribution: m1a has its own executor round.
  assert.equal(a.rounds[0].role, 'executor');
  assert.equal(a.rounds[0].module_id, undefined); // rounds are already trimmed
  assert.equal(b.id, 'm1b');
  assert.equal(b.status, 'pending');
  // submodules carry no further fork.
  assert.equal(a.fork, null);
});

test('logic: buildPipelineChain handles a splitless tree and explicit tree.root', () => {
  const p = LOGIC.buildPipelineChain(
    { root: { id: 'planner', name: 'P' }, modules: [{ id: 'm0', status: 'done', dependencies: ['planner'] }] },
    [{ seq: 1, module_id: 'planner', role: 'executor' }]
  );
  assert.equal(p.root.id, 'planner');
  assert.equal(p.chains.length, 1);
  assert.equal(p.chains[0].id, 'm0');
});

test('logic: buildPipelineChain is safe with empty inputs', () => {
  const p = LOGIC.buildPipelineChain({}, null);
  assert.equal(p.root, null);
  assert.deepEqual(p.chains, []);
  assert.equal(p.dispatchTotal, 0);
});

/* ------------------------------------------------------------------ *
 * client.js renders the explicit recursive chain + keeps legacy markers
 * ------------------------------------------------------------------ */
test('client.js renders the explicit recursive pipeline chain', () => {
  const client = read('lib/client.js');
  for (const marker of [
    "logic.buildPipelineChain",
    "setPipeline",
    "renderPipeline",
    "renderPipelineModule",
    "renderRoundCards",
    "renderFork",
    "'data-ak-pipeline'",
    "'data-ak-round-role'",
    "'data-ak-fork'",
    "'data-ak-recursion'",
    "ak-module-root"
  ]) {
    assert.ok(client.includes(marker), `client.js must contain ${marker}`);
  }
});

test('styles: pipeline round cards / fork / recursion present in both style sources', () => {
  const styleJs = read('lib/style.js');
  const css = read('lib/styles.css');
  for (const sel of ['.ak-pipeline', '.ak-round-card', '.ak-fork', '.ak-recursion', '.ak-module-root']) {
    assert.ok(styleJs.includes(sel), `style.js must contain ${sel}`);
    assert.ok(css.includes(sel), `styles.css must contain ${sel}`);
  }
});

/* ------------------------------------------------------------------ *
 * Final-block polish helpers — roundKind / isActiveChain / recursion stats
 * ------------------------------------------------------------------ */
test('logic: roundKind normalizes roles to stable accent kinds', () => {
  assert.equal(LOGIC.roundKind('executor'), 'executor');
  assert.equal(LOGIC.roundKind('auditor'), 'auditor');
  assert.equal(LOGIC.roundKind('split'), 'split');
  assert.equal(LOGIC.roundKind('fork'), 'split');
  assert.equal(LOGIC.roundKind('EXECUTOR'), 'executor');
  assert.equal(LOGIC.roundKind('mystery'), 'other');
  assert.equal(LOGIC.roundKind(''), 'other');
  assert.equal(LOGIC.roundKind(null), 'other');
  assert.deepEqual(LOGIC.ROLE_KINDS, ['executor', 'auditor', 'split', 'other']);
});

test('logic: isActiveChain detects an active top-level and any active fork descendant', () => {
  const activeTop = { id: 'a', status: 'running', fork: null };
  const activeChild = {
    id: 'p', status: 'done',
    fork: { splitCount: 2, children: [
      { id: 'p1', status: 'pending', fork: null },
      { id: 'p2', status: 'needs_human', fork: null }
    ] }
  };
  const deepActive = {
    id: 'q', status: 'done',
    fork: { children: [
      { id: 'q1', status: 'done', fork: { children: [{ id: 'q1a', status: 'running', fork: null }] } }
    ] }
  };
  const allDone = { id: 'r', status: 'done', fork: { children: [{ id: 'r1', status: 'pending', fork: null }] } };
  assert.equal(LOGIC.isActiveChain(activeTop), true);
  assert.equal(LOGIC.isActiveChain(activeChild), true, 'active fork child must mark the subtree active');
  assert.equal(LOGIC.isActiveChain(deepActive), true, 'deep recursion active must propagate');
  assert.equal(LOGIC.isActiveChain(allDone), false);
  assert.equal(LOGIC.isActiveChain(null), false);
});

test('logic: countSubmodules counts deeply nested split children recursively', () => {
  const flat = { id: 'x', fork: { children: [{ id: 'a' }, { id: 'b' }] } };
  const nested = {
    id: 'x', fork: { children: [
      { id: 'a' },
      { id: 'b', fork: { children: [{ id: 'b1' }, { id: 'b2', fork: { children: [{ id: 'b2a' }] } }] } }
    ] }
  };
  assert.equal(LOGIC.countSubmodules(flat), 2);
  assert.equal(LOGIC.countSubmodules(nested), 5); // a + (b1 + b2 + b2a + b) = 1+3+1
  assert.equal(LOGIC.countSubmodules({ id: 'nofork' }), 0);
  assert.equal(LOGIC.countSubmodules(null), 0);
});

test('logic: chainDepth measures recursion depth and flattenPipeline preserves reading order', () => {
  const chain = {
    id: 'm', fork: { children: [
      { id: 'm1', fork: null },
      { id: 'm2', fork: { children: [{ id: 'm2a' }] } }
    ] }
  };
  assert.equal(LOGIC.chainDepth(chain), 2);
  assert.equal(LOGIC.chainDepth({ id: 'leaf' }), 0);

  const flat = LOGIC.flattenChain(chain, 0);
  assert.deepEqual(flat.map((e) => e.chain.id), ['m', 'm1', 'm2', 'm2a']);
  assert.deepEqual(flat.map((e) => e.depth), [0, 1, 1, 2]);

  const pipeline = LOGIC.buildPipelineChain(
    { root: { id: 'root' }, modules: [{ id: 'root' }, { id: 't', status: 'done' }] },
    []
  );
  const all = LOGIC.flattenPipeline(pipeline);
  assert.ok(all.some((e) => e.chain.id === 'root'), 'root must be flattened');
  assert.ok(all.some((e) => e.chain.id === 't'), 'top-level chains must be flattened');
  assert.equal(LOGIC.flattenPipeline(null).length, 0);
});

/* ------------------------------------------------------------------ *
 * Final-block 对拍 fixture: a real phase-2 run with a split (m03→m03a/b)
 * renders planner → module → [E→A] chain + split fork + submodule recursion.
 * ------------------------------------------------------------------ */
function realSplitRunTree() {
  // Mirrors a phase-2 tree: planner root + module m03 which split into m03a /
  // m03b, and m03a itself split further into m03a1 (deep recursion).
  return {
    run: { run_id: 'run-2026-08-29-01' },
    modules: [
      { id: 'planner', name: 'orchestrator', status: 'done', dependencies: [], token_used: 40 },
      {
        id: 'm03', name: '面板-递归管道布局', status: 'running', dependencies: ['planner'], token_used: 180,
        split: [
          {
            id: 'm03a', name: '递归链样式', status: 'running',
            split: [{ id: 'm03a1', name: '分叉连线', status: 'pending' }]
          },
          { id: 'm03b', name: '渲染对拍', status: 'pending' }
        ]
      }
    ]
  };
}

function realSplitRunTimeline() {
  // dispatch.jsonl by seq: planner E/A → m03 E/A → split → m03a E/A → split → m03a1 E/A.
  return [
    { seq: 1, module_id: 'planner', role: 'executor', round: 1, verdict: 'ok', duration_ms: 1200 },
    { seq: 2, module_id: 'planner', role: 'auditor', round: 1, verdict: 'ok', duration_ms: 800 },
    { seq: 3, module_id: 'm03', role: 'executor', round: 1, verdict: 'ok', duration_ms: 5000 },
    { seq: 4, module_id: 'm03', role: 'auditor', round: 1, verdict: 'revise', duration_ms: 4000 },
    { seq: 5, module_id: 'm03', role: 'executor', round: 2, verdict: 'ok', duration_ms: 3000 },
    { seq: 6, module_id: 'm03', role: 'split' },
    { seq: 7, module_id: 'm03a', role: 'executor', round: 1, verdict: 'ok', duration_ms: 2000 },
    { seq: 8, module_id: 'm03a', role: 'split' },
    { seq: 9, module_id: 'm03a1', role: 'executor', round: 1, verdict: 'pending', duration_ms: 1000 }
  ];
}

test('logic: 对拍 — real split run restores planner → m03 → [E→A] → fork(m03a recursion)', () => {
  const p = LOGIC.buildPipelineChain(realSplitRunTree(), realSplitRunTimeline());
  assert.equal(p.run_id, 'run-2026-08-29-01');
  // planner is the root (left).
  assert.ok(p.root, 'planner must be the root');
  assert.equal(p.root.id, 'planner');
  // m03 is the only top-level chain.
  assert.equal(p.chains.length, 1);
  const m03 = p.chains[0];
  assert.equal(m03.id, 'm03');
  // m03 keeps its E/A round cards in dispatch order; split record not a card.
  assert.deepEqual(m03.rounds.map((r) => r.role), ['executor', 'auditor', 'executor']);
  assert.deepEqual(m03.rounds.map((r) => r.round), [1, 1, 2]);
  // m03 forks into two submodules.
  assert.ok(m03.fork);
  assert.equal(m03.fork.splitCount, 2);
  const a = m03.fork.children.find((c) => c.id === 'm03a');
  const b = m03.fork.children.find((c) => c.id === 'm03b');
  assert.ok(a && b, 'fork must contain m03a + m03b');
  // m03a is itself a fork (deep recursion) with its own E/A round.
  assert.deepEqual(a.rounds.map((r) => r.role), ['executor']);
  assert.equal(a.fork.splitCount, 1);
  assert.equal(a.fork.children[0].id, 'm03a1');
  // recursion stats reflect the deep subtree.
  assert.equal(LOGIC.countSubmodules(m03), 3);
  assert.equal(LOGIC.chainDepth(m03), 2);
  // m03 is active (running) and its subtree is active too.
  assert.equal(LOGIC.isActiveChain(m03), true);
  assert.equal(m03.status, 'running');
});

test('client.js renders final-block polish markers (branch/fork-toggle/root-label/active round)', () => {
  const client = read('lib/client.js');
  for (const marker of [
    "logic.roundKind",
    "logic.isActiveChain",
    "logic.countSubmodules",
    "logic.chainDepth",
    "'data-ak-branch'",
    "'data-ak-fork-toggle'",
    "'data-ak-root-label'",
    "ak-round-active",
    "ak-chain-active",
    "ak-root-label",
    "t('flow.recursion')"
  ]) {
    assert.ok(client.includes(marker), `client.js must contain ${marker}`);
  }
});

test('styles: final-block polish selectors present in both style sources', () => {
  const styleJs = read('lib/style.js');
  const css = read('lib/styles.css');
  for (const sel of ['.ak-branch-no', '.ak-fork-toggle', '.ak-root-label', '.ak-chain-active', '.ak-round-verdict-ok']) {
    assert.ok(styleJs.includes(sel), `style.js must contain ${sel}`);
    assert.ok(css.includes(sel), `styles.css must contain ${sel}`);
  }
  // The in-flight round pulse animation must exist (CSS-only, no JS polling).
  assert.ok(styleJs.includes('@keyframes ak-flow-pulse'), 'style.js must keep ak-flow-pulse');
  assert.ok(css.includes('@keyframes ak-flow-pulse'), 'styles.css must keep ak-flow-pulse');
});

/* ------------------------------------------------------------------ *
 * Bridge timeline adapter — real dispatch event stream (v1.1)
 * ------------------------------------------------------------------ */
test('logic: isBridgeTimeline detects the real event stream shape', () => {
  assert.equal(LOGIC.isBridgeTimeline([
    { seq: 1, event: 'run.start', module: '', detail: {} }
  ]), true);
  assert.equal(LOGIC.isBridgeTimeline([
    { seq: 1, role: 'executor', module_id: 'm1' }
  ]), false);
  assert.equal(LOGIC.isBridgeTimeline(null), false);
  assert.equal(LOGIC.isBridgeTimeline([]), false);
});

test('logic: parseBridgeTimeline turns the event stream into E/A round cards', () => {
  const real = [
    { seq: 1, ts: '2026-08-29T10:06:22+08:00', event: 'run.start', module: '', detail: { task: '二期' } },
    { seq: 2, ts: '2026-08-29T10:06:22+08:00', event: 'module.dispatch', module: 'm01', detail: { executor_id: 'E1', executor_round: 1 } },
    { seq: 3, ts: '2026-08-29T10:06:22+08:00', event: 'executor.round.start', module: 'm01', detail: { round: 1, executor_id: 'E1' } },
    { seq: 4, ts: '2026-08-29T10:15:24+08:00', event: 'executor.round.done', module: 'm01', detail: { round: 1, outcome_status: 'ok', tokens: 100714 } },
    { seq: 5, ts: '2026-08-29T10:15:24+08:00', event: 'auditor.round.start', module: 'm01', detail: { auditor_round: 1 } },
    { seq: 6, ts: '2026-08-29T10:16:00+08:00', event: 'auditor.round', module: 'm01', detail: { auditor_round: 1, verdict: 'pass', confidence: 0.95 } },
    { seq: 7, ts: '2026-08-29T10:16:00+08:00', event: 'module.done', module: 'm01', detail: { auditor_round: 1 } },
    { seq: 8, ts: '2026-08-29T10:16:00+08:00', event: 'integration.check', module: '', detail: { status: 'deferred' } }
  ];
  const cards = LOGIC.parseBridgeTimeline(real);
  // Only the two E/A rounds survive; run.start / module.dispatch / module.done / integration.check are not cards.
  assert.equal(cards.length, 2);
  assert.equal(cards[0].role, 'executor');
  assert.equal(cards[0].round, 1);
  assert.equal(cards[0].verdict, 'ok');
  assert.equal(cards[0].module_id, 'm01');
  // 10:06:22 → 10:15:24 = 542s
  assert.equal(cards[0].duration_ms, 542000);
  assert.equal(cards[1].role, 'auditor');
  assert.equal(cards[1].round, 1);
  assert.equal(cards[1].verdict, 'ok');
  // 10:15:24 → 10:16:00 = 36s
  assert.equal(cards[1].duration_ms, 36000);
});

test('logic: parseBridgeTimeline keeps seq order and honors camelCase module', () => {
  const cards = LOGIC.parseBridgeTimeline([
    { seq: 10, ts: 'T', event: 'executor.round.start', module: 'm02', detail: { round: 2 } },
    { seq: 2, ts: 'T', event: 'executor.round.start', module: 'm01', detail: { round: 1 } },
    { seq: 3, ts: 'T', event: 'executor.round.done', module: 'm01', detail: { round: 1, outcome_status: 'revise' } },
    { seq: 11, ts: 'T', event: 'executor.round.done', module: 'm02', detail: { round: 2, outcome_status: 'ok' } }
  ]);
  assert.equal(cards.length, 2);
  assert.equal(cards[0].module_id, 'm01');
  assert.equal(cards[0].verdict, 'revise');
  assert.equal(cards[1].module_id, 'm02');
});

test('logic: parseBridgeTimeline orphan start becomes a pending card', () => {
  const cards = LOGIC.parseBridgeTimeline([
    { seq: 1, ts: '2026-08-29T10:00:00+08:00', event: 'executor.round.start', module: 'm01', detail: { round: 3 } }
  ]);
  assert.equal(cards.length, 1);
  assert.equal(cards[0].role, 'executor');
  assert.equal(cards[0].round, 3);
  assert.equal(cards[0].verdict, 'pending');
  assert.equal(cards[0].duration_ms, null);
});

test('logic: normalizeRoundVerdict maps bridge verdicts', () => {
  assert.equal(LOGIC.normalizeRoundVerdict('pass'), 'ok');
  assert.equal(LOGIC.normalizeRoundVerdict('ok'), 'ok');
  assert.equal(LOGIC.normalizeRoundVerdict('revise'), 'revise');
  assert.equal(LOGIC.normalizeRoundVerdict('blocked'), 'block');
  assert.equal(LOGIC.normalizeRoundVerdict(undefined), 'pending');
  assert.equal(LOGIC.normalizeRoundVerdict(''), 'pending');
});

test('logic: buildPipelineChain consumes the real event stream (integration)', () => {
  const usage = { run: { input: 900000, output: 300000, cache_read: 20000000, billable: 1200000 }, per_module: { m01: {} } };
  const tree = {
    run: { run_id: 'run-x' },
    modules: [
      { id: 'm01', name: '数据桥', status: 'done', dependencies: [], split: [], last_verdict: 'pass', token_used: 333229, started_at: '2026-08-29T10:06:22+08:00', ended_at: '2026-08-29T10:31:30+08:00' },
      { id: 'm02', name: '面板', status: 'done', dependencies: ['m01'], split: [], last_verdict: 'pass', token_used: 303234, started_at: '2026-08-29T10:31:30+08:00', ended_at: '2026-08-29T10:44:13+08:00' }
    ]
  };
  const timeline = [
    { seq: 1, ts: '2026-08-29T10:06:22+08:00', event: 'module.dispatch', module: 'm01', detail: {} },
    { seq: 2, ts: '2026-08-29T10:06:22+08:00', event: 'executor.round.start', module: 'm01', detail: { round: 1 } },
    { seq: 3, ts: '2026-08-29T10:15:24+08:00', event: 'executor.round.done', module: 'm01', detail: { round: 1, outcome_status: 'ok' } },
    { seq: 4, ts: '2026-08-29T10:15:24+08:00', event: 'auditor.round.start', module: 'm01', detail: { auditor_round: 1 } },
    { seq: 5, ts: '2026-08-29T10:16:00+08:00', event: 'auditor.round', module: 'm01', detail: { auditor_round: 1, verdict: 'pass' } },
    { seq: 6, ts: '2026-08-29T10:16:00+08:00', event: 'module.done', module: 'm01', detail: {} }
  ];
  const pipeline = LOGIC.buildPipelineChain(tree, timeline, usage);
  // No explicit tree.root → no promoted planner: m01 stays a plain top-level chain.
  assert.equal(pipeline.root, null);
  const m01 = pipeline.chains.find((c) => c.id === 'm01');
  assert.ok(m01, 'm01 chain exists');
  assert.equal(m01.rounds.length, 2);
  assert.equal(m01.rounds[0].role, 'executor');
  assert.equal(m01.rounds[0].verdict, 'ok');
  assert.equal(m01.rounds[1].role, 'auditor');
  assert.equal(m01.rounds[1].verdict, 'ok');
});

/* ------------------------------------------------------------------ *
 * Token breakdown + detail popover real-data fixes (v1.1)
 * ------------------------------------------------------------------ */
test('logic: normalizeUsage accepts the bridge {run:{...}} shape', () => {
  const u = LOGIC.normalizeUsage({
    run: { input: 924283, output: 364949, cache_read: 34819840, calls: 529, billable: 1289232 },
    per_module: { m01: { input: 1, output: 2, cache_read: 3 } }
  });
  assert.equal(u.total, 1289232);
  // 口径：输入含缓存 = 924283 + 34819840；缓存命中单独展示
  assert.equal(u.input, 924283 + 34819840);
  assert.equal(u.output, 364949);
  assert.equal(u.cache, 34819840);
  assert.equal(u.hasSplit, true);
});

test('logic: normalizeUsage keeps the flat legacy shape working', () => {
  const u = LOGIC.normalizeUsage({ total: 100, input: 40, output: 30, cache: 20 });
  assert.equal(u.total, 100);
  // 口径：输入含缓存 = 40 + 20
  assert.equal(u.input, 60);
  assert.equal(u.output, 30);
  assert.equal(u.cache, 20);
  assert.equal(u.hasSplit, true);
});

test('logic: normalizeUsage flags no-split payloads', () => {
  const u = LOGIC.normalizeUsage({ run: { billable: 100 }, per_module: {} });
  assert.equal(u.total, 100);
  assert.equal(u.hasSplit, false);
});

test('logic: normalizeUsageByModule extracts per-module breakdowns', () => {
  const map = LOGIC.normalizeUsageByModule({
    run: { input: 1, output: 1, cache_read: 1, billable: 9 },
    per_module: {
      m01: { input: 10, output: 5, cache_read: 99, billable: 123 },
      m02: { input: 7, output: 3, cache_read: 0, billable: 77 }
    }
  });
  assert.equal(map.m01.total, 123);
  // 口径：输入含缓存 = 10 + 99
  assert.equal(map.m01.input, 10 + 99);
  assert.equal(map.m01.cache, 99);
  assert.equal(map.m02.output, 3);
  assert.equal(map.m02.hasSplit, true);
});

test('logic: buildModuleDetail uses the real event stream for rounds', () => {
  const m = { id: 'm01', name: '数据桥', status: 'done', split: [], last_verdict: 'pass' };
  const timeline = [
    { seq: 1, ts: '2026-08-29T10:06:22+08:00', event: 'module.dispatch', module: 'm01', detail: {} },
    { seq: 2, ts: '2026-08-29T10:06:22+08:00', event: 'executor.round.start', module: 'm01', detail: { round: 1 } },
    { seq: 3, ts: '2026-08-29T10:15:24+08:00', event: 'executor.round.done', module: 'm01', detail: { round: 1, outcome_status: 'ok' } },
    { seq: 4, ts: '2026-08-29T10:15:24+08:00', event: 'auditor.round.start', module: 'm01', detail: { auditor_round: 1 } },
    { seq: 5, ts: '2026-08-29T10:16:00+08:00', event: 'auditor.round', module: 'm01', detail: { auditor_round: 1, verdict: 'pass' } },
    { seq: 6, ts: '2026-08-29T10:16:00+08:00', event: 'module.done', module: 'm01', detail: {} }
  ];
  const usage = { run: { input: 100, output: 50, cache_read: 200, billable: 350 }, per_module: { m01: { input: 100, output: 50, cache_read: 200, billable: 350 } } };
  const d = LOGIC.buildModuleDetail(m, timeline, usage);
  assert.equal(d.roundsTotal, 2);
  assert.equal(d.rounds[0].role, 'executor');
  assert.equal(d.rounds[0].verdict, 'ok');
  assert.equal(d.rounds[0].duration_ms, 542000);
  assert.equal(d.rounds[1].role, 'auditor');
  assert.equal(d.rounds[1].verdict, 'ok');
  // module-level usage breakdown wired through
  // 口径：输入含缓存 = 100 + 200
  assert.equal(d.usage.input, 100 + 200);
  assert.equal(d.usage.cache, 200);
  assert.equal(d.usage.hasSplit, true);
});

test('logic: summarizeModuleRounds filters to one module and sums durations', () => {
  const timeline = [
    { seq: 1, ts: '2026-08-29T10:00:00+08:00', event: 'executor.round.start', module: 'm01', detail: { round: 1 } },
    { seq: 2, ts: '2026-08-29T10:01:00+08:00', event: 'executor.round.done', module: 'm01', detail: { round: 1, outcome_status: 'ok' } },
    { seq: 3, ts: '2026-08-29T10:02:00+08:00', event: 'executor.round.start', module: 'm02', detail: { round: 1 } },
    { seq: 4, ts: '2026-08-29T10:03:00+08:00', event: 'executor.round.done', module: 'm02', detail: { round: 1, outcome_status: 'ok' } }
  ];
  const s = LOGIC.summarizeModuleRounds(timeline, 'm01');
  assert.equal(s.total, 1);
  assert.equal(s.rounds[0].module_id, 'm01');
  assert.equal(s.totalMs, 60000);
});

/* ------------------------------------------------------------------ *
 * Planner summary — plan-phase usage (bridge planner bucket)
 * ------------------------------------------------------------------ */
test('logic: buildPlannerSummary carries plan-phase usage separately', () => {
  const tree = { modules: [{ id: 'm01' }, { id: 'm02' }] };
  const timeline = [
    { seq: 1, ts: '2026-08-29T13:12:22+08:00', event: 'run.start', module: '', detail: {} },
    { seq: 2, ts: '2026-08-29T13:12:22+08:00', event: 'module.dispatch', module: 'm01', detail: {} }
  ];
  const usage = {
    run: { input: 100, output: 50, cache_read: 200, billable: 150 },
    planner: { input: 30, output: 10, cache_read: 50, billable: 40 }
  };
  const p = LOGIC.buildPlannerSummary(tree, timeline, usage);
  assert.equal(p.modulesCount, 2);
  assert.equal(p.usage.total, 150);      // run 级总消耗
  assert.equal(p.planUsage.total, 40);   // 规划阶段消耗
  assert.equal(p.planUsage.hasSplit, true);
});

/* ------------------------------------------------------------------ *
 * cache rate + plan duration from bridge planner bucket
 * ------------------------------------------------------------------ */
test('logic: normalizeUsage computes cache hit rate', () => {
  const u = LOGIC.normalizeUsage({ run: { input: 924283, output: 364949, cache_read: 34819840, billable: 1289232 } });
  // rate = cache / (input+cache) = 34819840 / (924283+34819840) ≈ 97.4% → 97
  assert.equal(u.cacheRate, 97);
  // no cache → rate null
  assert.equal(LOGIC.normalizeUsage({ run: { input: 100, output: 10, cache_read: 0, billable: 110 } }).cacheRate, null);
});

test('logic: buildPlannerSummary prefers bridge planner duration_ms for plan time', () => {
  const tree = { modules: [{ id: 'm01' }] };
  const timeline = [
    { seq: 1, ts: '2026-08-29T13:12:22+08:00', event: 'run.start', module: '', detail: {} },
    { seq: 2, ts: '2026-08-29T13:12:22+08:00', event: 'module.dispatch', module: 'm01', detail: {} }
  ];
  const usage = {
    run: { input: 100, output: 50, cache_read: 200, billable: 350 },
    planner: { input: 30, output: 10, cache_read: 50, billable: 90, duration_ms: 187000 }
  };
  const p = LOGIC.buildPlannerSummary(tree, timeline, usage);
  // 优先用桥 planner 桶会话时长（真实规划耗时），而不是 run.start→首模块（同秒=0）
  assert.equal(p.planMs, 187000);
});

/* ------------------------------------------------------------------ *
 * split parent rolls up submodule stats (m03 含 m03a)
 * ------------------------------------------------------------------ */
test('logic: buildBlockSummary rolls up a split parent + submodules', () => {
  const timeline = [
    { seq: 1, ts: '2026-08-29T10:00:00+08:00', event: 'executor.round.start', module: 'm03', detail: { round: 1 } },
    { seq: 2, ts: '2026-08-29T10:10:00+08:00', event: 'executor.round.done', module: 'm03', detail: { round: 1, outcome_status: 'ok' } },
    { seq: 3, ts: '2026-08-29T10:10:00+08:00', event: 'executor.round.start', module: 'm03a', detail: { round: 1 } },
    { seq: 4, ts: '2026-08-29T10:20:00+08:00', event: 'executor.round.done', module: 'm03a', detail: { round: 1, outcome_status: 'ok' } },
    { seq: 5, ts: '2026-08-29T10:20:00+08:00', event: 'executor.round.start', module: 'm03a', detail: { round: 2 } },
    { seq: 6, ts: '2026-08-29T10:25:00+08:00', event: 'executor.round.done', module: 'm03a', detail: { round: 2, outcome_status: 'ok' } }
  ];
  // m03 chain with a split child m03a (as buildModuleChain produces)
  const m03a = { id: 'm03a', token_used: 500, fork: null };
  const m03 = { id: 'm03', token_used: 300, fork: { children: [m03a] } };
  const s = LOGIC.buildBlockSummary(m03, timeline);
  // m03 自身 1 轮(10m) + m03a 2 轮(10m+5m) = 3 轮 / 25m；token 300+500
  assert.equal(s.rounds, 3);
  assert.equal(s.totalMs, 25 * 60 * 1000);
  assert.equal(s.token_used, 800);
});

test('logic: aggregateUsageByTree rolls up split submodule usage', () => {
  const byMod = {
    m03: LOGIC.normalizeUsage({ run: { input: 500, output: 200, cache_read: 1000, billable: 1700 } }),
    m03a: LOGIC.normalizeUsage({ run: { input: 100, output: 50, cache_read: 400, billable: 550 } })
  };
  const parent = { id: 'm03', split: [{ id: 'm03a', split: [] }] };
  const agg = LOGIC.aggregateUsageByTree(byMod, parent);
  assert.equal(agg.input, (500 + 1000) + (100 + 400));  // 输入含缓存
  assert.equal(agg.total, 1700 + 550);
  assert.equal(agg.cache, 1000 + 400);
  // cacheRate 重算: cache/输入(含缓存) = 1400/2000 = 70%
  assert.equal(agg.cacheRate, 70);
});

/* ------------------------------------------------------------------ *
 * double-count regression: already-normalized usage passes through
 * ------------------------------------------------------------------ */
test('logic: buildModuleDetail does not double-count cache on normalized usage', () => {
  const m = { id: 'm01', name: 'm01', status: 'done', split: [] };
  const timeline = [
    { seq: 1, ts: '2026-08-29T13:12:22+08:00', event: 'module.dispatch', module: 'm01', detail: {} },
    { seq: 2, ts: '2026-08-29T13:12:22+08:00', event: 'executor.round.start', module: 'm01', detail: { round: 1 } },
    { seq: 3, ts: '2026-08-29T13:13:22+08:00', event: 'executor.round.done', module: 'm01', detail: { round: 1, outcome_status: 'ok' } }
  ];
  // usageByModule / aggregateUsageByTree 产出已归一化形态（input 已含缓存）
  const normalized = { total: 11089225, input: 10989002, output: 100223, cache: 10740608, cacheRate: 98, hasSplit: true };
  const d = LOGIC.buildModuleDetail(m, timeline, normalized);
  // 不重复归一化 → 缓存不被加两遍
  assert.equal(d.usage.input, 10989002);
  assert.equal(d.usage.cache, 10740608);
  assert.equal(d.usage.cacheRate, 98);
  assert.equal(d.usage.total, 11089225);
});
