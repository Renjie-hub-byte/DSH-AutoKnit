/**
 * Shared test helpers for dsh-client-ui-autoknit.
 * Single source of truth consumed by both `node --test test/*.test.mjs` and
 * `verify_bundle.mjs`. Every `run*` function returns `{ name, ok, errors[] }`.
 */
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

const LOGIC = require(path.join(ROOT, 'lib', 'logic.js'));
const BRIDGE = require(path.join(ROOT, 'lib', 'data-bridge.js'));
const I18N = require(path.join(ROOT, 'lib', 'i18n.js'));
const STYLE = require(path.join(ROOT, 'lib', 'style.js'));

function read(p) {
  return readFileSync(path.join(ROOT, p), 'utf8');
}

/* ------------------------------------------------------------------ *
 * Node-half structure checks
 * ------------------------------------------------------------------ */
export async function runNodeHalfChecks() {
  const errors = [];
  const source = read('lib/index.mjs');
  const mod = await import(pathToFileURL(path.join(ROOT, 'lib', 'index.mjs')).href);

  // Hard requirement: the node half must never access the DSH layout.
  if (/ctx\.layout/.test(source)) errors.push('index.mjs source must not contain ctx.layout');

  if (typeof mod.apply !== 'function') errors.push('apply must be a function');
  if (typeof mod.inject !== 'function') errors.push('inject must be a function');

  // apply()/inject() must be no-ops: calling them changes nothing and returns undefined.
  if (mod.apply({}) !== undefined) errors.push('apply() must return undefined');
  if (mod.inject({}) !== undefined) errors.push('inject() must return undefined');
  const applyBody = Function.prototype.toString.call(mod.apply);
  if (applyBody.includes('ctx.layout')) errors.push('apply() body must not touch ctx.layout');

  return {
    name: 'node-half structure (ESM apply/inject, no ctx.layout)',
    ok: errors.length === 0,
    errors
  };
}

/* ------------------------------------------------------------------ *
 * Render payload logic tests
 * ------------------------------------------------------------------ */
export function runRenderPayloadTests() {
  const errors = [];
  const rawTasks = [
    { run_id: 'run-1', stage: 'executor', module_states: { m01: 'done' }, consumption: { tokens: 100 } },
    { run_id: 'run-2', stage: 'needs_human', module_states: { m02: 'pending' }, consumption: { tokens: 200 } },
    { run_id: 'run-3', stage: 'unknown', module_states: { m03: 'error' }, consumption: { tokens: 300 } }
  ];
  const payload = LOGIC.buildRenderPayload(rawTasks, { archivedRunIds: ['run-2'] });

  if (payload.total !== 3) errors.push(`payload.total expected 3 got ${payload.total}`);
  if (payload.active !== 2) errors.push(`payload.active expected 2 got ${payload.active}`);
  if (payload.tasks.length !== 2) errors.push(`payload.tasks length expected 2 got ${payload.tasks.length}`);

  // every rendered task carries the contract fields
  for (const t of payload.tasks) {
    if (!('run_id' in t)) errors.push('rendered task missing run_id');
    if (!('stage' in t)) errors.push('rendered task missing stage');
    if (!('module_states' in t)) errors.push('rendered task missing module_states');
    if (!('consumption' in t)) errors.push('rendered task missing consumption');
  }
  // archived run-2 must be gone from the active list
  if (payload.tasks.some((t) => t.run_id === 'run-2')) errors.push('archived run-2 still in active list');

  // camelCase input + unknown stage normalization
  const camel = LOGIC.normalizeTask({ runId: 'run-x', stage: 'foo', module_states: {}, consumption: {} });
  if (camel.run_id !== 'run-x') errors.push(`camelCase run_id mismatch: ${camel.run_id}`);
  if (camel.stage !== 'unknown') errors.push(`unknown stage should normalize to 'unknown', got ${camel.stage}`);

  return {
    name: 'render payload (run_id/stage/module_states/consumption)',
    ok: errors.length === 0,
    errors
  };
}

/* ------------------------------------------------------------------ *
 * Archive / switch logic tests
 * ------------------------------------------------------------------ */
