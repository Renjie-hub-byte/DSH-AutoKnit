'use strict';

/**
 * dsh-client-ui-autoknit — pure domain logic (framework-agnostic).
 *
 * Holds the data-normalization, render-payload, archive-filtering and
 * detail-selection rules shared by the browser half. It has **no DOM, no
 * React and no global state**, so the exact same module is unit-tested in
 * plain Node and executed inside the browser bundle.
 */

/** Shared task_stage enum — must stay aligned with contracts/data.yaml. */
var TASK_STAGES = ['executor', 'auditor', 'switch', 'needs_human', 'planning', 'unknown'];

/**
 * Extract a stable run_id from either the data-bridge camelCase form or the
 * contract snake_case form.
 * @param {object} task raw task record
 * @returns {string}
 */
function getRunId(task) {
  if (!task || typeof task !== 'object') return '';
  var id = task.run_id != null ? task.run_id : task.runId;
  return String(id == null ? '' : id);
}

/**
 * Normalize a raw task record from the data bridge into a renderable shape.
 * Guarantees the fields the details panel renders: run_id / stage /
 * module_states / consumption.
 * @param {object} raw
 * @returns {object} normalized task
 */
function normalizeTask(raw) {
  raw = raw || {};
  var stage = raw.stage != null ? String(raw.stage) : 'unknown';
  return {
    run_id: getRunId(raw),
    stage: TASK_STAGES.indexOf(stage) !== -1 ? stage : 'unknown',
    module_states: raw.module_states && typeof raw.module_states === 'object' ? raw.module_states : {},
    consumption: raw.consumption && typeof raw.consumption === 'object' ? raw.consumption : {},
    archived: !!(raw.archived || raw.is_archived)
  };
}

/**
 * Build the render payload for the task-list panel.
 * @param {Array} tasks raw task list from the data bridge
 * @param {object} [opts]
 * @param {Array<string>} [opts.archivedRunIds] run_ids already archived
 * @returns {{total:number, active:number, tasks:Array<object>}} payload
 */
function buildRenderPayload(tasks, opts) {
  opts = opts || {};
  var normalized = (Array.isArray(tasks) ? tasks : []).map(normalizeTask);
  var active = normalized.filter(function (t) {
    return !t.archived;
  });
  var archivedRunIds = Array.isArray(opts.archivedRunIds) ? opts.archivedRunIds : [];
  var archivedSet = {};
  archivedRunIds.forEach(function (id) {
    archivedSet[String(id)] = true;
  });
  active = active.filter(function (t) {
    return !archivedSet[t.run_id];
  });
  return {
    total: normalized.length,
    active: active.length,
    tasks: active
  };
}

/**
 * Keep only active (non-archived) tasks, given the set of archived run_ids.
 * @param {Array} tasks raw task list
 * @param {Array<string>} archivedRunIds
 * @returns {Array<object>} normalized active tasks
 */
function filterActive(tasks, archivedRunIds) {
  return buildRenderPayload(tasks, { archivedRunIds: archivedRunIds }).tasks;
}

/**
 * Return a new task list with the given run_id removed (post-archive refresh).
 * @param {Array} tasks normalized tasks
 * @param {string} runId
 * @returns {Array<object>}
 */
function removeArchived(tasks, runId) {
  var id = String(runId);
  return (Array.isArray(tasks) ? tasks : []).filter(function (t) {
    return getRunId(t) !== id;
  });
}

/**
 * Select a task for the detail view.
 * @param {Array} tasks normalized tasks
 * @param {string} runId
 * @returns {object|null} matched task or null
 */
function selectTask(tasks, runId) {
  var id = String(runId);
  var hit = null;
  (Array.isArray(tasks) ? tasks : []).some(function (t) {
    if (getRunId(t) === id) {
      hit = t;
      return true;
    }
    return false;
  });
  return hit;
}

/**
 * A pure reducer that applies an archive action to the current UI state.
 * Keeps the archive interaction side-effect-free so it can be unit-tested.
 * @param {{tasks:Array, detail:object|null}} state
 * @param {string} runId
 * @returns {{tasks:Array, detail:object|null}} next state
 */
function reduceArchive(state, runId) {
  state = state || {};
  var tasks = removeArchived(state.tasks, runId);
  var detail = state.detail && getRunId(state.detail) === String(runId) ? null : (state.detail || null);
  return { tasks: tasks, detail: detail };
}

/**
 * Resolve the data-bridge base URL from, in order of priority:
 *   1. an explicit `baseURL` option,
 *   2. a host-provided config value (e.g. `window.__DSH__.config.autoknit.baseURL`),
 *   3. the default '/api'.
 * Pure and testable — the caller extracts the host config from the environment.
 * @param {string} [baseURL]
 * @param {string} [hostConfigBaseURL]
 * @param {string} [defaultBase] fallback base path, defaults to '/api'
 * @returns {string} resolved base URL, right-trimmed of trailing slashes
 */
function resolveBaseURL(baseURL, hostConfigBaseURL, defaultBase) {
  var chosen = baseURL || hostConfigBaseURL || defaultBase || '/api';
  return String(chosen).replace(/\/+$/, '');
}

/**
 * Build structured sections for the details view from a normalized task.
 * Turns raw `module_states` / `consumption` objects into ordered `{key,value}`
 * item lists so the UI can render them as readable rows instead of raw JSON.
 * @param {object} detail normalized task detail
 * @returns {Array<{label:string, items:Array<{key:string, value:*}>}>}
 */
function buildDetailSections(detail) {
  detail = detail || {};
  var sections = [];

  var metaItems = [];
  metaItems.push({ key: 'run', value: getRunId(detail) });
  metaItems.push({ key: 'stage', value: detail.stage != null ? detail.stage : 'unknown' });
  if (detail.archived) metaItems.push({ key: 'archived', value: true });
  sections.push({ label: 'meta', items: metaItems });

  sections.push({ label: 'module_states', items: toItems(detail.module_states) });
  sections.push({ label: 'consumption', items: toItems(detail.consumption) });

  return sections;
}

/** Flatten an object into an ordered `{key,value}` item list (empty → []). */
function toItems(obj) {
  if (!obj || typeof obj !== 'object') return [];
  return Object.keys(obj).map(function (k) {
    return { key: k, value: obj[k] };
  });
}

/* ==================================================================== *
 * Route-map domain logic (this block)
 *
 * Pure, framework-agnostic helpers consumed by the horizontal flow-chart
 * renderer in client.js. No DOM, no React, no global state — unit-tested in
 * plain Node via test/route-map.test.mjs.
 * ==================================================================== */

/** Canonical module status enum for the route map (must align with contracts). */
var MODULE_STATUSES = ['done', 'pending', 'running', 'needs_human', 'block'];

/**
 * Derive the canonical module status badge from raw `status` + `last_verdict`.
 * `status` is authoritative when it is one of the known states; otherwise we
 * fall back on `last_verdict` (ok→done, revise→needs_human, block→block) and
 * finally to `pending`.
 * @param {object} node raw module record
 * @returns {string} one of done|pending|running|needs_human|block
 */
function deriveStatus(node) {
  node = node || {};
  var s = node.status != null ? String(node.status) : '';
  if (MODULE_STATUSES.indexOf(s) !== -1) return s;
  var verdict = node.last_verdict != null ? String(node.last_verdict) : '';
  if (verdict === 'ok') return 'done';
  if (verdict === 'revise') return 'needs_human';
  if (verdict === 'block') return 'block';
  return 'pending';
}

/**
 * Topologically layer a set of modules by their `dependencies` (Kahn's
 * algorithm). Nodes with no dependencies land in layer 0; a node lands in the
 * first layer strictly after all of its in-graph dependencies. Same-layer
 * nodes are parallel siblings rendered as one column. Dangling dependency ids
 * (not present in the set) and self-edges are ignored; nodes trapped in a
 * dependency cycle are flushed as a final layer so the renderer never drops a
 * module.
 * @param {Array<object>} nodes modules, each with `id` and optional `dependencies`
 * @returns {{layers: Array<Array<object>>}} layers of module records
 */
function topoLayer(nodes) {
  var list = Array.isArray(nodes) ? nodes : [];
  var byId = {};
  list.forEach(function (n) {
    if (n && n.id != null) byId[String(n.id)] = n;
  });

  var indegree = {};
  var dependents = {};
  list.forEach(function (n) {
    if (n == null || n.id == null) return;
    var id = String(n.id);
    indegree[id] = 0;
  });
  list.forEach(function (n) {
    if (n == null || n.id == null) return;
    var id = String(n.id);
    var deps = Array.isArray(n.dependencies) ? n.dependencies : [];
    deps.forEach(function (d) {
      if (d == null) return;
      var did = String(d);
      if (did === id) return;                 // ignore self-edge
      if (!(did in byId)) return;             // ignore dangling dependency
      (dependents[did] = dependents[did] || []).push(id);
      indegree[id] = (indegree[id] || 0) + 1;
    });
  });
  Object.keys(dependents).forEach(function (k) {
    dependents[k] = Array.from(new Set(dependents[k]));
  });

  var layers = [];
  var queued = {};
  var ready = function () {
    return list.filter(function (n) {
      if (n == null || n.id == null) return false;
      var id = String(n.id);
      return !queued[id] && (indegree[id] || 0) === 0;
    });
  };

  var layer = ready();
  while (layer.length) {
    layers.push(layer);
    layer.forEach(function (n) {
      var id = String(n.id);
      queued[id] = true;
      (dependents[id] || []).forEach(function (depId) {
        indegree[depId] = (indegree[depId] || 0) - 1;
      });
    });
    layer = ready();
  }

  // Cycle stragglers: anything never enqueued lands in one final layer.
  var stragglers = list.filter(function (n) {
    return n != null && n.id != null && !queued[String(n.id)];
  });
  if (stragglers.length) layers.push(stragglers);

  return { layers: layers };
}

