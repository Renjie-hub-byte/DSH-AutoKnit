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

test('logic: active border-spin animation is CSS-only and its class is assertable', () => {
  const cssFile = read('lib/styles.css');
  const styleJs = read('lib/style.js');
  const client = read('lib/client.js');

  // CSS animation only (no JS polling) — the keyframe must live in the stylesheet.
  assert.ok(cssFile.includes('@keyframes ak-border-spin'), 'styles.css must define ak-border-spin');
  assert.ok(cssFile.includes('.ak-module.ak-active'), 'styles.css must style the active module');
  assert.ok(styleJs.includes('ak-border-spin'), 'style.js must inline the same keyframes');
  assert.ok(styleJs.includes('.ak-module.ak-active'), 'style.js must inline the active style');
  // The renderer must apply the class + a testable data attribute.
  assert.ok(client.includes("' ak-active'"), 'client.js must apply the ak-active class');
  assert.ok(client.includes("'data-ak-active'"), 'client.js must emit data-ak-active');
});

test('logic: decision dialog renders 4 commands + free input + submit/cancel on needs_human', () => {
  const client = read('lib/client.js');
  for (const marker of [
    "'data-ak-reply': '1'",
    "'data-ak-reply-module'",
    "'ak-reply-commands'",
    "'data-ak-reply-instruction'",
    "'data-ak-reply-submit'",
    "'data-ak-reply-cancel'",
    "logic.REPLY_COMMANDS.map",
    "reply.command.' + cmd",
    "logic.validateReplyCommand",
    "client.reply(selected"
  ]) {
    assert.ok(client.includes(marker), `client.js must contain ${marker}`);
  }
});

test('logic: REPLY_COMMANDS whitelist is continue/retry/revise/custom', () => {
  assert.deepEqual(LOGIC.REPLY_COMMANDS, ['continue', 'retry', 'revise', 'custom']);
});

test('logic: validateReplyCommand accepts every whitelisted command (with a note for custom)', () => {
  for (const cmd of LOGIC.REPLY_COMMANDS) {
    const r = LOGIC.validateReplyCommand(cmd, cmd === 'custom' ? '补充说明' : '');
    assert.equal(r.ok, true, `${cmd} should be valid`);
    assert.equal(r.command, cmd);
    assert.deepEqual(r.errors, {});
  }
});

test('logic: validateReplyCommand requires instruction when command is custom', () => {
  const empty = LOGIC.validateReplyCommand('custom', '');
  assert.equal(empty.ok, false);
  assert.equal(empty.errors.instruction, 'reply.instruction.required');

  const whitespace = LOGIC.validateReplyCommand('custom', '   ');
  assert.equal(whitespace.ok, false);
  assert.equal(whitespace.errors.instruction, 'reply.instruction.required');

  const filled = LOGIC.validateReplyCommand('custom', '请补充上下文');
  assert.equal(filled.ok, true);
  assert.equal(filled.command, 'custom');
  assert.deepEqual(filled.errors, {});
});

test('logic: validateReplyCommand rejects unknown/empty commands', () => {
  const unknown = LOGIC.validateReplyCommand('nope', 'x');
  assert.equal(unknown.ok, false);
  assert.equal(unknown.errors.command, 'reply.command.invalid');

  const empty = LOGIC.validateReplyCommand(undefined, 'x');
  assert.equal(empty.ok, false);
  assert.equal(empty.errors.command, 'reply.command.invalid');

  const nullCmd = LOGIC.validateReplyCommand(null, 'x');
  assert.equal(nullCmd.ok, false);
});

test('logic: validateReplyCommand trims whitespace and normalizes the command', () => {
  const r = LOGIC.validateReplyCommand('  continue  ', 'notes');
  assert.equal(r.ok, true);
  assert.equal(r.command, 'continue');
});

test('logic: deriveActiveModule returns the first running/needs_human module in topo order', () => {
  const routeMap = {
    layers: [
      [{ id: 'm0', status: 'done' }],
      [
        { id: 'm1', status: 'needs_human' },
        { id: 'm2', status: 'pending' }
      ]
    ]
  };
  const active = LOGIC.deriveActiveModule(routeMap);
  assert.ok(active, 'should find an active module');
  assert.equal(active.id, 'm1');
  assert.equal(active.status, 'needs_human');
});

test('logic: deriveActiveModule prefers running over needs_human in order', () => {
  const routeMap = {
    layers: [
      [{ id: 'a', status: 'running' }],
      [{ id: 'b', status: 'needs_human' }]
    ]
  };
  const active = LOGIC.deriveActiveModule(routeMap);
  assert.equal(active.id, 'a');
});

test('logic: deriveActiveModule surfaces an active split child', () => {
  const routeMap = {
    layers: [
      [
        {
          id: 'parent', status: 'pending',
          split: [
            { id: 'sub-running', status: 'running' },
            { id: 'sub-idle', status: 'done' }
          ]
        }
      ]
    ]
  };
  const active = LOGIC.deriveActiveModule(routeMap);
  assert.ok(active);
  assert.equal(active.id, 'sub-running');
});

test('logic: deriveActiveModule returns null when nothing is active', () => {
  assert.equal(LOGIC.deriveActiveModule(null), null);
  assert.equal(LOGIC.deriveActiveModule({}), null);
  const idle = {
    layers: [[{ id: 'a', status: 'done' }, { id: 'b', status: 'block' }]]
  };
  assert.equal(LOGIC.deriveActiveModule(idle), null);
});