export function runArchiveSwitchTests() {
  const errors = [];
  const tasks = [
    { run_id: 'run-1', stage: 'executor' },
    { run_id: 'run-2', stage: 'auditor' },
    { run_id: 'run-3', stage: 'switch' }
  ];

  // filterActive drops archived ids
  const active = LOGIC.filterActive(tasks, ['run-2']);
  if (active.length !== 2) errors.push(`filterActive expected 2 got ${active.length}`);
  if (active.some((t) => t.run_id === 'run-2')) errors.push('filterActive kept archived task');

  // removeArchived removes one task
  const afterRemove = LOGIC.removeArchived(tasks, 'run-1');
  if (afterRemove.length !== 2) errors.push(`removeArchived expected 2 got ${afterRemove.length}`);
  if (afterRemove.some((t) => t.run_id === 'run-1')) errors.push('removeArchived kept removed task');

  // selectTask picks the right task / null for missing
  const sel = LOGIC.selectTask(tasks, 'run-3');
  if (!sel || sel.run_id !== 'run-3') errors.push('selectTask did not find run-3');
  if (LOGIC.selectTask(tasks, 'missing') !== null) errors.push('selectTask should return null for unknown id');

  // reduceArchive: removes task and clears detail if it was the archived one
  let state = { tasks, detail: { run_id: 'run-2' } };
  state = LOGIC.reduceArchive(state, 'run-2');
  if (state.tasks.length !== 2) errors.push(`reduceArchive tasks expected 2 got ${state.tasks.length}`);
  if (state.detail !== null) errors.push('reduceArchive should clear detail of archived task');

  let state2 = { tasks, detail: { run_id: 'run-1' } };
  state2 = LOGIC.reduceArchive(state2, 'run-2');
  if (state2.detail === null || state2.detail.run_id !== 'run-1') errors.push('reduceArchive must keep unrelated detail');

  return {
    name: 'archive / switch logic',
    ok: errors.length === 0,
    errors
  };
}

/* ------------------------------------------------------------------ *
 * Data-bridge client tests (fake fetch)
 * ------------------------------------------------------------------ */
function fakeFetch(router) {
  return function (url, init) {
    const method = (init && init.method) || 'GET';
    const entry = router[method + ' ' + url];
    if (!entry) {
      return Promise.resolve({
        ok: false,
        status: 404,
        text: () => Promise.resolve('not found')
      });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve(JSON.stringify(entry))
    });
  };
}

export function runDataBridgeTests() {
  const errors = [];
  const router = {
    'GET /api/tasks': [{ run_id: 'r1' }, { run_id: 'r2' }],
    'GET /api/tasks/r1': { run_id: 'r1', stage: 'executor' },
    'GET /api/tasks/archived': ['r2'],
    'POST /api/runs/r1/archive': { run_id: 'r1', status: 'archived', ok: true }
  };
  const client = BRIDGE.createClient({ fetch: fakeFetch(router) });

  return client.listTasks().then((tasks) => {
    if (!Array.isArray(tasks) || tasks.length !== 2) errors.push('listTasks returned wrong payload');
    return client.taskDetail('r1');
  }).then((d) => {
    if (!d || d.run_id !== 'r1') errors.push('taskDetail returned wrong payload');
    return client.archived();
  }).then((arch) => {
    if (!Array.isArray(arch) || arch[0] !== 'r2') errors.push('archived returned wrong payload');
    return client.archive('r1');
  }).then((res) => {
    if (!res || res.ok !== true) errors.push('archive returned wrong payload');
    return {
      name: 'data-bridge HTTP client (list/detail/archive/archived)',
      ok: errors.length === 0,
      errors
    };
  }).catch((err) => {
    errors.push('data-bridge test threw: ' + err.message);
    return {
      name: 'data-bridge HTTP client (list/detail/archive/archived)',
      ok: false,
      errors
    };
  });
}

/* ------------------------------------------------------------------ *
 * Browser-half plugin test (fake window + __ModuleLoader__ + slots)
 *
 * Verifies the DSH client-plugin contract that the real front-end Boot
 * enforces: the factory's materialized exports MUST carry `apply` (DSH
 * client-modules calls l.create(name) and the loader applies the module);
 * apply(ctx) registers a conversation.view tab via the official `slots` service.
 * ------------------------------------------------------------------ */