/**
 * Parse a raw timeline payload (role-rounds chain) into a normalized list of
 * round cards plus aggregate totals. Accepts a bare array or an object with a
 * `rounds` / `events` / `timeline` array field.
 * @param {Array|object} raw timeline payload
 * @returns {{rounds:Array<object>, total:number, totalMs:number}}
 *   each round: {role, round, verdict, duration_ms}
 */
function parseTimeline(raw) {
  var items = Array.isArray(raw) ? raw : null;
  if (!items && raw && typeof raw === 'object') {
    var candidate = raw.rounds || raw.events || raw.timeline;
    items = Array.isArray(candidate) ? candidate : [];
  }
  items = items || [];

  var rounds = [];
  var totalMs = 0;
  items.forEach(function (r, idx) {
    r = r || {};
    var role = r.role != null ? String(r.role) : (r.type != null ? String(r.type) : 'unknown');
    var round = r.round != null ? Number(r.round) : idx + 1;
    if (!isFinite(round) || round < 1) round = idx + 1;
    var duration = r.duration_ms != null ? Number(r.duration_ms) : 0;
    if (!isFinite(duration) || duration < 0) duration = 0;
    var verdict = r.verdict != null ? String(r.verdict) : 'pending';
    totalMs += duration;
    rounds.push({
      role: role,
      round: round,
      verdict: verdict,
      duration_ms: duration,
      started_at: r.started_at != null ? r.started_at : (r.ts != null ? r.ts : null)
    });
  });
  return { rounds: rounds, total: rounds.length, totalMs: totalMs };
}

/**
 * Format a millisecond duration as a compact human label (e.g. "3m 20s").
 * @param {number} ms
 * @returns {string}
 */
function formatDuration(ms) {
  var v = Number(ms);
  if (!isFinite(v) || v < 0) v = 0;
  var totalSec = Math.round(v / 1000);
  var h = Math.floor(totalSec / 3600);
  var m = Math.floor((totalSec % 3600) / 60);
  var s = totalSec % 60;
  if (h > 0) return h + 'h ' + m + 'm';
  if (m > 0) return m + 'm ' + s + 's';
  return s + 's';
}

/**
 * Build one normalized module view for the route-map renderer, recursively
 * including its `split` submodules (which hang below the parent with a
 * connector line). Exposes the derived status badge, dependencies and any
 * per-module token usage.
 * @param {object} m raw module record
 * @returns {object} normalized module view
 */
function buildModuleView(m) {
  m = m || {};
  var children = Array.isArray(m.split) ? m.split.map(buildModuleView) : [];
  return {
    id: m.id != null ? String(m.id) : '',
    name: m.name != null ? String(m.name) : (m.id != null ? String(m.id) : ''),
    status: deriveStatus(m),
    last_verdict: m.last_verdict != null ? String(m.last_verdict) : '',
    reason: m.reason != null ? String(m.reason) : '',
    dependencies: Array.isArray(m.dependencies) ? m.dependencies.map(String) : [],
    split: children,
    token_used: m.token_used != null ? Number(m.token_used) : 0,
    started_at: m.started_at != null ? m.started_at : null,
    ended_at: m.ended_at != null ? m.ended_at : null
  };
}

/**
 * Build the route-map render payload from a raw tree payload (plus an optional
 * usage payload). Layered by topology; each layer is a horizontal column of
 * module views; split submodules travel inside their parent view.
 * @param {object} tree raw tree payload ({modules:[...]} or {nodes:[...]})
 * @param {object} [usage] raw usage payload ({total, input, output, cache})
 * @returns {{run_id:string, layers:Array<Array<object>>, summary:object}}
 */
function buildRouteMap(tree, usage) {
  tree = tree || {};
  var nodes = Array.isArray(tree.modules) ? tree.modules : (Array.isArray(tree.nodes) ? tree.nodes : []);
  var topo = topoLayer(nodes);
  var layers = topo.layers.map(function (layer) {
    return layer.map(buildModuleView);
  });
  var summary = {
    moduleCount: nodes.length,
    token_used: 0
  };
  layers.forEach(function (layer) {
    layer.forEach(function (m) {
      summary.token_used += m.token_used || 0;
    });
  });
  return {
    run_id: (tree.run && (tree.run.run_id || tree.run.id)) || tree.run_id || tree.runId || '',
    layers: layers,
    summary: summary,
    usage: normalizeUsage(usage),
    usageByModule: normalizeUsageByModule(usage)
  };
}

/**
 * Normalize a raw usage payload into a token-breakdown shape. A run without
 * split data is flagged so the UI can show "无拆分数据" instead of zeros.
 * Accepts both the bridge shape ({run:{input, output, cache_read, billable},
 * per_module:{...}}) and the flat legacy shape ({total, input, output, cache}).
 * @param {object} raw usage payload
 * @returns {{total:number, input:number, output:number, cache:number, hasSplit:boolean}}
 */
function normalizeUsage(raw) {
  raw = raw || {};
  var run = (raw.run && typeof raw.run === 'object') ? raw.run : raw;
  var num = function (v) { var n = Number(v); return isFinite(n) ? n : 0; };
  var inputRaw = run.input != null ? run.input : raw.input;
  var outputRaw = run.output != null ? run.output : raw.output;
  var cacheRaw = run.cache_read != null ? run.cache_read
    : (run.cache != null ? run.cache : raw.cache);
  var totalRaw = run.billable != null ? run.billable
    : (run.total != null ? run.total
      : (raw.total != null ? raw.total
        : (run.token_used != null ? run.token_used : raw.token_used)));
  var durationRaw = run.duration_ms != null ? run.duration_ms : raw.duration_ms;
  var hasSplit = inputRaw != null || outputRaw != null || cacheRaw != null;
  // 口径对齐（2026-09-01 杰哥拍板，与桥 usage.py 一致）：inputTokens 本身即
  // 非缓存输入（pi-ai 层 input = prompt_tokens − cacheRead）→ 展示层「输入」
  // = input + cache_read（总输入），缓存命中/命中率单独一列；total 取桥
  // billable = 非缓存输入 + 输出（计费口径，缓存不计费）。
  var inputTotal = num(inputRaw) + num(cacheRaw);
  var cacheRate = (inputTotal > 0 && num(cacheRaw) > 0)
    ? Math.round(num(cacheRaw) / inputTotal * 100)
    : null;
  return {
    total: num(totalRaw),
    input: inputTotal,
    output: num(outputRaw),
    cache: num(cacheRaw),
    cacheRate: cacheRate,
    durationMs: num(durationRaw),
    hasSplit: !!hasSplit
  };
}

/**
 * Normalize the per-module usage breakdown (bridge {per_module:{id:{...}}})
 * into a {moduleId → normalized usage} map for per-module detail views.
 * @param {object} raw usage payload
 * @returns {Object<string, {total:number, input:number, output:number, cache:number, hasSplit:boolean}>}
 */
function normalizeUsageByModule(raw) {
  raw = raw || {};
  var per = (raw.per_module && typeof raw.per_module === 'object') ? raw.per_module : {};
  var out = {};
  Object.keys(per).forEach(function (id) {
    out[id] = normalizeUsage(per[id]);
  });
  return out;
}

/**
 * Aggregate a module's normalized usage INCLUDING all its recursive split
 * submodules (a split parent like m03 rolls up its children m03a/…). `m` is a
 * route-map module view (with a `split` array). Returns null when neither the
 * module nor any descendant has a usage record.
 * @param {Object<string, object>} usageByModule normalized per-module usage
 * @param {object} m module view (id + split descendants)
 * @returns {{total:number, input:number, output:number, cache:number, cacheRate:number|null, hasSplit:boolean}|null}
 */
