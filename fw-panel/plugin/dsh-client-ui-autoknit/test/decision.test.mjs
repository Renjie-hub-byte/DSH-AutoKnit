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

/* -------------------------------------------------------------- *
 * 需求 A：pending 态完整问询内容（5 项）
 * -------------------------------------------------------------- */
test('decision: pending state carries full inquiry content', () => {
  const facts = LOGIC.needsHumanFactsFromTimeline([
    { seq: 1, event: 'run.start', module: null, ts: '2026-09-02T12:16:12+08:00' },
    { seq: 42, event: 'module.needs_human', module: 'm03', ts: '2026-09-02T12:51:27+08:00',
      detail: { reason: 'agent 非零退出(124)：模块无法拆分' } }
  ]);
  assert.equal(facts.was.m03, true);
  assert.equal(facts.pendingSince.m03, '2026-09-02T12:51:27+08:00');

  const d = LOGIC.deriveHumanDecision('m03', {
    needsHuman: ['m03'],
    humanAnswers: {},
    perModule: { m03: { reason: 'agent 非零退出(124)：模块无法拆分' } }
  }, facts);
  assert.equal(d.state, 'pending');
  assert.equal(d.by, null);
  assert.equal(d.reason, 'agent 非零退出(124)：模块无法拆分');
  assert.equal(d.pendingSince, '2026-09-02T12:51:27+08:00');
  assert.equal(d.draftText, '');
});

test('decision: pending shows draft reply text (avoid duplicate decision)', () => {
  const facts = LOGIC.needsHumanFactsFromTimeline([
    { seq: 42, event: 'module.needs_human', module: 'm03', ts: 't1' }
  ]);
  const d = LOGIC.deriveHumanDecision('m03', {
    needsHuman: ['m03'],
    humanAnswers: { m03: { code: '?', text: '草稿：建议继续', answered_at: 't2', reason: '' } },
    perModule: {}
  }, facts);
  assert.equal(d.state, 'pending');
  assert.equal(d.draftText, '草稿：建议继续');
});

/* -------------------------------------------------------------- *
 * 需求 B：三态生命周期
 * -------------------------------------------------------------- */
test('decision: human answer (code != ?) → resolved-by-human', () => {
  const facts = LOGIC.needsHumanFactsFromTimeline([
    { seq: 42, event: 'module.needs_human', module: 'm03', ts: 't1' },
    { seq: 50, event: 'module.human_rerun', module: 'm03', ts: 't3' }
  ]);
  const d = LOGIC.deriveHumanDecision('m03', {
    needsHuman: [],
    humanAnswers: { m03: { code: 'D', text: '把目录移到 src/ 下', answered_at: '2026-09-02T01:45:00+08:00', reason: 'agent 非零退出(124)' } },
    perModule: { m03: { last_verdict: 'pass' } }
  }, facts);
  assert.equal(d.state, 'resolved');
  assert.equal(d.by, 'human');
  assert.equal(d.code, 'D');
  assert.equal(d.answeredAt, '2026-09-02T01:45:00+08:00');
});

test('decision: done after needs_human (no answer) → resolved-by-process', () => {
  const facts = LOGIC.needsHumanFactsFromTimeline([
    { seq: 42, event: 'module.needs_human', module: 'm03', ts: 't1' },
    { seq: 60, event: 'module.done', module: 'm03', ts: 't9',
      detail: { needs_human_resolved_by: 'process' } }
  ]);
  const d = LOGIC.deriveHumanDecision('m03', {
    needsHuman: [], humanAnswers: {}, perModule: { m03: { last_verdict: 'pass' } }
  }, facts);
  assert.equal(d.state, 'resolved');
  assert.equal(d.by, 'process');
  assert.equal(d.answeredAt, 't9');
});