export function runBrowserHarnessTest() {
  const errors = [];
  const clientSrc = read('lib/client.js');

  if (!clientSrc.includes('__ModuleLoader__.load')) {
    errors.push('lib/client.js must call __ModuleLoader__.load');
  }
  if (!clientSrc.includes("require('react')")) {
    errors.push('lib/client.js must require react');
  }
  if (!clientSrc.includes('exports.apply')) {
    errors.push('lib/client.js factory must export apply (DSH client-plugin contract)');
  }
  if (!clientSrc.includes("exports.inject")) {
    errors.push('lib/client.js factory must export inject');
  }
  if (!clientSrc.includes("ctx.slots.inject('conversation.view'")) {
    errors.push("lib/client.js apply() must register via ctx.slots.inject('conversation.view')");
  }

  // Source is scanned structurally here. The real materialization check runs
  // against the BUILT bundle in runBundleChecks (dist/client.js declares
  // `var exports = module.exports` so it materializes under the loader); the
  // raw lib/client.js runs only inside a browser where DSH seeds the module
  // loader, so it is not itself executed in Node.
  if (clientSrc.includes('register(AutoknitPanel)')) {
    errors.push('lib/client.js must NOT call register() directly — it must export apply');
  }

  return {
    name: 'browser client-plugin source (factory exports apply/inject + ctx.slots)',
    ok: errors.length === 0,
    errors
  };
}

/* ------------------------------------------------------------------ *
 * package.json declaration checks (DSH-official client-plugin format)
 * ------------------------------------------------------------------ */
export function runPackageChecks() {
  const errors = [];
  const pkg = JSON.parse(read('package.json'));
  if (!pkg.dsh || !pkg.dsh.client) errors.push('package.json must declare dsh.client');
  if (pkg.dsh.client.platform !== 'web') errors.push('dsh.client.platform must be "web"');
  if (!Array.isArray(pkg.dsh.client.inject)) errors.push('dsh.client.inject must be an array');
  if (pkg.main !== 'lib/index.mjs') errors.push('package main should be lib/index.mjs (ESM node half)');
  const clientExport = pkg.exports && pkg.exports['./client'];
  const clientPath = clientExport && (clientExport.default || (typeof clientExport === 'string' ? clientExport : null));
  if (clientPath !== './dist/client.js') {
    errors.push('exports["./client"] must point to the bundled ./dist/client.js (got ' + clientPath + ')');
  }
  const rootExport = pkg.exports && pkg.exports['.'];
  const rootPath = rootExport && (rootExport.default || (typeof rootExport === 'string' ? rootExport : null));
  if (rootPath !== './lib/index.mjs') {
    errors.push('exports["."] must point to ./lib/index.mjs (got ' + rootPath + ')');
  }

  return {
    name: 'package.json dsh.client declaration (DSH-official format)',
    ok: errors.length === 0,
    errors
  };
}

/* ------------------------------------------------------------------ *
 * Bundled browser-half checks (dist/client.js — the real DSH-served file)
 * ------------------------------------------------------------------ */
