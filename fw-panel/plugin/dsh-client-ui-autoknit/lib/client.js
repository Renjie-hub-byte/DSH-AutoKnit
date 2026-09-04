/* dsh-client-ui-autoknit — browser half.
 *
 * Registers the AutoKnit route-map panel into the DSH conversation.view slot
 * (list-type multi-tab content). It is loaded by the DSH front-end module
 * loader:
 *     window.__ModuleLoader__.load('dsh-client-ui-autoknit', factory(require))
 *
 * The panel consumes the fw-api data bridge (/api/tasks list + the new
 * /api/runs/{id}/{tree,timeline,usage} + POST /api/runs/{id}/reply endpoints)
 * and renders a horizontal flow-chart route map:
 *   - multi-run selector (active runs first, latest active followed by default;
 *     each active run carries an archive button)
 *   - the selected run's modules laid out as topology columns (same-layer
 *     modules are parallel siblings), split submodules hanging below their
 *     parent with a connector line
 *   - each module block carries a derived status badge
 *     (done/pending/running/needs_human/block) computed by logic.js
 *
 * This block covers the main horizontal rendering only. The role-rounds
 * chain, active animations, detail popover, token-split expansion and the
 * needs_human decision dialog are delivered in the module's remaining blocks.
 *
 * Rendered with plain React.createElement (no JSX) so the bundle needs no
 * build step beyond the dependency-free inline in build.mjs.
 */