function aggregateUsageByTree(usageByModule, m) {
  if (!m) return null;
  var id = m.id != null ? String(m.id) : '';
  var acc = null;
  var self = (usageByModule && usageByModule[id]);
  if (self) {
    acc = {
      total: self.total,
      input: self.input,
      output: self.output,
      cache: self.cache,
      cacheRate: self.cacheRate,
      durationMs: self.durationMs || 0,
      hasSplit: self.hasSplit
    };
  }
  var children = Array.isArray(m.split) ? m.split : [];
  children.forEach(function (c) {
    var sub = aggregateUsageByTree(usageByModule, c);
    if (sub) {
      if (!acc) acc = { total: 0, input: 0, output: 0, cache: 0, cacheRate: null, durationMs: 0, hasSplit: false };
      acc.total += sub.total;
      acc.input += sub.input;
      acc.output += sub.output;
      acc.cache += sub.cache;
      // 用时是时间窗跨度：父子模块时间重叠，取最大值近似子树总时长（不双计）。
      acc.durationMs = Math.max(acc.durationMs || 0, sub.durationMs || 0);
      acc.hasSplit = acc.hasSplit || sub.hasSplit;
    }
  });
  if (acc) {
    acc.cacheRate = (acc.input > 0 && acc.cache > 0)
      ? Math.round(acc.cache / acc.input * 100)
      : null;
  }
  return acc;
}

/**
 * Canonical human-decision commands for a needs_human module. Must stay aligned
 * with the m01 reply bridge whitelist (continue/retry/revise/custom).
 */
var REPLY_COMMANDS = ['continue', 'retry', 'revise', 'custom'];

/**
 * Pure form validation for the needs_human decision dialog.
 *
 * Rules:
 *   - `command` must be one of REPLY_COMMANDS (continue/retry/revise/custom).
 *   - `instruction` (the free-input box) is optional for continue/retry/revise,
 *     but REQUIRED when the command is `custom` (自定义必填).
 *
 * The result carries a `command` normalized to the trimmed canonical value and
 * i18n error keys (not user-facing strings) so the UI maps them to messages and
 * the whole function stays framework-free / unit-testable.
 * @param {string} [command]
 * @param {string} [instruction]
 * @returns {{ok:boolean, command:string, errors:{command?:string, instruction?:string}}}
 */
function validateReplyCommand(command, instruction) {
  var errors = {};
  var cmd = command == null ? '' : String(command).trim();
  if (REPLY_COMMANDS.indexOf(cmd) === -1) {
    errors.command = 'reply.command.invalid';
  }
  if (cmd === 'custom') {
    var txt = instruction == null ? '' : String(instruction).trim();
    if (!txt) {
      errors.instruction = 'reply.instruction.required';
    }
  }
  return {
    ok: Object.keys(errors).length === 0,
    command: cmd,
    errors: errors
  };
}

/** Statuses that mark a module as currently active (needs the highlight ring). */
var ACTIVE_STATUSES = ['running', 'needs_human'];

/**
 * Derive the currently active module view from a built route-map payload.
 *
 * Walks layers in topological order (and, within a layer, the module array
 * order), then each module's split children, and returns the first module whose
 * derived status is `running` or `needs_human`. These are the cards that get the
 * highlight + border-spin animation. Returns null when nothing is active.
 * @param {object} routeMap payload from buildRouteMap
 * @returns {object|null} the active module view (has id/status) or null
 */
function deriveActiveModule(routeMap) {
  var layers = (routeMap && Array.isArray(routeMap.layers)) ? routeMap.layers : [];
  var found = null;
  layers.some(function (layer) {
    return (Array.isArray(layer) ? layer : []).some(function (m) {
      if (!m) return false;
      if (ACTIVE_STATUSES.indexOf(m.status) !== -1) {
        found = m;
        return true;
      }
      if (Array.isArray(m.split)) {
        var sub = null;
        m.split.some(function (c) {
          if (c && ACTIVE_STATUSES.indexOf(c.status) !== -1) {
            sub = c;
            return true;
          }
          return false;
        });
        if (sub) { found = sub; return true; }
      }
      return false;
    });
  });
  return found;
}

/* ==================================================================== *
 * Detail / polling domain logic (final block)
 *
 * Pure helpers that (a) derive a module block's popover payload and
 * block-bottom aggregate metrics and (b) normalize + apply lightweight poll
 * payloads (full tree re-render or /api/events incremental). No DOM, no React,
 * no global state — unit-tested in plain Node via test/final.test.mjs.
 * ==================================================================== */

/**
 * Derive the block-bottom aggregate metrics for a module block.
 * `token_used` comes from the module view; total duration and round count come
 * from the run's role-rounds timeline. Accepts either a raw timeline payload or
 * an already-parsed `{rounds,total,totalMs}` object.
 * @param {object} m normalized module view
 * @param {Array|object} timeline raw timeline payload or parsed result
 * @returns {{token_used:number, totalMs:number, rounds:number}}
 */
function buildBlockSummary(m, timeline) {
  m = m || {};
  // Already-parsed `{rounds,total,totalMs}` passes through; a raw bridge event
  // stream is summarized per-module (real E/A durations); legacy idealized
  // timelines keep parseTimeline. Without this, the real stream yielded
  // totalMs=0 → every module block showed "0s".
  var t = (timeline && typeof timeline === 'object' &&
           typeof timeline.total === 'number' && typeof timeline.totalMs === 'number')
    ? timeline
    : (isBridgeTimeline(timeline) ? summarizeModuleRounds(timeline, m.id) : parseTimeline(timeline));
  var totalMs = t.totalMs;
  var rounds = t.total;
  // 外层模块块 token：优先 usageTotal（含缓存总消耗、已含全部子模块，与详情
  // 一致）；否则回退快照 token_used（该口径下才需要递归累加子模块）。
  var hasUsageTotal = m.usageTotal != null;
  var token = hasUsageTotal
    ? Number(m.usageTotal)
    : (m.token_used != null ? Number(m.token_used) : 0);
  // A split parent module's block shows ITS OWN + all recursive submodules'
  // rounds/duration/token (e.g. m03's block includes its split child m03a).
  // Without this, m03's time/consumption looked like only its own 2 rounds.
  if (m.fork && Array.isArray(m.fork.children)) {
    m.fork.children.forEach(function (c) {
      var sub = buildBlockSummary(c, timeline);
      totalMs += sub.totalMs;
      rounds += sub.rounds;
      if (!hasUsageTotal) token += sub.token_used;
    });
  }
  return {
    token_used: token,
    totalMs: totalMs,
    rounds: rounds
  };
}

/**
 * Derive the full popover payload for a module block: module meta (incl.
 * reason), the executor/auditor round chain (+ verdict + duration), the token
 * breakdown (input/output/cache), timing, and split-children info.
 * @param {object} m normalized module view
 * @param {Array|object} timeline raw timeline payload
 * @param {object} usage raw usage payload
 * @returns {object} detail payload
 */
function buildModuleDetail(m, timeline, usage) {
  m = m || {};
  // The live bridge timeline is the real dispatch event stream; summarize the
  // module's own E/A rounds from it. Legacy/idealized timelines keep parseTimeline.
  var t = isBridgeTimeline(timeline)
    ? summarizeModuleRounds(timeline, m.id)
    : parseTimeline(timeline);
  // usage 可能已是归一化形态（hasSplit 布尔字段，input 已含缓存——来自
  // usageByModule/aggregateUsageByTree/routeMap.usage）；此时若再 normalizeUsage
  // 一次，缓存会被加两遍（输入变 input+2×cache、缓存率≈50%）。检测到已归一化
  // 即透传；原始桥结构（{run:{...}}/flat）才走 normalizeUsage。
  var u = (usage && typeof usage === 'object' && typeof usage.hasSplit === 'boolean')
    ? usage
    : normalizeUsage(usage);
  var split = Array.isArray(m.split) ? m.split : [];
  return {
    id: m.id != null ? String(m.id) : '',
    name: m.name != null ? String(m.name) : '',
    status: m.status != null ? String(m.status) : 'unknown',
    reason: m.reason != null ? String(m.reason) : '',
    started_at: m.started_at != null ? m.started_at : null,
    ended_at: m.ended_at != null ? m.ended_at : null,
    splitCount: split.length,
    split: split.map(function (s) {
      return {
        id: s.id != null ? String(s.id) : '',
        name: s.name != null ? String(s.name) : '',
        status: s.status != null ? String(s.status) : 'unknown'
      };
    }),
    rounds: t.rounds,
    roundsTotal: t.total,
    durationMs: t.totalMs,
    usage: u
  };
}

/**
 * Summarize a single module's E/A rounds from the real bridge event stream
 * (same adapter parseBridgeTimeline uses). Filters cards to one module and
 * sums durations, mirroring the shape parseTimeline returns.
 * @param {Array|object} timeline real dispatch event stream
 * @param {string} moduleId module id to keep (null = all modules)
 * @returns {{rounds:Array<object>, total:number, totalMs:number}}
 */
function summarizeModuleRounds(timeline, moduleId) {
  var cards = parseBridgeTimeline(timeline).filter(function (r) {
    return moduleId == null || r.module_id === moduleId;
  });
  var totalMs = cards.reduce(function (a, c) {
    return a + (c.duration_ms != null ? Number(c.duration_ms) : 0);
  }, 0);
  return { rounds: cards, total: cards.length, totalMs: totalMs };
}