export function runBundleChecks() {
  const errors = [];
  let dist;
  try {
    dist = read('dist/client.js');
  } catch (e) {
    errors.push('dist/client.js missing — run `node build.mjs` first');
    return { name: 'bundled browser half (dist/client.js materializes)', ok: false, errors };
  }

  // The real DSH factory require has no relative-path branch.
  if (dist.includes("require('./")) {
    errors.push('dist/client.js must not contain relative require() calls');
  }
  if (!dist.includes('__ModuleLoader__.load({')) {
    errors.push('dist/client.js must use object-form __ModuleLoader__.load({id, factory})');
  }
  if (!dist.includes('id: "dsh-client-ui-autoknit"')) {
    errors.push('dist/client.js bundle id mismatch');
  }

  // Materialize the bundle in a fake window whose only require target is react.
  const reactStub = {
    useState: () => [null, () => {}],
    useEffect: () => {},
    useRef: (init) => ({ current: init }),
    createElement: (t) => t
  };
  let captured = { id: null, factory: null };
  let viewInjected = null;
  const fakeWindow = {
    __ModuleLoader__: {
      load: (...args) => {
        const reg = typeof args[0] === 'string' ? { id: args[0], factory: args[1] } : args[0];
        captured.id = reg.id;
        captured.factory = reg.factory;
      }
    }
  };
  const fakeSlots = {
    inject: (template, registrant) => { if (template === 'conversation.view') viewInjected = registrant; },
    register: (...args) => ({ registered: args })
  };
  const stubRequire = (id) => {
    if (id === 'react') return reactStub;
    throw new Error('unexpected require in bundle: ' + id);
  };

  const sandbox = { window: fakeWindow, require: stubRequire };
  vm.createContext(sandbox);
  try {
    vm.runInContext(dist, sandbox, { filename: 'dist/client.js' });
  } catch (e) {
    errors.push('dist/client.js failed to load: ' + e.message);
    return { name: 'bundled browser half (dist/client.js materializes)', ok: false, errors };
  }
  if (captured.id !== 'dsh-client-ui-autoknit') errors.push('bundle did not register expected id');
  if (typeof captured.factory !== 'function') errors.push('bundle factory must be a function');
  let mod;
  try {
    mod = captured.factory(stubRequire);
  } catch (e) {
    errors.push('bundle factory failed to materialize: ' + e.message);
    return { name: 'bundled browser half (dist/client.js materializes)', ok: false, errors };
  }
  if (typeof mod.apply !== 'function') {
    errors.push('bundle factory exports apply must be a function (got ' + typeof mod.apply + ')');
  } else {
    mod.apply({ slots: fakeSlots });
    if (typeof viewInjected !== 'function') {
      errors.push("bundle apply() did not register via ctx.slots.inject('conversation.view')");
    } else {
      const reg = viewInjected();
      if (!reg || typeof reg.registered === 'undefined') errors.push('conversation.view registrant did not call slots.register');
      else {
        // Component is a React function component. Invoking it with mock props
        // exercises the inlined helpers (i18n.makeT, logic, style) so a broken
        // inline module (e.g. empty exports) surfaces here instead of as a
        // blank view at runtime.
        const Component = reg.registered[1];
        try {
          Component({});
        } catch (e) {
          errors.push('panel component render threw: ' + e.message);
        }
      }
    }
  }

  return {
    name: 'bundled browser half (dist/client.js materializes + exports apply + conversation.view)',
    ok: errors.length === 0,
    errors
  };
}

/** Collect every check into one flat list (async). */
export async function runAllChecks() {
  const results = [
    await runNodeHalfChecks(),
    runRenderPayloadTests(),
    runArchiveSwitchTests(),
    runI18nTests(),
    runConfigResolutionTests(),
    runDetailSectionsTests(),
    runStyleInjectionTest(),
    runBrowserHarnessTest(),
    runBundleChecks(),
    runPackageChecks(),
    await runDataBridgeTests()
  ];
  return results;
}

/* ------------------------------------------------------------------ *
 * i18n tests (final-block polish)
 * ------------------------------------------------------------------ */
export function runI18nTests() {
  const errors = [];
  const zh = I18N.makeT('zh');
  const en = I18N.makeT('en-US');
  const zhFallback = I18N.makeT('fr');

  if (zh('panel.title') !== 'AutoKnit 任务') errors.push(`zh title mismatch: ${zh('panel.title')}`);
  if (en('panel.title') !== 'AutoKnit Tasks') errors.push(`en title mismatch: ${en('panel.title')}`);
  // zh fallback for unknown locale
  if (zhFallback('panel.title') !== 'AutoKnit 任务') errors.push('unknown locale should fall back to zh');
  // placeholder substitution
  const count = zh('panel.count', { active: 3, total: 5 });
  if (count !== '3 活动 / 5 全部') errors.push(`placeholder substitution mismatch: ${count}`);
  // unknown key returns the key itself
  if (zh('no.such.key') !== 'no.such.key') errors.push('unknown key should be returned verbatim');
  // en placeholder
  if (en('panel.count', { active: 1, total: 2 }) !== '1 active / 2 total') {
    errors.push(`en count mismatch: ${en('panel.count', { active: 1, total: 2 })}`);
  }
  if (I18N.normalizeLocale('EN') !== 'en') errors.push('normalizeLocale EN should map to en');

  return {
    name: 'i18n messages (zh/en, placeholders, fallback)',
    ok: errors.length === 0,
    errors
  };
}

