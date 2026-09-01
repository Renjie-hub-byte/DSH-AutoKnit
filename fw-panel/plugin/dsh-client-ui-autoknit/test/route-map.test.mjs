import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  runStatusDerivationTests,
  runTopologyTests,
  runTimelineTests,
  runRouteMapBuildTests,
  runDataBridgeRouteTests,
  runRouteMapClientRenderTest
} from './helpers.mjs';

test('route-map: deriveStatus derives badge from status + last_verdict', () => {
  const r = runStatusDerivationTests();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('route-map: topoLayer layers modules by dependencies (Kahn)', () => {
  const r = runTopologyTests();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('route-map: parseTimeline + formatDuration', () => {
  const r = runTimelineTests();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('route-map: buildRouteMap layers/split/status/summary', () => {
  const r = runRouteMapBuildTests();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('data-bridge: tree/timeline/usage/reply endpoints', async () => {
  const r = await runDataBridgeRouteTests();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('client.js renders route-map structure + lifecycle intact', () => {
  const r = runRouteMapClientRenderTest();
  assert.ok(r.ok, r.errors.join('\n'));
});