/** Poll payload kind enum. */
var POLL_KINDS = ['full', 'events', 'empty'];

/**
 * Normalize a lightweight poll payload. A payload carrying a `modules`/`nodes`
 * array is treated as a full route-map tree (kind 'full'); a bare array or
 * `{events:[...]}` is treated as incremental events (kind 'events'); anything
 * else is 'empty' (no change). Events are normalized via normalizeEvent.
 * @param {Array|object|null} payload raw poll payload
 * @returns {{kind:string, tree:object|null, events:Array<object>}}
 */
function normalizePoll(payload) {
  if (payload && typeof payload === 'object' &&
      (Array.isArray(payload.modules) || Array.isArray(payload.nodes))) {
    return { kind: 'full', tree: payload, events: [] };
  }
  var events = Array.isArray(payload)
    ? payload
    : (payload && Array.isArray(payload.events) ? payload.events : null);
  if (events) {
    return { kind: 'events', tree: null, events: events.map(normalizeEvent) };
  }
  return { kind: 'empty', tree: null, events: [] };
}

/**
 * Normalize a single incremental event to a patch shape. Accepts both camelCase
 * (moduleId) and snake_case (module_id). Unknown fields are dropped; numeric
 * fields are coerced.
 * @param {object} ev raw event
 * @returns {{module_id:string, status:string|null, last_verdict:string|null,
 *            token_used:number|null, started_at:*, ended_at:*, reason:string|null}}
 */
function normalizeEvent(ev) {
  ev = ev || {};
  return {
    module_id: ev.module_id != null ? String(ev.module_id) : (ev.moduleId != null ? String(ev.moduleId) : ''),
    status: ev.status != null ? String(ev.status) : null,
    last_verdict: ev.last_verdict != null ? String(ev.last_verdict) : null,
    token_used: ev.token_used != null ? Number(ev.token_used) : null,
    started_at: ev.started_at != null ? ev.started_at : null,
    ended_at: ev.ended_at != null ? ev.ended_at : null,
    reason: ev.reason != null ? String(ev.reason) : null
  };
}

/**
 * Find a module view (top-level or split child) by id within a route-map
 * payload. Returns null when absent.
 * @param {object} routeMap payload from buildRouteMap
 * @param {string} moduleId
 * @returns {object|null}
 */
function findModuleView(routeMap, moduleId) {
  var id = String(moduleId);
  var hit = null;
  function walk(m) {
    if (!m || hit) return;
    if (m.id === id) { hit = m; return; }
    if (Array.isArray(m.split)) m.split.forEach(walk);
  }
  var layers = (routeMap && Array.isArray(routeMap.layers)) ? routeMap.layers : [];
  layers.forEach(function (layer) {
    (Array.isArray(layer) ? layer : []).forEach(walk);
  });
  return hit;
}

/**
 * Apply a shallow patch to a module (top-level or split child) and return a NEW
 * route-map payload. Returns the original payload unchanged when the id is
 * absent. Fully immutable — the source payload is never mutated.
 * @param {object} routeMap payload from buildRouteMap
 * @param {string} moduleId
 * @param {object} patch field overrides
 * @returns {object} new route map (or the same object when nothing matched)
 */
function patchModule(routeMap, moduleId, patch) {
  var id = String(moduleId);
  var layers = (routeMap && Array.isArray(routeMap.layers)) ? routeMap.layers : [];
  if (!layers.length) return routeMap;

  function walk(view) {
    if (!view) return view;
    if (view.id === id) return Object.assign({}, view, patch);
    if (Array.isArray(view.split)) {
      var changed = false;
      var split = view.split.map(function (c) {
        var nc = walk(c);
        if (nc !== c) changed = true;
        return nc;
      });
      if (changed) return Object.assign({}, view, { split: split });
    }
    return view;
  }

  var anyChanged = false;
  var nextLayers = layers.map(function (layer) {
    return layer.map(function (m) {
      var nm = walk(m);
      if (nm !== m) anyChanged = true;
      return nm;
    });
  });
  if (!anyChanged) return routeMap;
  return Object.assign({}, routeMap, { layers: nextLayers });
}

/**
 * Apply a normalized poll payload to the current route-map, returning the next
 * route-map state. A full-tree poll rebuilds via buildRouteMap (opts.usage is
 * used for the token breakdown); an events poll applies each event as a patch;
 * an empty poll returns the current state unchanged.
 * @param {object} routeMap payload from buildRouteMap
 * @param {Array|object} poll raw poll payload (normalized internally)
 * @param {object} [opts]
 * @param {object} [opts.usage] raw usage payload used for a full rebuild
 * @returns {object} next route-map state
 */
function applyPoll(routeMap, poll, opts) {
  opts = opts || {};
  var p = normalizePoll(poll);
  if (p.kind === 'full') {
    return buildRouteMap(p.tree, opts.usage);
  }
  if (p.kind === 'events') {
    var next = routeMap;
    p.events.forEach(function (ev) {
      if (!ev.module_id) return;
      var patch = {};
      if (ev.status != null) patch.status = ev.status;
      if (ev.last_verdict != null) patch.last_verdict = ev.last_verdict;
      if (ev.token_used != null) patch.token_used = ev.token_used;
      if (ev.started_at != null) patch.started_at = ev.started_at;
      if (ev.ended_at != null) patch.ended_at = ev.ended_at;
      if (ev.reason != null) patch.reason = ev.reason;
      next = patchModule(next, ev.module_id, patch);
    });
    return next;
  }
  return routeMap;
}

/* ==================================================================== *
 * Multi-run registry logic (this block)
 *
 * Pure, framework-agnostic helpers that drive the multi-run selector and the
 * archive action. The data-bridge `runs()` endpoint returns the run registry
 * (contract columns run_id / task_dir / task / status / started_at / updated_at);
 * these functions normalize that payload, order runs active-first with the
 * newest active run on top, pick the latest active run for default-follow, and
 * provide an immutable reducer that drops an archived run from the selector.
 * No DOM, no React, no global state — unit-tested in plain Node via
 * test/runs.test.mjs.
 * ==================================================================== */

/** Run status enum — must stay aligned with contracts/data.yaml. */
var RUN_STATUSES = ['active', 'complete', 'archived'];

/**
 * Normalize a raw run-registry record into a stable renderable shape.
 * Unknown statuses collapse to 'active' (a registry entry with a run_id is
 * presumed in flight). Both the contract snake_case form and the bridge's
 * camelCase form are accepted for run_id.
 * @param {object} raw raw run record from the bridge
 * @returns {{run_id:string, task_dir:string, task:string, status:string,
 *            started_at:*, updated_at:*}}
 */
function normalizeRun(raw) {
  raw = raw || {};
  var status = raw.status != null ? String(raw.status) : 'active';
  if (RUN_STATUSES.indexOf(status) === -1) status = 'active';
  return {
    run_id: getRunId(raw),
    task_dir: raw.task_dir != null ? String(raw.task_dir) : '',
    task: raw.task != null ? String(raw.task) : '',
    status: status,
    started_at: raw.started_at != null ? raw.started_at : null,
    updated_at: raw.updated_at != null ? raw.updated_at : null
  };
}

/**
 * Sort comparator placing the newest run first by `started_at` (ISO-8601 strings
 * compare lexicographically). Runs without a started_at sort last, and ties are
 * left in original order (stable sort).
 * @returns {number} negative/zero/positive
 */
function byLatestFirst(a, b) {
  var ka = a && a.started_at != null ? String(a.started_at) : '';
  var kb = b && b.started_at != null ? String(b.started_at) : '';
  if (ka === kb) return 0;
  if (!ka) return 1;
  if (!kb) return -1;
  return ka < kb ? 1 : -1;
}

/**
 * Order the run registry for the selector: active runs first, then the rest
 * (complete), each group newest-first by started_at. Archived runs are dropped —
 * an archived run is "gone" from the selector (it is removed on archive). The
 * returned records are normalized.
 * @param {Array} runs raw run list from the bridge
 * @returns {Array<object>} ordered normalized runs
 */
function orderRunsActiveFirst(runs) {
  var list = (Array.isArray(runs) ? runs : []).map(normalizeRun);
  var selectable = list.filter(function (r) { return r.status !== 'archived'; });
  var active = selectable.filter(function (r) { return r.status === 'active'; });
  var others = selectable.filter(function (r) { return r.status !== 'active'; });
  active.sort(byLatestFirst);
  others.sort(byLatestFirst);
  return active.concat(others);
}

/**
 * Pick the run to default-follow: the LATEST active run (newest started_at
 * among runs whose status is 'active'). Returns null when there is no active
 * run (e.g. only complete runs, or an empty registry).
 * @param {Array} runs raw or normalized run list
 * @returns {object|null} normalized latest active run, or null
 */
function pickLatestActive(runs) {
  var list = (Array.isArray(runs) ? runs : []).map(normalizeRun);
  var active = list.filter(function (r) { return r.status === 'active'; });
  if (!active.length) return null;
  active.sort(byLatestFirst);
  return active[0];
}