/* ------------------------------------------------------------------ *
 * Data-bridge base-URL config resolution tests (final-block polish)
 * ------------------------------------------------------------------ */
export function runConfigResolutionTests() {
  const errors = [];
  if (LOGIC.resolveBaseURL() !== '/api') errors.push('default base should be /api');
  if (LOGIC.resolveBaseURL('/custom') !== '/custom') errors.push('explicit baseURL should win');
  if (LOGIC.resolveBaseURL('', '/host-base') !== '/host-base') {
    errors.push('host config base should be used when no explicit base');
  }
  if (LOGIC.resolveBaseURL('/a/', '/ignored') !== '/a') {
    errors.push('explicit base should be right-trimmed of trailing slashes');
  }
  if (LOGIC.resolveBaseURL(undefined, undefined, '/default') !== '/default') {
    errors.push('defaultBase fallback should be honored');
  }
  return {
    name: 'data-bridge base-URL config resolution (options → host → /api)',
    ok: errors.length === 0,
    errors
  };
}

/* ------------------------------------------------------------------ *
 * Multi-task detail sections tests (final-block polish)
 * ------------------------------------------------------------------ */
export function runDetailSectionsTests() {
  const errors = [];
  const detail = LOGIC.normalizeTask({
    run_id: 'run-1',
    stage: 'executor',
    module_states: { m01: 'done', m02: 'running' },
    consumption: { tokens: 100, cost: 0.02 }
  });
  const sections = LOGIC.buildDetailSections(detail);

  if (!Array.isArray(sections) || sections.length !== 3) {
    errors.push(`expected 3 sections got ${sections.length}`);
  }
  const labels = sections.map((s) => s.label);
  if (labels.indexOf('meta') === -1 || labels.indexOf('module_states') === -1 || labels.indexOf('consumption') === -1) {
    errors.push('sections missing meta/module_states/consumption');
  }
  const modSection = sections.find((s) => s.label === 'module_states');
  if (!modSection || modSection.items.length !== 2) errors.push('module_states items should be 2');
  if (modSection && !modSection.items.some((i) => i.key === 'm02' && i.value === 'running')) {
    errors.push('module_states item m02/running missing');
  }
  // empty consumption → empty items, not crash
  const emptySections = LOGIC.buildDetailSections({ run_id: 'r', stage: 'x' });
  const emptyCons = emptySections.find((s) => s.label === 'consumption');
  if (!emptyCons || emptyCons.items.length !== 0) errors.push('empty consumption should yield zero items');
  // meta always carries run + stage
  const meta = sections.find((s) => s.label === 'meta');
  if (!meta || meta.items.length < 2) errors.push('meta should carry run + stage');

  return {
    name: 'multi-task detail sections (structured meta/module_states/consumption)',
    ok: errors.length === 0,
    errors
  };
}

/* ==================================================================== *
 * Route-map logic tests (this block)
 *
 * These are covered by `node --test test/route-map.test.mjs`. They are
 * intentionally NOT added to runAllChecks() so verify_bundle.mjs keeps its
 * 11/11 gate unchanged.
 * ==================================================================== */

export function runStatusDerivationTests() {
  const errors = [];
  const cases = [
    [{ status: 'done', last_verdict: 'ok' }, 'done'],
    [{ status: 'pending' }, 'pending'],
    [{ status: 'running' }, 'running'],
    [{ status: 'needs_human' }, 'needs_human'],
    [{ status: 'block' }, 'block'],
    // status unknown → derive from last_verdict
    [{ last_verdict: 'ok' }, 'done'],
    [{ last_verdict: 'revise' }, 'needs_human'],
    [{ last_verdict: 'block' }, 'block'],
    [{ last_verdict: 'other' }, 'pending'],
    [{}, 'pending'],
    [null, 'pending']
  ];
  for (const [input, expected] of cases) {
    const got = LOGIC.deriveStatus(input);
    if (got !== expected) errors.push(`deriveStatus(${JSON.stringify(input)}) expected ${expected} got ${got}`);
  }
  if (LOGIC.deriveStatus({ status: 'bogus', last_verdict: 'ok' }) !== 'done') {
    errors.push('unknown status should fall back to last_verdict derivation');
  }
  if (LOGIC.MODULE_STATUSES.join(',') !== 'done,pending,running,needs_human,block') {
    errors.push('MODULE_STATUSES enum drift');
  }
  return {
    name: 'route-map: deriveStatus (status + last_verdict)',
    ok: errors.length === 0,
    errors
  };
}

