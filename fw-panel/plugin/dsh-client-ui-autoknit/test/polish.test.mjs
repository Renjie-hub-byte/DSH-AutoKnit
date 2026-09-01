import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  runI18nTests,
  runConfigResolutionTests,
  runDetailSectionsTests,
  runStyleInjectionTest
} from './helpers.mjs';

test('i18n messages: zh/en, placeholders, fallback', () => {
  const r = runI18nTests();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('data-bridge base-URL config resolution', () => {
  const r = runConfigResolutionTests();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('multi-task detail sections (structured meta/module_states/consumption)', () => {
  const r = runDetailSectionsTests();
  assert.ok(r.ok, r.errors.join('\n'));
});

test('styles: styles.css present + style.js injects once, idempotent, DOM-guarded', () => {
  const r = runStyleInjectionTest();
  assert.ok(r.ok, r.errors.join('\n'));
});