/**
 * Pure reducer applying an archive action to the current UI state. Removes the
 * run from the selector list and clears `selected` if the archived run was the
 * one being viewed. Fully immutable — neither input array is mutated.
 * @param {{runs:Array, selected:string|null}} state
 * @param {string} runId
 * @returns {{runs:Array, selected:string|null}} next state
 */
function reduceArchiveRun(state, runId) {
  state = state || {};
  var id = String(runId);
  var runs = (Array.isArray(state.runs) ? state.runs : []).filter(function (r) {
    return getRunId(r) !== id;
  });
  var selected = state.selected;
  if (selected != null && String(selected) === id) selected = null;
  return { runs: runs, selected: selected };
}

/* ==================================================================== *
 * Event-driven refresh logic (final block)
 *
 * Pure, framework-agnostic helpers that drive the P2 event-driven refresh.
 * The data-bridge `events(since)` method returns incremental run/module
 * events from GET /api/events; these functions normalize that payload, decide
 * when a freshly started run must be auto-followed, and reduce the events
 * against the multi-run selector state. No DOM, no React, no global state —
 * unit-tested in plain Node via test/events.test.mjs.
 * ==================================================================== */

/** Run-level event types emitted by GET /api/events. */
var RUN_EVENT_TYPES = ['run.start', 'task.update'];

/**
 * Normalize a single run-level event from GET /api/events to a patch shape.
 * Accepts snake_case (run_id) and camelCase (runId); the carried run record
 * (present on run.start) is normalized via normalizeRun so the selector can
 * upsert it directly.
 * @param {object} ev raw event
 * @returns {{type:string, run_id:string, run:object, at:*}}
 */
function normalizeRunEvent(ev) {
  ev = ev || {};
  var run_id = ev.run_id != null ? String(ev.run_id)
    : (ev.runId != null ? String(ev.runId) : '');
  var record = (ev.run && typeof ev.run === 'object') ? ev.run
    : ((ev.data && typeof ev.data === 'object') ? ev.data : {});
  return {
    type: ev.type != null ? String(ev.type) : '',
    run_id: run_id,
    run: normalizeRun(record),
    at: ev.at != null ? ev.at : (ev.ts != null ? ev.ts : null)
  };
}

/**
 * Extract + normalize the payload returned by GET /api/events into a
 * `{events, cursor}` shape. Accepts a bare event array, `{events:[...]}`, or
 * `{events, since/next/cursor}`; the cursor is the value the caller should pass
 * back as `since` on the next pull (null when the payload carries none).
 * @param {Array|object} payload raw /api/events response
 * @returns {{events:Array<object>, cursor:*}}
 */
function extractRunEvents(payload) {
  var events = Array.isArray(payload)
    ? payload
    : (payload && Array.isArray(payload.events) ? payload.events : []);
  var cursor = null;
  if (payload && typeof payload === 'object') {
    if (payload.next != null) cursor = payload.next;
    else if (payload.cursor != null) cursor = payload.cursor;
    else if (payload.since != null) cursor = payload.since;
  }
  return {
    events: events.map(normalizeRunEvent),
    cursor: cursor
  };
}

/**
 * Decide whether a batch of run events triggers an auto-follow switch to a new
 * run. Returns the run_id of the FIRST run.start event (the freshly started
 * run to follow), or null when no run.start is present.
 * @param {Array} events raw run events from GET /api/events
 * @returns {string|null}
 */
function followRunIdFromEvents(events) {
  var id = null;
  (Array.isArray(events) ? events : []).some(function (raw) {
    var ev = normalizeRunEvent(raw);
    if (ev.type === 'run.start' && ev.run_id) {
      id = ev.run_id;
      return true;
    }
    return false;
  });
  return id;
}

/**
 * Pure reducer applying run-level events to the multi-run selector state.
 * Semantics (P2 contract):
 *   - `run.start`  → the freshly started run is the newest active run: switch
 *     follow to it (auto-follow), upsert it into the selector active-first, and
 *     mark it for a fresh tree pull. When the event carries no run record a
 *     minimal active run is synthesized from the event.
 *   - `task.update` → no selector change; mark the event's run (when it is the
 *     currently followed one) for a tree refresh so the active state stays
 *     current.
 * Fully immutable — input arrays/objects are never mutated.
 * @param {{runs:Array, selected:string|null}} state current selector state
 * @param {Array} events raw run events from GET /api/events
 * @returns {{runs:Array, selected:string|null, refreshRunIds:Array<string>,
 *            followedNewRun:boolean}}
 */
function reduceRunEvents(state, events) {
  state = state || {};
  var runs = (Array.isArray(state.runs) ? state.runs : []).slice();
  var selected = state.selected != null ? String(state.selected) : null;
  var refresh = [];
  var followedNew = false;
  (Array.isArray(events) ? events : []).forEach(function (raw) {
    var ev = normalizeRunEvent(raw);
    if (!ev.run_id) return;
    if (ev.type === 'run.start') {
      // Drop any stale entry, upsert the fresh active run, re-order active-first.
      var record = (ev.run && ev.run.run_id) ? ev.run : {
        run_id: ev.run_id,
        status: 'active',
        started_at: ev.at,
        updated_at: ev.at
      };
      // A run already known to the selector (e.g. a historical run's run.start
      // arriving on the first full /api/events pull) is only re-upserted — it
      // must NOT steal the follow. Auto-follow is reserved for genuinely NEW
      // runs, so the panel keeps the default selection (latest active) instead
      // of jumping to the last historical run.start.
      var knownBefore = runs.some(function (r) { return getRunId(r) === ev.run_id; });
      runs = runs.filter(function (r) { return getRunId(r) !== ev.run_id; });
      runs.push(record);
      runs = orderRunsActiveFirst(runs);
      if (!knownBefore) {
        // Auto-follow the freshly started run.
        selected = ev.run_id;
        followedNew = true;
        if (refresh.indexOf(ev.run_id) === -1) refresh.push(ev.run_id);
      }
    } else if (ev.type === 'run.archived') {
      // 归档：从选择器移除该 run；若正在查看则清空 selected（调用方 fallback）。
      // 缺少此分支时，历史 run.start（首次全量）会把刚归档的 run 重新 upsert
      // 回来 → "归档完了还会出来"。
      runs = runs.filter(function (r) { return getRunId(r) !== ev.run_id; });
      if (selected != null && ev.run_id === selected) selected = null;
    } else if (ev.type === 'task.update') {
      if (selected != null && ev.run_id === selected && refresh.indexOf(selected) === -1) {
        refresh.push(selected);
      }
    }
  });
  return {
    runs: runs,
    selected: selected,
    refreshRunIds: refresh,
    followedNewRun: followedNew
  };
}

/* ==================================================================== *
 * Recursive pipeline chain (this block)
 *
 * Pure, framework-agnostic reconstruction of the EXPLICIT per-module flow
 * chain from the run tree (split subtrees) + timeline (dispatch.jsonl by
 * seq). Turns those two sources into a renderable recursive chain:
 *
 *   planner/root → module block → [executor/auditor round cards] →
 *   [split fork] → [submodule recursion blocks]
 *
 * No DOM, no React, no global state — unit-tested in plain Node via
 * test/pipeline.test.mjs.
 * ==================================================================== */

/** Timeline record roles that mark a split fork point (normalized lowercase). */
var FORK_ROLES = ['split', 'fork'];

/**
 * Normalize a raw timeline payload (dispatch.jsonl by seq) into an ordered
 * array of dispatch records, preserving per-module attribution, the seq order
 * and the fields the round cards need. Accepts a bare array or an object with
 * a `rounds` / `events` / `timeline` / `dispatches` array field.
 *
 * Records are ordered by their `seq` (falling back to array index when absent)
 * so E → A → split ordering is preserved exactly as dispatched.
 * @param {Array|object} raw raw timeline payload
 * @returns {Array<object>} ordered dispatch records
 *   each: {seq, role, round, verdict, duration_ms, started_at, module_id}
 */
function parseDispatch(raw) {
  var items = Array.isArray(raw) ? raw : null;
  if (!items && raw && typeof raw === 'object') {
    var candidate = raw.rounds || raw.events || raw.timeline || raw.dispatches;
    items = Array.isArray(candidate) ? candidate : [];
  }
  items = items || [];
  return items.map(function (r, idx) {
    r = r || {};
    var role = r.role != null ? String(r.role) : (r.type != null ? String(r.type) : 'unknown');
    var round = r.round != null ? Number(r.round) : null;
    if (!isFinite(round) || round < 1) round = null;
    var duration = r.duration_ms != null ? Number(r.duration_ms) : 0;
    if (!isFinite(duration) || duration < 0) duration = 0;
    var seq = r.seq != null ? Number(r.seq) : idx;
    if (!isFinite(seq)) seq = idx;
    return {
      seq: seq,
      role: role,
      round: round,
      verdict: r.verdict != null ? String(r.verdict) : 'pending',
      duration_ms: duration,
      started_at: r.started_at != null ? r.started_at : (r.ts != null ? r.ts : null),
      module_id: r.module_id != null ? String(r.module_id)
        : (r.moduleId != null ? String(r.moduleId)
          : (r.module != null ? String(r.module) : ''))
    };
  }).sort(function (a, b) { return a.seq - b.seq; });
}