export function runTopologyTests() {
  const errors = [];
  // m0 has no deps; m1 depends on m0; m2 depends on m0 (parallel with m1);
  // m3 depends on m1+m2.
  const nodes = [
    { id: 'm0', dependencies: [] },
    { id: 'm1', dependencies: ['m0'] },
    { id: 'm2', dependencies: ['m0'] },
    { id: 'm3', dependencies: ['m1', 'm2'] }
  ];
  const { layers } = LOGIC.topoLayer(nodes);
  if (layers.length !== 3) errors.push(`expected 3 layers got ${layers.length}`);
  const names = (l) => l.map((n) => n.id).sort();
  if (JSON.stringify(names(layers[0])) !== JSON.stringify(['m0'])) {
    errors.push(`layer0 should be [m0], got ${names(layers[0])}`);
  }
  if (JSON.stringify(names(layers[1])) !== JSON.stringify(['m1', 'm2'])) {
    errors.push(`layer1 should be [m1,m2], got ${names(layers[1])}`);
  }
  if (JSON.stringify(names(layers[2])) !== JSON.stringify(['m3'])) {
    errors.push(`layer2 should be [m3], got ${names(layers[2])}`);
  }

  // empty input
  if (LOGIC.topoLayer([]).layers.length !== 0) errors.push('empty input should yield zero layers');

  // dangling + self dependencies are ignored
  const g2 = LOGIC.topoLayer([
    { id: 'a', dependencies: ['ghost', 'a'] },
    { id: 'b', dependencies: ['a'] }
  ]);
  if (g2.layers.length !== 2 || g2.layers[0][0].id !== 'a') {
    errors.push('dangling/self deps should not break layering');
  }

  // cycle never drops a module (stragglers flushed into a final layer)
  const cycle = LOGIC.topoLayer([
    { id: 'x', dependencies: ['y'] },
    { id: 'y', dependencies: ['x'] }
  ]);
  const flat = cycle.layers.reduce((acc, l) => acc.concat(l.map((n) => n.id)), []);
  if (flat.length !== 2 || flat.indexOf('x') === -1 || flat.indexOf('y') === -1) {
    errors.push('cycle should still place every node');
  }

  return {
    name: 'route-map: topoLayer (Kahn topological layering)',
    ok: errors.length === 0,
    errors
  };
}

export function runTimelineTests() {
  const errors = [];
  const raw = {
    rounds: [
      { role: 'executor', round: 1, verdict: 'ok', duration_ms: 12000 },
      { role: 'auditor', round: 1, verdict: 'ok', duration_ms: 8000 },
      { role: 'executor', round: 2, verdict: 'revise', duration_ms: 5000 }
    ]
  };
  const parsed = LOGIC.parseTimeline(raw);
  if (parsed.total !== 3) errors.push(`timeline total expected 3 got ${parsed.total}`);
  if (parsed.totalMs !== 25000) errors.push(`timeline totalMs expected 25000 got ${parsed.totalMs}`);
  if (parsed.rounds[0].role !== 'executor') errors.push('round role mismatch');
  if (parsed.rounds[2].verdict !== 'revise') errors.push('round verdict mismatch');

  // bare-array form + auto round numbering + negative/non-numeric duration guard
  const bare = LOGIC.parseTimeline([
    { role: 'auditor', verdict: 'block' },
    { role: 'executor', duration_ms: -5 }
  ]);
  if (bare.rounds[0].round !== 1 || bare.rounds[1].round !== 2) errors.push('auto round numbering broken');
  if (bare.rounds[1].duration_ms !== 0) errors.push('negative duration should clamp to 0');
  if (bare.totalMs !== 0) errors.push('totalMs should ignore clamped durations');

  // invalid input shapes
  if (LOGIC.parseTimeline(null).total !== 0) errors.push('null timeline should be empty');
  if (LOGIC.parseTimeline({}).total !== 0) errors.push('empty object timeline should be empty');

  // formatDuration
  if (LOGIC.formatDuration(0) !== '0s') errors.push('formatDuration 0');
  if (LOGIC.formatDuration(125000) !== '2m 5s') errors.push(`formatDuration 125000: ${LOGIC.formatDuration(125000)}`);
  if (LOGIC.formatDuration(3700000) !== '1h 1m') errors.push(`formatDuration 3700000: ${LOGIC.formatDuration(3700000)}`);

  return {
    name: 'route-map: parseTimeline + formatDuration',
    ok: errors.length === 0,
    errors
  };
}

