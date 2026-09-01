import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  runRenderPayloadTests,
  runArchiveSwitchTests,
  runDataBridgeTests,
  runBrowserHarnessTest,
  runPackageChecks
} from './helpers.mjs';

test('render payload includes run_id/stage/module_states/consumption', () => {
  const r = runRenderPayloadTests();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('archive / switch logic', () => {
  const r = runArchiveSwitchTests();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('data-bridge HTTP client', async () => {
  const r = await runDataBridgeTests();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('browser registers details slot via __ModuleLoader__.load', () => {
  const r = runBrowserHarnessTest();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('package.json declares dsh.client', () => {
  const r = runPackageChecks();
  assert.ok(r.ok, r.errors.join('\n'));
});
