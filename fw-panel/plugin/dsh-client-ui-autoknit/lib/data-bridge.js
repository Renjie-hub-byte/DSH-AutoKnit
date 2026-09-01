'use strict';

/**
 * dsh-client-ui-autoknit — fw-api data-bridge HTTP client.
 *
 * Thin, framework-free client over the JSON endpoints exposed by the m01
 * data bridge. `fetch` is injectable so tests can pass a fake; this module
 * has no DOM dependency and runs identically in Node and in the browser.
 */

/** Default base path for the data-bridge endpoints. */
var DEFAULT_BASE = '/api';

/**
 * Create a data-bridge client.
 * @param {object} [opts]
 * @param {string} [opts.baseURL] base URL, defaults to '/api'
 * @param {Function} [opts.fetch] fetch implementation, defaults to globalThis.fetch
 * @returns {object} client with listTasks / taskDetail / archive / archived plus the
 *                   route-map endpoints: runs / tree / timeline / usage / reply
 */
function createClient(opts) {
  opts = opts || {};
  var base = (opts.baseURL || DEFAULT_BASE).replace(/\/+$/, '');
  var fetchImpl = opts.fetch || (typeof globalThis !== 'undefined' && globalThis.fetch);

  function buildUrl(path) {
    return base + path;
  }

  function request(method, path, body) {
    if (typeof fetchImpl !== 'function') {
      return Promise.reject(new Error('data-bridge: no fetch implementation available'));
    }
    var init = { method: method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) {
      init.body = JSON.stringify(body);
    }
    return fetchImpl(buildUrl(path), init).then(function (res) {
      if (!res || !res.ok) {
        var status = res ? res.status : 0;
        return res.text().then(function (text) {
          throw new Error('data-bridge: ' + method + ' ' + path + ' failed (' + status + '): ' + text);
        });
      }
      return res.text().then(function (text) {
        if (!text) return null;
        try {
          return JSON.parse(text);
        } catch (e) {
          return text;
        }
      });
    });
  }

  return {
    /** GET /api/tasks — full task list. */
    listTasks: function listTasks() {
      return request('GET', '/tasks');
    },
    /** GET /api/tasks/{run_id} — single task detail. */
    taskDetail: function taskDetail(runId) {
      return request('GET', '/tasks/' + encodeURIComponent(String(runId)));
    },
    /** POST /api/runs/{runId}/archive — registry archive (marks status=archived,
     *  run disappears from /api/runs; persisted across panel reloads). The old
     *  /tasks/archive only wrote task_dir/总日志/archived.json and did NOT touch
     *  the registry → the archived run reappeared on next panel mount. */
    archive: function archive(runId) {
      return request('POST', '/runs/' + encodeURIComponent(String(runId)) + '/archive');
    },
    /** GET /api/tasks/archived — list of archived run_ids. */
    archived: function archived() {
      return request('GET', '/tasks/archived');
    },
    /** GET /api/runs — full run list (new bridge surface, kept alongside /api/tasks). */
    runs: function runs() {
      return request('GET', '/runs');
    },
    /** GET /api/runs/{runId}/tree — module topology for a run (dependencies/split/status). */
    tree: function tree(runId) {
      return request('GET', '/runs/' + encodeURIComponent(String(runId)) + '/tree');
    },
    /** GET /api/runs/{runId}/timeline — role-rounds chain for a run (E/A cards). */
    timeline: function timeline(runId) {
      return request('GET', '/runs/' + encodeURIComponent(String(runId)) + '/timeline');
    },
    /** GET /api/runs/{runId}/usage — token usage breakdown (total/input/output/cache). */
    usage: function usage(runId) {
      return request('GET', '/runs/' + encodeURIComponent(String(runId)) + '/usage');
    },
    /** POST /api/runs/{runId}/reply — write a human decision for a needs_human module. */
    reply: function reply(runId, decision) {
      return request('POST', '/runs/' + encodeURIComponent(String(runId)) + '/reply', decision || {});
    },
    /** GET /api/events — long-poll incremental run/module events since a cursor.
     *  wait: seconds the server may hold the request when there are no new
     *  events (event-driven refresh; 0 = legacy immediate return). */
    events: function events(since, wait) {
      var qs = [];
      if (since != null) qs.push('since=' + encodeURIComponent(String(since)));
      if (wait != null && Number(wait) > 0) qs.push('wait=' + encodeURIComponent(String(wait)));
      return request('GET', '/events' + (qs.length ? '?' + qs.join('&') : ''));
    }
  };
}

module.exports = {
  DEFAULT_BASE: DEFAULT_BASE,
  createClient: createClient
};