(function () {
  'use strict';

  window.__ModuleLoader__.load('dsh-client-ui-autoknit', function (require) {
    var React = require('react');
    var logic = require('./logic.js');
    var bridge = require('./data-bridge.js');
    var i18n = require('./i18n.js');
    var style = require('./style.js');

    // Inject the panel stylesheet once, as early as possible and idempotently.
    // DOM-guarded so it is a no-op in the node test harness.
    if (typeof style.injectStyles === 'function') {
      try {
        style.injectStyles(window);
      } catch (e) {
        /* non-fatal: host may supply its own styles */
      }
    }

    /** Lightweight poll cadence for refreshing the active state (3-5s). */
    var POLL_INTERVAL_MS = 4000;
    /** Long-poll window (seconds): the server holds /api/events this long when
     *  there are no new events. Idle cost = one request per window. */
    var EVENTS_WAIT_S = 25;
    /** Error-backoff before retrying /api/events (fallback polling kicks in
     *  after the first failure, so this only applies to transient errors). */
    var EVENT_RETRY_MS = 2000;

    /** Read the host-supplied autoknit config (e.g. baseURL / locale). */
    function hostConfig(win) {
      var dsh = (win && win.__DSH__) || {};
      var cfg = (dsh.config && dsh.config.autoknit) || {};
      return cfg;
    }

    /**
     * @type {React.Component} route-map panel for AutoKnit.
     */
    function AutoknitPanel(props) {
      var panelOptions = (props && props.options) || {};
      var hcfg = hostConfig(window);
      var t = i18n.makeT(panelOptions.locale || hcfg.locale);
      var client = (props && props.bridge) || bridge.createClient({
        baseURL: logic.resolveBaseURL(panelOptions.baseURL, hcfg.baseURL),
        fetch: panelOptions.fetch || (props && props.fetch)
      });

      // Event-driven scratch state must survive re-renders (component-body
      // `var`s reset on every render → eventCursor/currentRuns/currentSelected
      // would go null/[] on selection change → the first full /api/events pull
      // saw every run as "new" and kept stealing the follow → run selection
      // jumped between runs). Refs keep the loop continuous per panel instance.
      var eventCursorRef = React.useRef(null);
      var eventsEnabledRef = React.useRef(true);
      var currentRunsRef = React.useRef([]);
      var currentSelectedRef = React.useRef(null);

      var runsState = React.useState([]);
      var runs = runsState[0];
      var setRuns = runsState[1];

      var selectedState = React.useState(null);
      var selected = selectedState[0];
      var setSelected = selectedState[1];

      var routeMapState = React.useState(null);
      var routeMap = routeMapState[0];
      var setRouteMap = routeMapState[1];

      // Explicit recursive pipeline chain (this block): planner/root → module
      // blocks → per-module E/A round cards + split fork + submodule recursion.
      var pipelineState = React.useState(null);
      var pipeline = pipelineState[0];
      var setPipeline = pipelineState[1];

      var loadingState = React.useState(false);
      var loading = loadingState[0];
      var setLoading = loadingState[1];

      var errorState = React.useState(null);
      var error = errorState[0];
      var setError = errorState[1];

      // needs_human decision-dialog state. Shape: { moduleId, command,
      // instruction, error, submitting } or null when no dialog is open.
      var replyState = React.useState(null);
      var reply = replyState[0];
      var setReply = replyState[1];

      // Run-level role-rounds chain (for the detail popover's round breakdown).
      var timelineState = React.useState(null);
      var timeline = timelineState[0];
      var setTimeline = timelineState[1];

      // Detail popover state: the module id currently open, or null.
      var popoverState = React.useState(null);
      var popoverId = popoverState[0];
      var setPopoverId = popoverState[1];

      /** Load and render the route-map payload for a run. */
      function loadTree(runId) {
        return client.tree(runId).then(function (rawTree) {
          // usage + timeline are best-effort: runs missing either endpoint still render.
          return Promise.all([
            client.usage(runId).catch(function () { return {}; }),
            client.timeline(runId).catch(function () { return {}; })
          ]).then(function (parts) {
            return {
              rm: logic.buildRouteMap(rawTree, parts[0]),
              pipeline: logic.buildPipelineChain(rawTree, parts[1], parts[0]),
              timeline: parts[1]
            };
          });
        }).then(function (res) {
          setRouteMap(res.rm);
          setPipeline(res.pipeline);
          setTimeline(res.timeline);
          setError(null);
        }).catch(function (err) {
          setError(err && err.message ? err.message : String(err));
        });
      }

      /**
       * Event-driven refresh (P2) of the followed run. Prefers GET /api/events
       * (long-poll-ish short-poll with a since cursor): a `run.start` event
       * auto-switches follow to the new run and pulls its tree, a `task.update`
       * event refreshes the current run tree. When /api/events is unavailable
       * (rejects or is missing), it falls back to a 3-5s tree poll of the
       * followed run. All event→state mapping is delegated to pure logic.js
       * reducers so it stays unit-testable; transient errors are non-fatal
       * (the last good state is kept).
       */
      React.useEffect(function () {
        if (!selected) return undefined;
        var cancelled = false;
        var fallbackTimer = null;

        /** Pull a run's fresh route map (re-normalized via applyPoll). */
        function refreshTree(runId) {
          if (!runId || cancelled) return Promise.resolve();
          return client.tree(runId).then(function (raw) {
            return client.usage(runId).catch(function () { return {}; }).then(function (usage) {
              var poll = logic.normalizePoll(raw);
              setRouteMap(logic.applyPoll(routeMap, poll, { usage: usage }));
              // Full-tree polls also refresh the explicit recursive chain so the
              // E/A round cards + fork stay in step with the last tree snapshot.
              if (poll.kind === 'full') {
                setPipeline(logic.buildPipelineChain(raw, timeline, usage));
              }
              // 决策弹窗自动关（铁律：窗口不允许悬挂）：模块已不在快照
              // needs_human 清单（流程/人已解决）→ 关闭未提交的弹窗。
              // submitting 中不强关（避免吞掉提交反馈）。
              setReply(function (prev) {
                if (!prev || prev.submitting) return prev;
                var pendingList = Array.isArray(raw.needs_human) ? raw.needs_human.map(String) : [];
                return pendingList.indexOf(String(prev.moduleId)) === -1 ? null : prev;
              });
            });
          }).catch(function () { /* non-fatal: keep last good state */ });
        }

        /** 3-5s fallback: re-poll the followed run's tree. */
        function pollTree() {
          refreshTree(currentSelectedRef.current || selected);
        }

        /** Event-driven loop: long-poll GET /api/events (since cursor + wait) +
         *  apply reducers. The server holds each request until a new event
         *  (module/round done → dispatch.jsonl append) or the wait timeout —
         *  so idle cost is one request per wait window, and updates arrive
         *  within milliseconds of an event. */
        function startEvents() {
          if (cancelled || !eventsEnabledRef.current) return;
          client.events(eventCursorRef.current, EVENTS_WAIT_S).then(function (payload) {
            if (cancelled) return;
            var parsed = logic.extractRunEvents(payload);
            var isFirstPull = eventCursorRef.current == null;
            if (parsed.cursor != null) eventCursorRef.current = parsed.cursor;
            // First pull returns the FULL event history (historical run.start /
            // run.archived from previous sessions). Replaying those through the
            // reducer would override the user's selection (every historical
            // run.start normalizes to 'active' → steals the follow; a stale
            // run.archived clears `selected` → fallback jumps to another run).
            // The selector/list is already initialized by loadRuns, so the first
            // pull only advances the cursor; ONLY incremental events are applied.
            if (isFirstPull) {
              if (!cancelled) setTimeout(startEvents, 0);
              return;
            }
            var next = logic.reduceRunEvents(
              { runs: currentRunsRef.current, selected: currentSelectedRef.current },
              parsed.events
            );
            if (next.runs !== currentRunsRef.current) { currentRunsRef.current = next.runs; setRuns(next.runs); }
            if (next.selected === null && currentSelectedRef.current != null && (next.runs || []).length) {
              // 当前选中被归档移除 → fallback 到最新 active / 第一个剩余 run。
              var fb = logic.pickLatestActive(next.runs);
              var fbId = fb ? logic.getRunId(fb) : logic.getRunId(next.runs[0]);
              currentSelectedRef.current = fbId;
              setSelected(fbId);
              loadTree(fbId);
            } else if (next.selected !== currentSelectedRef.current) {
              currentSelectedRef.current = next.selected; setSelected(next.selected);
            }
            (next.refreshRunIds || []).forEach(refreshTree);
            // Long-poll: re-arm immediately (server already waited / returned
            // events). No fixed-interval polling in the steady state.
            if (!cancelled) setTimeout(startEvents, 0);
          }).catch(function () {
            // /api/events unavailable → disable it and fall back to polling.
            eventsEnabledRef.current = false;
            if (!cancelled) {
              fallbackTimer = setInterval(pollTree, POLL_INTERVAL_MS);
            }
          });
        }

        if (eventsEnabledRef.current) {
          startEvents();
        } else {
          fallbackTimer = setInterval(pollTree, POLL_INTERVAL_MS);
        }

        return function () {
          cancelled = true;
          if (fallbackTimer) clearInterval(fallbackTimer);
        };
      }, [selected, timeline]);

      /**
       * Load the run registry, order it active-first for the multi-run selector,
       * then auto-follow the LATEST active run's route map (newest started_at
       * among active runs). Falls back to the first remaining run when nothing
       * is active.
       */
      function loadRuns() {
        setLoading(true);
        setError(null);
        // BUG-20260829：旧实现调 client.listTasks()（/api/tasks → 读 runs.json），
        // 真实任务目录只有 快照.json/dispatch.jsonl，/api/tasks 返回空 → 面板永远"未选择运行"。
        // 改为新桥面 client.runs()（GET /api/runs → 真实快照聚合）。
        return client.runs().then(function (raw) {
          // Order runs active-first (latest active on top) so the selector lists
          // multiple runs with the current focus run up front.
          return logic.orderRunsActiveFirst(raw);
        }).then(function (ordered) {
          setRuns(ordered);
          currentRunsRef.current = ordered;
          // Default-follow the latest active run; fall back to the first run.
          var latest = logic.pickLatestActive(ordered);
          var first = latest ? logic.getRunId(latest) : (ordered.length ? logic.getRunId(ordered[0]) : null);
          if (!first) return null;
          setSelected(first);
          currentSelectedRef.current = first;
          return loadTree(first);
        }).catch(function (err) {
          setError(err && err.message ? err.message : String(err));
        }).then(function () {
          setLoading(false);
        });
      }

      /**
       * Archive an active run: POST via the data-bridge, then apply the pure
       * reducer to drop it from the selector. If the archived run was the one
       * being viewed, re-follow the latest remaining active run (or the first
       * run left).
       */
      function handleArchive(runId) {
        setError(null);
        client.archive(runId).then(function () {
          var next = logic.reduceArchiveRun({ runs: runs, selected: selected }, runId);
          setRuns(next.runs);
          currentRunsRef.current = next.runs;
          currentSelectedRef.current = next.selected;
          if (next.selected === null) {
            var latest = logic.pickLatestActive(next.runs);
            var fallback = latest ? logic.getRunId(latest) : (next.runs.length ? logic.getRunId(next.runs[0]) : null);
            setSelected(fallback);
            currentSelectedRef.current = fallback;
            if (fallback) {
              loadTree(fallback);
            } else {
              setRouteMap(null);
              setTimeline(null);
            }
          }
        }).catch(function (err) {
          setError(err && err.message ? err.message : String(err));
        });
      }

      React.useEffect(function () {
        var cancelled = false;
        var p = loadRuns();
        if (p && typeof p.then === 'function') {
          p.then(function () { /* settled */ });
        }
        return function () { cancelled = true; };
      }, []);

      function handleRetry() {
        loadRuns();
      }

      function handleSelectRun(runId) {
        if (selected === runId) return;
        setSelected(runId);
        currentSelectedRef.current = runId;
        setLoading(true);
        setError(null);
        loadTree(runId).then(function () { setLoading(false); });
      }

      /** Open the decision dialog for a needs_human module (defaults to continue). */
      function handleOpenReply(moduleId) {
        setReply({ moduleId: moduleId, command: 'continue', instruction: '', error: null, submitting: false });
      }

      function handleCloseReply() {
        setReply(null);
      }

      function handleReplyCommand(command) {
        setReply(function (prev) {
          var p = prev || { moduleId: null, instruction: '' };
          return { moduleId: p.moduleId, command: command, instruction: p.instruction || '', error: null, submitting: false };
        });
      }

      function handleReplyInstruction(text) {
        setReply(function (prev) {
          var p = prev || { moduleId: null, command: 'continue' };
          return { moduleId: p.moduleId, command: p.command, instruction: text, error: null, submitting: false };
        });
      }

      /** Validate + submit the decision via data-bridge.reply → POST /api/runs/{id}/reply. */
      function handleSubmitReply(moduleId) {
        if (!reply || reply.moduleId !== moduleId) return;
        // bugfix(2026-09-02, 杰哥实测): 文本回复智能归类——默认 continue + 非空文本 → custom（D 语义）。
        // 否则人的文字反馈被记成 B 且到不了 executor（BUG-124 排查 F2 遗留缺口）。
        var effCommand = reply.command;
        var effText = reply.instruction;
        if (effText && String(effText).trim() && effCommand === 'continue') {
          effCommand = 'custom';
        }
        var v = logic.validateReplyCommand(effCommand, effText);
        if (!v.ok) {
          var errKey = v.errors.instruction || v.errors.command;
          setReply({ moduleId: moduleId, command: v.command, instruction: effText, error: t(errKey), submitting: false });
          return;
        }
        setReply({ moduleId: moduleId, command: v.command, instruction: effText, error: null, submitting: true });
        client.reply(selected, { module_id: moduleId, command: v.command, instruction: effText })
          .then(function () {
            // Success: close the dialog and refresh the run so the block's
            // status badge reflects the new stage (needs_human → running, etc.).
            setReply(null);
            return loadTree(selected);
          })
          .catch(function (err) {
            setReply({
              moduleId: moduleId,
              command: v.command,
              instruction: reply.instruction,
              error: (err && err.message) ? err.message : String(err),
              submitting: false
            });
          });
      }

      // Context handed to the route-map renderer for the needs_human dialogs
      // and the active-module highlight.
      var replyUi = {
        reply: reply,
        openReply: handleOpenReply,
        closeReply: handleCloseReply,
        setCommand: handleReplyCommand,
        setInstruction: handleReplyInstruction,
        submitReply: handleSubmitReply
      };

      // Detail popover context (open a module's detail, or close it).
      var detailUi = {
        popoverId: popoverId,
        open: function (moduleId) { setPopoverId(moduleId); },
        close: function () { setPopoverId(null); }
      };

      // Split-fork collapse interaction (per module id → collapsed bool). Lets
      // the user fold / unfold the submodule recursion blocks under a fork.
      var collapsedState = React.useState({});
      var collapsedForks = collapsedState[0];
      var setCollapsedForks = collapsedState[1];
      var forkUi = {
        isCollapsed: function (moduleId) { return !!(collapsedForks && collapsedForks[moduleId]); },
        toggle: function (moduleId) {
          setCollapsedForks(function (prev) {
            var p = prev || {};
            var next = {};
            Object.keys(p).forEach(function (k) { next[k] = p[k]; });
            next[moduleId] = !p[moduleId];
            return next;
          });
        }
      };

      return React.createElement('div', { className: 'ak-details-panel', 'data-ak-panel': 'autoknit' },
        renderHeader(React, runs, t),
        renderError(React, error, handleRetry, t),
        renderRunSelector(React, runs, selected, handleSelectRun, handleArchive, loading, t),
        renderRouteMap(React, routeMap, pipeline, loading, t, replyUi, detailUi, timeline, forkUi)
      );
    }

    function renderHeader(React, runs, t) {
      var n = Array.isArray(runs) ? runs.length : 0;
      return React.createElement('div', { className: 'ak-header' },
        React.createElement('h3', { className: 'ak-title' }, t('panel.title')),
        React.createElement('span', { className: 'ak-count', 'data-ak-count': String(n) },
          t('route.count', { n: n }))
      );
    }

    function renderError(React, error, handleRetry, t) {
      if (!error) return null;
      return React.createElement('div', { className: 'ak-error', role: 'alert', 'data-ak-error': '1' },
        React.createElement('span', { className: 'ak-error-title' }, t('panel.error.title')),
        React.createElement('span', { className: 'ak-error-msg' }, error),
        React.createElement('button', {
          className: 'ak-retry',
          type: 'button',
          'data-ak-retry': '1',
          onClick: handleRetry
        }, t('panel.retry'))
      );
    }

    function renderRunSelector(React, runs, selected, handleSelectRun, handleArchive, loading, t) {
      var runList = Array.isArray(runs) ? runs : [];
      // runs arrive pre-ordered active-first (latest active on top) from loadRuns.
      var pills = runList.map(function (run) {
        var id = logic.getRunId(run);
        var active = selected === id;
        // Any non-archived run (active OR complete) is archivable: archiving
        // drops its row from the selector and marks the registry entry archived.
        var archivable = run.status !== 'archived';
        var label = run.task ? id + ' · ' + run.task : id;
        // Archive affordance: click posts the archive and the pure reducer
        // drops the run from the selector.
        var archiveBtn = archivable
          ? React.createElement('button', {
              className: 'ak-run-archive',
              type: 'button',
              'data-ak-archive': id,
              title: t('task.archive'),
              disabled: !!loading,
              onClick: function (e) {
                e.stopPropagation();
                handleArchive(id);
              }
            }, t('task.archive'))
          : null;
        return React.createElement('div', {
          key: id,
          className: 'ak-run-pill' + (active ? ' ak-run-pill-active' : ''),
          'data-run-id': id,
          'data-active': active ? '1' : '0',
          'data-status': run.status
        },
          React.createElement('button', {
            type: 'button',
            className: 'ak-run-select',
            disabled: !!loading,
            onClick: function () { handleSelectRun(id); }
          }, label),
          archiveBtn
        );
      });
      return React.createElement('div', { className: 'ak-runs', 'data-ak-runs': '1' },
        React.createElement('span', { className: 'ak-runs-label' }, t('route.runs')),
        pills.length
          ? React.createElement('div', { className: 'ak-run-pills' }, pills)
          : React.createElement('span', { className: 'ak-runs-empty' }, t('route.empty'))
      );
    }

    function renderRouteMap(React, routeMap, pipeline, loading, t, ui, detailUi, timeline, forkUi) {
      if (loading) {
        return React.createElement('div', { className: 'ak-loading', 'data-ak-loading': '1' },
          React.createElement('span', { className: 'ak-spinner' }),
          React.createElement('span', {}, t('panel.loading'))
        );
      }
      if (!routeMap) {
        return React.createElement('div', { className: 'ak-route-empty', 'data-ak-route-map': '0' },
          t('route.empty'));
      }
      // The active module (running / needs_human) gets the highlight ring.
      var activeModule = logic.deriveActiveModule(routeMap);
      var activeId = activeModule ? activeModule.id : null;
      // Prefer the explicit recursive pipeline chain (planner → module →
      // E/A round cards + split fork + submodule recursion). Fall back to the
      // legacy topology columns when no chain is available.
      var mapEl;
      if (pipeline && (pipeline.root || (pipeline.chains && pipeline.chains.length))) {
        mapEl = renderPipeline(React, pipeline, activeId, t, ui, detailUi, timeline, forkUi);
      } else {
        var columns = routeMap.layers.map(function (layer, idx) {
          var blocks = layer.map(function (m) {
            return renderModuleBlock(React, m, t, ui, activeId, detailUi, timeline);
          });
          return React.createElement('div', { key: 'layer-' + idx, className: 'ak-column', 'data-ak-layer': String(idx) }, blocks);
        });
        mapEl = React.createElement('div', { className: 'ak-route-map', 'data-ak-route-map': '1' }, columns);
      }
      return React.createElement(React.Fragment, null,
        mapEl,
        renderDetailPopover(React, routeMap, detailUi, timeline, t)
      );
    }

    function renderModuleBlock(React, m, t, ui, activeId, detailUi, timeline) {
      var cls = 'ak-module ak-module-' + m.status;
      var isActive = !!m.id && m.id === activeId;
      if (isActive) cls += ' ak-active';
      var badge = React.createElement('span', {
        className: 'ak-status-badge ak-status-' + m.status,
        'data-ak-status': m.status
      }, t('status.' + m.status));
      var head = React.createElement('div', { className: 'ak-module-head' },
        React.createElement('span', { className: 'ak-module-name' }, m.name),
        React.createElement('div', { className: 'ak-module-head-actions' },
          badge,
          React.createElement('button', {
            className: 'ak-detail-btn',
            type: 'button',
            'data-ak-detail-open': '1',
            title: t('detail.open'),
            onClick: function (e) {
              e.stopPropagation();
              detailUi.open(m.id);
            }
          }, t('detail.open'))
        )
      );
      var token = (m.token_used > 0)
        ? React.createElement('span', { className: 'ak-module-token' },
            t('module.token') + ' ' + m.token_used)
        : null;
      // Block-bottom aggregate metrics (token_used total / total duration / round count).
      var summary = logic.buildBlockSummary(m, timeline);
      var summaryEl = (summary.token_used > 0 || summary.rounds > 0)
        ? React.createElement('div', { className: 'ak-block-summary', 'data-ak-summary': '1' },
            React.createElement('span', { className: 'ak-sum-item', 'data-ak-sum-token': String(summary.token_used) },
              t('summary.token'), React.createElement('b', {}, String(summary.token_used))),
            React.createElement('span', { className: 'ak-sum-item', 'data-ak-sum-duration': String(summary.totalMs) },
              t('summary.duration'), React.createElement('b', {}, logic.formatDuration(summary.totalMs))),
            React.createElement('span', { className: 'ak-sum-item', 'data-ak-sum-rounds': String(summary.rounds) },
              t('summary.rounds', { n: summary.rounds }))
          )
        : null;
      // needs_human modules carry a human-decision dialog (or its trigger).
      var replyEl = (m.status === 'needs_human')
        ? renderReplyUi(React, m, t, ui)
        : null;
      var children = null;
      if (Array.isArray(m.split) && m.split.length) {
        var childNodes = m.split.map(function (c) { return renderModuleBlock(React, c, t, ui, activeId, detailUi, timeline); });
        children = React.createElement('div', { className: 'ak-split', 'data-ak-split': '1' }, childNodes);
      }
      return React.createElement('div', {
        className: cls,
        'data-ak-module': m.id,
        'data-ak-status': m.status,
        'data-ak-active': isActive ? '1' : '0'
      },
        head,
        token,
        replyEl,
        summaryEl,
        children
      );
    }

    /* ============================================================ *
     * Recursive pipeline chain renderers (this block)
     *
     * Render the EXPLICIT flow chain produced by logic.buildPipelineChain:
     * planner/root (left) → module blocks → per-module E/A round cards →
     * split fork → submodule recursion. Keeps the status badge, active ring,
     * decision dialog, detail popover and token split from the legacy block.
     * ============================================================ */

    function renderPipeline(React, pipeline, activeId, t, ui, detailUi, timeline, forkUi) {
      var plannerEl = pipeline.planner
        ? renderPlannerCard(React, pipeline.planner, t)
        : null;
      var rootEl = null;
      if (pipeline.root) {
        rootEl = React.createElement('div', { className: 'ak-pipeline-root-wrap', 'data-ak-root': '1' },
          renderPipelineModule(React, pipeline.root, activeId, t, ui, detailUi, timeline, true, forkUi),
          React.createElement('span', { className: 'ak-pipeline-arrow', 'data-ak-arrow': '1' }, t('flow.to'))
        );
      }
      var chainEls = (pipeline.chains || []).map(function (chain, idx) {
        return React.createElement('div', { key: chain.id || 'chain-' + idx, className: 'ak-pipeline-module-wrap' },
          renderPipelineModule(React, chain, activeId, t, ui, detailUi, timeline, false, forkUi));
      });
      return React.createElement('div', { className: 'ak-pipeline', 'data-ak-route-map': '1', 'data-ak-pipeline': '1' },
        plannerEl,
        rootEl,
        React.createElement('div', { className: 'ak-pipeline-modules' }, chainEls)
      );
    }

    /**
     * Planner summary card at the pipeline head (NOT a module block). Layout:
     *   - plan section: modules split + planning duration + plan-phase cost
     *   - divider
     *   - total section: "总消耗" heading + input(+cache)/output/cache/cache
     *     hit rate + grand total (cache included per 杰哥口径).
     */
    function renderPlannerCard(React, planner, t) {
      if (!planner) return null;

      // --- plan section ---
      var planCells = [];
      planCells.push(React.createElement('div', { key: 'mods', className: 'ak-plan-cell', 'data-ak-plan-modules': '1' },
        React.createElement('span', { className: 'ak-plan-cell-lbl' }, t('plan.modules')),
        React.createElement('b', { className: 'ak-plan-cell-val' }, String(planner.modulesCount))));
      if (planner.planMs != null) {
        var durLbl = planner.planMs > 0 ? logic.formatDuration(planner.planMs) : '<1s';
        planCells.push(React.createElement('div', { key: 'dur', className: 'ak-plan-cell', 'data-ak-plan-duration': '1' },
          React.createElement('span', { className: 'ak-plan-cell-lbl' }, t('plan.duration')),
          React.createElement('b', { className: 'ak-plan-cell-val' }, durLbl)));
      }
      var pu = planner.planUsage || {};
      // 无规划阶段判定：planner 桶全 0 且规划耗时缺失/为 0（scaffold 直跑类
      // run 本来就没有 planner 会话）。显示明确文案而不是一排 0，避免误读成
      // 统计丢失。有规划消耗（哪怕还在增长）正常显示数值。
      var noPlanPhase = (!pu.hasSplit) ||
        (pu.total === 0 && pu.input === 0 && pu.output === 0 && !planner.planMs);
      if (noPlanPhase) {
        planCells.push(React.createElement('div', { key: 'plan-none', className: 'ak-plan-cell', 'data-ak-plan-none': '1' },
          React.createElement('span', { className: 'ak-plan-cell-lbl' }, t('plan.noPlan')),
          React.createElement('b', { className: 'ak-plan-cell-val' }, '—')));
      } else if (pu.hasSplit) {
        planCells.push(React.createElement('div', { key: 'plan-cost', className: 'ak-plan-cell', 'data-ak-plan-cost': '1' },
          React.createElement('span', { className: 'ak-plan-cell-lbl' }, t('plan.cost')),
          React.createElement('b', { className: 'ak-plan-cell-val' }, String(pu.total))));
      }

      // --- total section (with divider) ---
      var totalCells = [];
      var u = planner.usage || {};
      if (u.hasSplit) {
        totalCells.push(React.createElement('div', { key: 'in', className: 'ak-plan-cell', 'data-ak-plan-input': '1' },
          React.createElement('span', { className: 'ak-plan-cell-lbl' }, t('token.input')),
          React.createElement('b', { className: 'ak-plan-cell-val' }, String(u.input))));
        totalCells.push(React.createElement('div', { key: 'out', className: 'ak-plan-cell', 'data-ak-plan-output': '1' },
          React.createElement('span', { className: 'ak-plan-cell-lbl' }, t('token.output')),
          React.createElement('b', { className: 'ak-plan-cell-val' }, String(u.output))));
        totalCells.push(React.createElement('div', { key: 'cache', className: 'ak-plan-cell', 'data-ak-plan-cache': '1' },
          React.createElement('span', { className: 'ak-plan-cell-lbl' }, t('token.cache')),
          React.createElement('b', { className: 'ak-plan-cell-val' }, String(u.cache))));
        if (u.cacheRate != null) {
          totalCells.push(React.createElement('div', { key: 'rate', className: 'ak-plan-cell', 'data-ak-plan-cache-rate': '1' },
            React.createElement('span', { className: 'ak-plan-cell-lbl' }, t('token.cacheRate')),
            React.createElement('b', { className: 'ak-plan-cell-val' }, u.cacheRate + '%')));
        }
        if (u.durationMs > 0) {
          totalCells.push(React.createElement('div', { key: 'dur', className: 'ak-plan-cell', 'data-ak-plan-run-duration': '1' },
            React.createElement('span', { className: 'ak-plan-cell-lbl' }, t('token.duration')),
            React.createElement('b', { className: 'ak-plan-cell-val' }, logic.formatDuration(u.durationMs))));
        }
        totalCells.push(React.createElement('div', { key: 'total', className: 'ak-plan-cell', 'data-ak-plan-total': '1' },
          React.createElement('span', { className: 'ak-plan-cell-lbl' }, t('plan.total')),
          React.createElement('b', { className: 'ak-plan-cell-val' }, String(u.total))));
      }

      var totalEl = totalCells.length
        ? React.createElement('div', { className: 'ak-plan-total', 'data-ak-plan-total-sec': '1' },
            React.createElement('span', { className: 'ak-plan-total-title' }, t('plan.total')),
            React.createElement('div', { className: 'ak-plan-cells' }, totalCells))
        : null;

      return React.createElement('div', { className: 'ak-plan-card', 'data-ak-plan': '1' },
        React.createElement('div', { className: 'ak-plan-head' },
          React.createElement('span', { className: 'ak-plan-title' }, t('flow.planner'))),
        React.createElement('div', { className: 'ak-plan-cells', 'data-ak-plan-cells': '1' }, planCells),
        totalEl
      );
    }

    /** One module block in the explicit recursive chain: head + round cards + fork. */
    function renderPipelineModule(React, chain, activeId, t, ui, detailUi, timeline, isRoot, forkUi) {
      var cls = 'ak-module ak-module-' + chain.status;
      if (isRoot) cls += ' ak-module-root';
      var isActive = !!chain.id && chain.id === activeId;
      if (isActive) cls += ' ak-active';
      // Whole recursion sub-tree active (a fork descendant is running) gets a
      // soft ring so the branch is readable without hunting the exact card.
      if (logic.isActiveChain(chain)) cls += ' ak-chain-active';
      var submodules = logic.countSubmodules(chain);
      var depth = logic.chainDepth(chain);
      var badge = React.createElement('span', {
        className: 'ak-status-badge ak-status-' + chain.status,
        'data-ak-status': chain.status
      }, t('status.' + chain.status));
      var rootTag = isRoot
        ? React.createElement('span', { className: 'ak-root-label', 'data-ak-root-label': '1' }, t('flow.planner'))
        : null;
      var depthTag = depth > 0
        ? React.createElement('span', { className: 'ak-depth-tag', 'data-ak-depth': String(depth) },
            t('flow.recursion') + ' · ' + t('flow.depth', { n: depth }))
        : null;
      var head = React.createElement('div', { className: 'ak-module-head' },
        React.createElement('span', { className: 'ak-module-name' },
          rootTag,
          chain.name),
        React.createElement('div', { className: 'ak-module-head-actions' },
          badge,
          React.createElement('button', {
            className: 'ak-detail-btn',
            type: 'button',
            'data-ak-detail-open': '1',
            title: t('detail.open'),
            onClick: function (e) {
              e.stopPropagation();
              detailUi.open(chain.id);
            }
          }, t('detail.open'))
        )
      );
      // 外层模块块 token：优先 usageTotal（含缓存总消耗，与详情一致）；
      // 否则回退快照 token_used。
      var blockToken = (chain.usageTotal != null) ? chain.usageTotal : chain.token_used;
      var token = (blockToken > 0)
        ? React.createElement('span', { className: 'ak-module-token' },
            t('module.token') + ' ' + blockToken)
        : null;
      var summary = logic.buildBlockSummary(chain, timeline);
      var summaryEl = (summary.token_used > 0 || summary.rounds > 0)
        ? React.createElement('div', { className: 'ak-block-summary', 'data-ak-summary': '1' },
            React.createElement('span', { className: 'ak-sum-item', 'data-ak-sum-token': String(summary.token_used) },
              t('summary.token'), React.createElement('b', {}, String(summary.token_used))),
            React.createElement('span', { className: 'ak-sum-item', 'data-ak-sum-duration': String(summary.totalMs) },
              t('summary.duration'), React.createElement('b', {}, logic.formatDuration(summary.totalMs))),
            React.createElement('span', { className: 'ak-sum-item', 'data-ak-sum-rounds': String(summary.rounds) },
              t('summary.rounds', { n: summary.rounds }))
          )
        : null;
      var replyEl = renderHumanDecisionUi(React, chain, t, ui);
      var roundsEl = renderRoundCards(React, chain.rounds, t, isActive);
      var forkEl = chain.fork
        ? renderFork(React, chain.fork, activeId, t, ui, detailUi, timeline, forkUi)
        : null;
      return React.createElement('div', {
        className: cls,
        'data-ak-module': chain.id,
        'data-ak-status': chain.status,
        'data-ak-active': isActive ? '1' : '0',
        'data-ak-depth': String(depth),
        'data-ak-submodules': String(submodules)
      },
        head,
        token,
        depthTag,
        replyEl,
        roundsEl,
        forkEl,
        summaryEl
      );
    }

    /**
     * Per-module E/A round cards, top-to-bottom in dispatch (seq) order.
     * Each card carries a role-kind accent, a verdict label and, when the
     * module is active, a pulse on the most recent (in-flight) round card.
     * @param {React} React
     * @param {Array} rounds ordered E/A round records
     * @param {function} t i18n lookup
     * @param {boolean} isActive true when the parent module is currently active
     */
    function renderRoundCards(React, rounds, t, isActive) {
      if (!Array.isArray(rounds) || !rounds.length) {
        return React.createElement('span', { className: 'ak-flow-empty', 'data-ak-rounds': '0' },
          t('flow.noRounds'));
      }
      var lastIdx = rounds.length - 1;
      var cards = rounds.map(function (r, idx) {
        var kind = logic.roundKind(r.role);
        var roleLbl = r.role === 'executor' ? t('flow.executor')
          : (r.role === 'auditor' ? t('flow.auditor') : (kind === 'split' ? t('flow.fork') : t('flow.unknown')));
        var roundLbl = (r.round != null)
          ? t('flow.round', { round: r.round }) + ' · '
          : '';
        var verdict = r.verdict != null ? String(r.verdict) : 'pending';
        var verdictLbl = t('flow.verdict.' + verdict);
        if (verdictLbl === 'flow.verdict.' + verdict) verdictLbl = verdict;
        var cls = 'ak-round-card ak-round-card-' + kind;
        if (isActive && idx === lastIdx) cls += ' ak-round-active';
        return React.createElement('div', {
          key: 'r-' + idx,
          className: cls,
          'data-ak-round-role': r.role,
          'data-ak-round-kind': kind,
          'data-ak-round-verdict': verdict
        },
          React.createElement('span', { className: 'ak-round-card-role' }, roleLbl),
          React.createElement('span', { className: 'ak-round-card-round' }, roundLbl),
          React.createElement('span', { className: 'ak-round-card-verdict ak-round-verdict-' + verdict },
            t('detail.verdict') + ': ' + verdictLbl),
          React.createElement('span', { className: 'ak-round-card-dur' },
            logic.formatDuration(r.duration_ms))
        );
      });
      return React.createElement('div', { className: 'ak-flow-rounds', 'data-ak-rounds': '1' }, cards);
    }

    /**
     * Split fork point + the submodule recursion blocks hanging below it.
     * Each child gets a branch number; the whole recursion subtree can be
     * collapsed/expanded via forkUi. Branch bubbles + connector lines give the
     * fork a visual read without losing the pure render structure.
     */
    function renderFork(React, fork, activeId, t, ui, detailUi, timeline, forkUi) {
      var childEls = (fork.children || []).map(function (child, idx) {
        var num = idx + 1;
        return React.createElement('div', { key: child.id || 'child-' + idx, className: 'ak-pipeline-child' },
          React.createElement('span', { className: 'ak-branch-no', 'data-ak-branch': String(num) },
            t('flow.branch') + ' ' + num),
          renderPipelineModule(React, child, activeId, t, ui, detailUi, timeline, false, forkUi));
      });
      var collapsed = !!(forkUi && forkUi.isCollapsed && forkUi.isCollapsed(childId(fork)));
      var toggleBtn = (forkUi && typeof forkUi.toggle === 'function')
        ? React.createElement('button', {
            type: 'button',
            className: 'ak-fork-toggle',
            'data-ak-fork-toggle': '1',
            onClick: function () { forkUi.toggle(childId(fork)); }
          }, collapsed ? t('flow.expand') : t('flow.collapse'))
        : null;
      var bodyEl = collapsed
        ? React.createElement('span', { className: 'ak-fork-collapsed', 'data-ak-fork-collapsed': '1' },
            t('flow.submodules', { n: fork.splitCount }))
        : React.createElement('div', { className: 'ak-recursion', 'data-ak-recursion': '1' }, childEls);
      return React.createElement('div', {
        className: 'ak-fork' + (collapsed ? ' ak-fork-collapsed-wrap' : ''),
        'data-ak-fork': '1',
        'data-ak-split': '1'
      },
        React.createElement('div', { className: 'ak-fork-label', 'data-ak-fork-label': '1' },
          React.createElement('span', {}, t('flow.fork') + ' · ' + t('flow.submodules', { n: fork.splitCount })),
          toggleBtn),
        bodyEl
      );
    }

    /** A stable id for the fork keyed by its parent chain id (children[0] carries it). */
    function childId(fork) {
      var children = fork && Array.isArray(fork.children) ? fork.children : [];
      var first = children[0];
      return (first && first.id != null) ? String(first.id) : '';
    }

    /**
     * Needs_human 三态生命周期卡（决策卡需求 A+B）：
     * - pending：完整问询内容（模块/打回原因全文/发生时间/A-D 选项含义/草稿）
     *   + 原有决策交互（触发按钮或弹开的 dialog）；
     * - resolved：已解决样式（解决者=人|流程 + 解决时间 + 结果摘要），不再有
     *   待处理交互；
     * - 关闭：数据侧 done 即移出快照 needs_human 清单 → humanDecision 为
     *   resolved 或 null，待处理卡自然消失（事件驱动刷新驱动，无需手动）。
     * humanDecision 缺失（旧桥/缺字段）时保底维持旧行为（status 驱动）。
     */
    function renderHumanDecisionUi(React, chain, t, ui) {
      var hd = chain && chain.humanDecision;
      if (!hd) {
        return (chain && chain.status === 'needs_human')
          ? renderReplyUi(React, chain, t, ui)
          : null;
      }
      if (hd.state === 'resolved') {
        return renderDecisionResolved(React, chain, hd, t);
      }
      return renderDecisionPending(React, chain, hd, t, ui);
    }

    /** 待处理卡：问询内容 5 项 + 决策交互。 */
    function renderDecisionPending(React, chain, hd, t, ui) {
      var sinceLbl = hd.pendingSince != null ? String(hd.pendingSince) : '—';
      var cells = [
        { key: 'reason', lbl: t('decision.reason'), val: hd.reason || t('time.none') },
        { key: 'since', lbl: t('decision.since'), val: sinceLbl }
      ];
      if (hd.draftText) {
        cells.push({ key: 'draft', lbl: t('decision.draft'), val: hd.draftText });
      }
      var infoRows = cells.map(function (c) {
        return React.createElement('div', { key: c.key, className: 'ak-decision-row', 'data-ak-decision-row': c.key },
          React.createElement('span', { className: 'ak-decision-lbl' }, c.lbl),
          React.createElement('span', { className: 'ak-decision-val' }, c.val));
      });
      return React.createElement('div', {
        className: 'ak-decision ak-decision-pending',
        'data-ak-decision': '1',
        'data-ak-decision-state': 'pending',
        'data-ak-decision-module': chain.id
      },
        React.createElement('div', { className: 'ak-decision-head' },
          React.createElement('span', { className: 'ak-decision-title' }, t('decision.pendingTitle')),
          React.createElement('span', { className: 'ak-decision-module' }, chain.id + (chain.name && chain.name !== chain.id ? ' · ' + chain.name : ''))),
        infoRows.length ? React.createElement('div', { className: 'ak-decision-rows', 'data-ak-decision-rows': '1' }, infoRows) : null,
        React.createElement('div', { className: 'ak-decision-options', 'data-ak-decision-options': '1' }, t('decision.options')),
        renderReplyUi(React, chain, t, ui));
    }

    /** 已解决卡：解决者（人|流程）+ 解决时间 + 结果摘要；不再有待处理交互。 */
    function renderDecisionResolved(React, chain, hd, t) {
      var byLbl = t(hd.by === 'human' ? 'decision.by.human' : 'decision.by.process');
      var whenLbl = hd.answeredAt != null ? String(hd.answeredAt) : '—';
      var summary = hd.by === 'human'
        ? ((hd.code ? '[' + hd.code + '] ' : '') + (hd.text || ''))
        : t('decision.resolvedByProcess');
      var rows = [
        { key: 'by', lbl: t('decision.resolver'), val: byLbl },
        { key: 'at', lbl: t('decision.answerAt'), val: whenLbl }
      ];
      if (summary) rows.push({ key: 'result', lbl: t('decision.result'), val: summary });
      return React.createElement('div', {
        className: 'ak-decision ak-decision-resolved',
        'data-ak-decision': '1',
        'data-ak-decision-state': 'resolved',
        'data-ak-decision-by': hd.by,
        'data-ak-decision-module': chain.id
      },
        React.createElement('div', { className: 'ak-decision-head' },
          React.createElement('span', { className: 'ak-decision-title' }, t('decision.resolvedTitle')),
          React.createElement('span', { className: 'ak-decision-module' }, chain.id)),
        React.createElement('div', { className: 'ak-decision-rows', 'data-ak-decision-rows': '1' },
          rows.map(function (c) {
            return React.createElement('div', { key: c.key, className: 'ak-decision-row', 'data-ak-decision-row': c.key },
              React.createElement('span', { className: 'ak-decision-lbl' }, c.lbl),
              React.createElement('span', { className: 'ak-decision-val' }, c.val));
          })));
    }

    /** Needs_human block: show a trigger button, or the dialog when open. */
    function renderReplyUi(React, m, t, ui) {
      var open = !!(ui.reply && ui.reply.moduleId === m.id);
      if (!open) {
        return React.createElement('div', { className: 'ak-reply-trigger', 'data-ak-reply-trigger': '1' },
          React.createElement('button', {
            className: 'ak-reply-btn',
            type: 'button',
            'data-ak-reply-open': '1',
            onClick: function () { ui.openReply(m.id); }
          }, t('reply.respond'))
        );
      }
      return renderReplyDialog(React, m, t, ui);
    }

    /** The decision dialog: 4 commands + free input (custom requires input). */
    function renderReplyDialog(React, m, t, ui) {
      var r = ui.reply;
      var commandButtons = logic.REPLY_COMMANDS.map(function (cmd) {
        var active = r.command === cmd;
        return React.createElement('button', {
          key: cmd,
          type: 'button',
          className: 'ak-reply-command' + (active ? ' ak-reply-command-active' : ''),
          'data-ak-command': cmd,
          'data-ak-command-active': active ? '1' : '0',
          onClick: function () { ui.setCommand(cmd); }
        }, t('reply.command.' + cmd));
      });
      var errorEl = r.error
        ? React.createElement('div', { className: 'ak-reply-error', role: 'alert', 'data-ak-reply-error': '1' }, r.error)
        : null;
      return React.createElement('div', {
        className: 'ak-reply',
        'data-ak-reply': '1',
        'data-ak-reply-module': m.id
      },
        React.createElement('div', { className: 'ak-reply-head' },
          React.createElement('span', { className: 'ak-reply-title' }, t('reply.title')),
          React.createElement('span', { className: 'ak-reply-hint' }, t('reply.hint'))
        ),
        React.createElement('div', { className: 'ak-reply-commands', 'data-ak-reply-commands': '1' }, commandButtons),
        React.createElement('div', { className: 'ak-reply-field' },
          React.createElement('label', { htmlFor: 'ak-reply-instruction-' + m.id }, t('reply.instruction.label')),
          React.createElement('textarea', {
            id: 'ak-reply-instruction-' + m.id,
            className: 'ak-reply-input',
            'data-ak-reply-instruction': '1',
            value: r.instruction || '',
            placeholder: t('reply.instruction.placeholder'),
            onChange: function (e) { ui.setInstruction(e.target.value); }
          })
        ),
        errorEl,
        React.createElement('div', { className: 'ak-reply-actions' },
          React.createElement('button', {
            className: 'ak-reply-cancel',
            type: 'button',
            'data-ak-reply-cancel': '1',
            disabled: !!r.submitting,
            onClick: function () { ui.closeReply(); }
          }, t('reply.cancel')),
          React.createElement('button', {
            className: 'ak-reply-submit',
            type: 'button',
            'data-ak-reply-submit': '1',
            disabled: !!r.submitting,
            onClick: function () { ui.submitReply(m.id); }
          }, r.submitting ? t('reply.submitting') : t('reply.submit'))
        )
      );
    }

    /**
     * Detail popover for a module block. Rendered as an overlay listing the
     * module's reason, executor/auditor round chain (+ verdict + duration),
     * token split (input/output/cache), timing and split-children info. All
     * derivation is delegated to pure logic.js helpers so it stays unit-testable.
     * @returns {ReactElement|null}
     */
    function renderDetailPopover(React, routeMap, detailUi, timeline, t) {
      var moduleId = detailUi && detailUi.popoverId;
      if (!moduleId) return null;
      var m = logic.findModuleView(routeMap, moduleId);
      if (!m) return null;
      // Prefer this module's own (or split-rolled-up) per-module breakdown,
      // then the run-level one. A split parent rolls up all its submodules.
      var usage = (routeMap && routeMap.usageByModule)
        ? (logic.aggregateUsageByTree(routeMap.usageByModule, m) || routeMap.usage || {})
        : ((routeMap && routeMap.usage) || {});
      var d = logic.buildModuleDetail(m, timeline, usage);

      // Round chain (executor/auditor + verdict + duration).
      var roundsEl;
      if (d.roundsTotal > 0) {
        var roundRows = d.rounds.map(function (r, idx) {
          return React.createElement('div', {
            key: idx,
            className: 'ak-round-row',
            'data-ak-round-role': r.role,
            'data-ak-round-verdict': r.verdict
          },
            React.createElement('span', { className: 'ak-round-role' },
              t('detail.round', { round: r.round }) + ' · ' + r.role),
            React.createElement('span', { className: 'ak-round-verdict' },
              t('detail.verdict') + ': ' + r.verdict),
            React.createElement('span', { className: 'ak-round-dur' },
              t('detail.duration') + ' ' + logic.formatDuration(r.duration_ms))
          );
        });
        roundsEl = React.createElement('div', { className: 'ak-rounds', 'data-ak-rounds': '1' }, roundRows);
      } else {
        roundsEl = React.createElement('span', { className: 'ak-popover-empty', 'data-ak-rounds': '0' },
          t('detail.rounds.empty'));
      }

      // Token split (input/output/cache when the run has split data).
      var tokenEl;
      if (d.usage.hasSplit) {
        var cells = [
          { key: 'input', lbl: t('token.input'), val: d.usage.input },
          { key: 'output', lbl: t('token.output'), val: d.usage.output },
          { key: 'cache', lbl: t('token.cache'), val: d.usage.cache }
        ];
        if (d.usage.cacheRate != null) {
          cells.push({ key: 'rate', lbl: t('token.cacheRate'), val: d.usage.cacheRate + '%' });
        }
        if (d.usage.durationMs > 0) {
          cells.push({ key: 'dur', lbl: t('token.duration'), val: logic.formatDuration(d.usage.durationMs) });
        }
        cells.push({ key: 'total', lbl: t('token.total'), val: d.usage.total });
        cells = cells.map(function (c) {
          return React.createElement('div', { key: c.key, className: 'ak-token-cell', 'data-ak-token': c.key },
            React.createElement('span', { className: 'ak-tok-lbl' }, c.lbl),
            React.createElement('span', { className: 'ak-tok-val' }, String(c.val))
          );
        });
        tokenEl = React.createElement('div', { className: 'ak-token-grid', 'data-ak-token-split': '1' }, cells);
      } else {
        tokenEl = React.createElement('span', { className: 'ak-popover-empty', 'data-ak-token-split': '0' },
          t('usage.noSplit') + (d.usage.total > 0 ? '（' + t('token.total') + ' ' + d.usage.total + '）' : ''));
      }

      // Timing rows.
      var startedVal = d.started_at != null ? String(d.started_at) : t('time.none');
      var endedVal = d.ended_at != null ? String(d.ended_at) : t('time.none');

      // Split children info.
      var splitEl;
      if (d.splitCount > 0) {
        var splitRows = d.split.map(function (s) {
          return React.createElement('div', { key: s.id, className: 'ak-split-item', 'data-ak-split-module': s.id },
            React.createElement('span', { className: 'ak-split-name' }, s.name),
            React.createElement('span', { className: 'ak-status-badge ak-status-' + s.status }, t('status.' + s.status))
          );
        });
        splitEl = React.createElement('div', { className: 'ak-split-list', 'data-ak-split-list': '1' }, splitRows);
      } else {
        splitEl = React.createElement('span', { className: 'ak-popover-empty', 'data-ak-split-list': '0' },
          t('detail.splitCount', { n: 0 }));
      }

      return React.createElement('div', { className: 'ak-popover', 'data-ak-popover': '1', onClick: detailUi.close },
        React.createElement('div', { className: 'ak-popover-card', 'data-ak-popover-card': '1',
          onClick: function (e) { e.stopPropagation(); } },
          React.createElement('div', { className: 'ak-popover-head' },
            React.createElement('span', { className: 'ak-popover-title' },
              t('detail.title') + ' — ' + d.name + ' (' + d.id + ')'),
            React.createElement('button', { className: 'ak-popover-close', type: 'button',
              'data-ak-popover-close': '1', onClick: detailUi.close }, t('detail.close'))
          ),
          React.createElement('div', { className: 'ak-popover-sec' },
            React.createElement('h4', {}, t('detail.reason')),
            React.createElement('span', { className: 'ak-popover-reason', 'data-ak-detail-reason': '1' },
              d.reason || t('detail.reason.empty'))
          ),
          React.createElement('div', { className: 'ak-popover-sec' },
            React.createElement('h4', {}, t('detail.rounds') + '（' + d.roundsTotal + '）'),
            roundsEl
          ),
          React.createElement('div', { className: 'ak-popover-sec' },
            React.createElement('h4', {}, t('detail.token')),
            tokenEl
          ),
          React.createElement('div', { className: 'ak-popover-sec' },
            React.createElement('h4', {}, t('detail.time')),
            React.createElement('div', { className: 'ak-popover-row' },
              React.createElement('span', { className: 'ak-lbl' }, t('detail.started')),
              React.createElement('span', { className: 'ak-val', 'data-ak-detail-started': '1' }, startedVal)),
            React.createElement('div', { className: 'ak-popover-row' },
              React.createElement('span', { className: 'ak-lbl' }, t('detail.ended')),
              React.createElement('span', { className: 'ak-val', 'data-ak-detail-ended': '1' }, endedVal)),
            React.createElement('div', { className: 'ak-popover-row' },
              React.createElement('span', { className: 'ak-lbl' }, t('detail.duration')),
              React.createElement('span', { className: 'ak-val', 'data-ak-detail-duration': '1' },
                logic.formatDuration(d.durationMs)))
          ),
          React.createElement('div', { className: 'ak-popover-sec' },
            React.createElement('h4', {}, t('detail.split') + '（' + d.splitCount + '）'),
            splitEl
          )
        )
      );
    }

    // #region DSH client-plugin lifecycle (official shape).
    //
    // DSH 前端 Boot 用 dsh-client-modules 对每个 `dsh.client` 包 create entry 并
    // apply：factory 物化后的导出必须含 `apply`（+ 可选的 `inject` services），
    // 否则 loader `unwrapExports` 拿到 undefined → `invalid plugin ... received
    // undefined`。这是真实浏览器端加载 client 插件的唯一通道。
    //
    // 槽位选择：`conversation.view` 是一个 **list 型多标签** content 槽
    // （`{kind:"list", scope:"session"}`），conversation 自身和 ui-trajectory
    // 都以不同 `id` 叠加成一个标签轮播。我们把 AutoKnit 路线图注册成自己的一个
    // 标签（id `autoknit`，label "AutoKnit"）。
    var inject = ['slots'];

    function apply(ctx) {
      // 数据桥地址：组件 mount 时读 window.__DSH__.config.autoknit.baseURL。
      // 未显式配置时给一个合理默认（本地 fw-api serve），让面板开箱即连本地数据桥，
      // 无需在控制台手动设；用户仍可通过 window.__DSH__.config.autoknit.baseURL 覆盖。
      var win = typeof window !== 'undefined' ? window : null;
      if (win) {
        var dsh = win.__DSH__ || (win.__DSH__ = {});
        dsh.config = dsh.config || {};
        dsh.config.autoknit = dsh.config.autoknit || {};
        if (!dsh.config.autoknit.baseURL) dsh.config.autoknit.baseURL = 'http://127.0.0.1:8765/api';
      }

      ctx.slots.inject('conversation.view', function () {
        return ctx.slots.register({
          name: 'conversation.view',
          id: 'autoknit',
          order: 20,
          locale: 'autoknit',
          label: function () { return 'AutoKnit'; },
          inject: function () { return {}; }
        }, AutoknitPanel);
      });
    }

    exports.apply = apply;
    exports.inject = inject;
    // #endregion
  });
})();