test('decision: module never needed human → null (no card)', () => {
  const facts = LOGIC.needsHumanFactsFromTimeline([
    { seq: 1, event: 'module.dispatch', module: 'm01', ts: 't1' },
    { seq: 9, event: 'module.done', module: 'm01', ts: 't9' }
  ]);
  const d = LOGIC.deriveHumanDecision('m01', {
    needsHuman: [], humanAnswers: {}, perModule: { m01: { last_verdict: 'pass' } }
  }, facts);
  assert.equal(d, null);
});

test('decision: human_rerun clears pendingSince but keeps was-memory', () => {
  const facts = LOGIC.needsHumanFactsFromTimeline([
    { seq: 42, event: 'module.needs_human', module: 'm03', ts: 't1' },
    { seq: 50, event: 'module.human_rerun', module: 'm03', ts: 't3' }
  ]);
  assert.equal(facts.pendingSince.m03, undefined);
  assert.equal(facts.was.m03, true);
});

/* -------------------------------------------------------------- *
 * buildPipelineChain 集成：chain 节点挂 humanDecision
 * -------------------------------------------------------------- */
function makeTree(overrides) {
  return Object.assign({
    run_id: 'run-t',
    modules: [{ id: 'm01', name: '模块一' }],
    dependencies: { m01: [] },
    per_module: { m01: { reason: '采证失败', last_verdict: 'revise' } },
    needs_human: ['m01'],
    human_answers: {}
  }, overrides || {});
}

const TIMELINE_PENDING = [
  { seq: 1, event: 'run.start', module: null, ts: 't0' },
  { seq: 7, event: 'module.needs_human', module: 'm01', ts: 't1' }
];
const TIMELINE_RESOLVED = [
  { seq: 1, event: 'run.start', module: null, ts: 't0' },
  { seq: 7, event: 'module.needs_human', module: 'm01', ts: 't1' },
  { seq: 60, event: 'module.done', module: 'm01', ts: 't9', detail: { needs_human_resolved_by: 'process' } }
];

test('pipeline: chain node carries pending humanDecision (integration)', () => {
  const p = LOGIC.buildPipelineChain(makeTree(), TIMELINE_PENDING, {});
  const chain = p.chains.find((c) => c.id === 'm01');
  assert.ok(chain.humanDecision, 'humanDecision must be attached');
  assert.equal(chain.humanDecision.state, 'pending');
  assert.equal(chain.humanDecision.reason, '采证失败');
});

test('pipeline: chain node carries resolved-by-process (integration)', () => {
  const tree = makeTree({
    needs_human: [],
    per_module: { m01: { reason: '采证失败', last_verdict: 'pass' } }
  });
  const p = LOGIC.buildPipelineChain(tree, TIMELINE_RESOLVED, {});
  const chain = p.chains.find((c) => c.id === 'm01');
  assert.equal(chain.humanDecision.state, 'resolved');
  assert.equal(chain.humanDecision.by, 'process');
  assert.equal(chain.humanDecision.answeredAt, 't9');
});

/* -------------------------------------------------------------- *
 * 渲染接线（源码级）：三态卡 + dialog 自动关 + i18n keys
 * -------------------------------------------------------------- */
test('client: decision card renderer wired into pipeline module block', () => {
  const src = read('lib/client.js');
  assert.ok(src.includes('renderHumanDecisionUi'), 'three-state renderer must exist');
  assert.ok(src.includes('renderDecisionPending'), 'pending card renderer must exist');
  assert.ok(src.includes('renderDecisionResolved'), 'resolved card renderer must exist');
  assert.ok(src.includes('var replyEl = renderHumanDecisionUi'), 'pipeline must use the three-state renderer');
  assert.ok(src.includes('pendingList.indexOf'), 'dialog auto-close on resolve must be wired');
});

test('i18n: decision card keys present in zh and en', () => {
  const src = read('lib/i18n.js');
  for (const key of ['decision.pendingTitle', 'decision.reason', 'decision.since',
    'decision.options', 'decision.resolvedTitle', 'decision.by.human',
    'decision.by.process', 'decision.draft']) {
    const count = src.split("'" + key + "'").length - 1;
    assert.ok(count >= 2, `i18n key ${key} must exist in both locales (found ${count})`);
  }
});