export function runRouteMapBuildTests() {
  const errors = [];
  const tree = {
    run: { run_id: 'run-1' },
    modules: [
      {
        id: 'm0', name: 'scaffold', status: 'done', last_verdict: 'ok',
        dependencies: [], token_used: 100,
        split: [
          { id: 'm0a', name: 'scaffold-a', status: 'running' },
          { id: 'm0b', name: 'scaffold-b', status: 'pending', dependencies: ['m0a'] }
        ]
      },
      { id: 'm1', name: 'panel', status: 'needs_human', last_verdict: 'revise', dependencies: ['m0'], token_used: 50 },
      { id: 'm2', name: 'bridge', status: 'block', dependencies: ['m0'], token_used: 25 }
    ]
  };
  const rm = LOGIC.buildRouteMap(tree, { total: 175, input: 100, output: 50, cache: 25 });

  if (rm.run_id !== 'run-1') errors.push(`route map run_id mismatch: ${rm.run_id}`);
  if (rm.layers.length !== 2) errors.push(`expected 2 layers got ${rm.layers.length}`);
  // layer0 = m0 (with its split children), layer1 = m1+m2
  if (rm.layers[0].length !== 1) errors.push('layer0 should hold just m0');
  const m0 = rm.layers[0][0];
  if (m0.status !== 'done') errors.push(`m0 status should be done got ${m0.status}`);
  if (m0.split.length !== 2) errors.push(`m0 should have 2 split children got ${m0.split.length}`);
  if (m0.split[0].status !== 'running') errors.push('m0a split status should be running');
  // split child status derived from its own last_verdict/dependencies? it is explicit pending
  if (m0.split[1].status !== 'pending') errors.push('m0b split status should be pending');

  if (rm.layers[1].length !== 2) errors.push('layer1 should hold m1+m2');
  const m1 = rm.layers[1].find((x) => x.id === 'm1');
  const m2 = rm.layers[1].find((x) => x.id === 'm2');
  if (!m1 || m1.status !== 'needs_human') errors.push('m1 status should be needs_human');
  if (!m2 || m2.status !== 'block') errors.push('m2 status should be block');

  if (rm.summary.moduleCount !== 3) errors.push(`moduleCount expected 3 got ${rm.summary.moduleCount}`);
  if (rm.summary.token_used !== 175) errors.push(`summary token_used expected 175 got ${rm.summary.token_used}`);
  if (!rm.usage.hasSplit) errors.push('usage hasSplit should be true');
  if (rm.usage.total !== 175) errors.push('usage.total expected 175');

  // no-split usage run
  const rm2 = LOGIC.buildRouteMap({ modules: [{ id: 'a', status: 'done' }] }, { total: 10 });
  if (rm2.usage.hasSplit) errors.push('usage without split fields should flag hasSplit=false');

  return {
    name: 'route-map: buildRouteMap (layers/split/status/summary)',
    ok: errors.length === 0,
    errors
  };
}

