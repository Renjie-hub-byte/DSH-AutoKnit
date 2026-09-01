import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runNodeHalfChecks } from './helpers.mjs';

test('node half: ESM empty apply/inject, no ctx.layout', async () => {
  const r = await runNodeHalfChecks();
  assert.ok(r.ok, r.errors.join('\n'));
});