/**
 * True when a dispatch record is a split/fork marker (its normalized role is
 * `split` or `fork`). Used for fork-point detection in the flow chain.
 * @param {object} rec dispatch record
 * @returns {boolean}
 */
function isSplitDispatch(rec) {
  if (!rec) return false;
  var role = String(rec.role || '').toLowerCase();
  return FORK_ROLES.indexOf(role) !== -1;
}

/**
 * Build one normalized pipeline module chain recursively. Rounds are the
 * timeline records attributed to this module id (split markers excluded),
 * kept in seq order so E/A round cards read top-to-bottom as executed. A
 * module that splits carries a fork point whose children are recursively
 * built submodule chains (子模块递归).
 * @param {object} m raw module record
 * @param {object} dispatchByModule timeline records indexed by module_id
 * @returns {object} normalized module chain
 */
/**
 * 从 timeline（dispatch.jsonl 原始行）推导模块的 needs_human 历史事实：
 * 哪些模块「曾经」进入过 needs_human（was）、进入时间（pendingSince）、
 * 以及此后被流程解决的时间（processResolvedAt，最后一个 module.done）。
 *
 * 事件序列语义：module.needs_human → 进入 pending；module.human_rerun →
 * 回到执行（pending 解除，但 was 记忆保留）；module.done → 曾 pending 的
 * 模块记一次流程解决时间。纯函数，seq 升序遍历，未知事件忽略。
 * @param {Array|object} timeline raw timeline payload
 * @returns {{was:Object<string,boolean>, pendingSince:Object<string,string>, processResolvedAt:Object<string,string>}}
 */
function needsHumanFactsFromTimeline(timeline) {
  var items = Array.isArray(timeline) ? timeline
    : (timeline && Array.isArray(timeline.events) ? timeline.events : []);
  var ordered = items.slice().sort(function (a, b) {
    return (a && a.seq != null ? Number(a.seq) : 0) - (b && b.seq != null ? Number(b.seq) : 0);
  });
  var was = {};
  var pendingSince = {};
  var processResolvedAt = {};
  ordered.forEach(function (r) {
    if (!r) return;
    var ev = String(r.event || '');
    var mid = r.module != null ? String(r.module) : '';
    if (!mid) return;
    var ts = r.ts != null ? r.ts : (r.started_at != null ? r.started_at : null);
    if (ev === 'module.needs_human') {
      was[mid] = true;
      pendingSince[mid] = ts != null ? ts : (pendingSince[mid] || null);
    } else if (ev === 'module.human_rerun') {
      delete pendingSince[mid];
    } else if (ev === 'module.done' && was[mid]) {
      processResolvedAt[mid] = ts != null ? ts : (processResolvedAt[mid] || null);
    }
  });
  return { was: was, pendingSince: pendingSince, processResolvedAt: processResolvedAt };
}

/**
 * 推导一个模块的人机决策三态（needs_human 生命周期）。
 *
 * 权威源：快照 needs_human 数组（pending：还在等）+ human_answer.json（code
 * 存在且非 '?'/空 = 人已回复）+ timeline 事实（曾 needs_human → 流程解决后
 * 才有「已解决（流程）」历史卡，从未 needs_human 的模块不渲染任何卡）。
 *
 * @param {string} mid module id
 * @param {object} ctx {needsHuman:string[], humanAnswers:{mid:{code,text,answered_at,reason}}, perModule:{mid:{reason}}}
 * @param {object} facts needsHumanFactsFromTimeline 的产物
 * @returns {null|{state:'pending'|'resolved', by:null|'human'|'process',
 *   code:string, text:string, answeredAt:string|null, reason:string,
 *   pendingSince:string|null, draftText:string}}
 *   null = 该模块与人机决策无关，不渲染卡。
 */
function deriveHumanDecision(mid, ctx, facts) {
  mid = mid != null ? String(mid) : '';
  ctx = ctx || {};
  facts = facts || {};
  var answer = (ctx.humanAnswers && ctx.humanAnswers[mid]) || null;
  var code = answer && answer.code != null ? String(answer.code) : '';
  var answered = !!(code && code !== '?');
  var pendingNow = (ctx.needsHuman || []).indexOf(mid) !== -1;
  var per = (ctx.perModule && ctx.perModule[mid]) || {};
  var reason = (per.reason != null && per.reason !== '') ? per.reason
    : (answer && answer.reason != null ? answer.reason : '');
  if (answered) {
    return {
      state: 'resolved', by: 'human', code: code,
      text: answer.text != null ? String(answer.text) : '',
      answeredAt: answer.answered_at != null ? answer.answered_at : null,
      reason: reason, pendingSince: facts.pendingSince[mid] || null, draftText: ''
    };
  }
  if (pendingNow) {
    var draft = answer && answer.text != null ? String(answer.text) : '';
    return {
      state: 'pending', by: null, code: '', text: '', answeredAt: null,
      reason: reason, pendingSince: facts.pendingSince[mid] || null, draftText: draft
    };
  }
  if (facts.was[mid]) {
    return {
      state: 'resolved', by: 'process', code: '', text: '',
      answeredAt: facts.processResolvedAt[mid] || null,
      reason: reason, pendingSince: facts.pendingSince[mid] || null, draftText: ''
    };
  }
  return null;
}

function buildModuleChain(m, dispatchByModule) {
  m = m || {};
  var id = m.id != null ? String(m.id) : '';
  var recs = (dispatchByModule && dispatchByModule[id]) || [];
  var rounds = recs.filter(function (r) { return !isSplitDispatch(r); }).map(function (r) {
    return {
      role: r.role,
      round: r.round,
      verdict: r.verdict,
      duration_ms: r.duration_ms,
      started_at: r.started_at
    };
  });
  var children = Array.isArray(m.split) ? m.split : [];
  var fork = null;
  if (children.length) {
    fork = {
      splitCount: children.length,
      children: children.map(function (c) { return buildModuleChain(c, dispatchByModule); })
    };
  }
  return {
    id: id,
    name: m.name != null ? String(m.name) : id,
    status: deriveStatus(m),
    last_verdict: m.last_verdict != null ? String(m.last_verdict) : '',
    reason: m.reason != null ? String(m.reason) : '',
    dependencies: Array.isArray(m.dependencies) ? m.dependencies.map(String) : [],
    token_used: m.token_used != null ? Number(m.token_used) : 0,
    started_at: m.started_at != null ? m.started_at : null,
    ended_at: m.ended_at != null ? m.ended_at : null,
    rounds: rounds,
    fork: fork,
    humanDecision: null
  };
}

/**
 * Build the explicit recursive pipeline chain for a run from its tree (split
 * subtree) and timeline (dispatch.jsonl by seq).
 *
 * The chain has a planner/root node (left) and an ordered list of top-level
 * module chains. Each module chain carries its time-ordered E/A round cards
 * and an optional split fork whose children are the submodule recursion.
 *
 * Root resolution: `tree.root` (an object) when present, else the first
 * top-level module with no dependencies. Top-level modules are the tree's
 * root `modules`/`nodes` that are not themselves a split child of another.
 * @param {object} tree raw tree payload ({modules:[...]} or {nodes:[...]})
 * @param {Array|object} timeline raw timeline payload
 * @returns {{run_id:string, root:object|null, chains:Array<object>,
 *            dispatchTotal:number}}
 */
/* ==================================================================== *
 * Bridge timeline adapter (real dispatch event stream)
 *
 * The live bridge's /api/runs/{id}/timeline returns the RAW dispatch
 * event stream — records shaped {seq, ts, event, module, detail} with
 * event kinds run.start / module.dispatch / executor.round.start|done /
 * auditor.round.start|round / module.done / module.final_block / scaffold /
 * integration.check. The round-card logic above (parseDispatch) instead
 * expects the idealized {seq, role, round, verdict, duration_ms, module_id}
 * shape. This adapter turns the real event stream into round-card records:
 *   - executor.round.start + executor.round.done  → one 'executor' card
 *     (round from detail.round, verdict from detail.outcome_status,
 *      duration = done.ts − start.ts)
 *   - auditor.round.start + auditor.round         → one 'auditor' card
 *     (round from detail.auditor_round, verdict from detail.verdict,
 *      duration = round.ts − start.ts)
 *   - any other event kind is not a round card and is skipped
 * Orphan starts (run interrupted before the matching done) still become
 * 'pending' cards so the flow never silently loses a started round.
 * ==================================================================== */

/** True when a raw timeline payload looks like the bridge's real event stream. */
function isBridgeTimeline(raw) {
  var items = Array.isArray(raw) ? raw : (raw && Array.isArray(raw.events) ? raw.events : null);
  if (!items || !items.length) return false;
  return items.some(function (r) {
    return r && typeof r === 'object' && r.event != null;
  });
}