export function runDataBridgeRouteTests() {
  const errors = [];
  const router = {
    'GET /api/runs/run-1/tree': { run: { run_id: 'run-1' }, modules: [{ id: 'm0' }] },
    'GET /api/runs/run-1/timeline': { rounds: [{ role: 'executor', verdict: 'ok' }] },
    'GET /api/runs/run-1/usage': { total: 42, input: 20, output: 20, cache: 2 },
    'POST /api/runs/run-1/reply': { ok: true }
  };
  const client = BRIDGE.createClient({ fetch: fakeFetch(router) });

  return client.tree('run-1').then((tr) => {
    if (!tr || !tr.modules || tr.modules[0].id !== 'm0') errors.push('tree returned wrong payload');
    return client.timeline('run-1');
  }).then((tl) => {
    if (!tl || !tl.rounds || tl.rounds[0].role !== 'executor') errors.push('timeline returned wrong payload');
    return client.usage('run-1');
  }).then((us) => {
    if (!us || us.total !== 42 || us.input !== 20) errors.push('usage returned wrong payload');
    return client.reply('run-1', { decision: 'continue', note: 'ok' });
  }).then((rep) => {
    if (!rep || rep.ok !== true) errors.push('reply returned wrong payload');
    return {
      name: 'data-bridge route endpoints (tree/timeline/usage/reply)',
      ok: errors.length === 0,
      errors
    };
  }).catch((err) => {
    errors.push('data-bridge route test threw: ' + err.message);
    return { name: 'data-bridge route endpoints (tree/timeline/usage/reply)', ok: false, errors };
  });
}

export function runRouteMapClientRenderTest() {
  const errors = [];
  const src = read('lib/client.js');
  const mustHave = [
    "client.tree(", "client.usage(", "logic.buildRouteMap(", "data-ak-route-map",
    "data-ak-module", "data-ak-status", "ak-split", "ak-column", "renderModuleBlock"
  ];
  for (const s of mustHave) {
    if (!src.includes(s)) errors.push(`lib/client.js must reference ${s}`);
  }
  // The lifecycle must remain intact.
  if (!src.includes("ctx.slots.inject('conversation.view'")) {
    errors.push("lib/client.js must still register conversation.view");
  }
  if (src.includes('register(AutoknitPanel)')) {
    errors.push('lib/client.js must not call register() directly');
  }
  return {
    name: 'client.js renders route-map (columns/split/status) + lifecycle intact',
    ok: errors.length === 0,
    errors
  };
}

/* ------------------------------------------------------------------ *
 * Style injection + stylesheet presence tests (final-block polish)
 * ------------------------------------------------------------------ */
function makeFakeDoc() {
  const head = { children: [], appendChild: (node) => { head.children.push(node); } };
  const doc = {
    head,
    createElement: () => {
      const el = { children: [], id: null, appendChild: (node) => { el.children.push(node); } };
      el.setAttribute = (name, value) => { el[name] = value; if (name === 'id') el.id = value; };
      return el;
    },
    createTextNode: (text) => ({ text }),
    getElementById: (id) => (head.children.find((c) => c.id === id) || null)
  };
  return doc;
}

export function runStyleInjectionTest() {
  const errors = [];
  if (typeof STYLE.injectStyles !== 'function') errors.push('style.js must export injectStyles');
  if (!STYLE.CSS || STYLE.CSS.indexOf('.ak-details-panel') === -1) {
    errors.push('style.js CSS must contain panel styles');
  }
  const cssFile = read('lib/styles.css');
  if (cssFile.indexOf('.ak-details-panel') === -1) errors.push('lib/styles.css must exist and be non-empty');
  if (cssFile.indexOf('@keyframes ak-spin') === -1) errors.push('styles.css missing loading spinner keyframes');

  // idempotent injection into a fake DOM
  const doc = makeFakeDoc();
  const first = STYLE.injectStyles(null, doc);
  const second = STYLE.injectStyles(null, doc);
  if (first !== true) errors.push('injectStyles should inject once');
  if (second !== true) errors.push('injectStyles should be idempotent (second call no-op)');
  if (doc.head.children.length !== 1) errors.push(`expected exactly 1 style element, got ${doc.head.children.length}`);

  // no-DOM environment must be a safe no-op (node test harness)
  const noDoc = STYLE.injectStyles({}, null);
  if (noDoc !== false) errors.push('injectStyles without a document should return false');

  // browser half must actually inject styles
  const clientSrc = read('lib/client.js');
  if (!clientSrc.includes('./style.js') || !clientSrc.includes('injectStyles')) {
    errors.push('lib/client.js must require ./style.js and call injectStyles');
  }

  return {
    name: 'styles: styles.css present + style.js injects once, idempotent, DOM-guarded',
    ok: errors.length === 0,
    errors
  };
}