/**
 * Normalize a bridge outcome_status / auditor verdict to the round-card
 * verdict vocabulary (ok / revise / block / pending). Anything unknown
 * passes through as-is; missing values collapse to 'pending'.
 */
function normalizeRoundVerdict(s) {
  var v = String(s == null ? '' : s).toLowerCase();
  if (v === 'ok' || v === 'pass') return 'ok';
  if (v === 'revise') return 'revise';
  if (v === 'block' || v === 'blocked') return 'block';
  return v || 'pending';
}

/** Parse the real bridge dispatch event stream into round-card records. */
function parseBridgeTimeline(raw) {
  var items = Array.isArray(raw) ? raw : (raw && Array.isArray(raw.events) ? raw.events : []);
  var ordered = items.map(function (r, idx) {
    r = r || {};
    var seq = r.seq != null ? Number(r.seq) : idx;
    if (!isFinite(seq)) seq = idx;
    return { _order: seq, rec: r };
  }).sort(function (a, b) { return a._order - b._order; }).map(function (x) { return x.rec; });

  // moduleId -> { executor: { round: {ts} }, auditor: { round: {ts} } }
  var pending = {};
  var out = [];

  function push(moduleId, role, round, verdict, duration, startedAt, seq) {
    out.push({
      seq: seq,
      role: role,
      round: round,
      verdict: verdict,
      duration_ms: duration,
      started_at: startedAt,
      module_id: moduleId
    });
  }

  function toMs(s) {
    var t = Date.parse(s);
    return isFinite(t) ? t : null;
  }
  function diff(from, to) {
    if (toMs(from) == null || toMs(to) == null) return null;
    return toMs(to) - toMs(from);
  }
  function slot(moduleId, role) {
    (pending[moduleId] = pending[moduleId] || {})[role] = pending[moduleId][role] || {};
    return pending[moduleId][role];
  }

  ordered.forEach(function (r) {
    var ev = String((r && r.event) || '');
    if (!ev) return;
    var moduleId = r.module != null ? String(r.module)
      : (r.module_id != null ? String(r.module_id) : '');
    var det = (r.detail && typeof r.detail === 'object') ? r.detail : {};
    var ts = r.ts != null ? r.ts : (r.started_at != null ? r.started_at : null);
    var seq = r.seq != null ? Number(r.seq) : 0;

    if (ev === 'executor.round.start') {
      slot(moduleId, 'executor')[det.round] = { ts: ts };
    } else if (ev === 'executor.round.done') {
      var es = slot(moduleId, 'executor')[det.round];
      push(moduleId, 'executor', det.round,
        normalizeRoundVerdict(det.outcome_status),
        es ? diff(es.ts, ts) : null,
        es ? es.ts : ts, seq);
      delete slot(moduleId, 'executor')[det.round];
    } else if (ev === 'auditor.round.start') {
      slot(moduleId, 'auditor')[det.auditor_round] = { ts: ts };
    } else if (ev === 'auditor.round') {
      var as = slot(moduleId, 'auditor')[det.auditor_round];
      push(moduleId, 'auditor', det.auditor_round,
        normalizeRoundVerdict(det.verdict),
        as ? diff(as.ts, ts) : null,
        as ? as.ts : ts, seq);
      delete slot(moduleId, 'auditor')[det.auditor_round];
    }
    // All other event kinds (module.dispatch / module.done / module.final_block
    // / run.start / scaffold / integration.check) are NOT round cards → skipped.
  });

  // Orphan starts (matching done/round missing) → pending cards at the end.
  var orphanSeq = 1e9;
  Object.keys(pending).forEach(function (moduleId) {
    Object.keys(pending[moduleId] || {}).forEach(function (role) {
      Object.keys(pending[moduleId][role] || {}).forEach(function (rnd) {
        var entry = pending[moduleId][role][rnd];
        push(moduleId, role, Number(rnd), 'pending', null, entry.ts, orphanSeq++);
      });
    });
  });

  return out.sort(function (a, b) { return a.seq - b.seq; });
}

function buildPipelineChain(tree, timeline, usage) {
  tree = tree || {};
  var nodes = Array.isArray(tree.modules) ? tree.modules : (Array.isArray(tree.nodes) ? tree.nodes : []);

  // Real bridge event stream vs idealized contract shape.
  var dispatch = isBridgeTimeline(timeline) ? parseBridgeTimeline(timeline) : parseDispatch(timeline);
  var byModule = {};
  dispatch.forEach(function (rec) {
    if (!rec.module_id) return;
    (byModule[rec.module_id] = byModule[rec.module_id] || []).push(rec);
  });
  Object.keys(byModule).forEach(function (k) {
    byModule[k].sort(function (a, b) { return a.seq - b.seq; });
  });

  // Identify top-level modules: root nodes that are not a split child.
  var splitChildIds = {};
  function collectSplit(m) {
    if (!m) return;
    (Array.isArray(m.split) ? m.split : []).forEach(function (c) {
      if (c && c.id != null) splitChildIds[String(c.id)] = true;
      collectSplit(c);
    });
  }
  nodes.forEach(collectSplit);
  var topLevel = nodes.filter(function (n) {
    return n && n.id != null && !splitChildIds[String(n.id)];
  });

  // Root / planner node. Only a REAL planner is rendered there:
  //   1. an explicit tree.root object, or
  //   2. a top-level module explicitly named planner/root/orchestrator.
  // An ordinary first module with no dependencies (e.g. m01) is deliberately
  // NOT promoted — that would mislabel it as the planner. When there is no real
  // planner, the pipeline shows a synthesized planner summary card followed by
  // the plain top-level module chains.
  var root = null;
  if (tree.root && typeof tree.root === 'object') {
    root = buildModuleChain(tree.root, byModule);
  } else {
    var plannerNode = topLevel.find(function (n) {
      if (!n) return false;
      var id = String(n.id != null ? n.id : '').toLowerCase();
      var name = String(n.name != null ? n.name : '').toLowerCase();
      return id === 'planner' || id === 'root' || id === 'orchestrator'
        || name === 'planner' || name === 'root' || name === 'orchestrator';
    });
    if (plannerNode) root = buildModuleChain(plannerNode, byModule);
  }

  var rootId = root ? root.id : null;
  var chains = topLevel.filter(function (n) {
    return String(n.id) !== rootId;
  }).map(function (n) { return buildModuleChain(n, byModule); });

  // 给每个 chain 附加 usageTotal（含缓存、含全部递归子模块的总消耗，口径与详情一致）。
  // 外层模块块的 token 展示用这个值（快照 token_used 不含缓存且可能未回填）。
  var usageByModule = normalizeUsageByModule(usage);
  function attachUsageTotals(chain) {
    if (!chain) return 0;
    var id = chain.id != null ? String(chain.id) : '';
    var self = (usageByModule[id] != null) ? usageByModule[id].total : 0;
    var sub = 0;
    if (chain.fork && Array.isArray(chain.fork.children)) {
      chain.fork.children.forEach(function (c) { sub += attachUsageTotals(c); });
    }
    chain.usageTotal = self + sub;
    return chain.usageTotal;
  }
  attachUsageTotals(root);
  chains.forEach(attachUsageTotals);

  // 人机决策三态（needs_human 生命周期）attach：权威源 = 快照 needs_human 数组
  // + human_answer.json（桥 tree.human_answers）；「曾 needs_human」由 timeline
  // 事件序列推导（module.needs_human 出现过即记，供 resolved-by-process 历史卡）。
  var humanCtx = {
    needsHuman: Array.isArray(tree.needs_human) ? tree.needs_human.map(String) : [],
    humanAnswers: (tree.human_answers && typeof tree.human_answers === 'object') ? tree.human_answers : {},
    perModule: (tree.per_module && typeof tree.per_module === 'object') ? tree.per_module : {}
  };
  var humanTimeline = needsHumanFactsFromTimeline(timeline);
  function attachHumanDecision(chain) {
    if (!chain) return;
    chain.humanDecision = deriveHumanDecision(chain.id, humanCtx, humanTimeline);
    if (chain.fork && Array.isArray(chain.fork.children)) {
      chain.fork.children.forEach(attachHumanDecision);
    }
  }
  attachHumanDecision(root);
  chains.forEach(attachHumanDecision);

  return {
    run_id: (tree.run && (tree.run.run_id || tree.run.id)) || tree.run_id || tree.runId || '',
    root: root,
    chains: chains,
    planner: buildPlannerSummary(tree, timeline, usage),
    dispatchTotal: dispatch.length
  };
}

/**
 * Build the synthesized planner summary card for the pipeline head: how many
 * modules the planner split the run into, the planning-phase duration
 * (run.start → first module dispatch), and the run's token-usage overview.
 * The bridge currently does not record planner-only token spend, so `usage`
 * is the run-level breakdown (presented as the run's resource overview).
 * @param {object} tree raw tree payload ({modules:[...]} or {nodes:[...]})
 * @param {Array|object} timeline raw timeline payload
 * @param {object} [usage] raw usage payload (bridge {run:{...}} shape)
 * @returns {{modulesCount:number, planMs:number|null, usage:object}}
 */
function buildPlannerSummary(tree, timeline, usage) {
  tree = tree || {};
  var nodes = Array.isArray(tree.modules) ? tree.modules : (Array.isArray(tree.nodes) ? tree.nodes : []);
  // Planning duration: run.start → first module.dispatch / executor.round.start.
  var items = Array.isArray(timeline)
    ? timeline
    : (timeline && Array.isArray(timeline.events) ? timeline.events : []);
  var ordered = items.slice().sort(function (a, b) {
    return (a && a.seq != null ? a.seq : 0) - (b && b.seq != null ? b.seq : 0);
  });
  var runStartTs = null;
  var firstModuleTs = null;
  ordered.forEach(function (r) {
    if (!r) return;
    var ev = String(r.event || '');
    var ts = r.ts != null ? r.ts : r.started_at;
    if (ev === 'run.start' && runStartTs == null && ts != null) runStartTs = ts;
    if ((ev === 'module.dispatch' || ev === 'executor.round.start') && firstModuleTs == null && ts != null) {
      firstModuleTs = ts;
    }
  });
  var planMs = null;
  // 优先用桥 planner 桶携带的会话时间窗（真实规划耗时）；fallback 到
  // run.start → 首模块 dispatch 时间差（时间戳秒级粒度常为 0）。
  var pdur = usage && usage.planner && usage.planner.duration_ms;
  if (pdur != null && Number(pdur) > 0) {
    planMs = Number(pdur);
  } else if (runStartTs != null && firstModuleTs != null) {
    var a = Date.parse(runStartTs);
    var b = Date.parse(firstModuleTs);
    if (isFinite(a) && isFinite(b)) planMs = b - a;
  }
  return {
    modulesCount: nodes.length,
    planMs: planMs,
    usage: normalizeUsage(usage),
    planUsage: normalizeUsage(usage && usage.planner)
  };
}

/* ==================================================================== *
 * Recursive pipeline chain — visual / interaction polish helpers (final)
 *
 * Pure, framework-agnostic helpers that drive the pipeline chain's visual and
 * interaction refinements: round-card accent kinds, whole-subtree active
 * detection, recursion stats (submodule count / depth) and an ordered flat
 * enumeration (with depth tags) for navigation & a11y labels. No DOM, no React
 * — unit-tested in plain Node via test/pipeline.test.mjs.
 * ==================================================================== */

/** Canonical round-role kinds used for card accent styling. */
var ROLE_KINDS = ['executor', 'auditor', 'split', 'other'];

/**
 * Normalize a dispatch role to a canonical kind for round-card styling.
 * executor / auditor keep their kind; split / fork map to 'split'; anything
 * else (including empty / unknown) maps to 'other'. Lets the renderer pick a
 * stable accent class per card regardless of raw role casing or spelling.
 * @param {string|*} role raw round role
 * @returns {string} 'executor'|'auditor'|'split'|'other'
 */
function roundKind(role) {
  var s = String(role == null ? '' : role).toLowerCase();
  if (s === 'executor' || s === 'auditor') return s;
  if (FORK_ROLES.indexOf(s) !== -1) return 'split';
  return 'other';
}

/**
 * True when a pipeline module chain — or any of its recursive fork descendants
 * — is currently active (running / needs_human). Lets the renderer highlight an
 * entire recursion sub-tree rather than only the exact active module.
 * @param {object} chain pipeline module chain from buildPipelineChain
 * @returns {boolean}
 */
function isActiveChain(chain) {
  if (!chain) return false;
  if (ACTIVE_STATUSES.indexOf(chain.status) !== -1) return true;
  var fork = chain.fork;
  if (fork && Array.isArray(fork.children)) {
    return fork.children.some(isActiveChain);
  }
  return false;
}

/**
 * Recursively count every split submodule under a pipeline module chain,
 * including deeply nested recursion. Used for the fork / recursion header stat
 * and the module-children badge.
 * @param {object} chain pipeline module chain
 * @returns {number}
 */
function countSubmodules(chain) {
  if (!chain) return 0;
  var fork = chain.fork;
  if (!fork || !Array.isArray(fork.children)) return 0;
  return fork.children.reduce(function (acc, c) {
    return acc + 1 + countSubmodules(c);
  }, 0);
}

/**
 * Maximum recursion depth of a pipeline module chain — how many nested fork
 * levels deep it goes. 0 means the module never splits.
 * @param {object} chain pipeline module chain
 * @returns {number}
 */
function chainDepth(chain) {
  if (!chain) return 0;
  var fork = chain.fork;
  if (!fork || !Array.isArray(fork.children)) return 0;
  return 1 + fork.children.reduce(function (acc, c) {
    return Math.max(acc, chainDepth(c));
  }, 0);
}

/**
 * Flatten a single pipeline chain into an ordered list of every module (the
 * chain itself plus its recursive fork descendants), each tagged with its
 * recursion depth. Preserves left-to-right / top-to-bottom reading order.
 * @param {object} chain pipeline module chain
 * @param {number} [depth] starting depth (default 0)
 * @returns {Array<{chain:object, depth:number}>}
 */
function flattenChain(chain, depth) {
  var start = depth || 0;
  var out = [];
  if (!chain) return out;
  out.push({ chain: chain, depth: start });
  var fork = chain.fork;
  if (fork && Array.isArray(fork.children)) {
    fork.children.forEach(function (c) {
      out = out.concat(flattenChain(c, start + 1));
    });
  }
  return out;
}

/**
 * Flatten a full pipeline payload into every module entry across the root and
 * all top-level chains, in reading order, each tagged with recursion depth.
 * @param {object} pipeline payload from buildPipelineChain
 * @returns {Array<{chain:object, depth:number}>}
 */
function flattenPipeline(pipeline) {
  pipeline = pipeline || {};
  var out = flattenChain(pipeline.root, 0);
  (Array.isArray(pipeline.chains) ? pipeline.chains : []).forEach(function (c) {
    out = out.concat(flattenChain(c, 0));
  });
  return out;
}

module.exports = {
  TASK_STAGES: TASK_STAGES,
  getRunId: getRunId,
  normalizeTask: normalizeTask,
  buildRenderPayload: buildRenderPayload,
  filterActive: filterActive,
  removeArchived: removeArchived,
  selectTask: selectTask,
  reduceArchive: reduceArchive,
  resolveBaseURL: resolveBaseURL,
  buildDetailSections: buildDetailSections,
  MODULE_STATUSES: MODULE_STATUSES,
  deriveStatus: deriveStatus,
  topoLayer: topoLayer,
  parseTimeline: parseTimeline,
  formatDuration: formatDuration,
  buildModuleView: buildModuleView,
  buildRouteMap: buildRouteMap,
  normalizeUsage: normalizeUsage,
  normalizeUsageByModule: normalizeUsageByModule,
  aggregateUsageByTree: aggregateUsageByTree,
  summarizeModuleRounds: summarizeModuleRounds,
  REPLY_COMMANDS: REPLY_COMMANDS,
  validateReplyCommand: validateReplyCommand,
  deriveActiveModule: deriveActiveModule,
  buildBlockSummary: buildBlockSummary,
  buildModuleDetail: buildModuleDetail,
  POLL_KINDS: POLL_KINDS,
  normalizePoll: normalizePoll,
  normalizeEvent: normalizeEvent,
  findModuleView: findModuleView,
  patchModule: patchModule,
  applyPoll: applyPoll,
  RUN_STATUSES: RUN_STATUSES,
  normalizeRun: normalizeRun,
  orderRunsActiveFirst: orderRunsActiveFirst,
  pickLatestActive: pickLatestActive,
  reduceArchiveRun: reduceArchiveRun,
  RUN_EVENT_TYPES: RUN_EVENT_TYPES,
  normalizeRunEvent: normalizeRunEvent,
  extractRunEvents: extractRunEvents,
  followRunIdFromEvents: followRunIdFromEvents,
  reduceRunEvents: reduceRunEvents,
  FORK_ROLES: FORK_ROLES,
  parseDispatch: parseDispatch,
  isSplitDispatch: isSplitDispatch,
  isBridgeTimeline: isBridgeTimeline,
  parseBridgeTimeline: parseBridgeTimeline,
  normalizeRoundVerdict: normalizeRoundVerdict,
  buildModuleChain: buildModuleChain,
  buildPipelineChain: buildPipelineChain,
  needsHumanFactsFromTimeline: needsHumanFactsFromTimeline,
  deriveHumanDecision: deriveHumanDecision,
  buildPlannerSummary: buildPlannerSummary,
  ROLE_KINDS: ROLE_KINDS,
  roundKind: roundKind,
  isActiveChain: isActiveChain,
  countSubmodules: countSubmodules,
  chainDepth: chainDepth,
  flattenChain: flattenChain,
  flattenPipeline: flattenPipeline
};
