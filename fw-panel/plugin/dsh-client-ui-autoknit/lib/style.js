'use strict';

/**
 * dsh-client-ui-autoknit — stylesheet injection.
 *
 * The browser half injects the details-panel CSS as a `<style>` element once,
 * so the plugin needs no build step / no bundler CSS handling. The CSS text
 * lives here as the single JS source; `styles.css` in the same directory holds
 * the identical content for hosts that prefer a static stylesheet (keep the
 * two in sync — the plugin author is responsible for that).
 *
 * `injectStyles` is DOM-guarded and idempotent, so it is safe to call in the
 * node test harness where `window.document` is absent.
 */

var CSS = '/* dsh-client-ui-autoknit route-map panel styles — see lib/styles.css */' +
  // Root theme: dark default via CSS custom properties (DSH theme vars take
  // precedence when the host defines them; no hardcoded black). The panel uses
  // a transparent bottom so the host's background shows through.
  '.ak-details-panel{box-sizing:border-box;display:flex;flex-direction:column;gap:8px;' +
  'padding:12px;min-width:360px;max-width:1140px;height:100%;overflow:hidden;' +
  'font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"PingFang SC","Microsoft YaHei",sans-serif;' +
  'color:var(--ak-fg,#e6edf3);background:var(--ak-panel-bg,transparent);border:1px solid var(--ak-border,#30363d);border-radius:8px;}' +
  '.ak-route-map{--ak-bg:#0d1117;--ak-fg:#e6edf3;--ak-border:#30363d;--ak-muted:#8b949e;--ak-module-bg:#161b22;' +
  '--ak-done:#3fb950;--ak-pending:#8b949e;--ak-running:#58a6ff;--ak-needs-human:#d29922;--ak-block:#f85149;' +
  'display:flex;flex-direction:row;gap:16px;overflow-x:auto;padding:8px 4px;flex:1 1 auto;}' +
  '.ak-header{display:flex;align-items:baseline;justify-content:space-between;gap:8px;padding-bottom:8px;border-bottom:1px solid var(--ak-border,#30363d);}' +
  '.ak-title{margin:0;font-size:14px;font-weight:600;color:var(--ak-fg,#e6edf3);}' +
  '.ak-count{font-size:12px;color:var(--ak-muted,#8b949e);white-space:nowrap;}' +
  '.ak-runs{display:flex;flex-direction:column;gap:6px;}' +
  '.ak-runs-label{font-size:11px;font-weight:600;color:var(--ak-muted,#8b949e);text-transform:uppercase;letter-spacing:.03em;}' +
  '.ak-run-pills{display:flex;flex-wrap:wrap;gap:6px;}' +
  '.ak-run-pill{display:inline-flex;align-items:center;gap:4px;padding:3px 4px 3px 10px;font-size:11px;color:var(--ak-fg,#e6edf3);background:var(--ak-module-bg,#161b22);border:1px solid var(--ak-border,#30363d);border-radius:14px;}' +
  '.ak-run-pill-active{color:var(--ak-running,#58a6ff);border-color:#1f6feb;background:#10233f;}' +
  '.ak-run-select{cursor:pointer;background:none;border:none;color:inherit;font-size:11px;padding:0;}' +
  '.ak-run-select:disabled,.ak-run-archive:disabled{opacity:.5;cursor:default;}' +
  '.ak-run-archive{cursor:pointer;font-size:10px;color:var(--ak-muted,#8b949e);background:none;border:1px solid var(--ak-border,#30363d);border-radius:10px;padding:1px 6px;}' +
  '.ak-run-archive:hover{color:var(--ak-block,#f85149);border-color:var(--ak-block,#f85149);}' +
  '.ak-runs-empty{font-size:12px;color:var(--ak-muted,#8b949e);}' +
  '.ak-loading{display:flex;align-items:center;gap:8px;color:var(--ak-muted,#8b949e);}' +
  '.ak-spinner{width:14px;height:14px;border:2px solid var(--ak-border,#30363d);border-top-color:var(--ak-running,#58a6ff);border-radius:50%;animation:ak-spin .8s linear infinite;}' +
  '@keyframes ak-spin{to{transform:rotate(360deg);}}' +
  '.ak-route-empty{font-size:12px;color:var(--ak-muted,#8b949e);padding:12px 4px;text-align:center;}' +
  '.ak-error{display:flex;flex-direction:column;gap:8px;align-items:flex-start;padding:10px;margin:0;background:#31181a;border:1px solid #da3633;border-radius:6px;color:#f85149;font-size:12px;word-break:break-word;}' +
  '.ak-error-title{font-weight:600;}.ak-retry{cursor:pointer;padding:4px 12px;font-size:12px;color:#fff;background:var(--ak-running,#58a6ff);border:1px solid #1f6feb;border-radius:6px;}' +
  '.ak-column{display:flex;flex-direction:column;gap:12px;min-width:150px;max-width:210px;flex:0 0 auto;}' +
  '.ak-module{display:flex;flex-direction:column;gap:6px;padding:8px;background:var(--ak-module-bg,#161b22);border:1px solid var(--ak-border,#30363d);border-radius:8px;position:relative;}' +
  '.ak-module-head{display:flex;align-items:center;justify-content:space-between;gap:6px;}' +
  '.ak-module-head-actions{display:flex;align-items:center;gap:4px;}' +
  '.ak-plan-card{display:flex;flex-direction:column;gap:6px;padding:8px 10px;background:var(--ak-module-bg,#161b22);border:1px solid var(--ak-border,#30363d);border-left:3px solid #a371f7;border-radius:8px;width:260px;flex:0 0 auto;align-self:flex-start;}' +
  '.ak-plan-head{display:flex;align-items:center;gap:6px;}' +
  '.ak-plan-title{font-weight:600;font-size:12px;letter-spacing:0.3px;}' +
  '.ak-plan-cells{display:flex;flex-wrap:wrap;gap:10px 16px;}' +
  '.ak-plan-total{display:flex;flex-direction:column;gap:6px;border-top:1px dashed var(--ak-border,#30363d);margin-top:4px;padding-top:8px;}' +
  '.ak-plan-total-title{font-size:11px;font-weight:600;color:var(--ak-muted,#8b949e);letter-spacing:0.3px;}' +
  '.ak-plan-cell{display:flex;align-items:baseline;gap:4px;font-size:12px;}' +
  '.ak-plan-cell-lbl{color:var(--ak-muted,#8b949e);}' +
  '.ak-plan-cell-val{font-variant-numeric:tabular-nums;}' +
  '.ak-module-name{font-weight:600;font-size:12px;color:var(--ak-fg,#e6edf3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}' +
  '.ak-module-token{font-size:11px;color:var(--ak-muted,#8b949e);}' +
  '.ak-status-badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap;}' +
  '.ak-status-done{background:#12261b;color:var(--ak-done,#3fb950);border:1px solid #238636;}' +
  '.ak-status-pending{background:#21262d;color:var(--ak-pending,#8b949e);border:1px solid #484f58;}' +
  '.ak-status-running{background:#10233f;color:var(--ak-running,#58a6ff);border:1px solid #1f6feb;}' +
  '.ak-status-needs_human{background:#2d2410;color:var(--ak-needs-human,#d29922);border:1px solid #9e6a03;}' +
  '.ak-status-block{background:#31181a;color:var(--ak-block,#f85149);border:1px solid #da3633;}' +
  '.ak-split{display:flex;flex-direction:column;gap:6px;margin-left:8px;padding-left:10px;border-left:2px solid var(--ak-border,#30363d);}' +
  '.ak-detail-empty{font-size:12px;color:var(--ak-muted,#8b949e);}' +
  // Active module: highlight ring + rotating border (pure CSS animation, no JS polling).
  '.ak-module.ak-active{border-color:var(--ak-running,#58a6ff);box-shadow:0 0 0 1px var(--ak-running,#58a6ff);}' +
  '.ak-module.ak-active::after{content:"";position:absolute;inset:-2px;border-radius:inherit;' +
  'background:conic-gradient(from 0deg,var(--ak-running,#58a6ff),transparent 24%);' +
  '-webkit-mask:radial-gradient(farthest-side,transparent calc(100% - 3px),#000 calc(100% - 2px));' +
  'mask:radial-gradient(farthest-side,transparent calc(100% - 3px),#000 calc(100% - 2px));' +
  'animation:ak-border-spin 1.6s linear infinite;pointer-events:none;}' +
  '@keyframes ak-border-spin{to{transform:rotate(360deg);}}' +
  // needs_human decision dialog (continue/retry/revise/custom + free input).
  '.ak-reply-trigger{margin-top:6px;display:flex;}' +
  '.ak-reply-btn{cursor:pointer;padding:2px 10px;font-size:11px;color:var(--ak-needs-human,#d29922);' +
  'background:#2d2410;border:1px solid #9e6a03;border-radius:4px;}' +
  '.ak-reply{display:flex;flex-direction:column;gap:8px;margin-top:8px;padding:8px;' +
  'background:var(--ak-module-bg,#161b22);border:1px dashed var(--ak-needs-human,#d29922);border-radius:6px;}' +
  '.ak-reply-head{display:flex;align-items:center;justify-content:space-between;gap:6px;}' +
  '.ak-reply-title{font-size:11px;font-weight:600;color:var(--ak-needs-human,#d29922);}' +
  '.ak-reply-hint{font-size:11px;color:var(--ak-muted,#8b949e);}' +
  '.ak-reply-commands{display:flex;flex-wrap:wrap;gap:4px;}' +
  '.ak-reply-command{cursor:pointer;padding:2px 8px;font-size:11px;color:var(--ak-fg,#e6edf3);' +
  'background:var(--ak-module-bg,#161b22);border:1px solid var(--ak-border,#30363d);border-radius:10px;}' +
  '.ak-reply-command-active{color:var(--ak-running,#58a6ff);border-color:#1f6feb;background:#10233f;}' +
  '.ak-reply-field{display:flex;flex-direction:column;gap:3px;}' +
  '.ak-reply-field label{font-size:11px;color:var(--ak-muted,#8b949e);}' +
  '.ak-reply-input{width:100%;box-sizing:border-box;padding:4px 6px;font:11px/1.4 inherit;resize:vertical;' +
  'color:var(--ak-fg,#e6edf3);background:var(--ak-module-bg,#161b22);border:1px solid var(--ak-border,#30363d);border-radius:4px;}' +
  '.ak-reply-actions{display:flex;gap:6px;justify-content:flex-end;}' +
  '.ak-reply-submit{cursor:pointer;padding:3px 12px;font-size:11px;color:#fff;background:var(--ak-running,#58a6ff);' +
  'border:1px solid #1f6feb;border-radius:4px;}' +
  '.ak-reply-cancel{cursor:pointer;padding:3px 12px;font-size:11px;color:var(--ak-fg,#e6edf3);' +
  'background:transparent;border:1px solid var(--ak-border,#30363d);border-radius:4px;}' +
  '.ak-reply-submit:disabled{opacity:.5;cursor:default;}' +
  '.ak-reply-error{font-size:11px;color:var(--ak-block,#f85149);word-break:break-word;}' +
  '.ak-reply-success{font-size:11px;color:var(--ak-done,#3fb950);}' +
  // Block-bottom aggregate summary (token_used total / total duration / round count).
  '.ak-block-summary{display:flex;flex-wrap:wrap;gap:6px 10px;margin-top:2px;padding-top:6px;border-top:1px solid var(--ak-border,#30363d);font-size:10px;color:var(--ak-muted,#8b949e);}' +
  '.ak-block-summary .ak-sum-item{display:inline-flex;align-items:center;gap:3px;white-space:nowrap;}' +
  '.ak-block-summary .ak-sum-item b{font-weight:600;color:var(--ak-fg,#e6edf3);}' +
  // Detail button in the module head (opens the popover).
  '.ak-detail-btn{cursor:pointer;padding:0 7px;font-size:11px;color:var(--ak-muted,#8b949e);background:transparent;border:1px solid var(--ak-border,#30363d);border-radius:4px;line-height:1.7;}' +
  '.ak-detail-btn:hover{color:var(--ak-running,#58a6ff);border-color:#1f6feb;}' +
  // Detail popover overlay (reason / round chain / token split / timing / split).
  '.ak-popover{position:fixed;inset:0;z-index:1200;display:flex;align-items:flex-start;justify-content:center;padding:32px 16px;background:rgba(1,4,9,.6);}' +
  '.ak-popover-card{max-width:560px;width:100%;max-height:80vh;overflow:auto;display:flex;flex-direction:column;gap:12px;padding:14px;' +
  'background:var(--ak-module-bg,#161b22);border:1px solid var(--ak-border,#30363d);border-radius:8px;color:var(--ak-fg,#e6edf3);}' +
  '.ak-popover-head{display:flex;align-items:center;justify-content:space-between;gap:8px;}' +
  '.ak-popover-title{font-size:13px;font-weight:600;}' +
  '.ak-popover-close{cursor:pointer;padding:1px 8px;font-size:12px;color:var(--ak-fg,#e6edf3);background:transparent;border:1px solid var(--ak-border,#30363d);border-radius:4px;}' +
  '.ak-popover-close:hover{color:var(--ak-block,#f85149);border-color:#da3633;}' +
  '.ak-popover-sec{display:flex;flex-direction:column;gap:6px;}' +
  '.ak-popover-sec h4{margin:0;font-size:11px;font-weight:600;color:var(--ak-muted,#8b949e);text-transform:uppercase;letter-spacing:.03em;}' +
  '.ak-popover-row{display:flex;gap:6px;font-size:12px;align-items:baseline;}' +
  '.ak-popover-row .ak-lbl{flex:0 0 64px;color:var(--ak-muted,#8b949e);}' +
  '.ak-popover-row .ak-val{color:var(--ak-fg,#e6edf3);word-break:break-word;}' +
  '.ak-popover-reason{font-size:12px;color:var(--ak-fg,#e6edf3);word-break:break-word;}' +
  '.ak-rounds{display:flex;flex-direction:column;gap:4px;}' +
  '.ak-round-row{display:flex;gap:6px;align-items:center;font-size:12px;}' +
  '.ak-round-role{font-weight:600;color:var(--ak-running,#58a6ff);min-width:72px;}' +
  '.ak-round-verdict{color:var(--ak-muted,#8b949e);}' +
  '.ak-round-dur{margin-left:auto;color:var(--ak-muted,#8b949e);white-space:nowrap;}' +
  '.ak-token-grid{display:flex;gap:8px;flex-wrap:wrap;}' +
  '.ak-token-cell{display:flex;flex-direction:column;gap:2px;min-width:64px;padding:6px 8px;background:var(--ak-bg,#0d1117);border:1px solid var(--ak-border,#30363d);border-radius:6px;}' +
  '.ak-token-cell .ak-tok-lbl{font-size:10px;color:var(--ak-muted,#8b949e);}' +
  '.ak-token-cell .ak-tok-val{font-size:12px;font-weight:600;color:var(--ak-fg,#e6edf3);}' +
  '.ak-popover-empty{font-size:12px;color:var(--ak-muted,#8b949e);}' +
  '.ak-split-list{display:flex;flex-direction:column;gap:4px;}' +
  '.ak-split-item{display:flex;gap:6px;align-items:center;font-size:12px;}' +
  '.ak-split-item .ak-split-name{font-weight:600;color:var(--ak-fg,#e6edf3);}' +
  '.ak-split-item .ak-status-badge{margin-left:auto;}' +
  // Recursive pipeline chain (planner → module → E/A round cards → split fork → recursion).
  '.ak-pipeline{display:flex;flex-direction:row;align-items:stretch;gap:16px;overflow-x:auto;padding:8px 4px;flex:1 1 auto;}' +
  '.ak-pipeline-modules{display:flex;flex-direction:row;align-items:stretch;gap:16px;}' +
  '.ak-pipeline-module-wrap{display:flex;flex-direction:column;gap:4px;width:300px;flex:0 0 auto;}' +
  '.ak-pipeline-root-wrap{display:flex;flex-direction:row;align-items:center;gap:8px;}' +
  '.ak-pipeline-arrow{font-size:16px;color:var(--ak-muted,#8b949e);user-select:none;}' +
  '.ak-module-root{border-color:var(--ak-needs-human,#d29922);box-shadow:0 0 0 1px var(--ak-needs-human,#d29922);}' +
  '.ak-flow-rounds{display:flex;flex-direction:column;gap:4px;margin-top:2px;}' +
  '.ak-round-card{display:flex;align-items:center;gap:6px;padding:4px 8px;font-size:11px;' +
  'background:var(--ak-bg,#0d1117);border:1px solid var(--ak-border,#30363d);border-radius:6px;}' +
  '.ak-round-card-executor{border-left:3px solid var(--ak-running,#58a6ff);}' +
  '.ak-round-card-auditor{border-left:3px solid var(--ak-needs-human,#d29922);}' +
  '.ak-round-card-role{font-weight:600;color:var(--ak-fg,#e6edf3);min-width:34px;}' +
  '.ak-round-card-round{color:var(--ak-muted,#8b949e);white-space:nowrap;}' +
  '.ak-round-card-verdict{color:var(--ak-muted,#8b949e);white-space:nowrap;}' +
  '.ak-round-card-dur{margin-left:auto;color:var(--ak-muted,#8b949e);white-space:nowrap;}' +
  '.ak-flow-empty{font-size:11px;color:var(--ak-muted,#8b949e);}' +
  '.ak-fork{margin-top:4px;padding-left:10px;border-left:2px solid var(--ak-needs-human,#d29922);}' +
  '.ak-fork-label{display:flex;align-items:center;justify-content:space-between;gap:6px;' +
  'font-size:10px;font-weight:600;color:var(--ak-needs-human,#d29922);text-transform:uppercase;letter-spacing:.03em;margin-bottom:4px;}' +
  '.ak-recursion{display:flex;flex-direction:column;gap:8px;}' +
  // Final-block polish: recursion-block visual container + fork connectors.
  '.ak-recursion{margin-left:6px;padding-left:8px;border-left:1px dashed var(--ak-border,#30363d);}' +
  '.ak-pipeline-child{display:flex;flex-direction:column;gap:4px;position:relative;}' +
  '.ak-branch-no{display:inline-block;align-self:flex-start;margin:2px 0 2px 8px;padding:0 6px;' +
  'font-size:9px;font-weight:600;letter-spacing:.03em;color:var(--ak-muted,#8b949e);' +
  'background:var(--ak-bg,#0d1117);border:1px solid var(--ak-border,#30363d);border-radius:8px;line-height:1.6;}' +
  '.ak-fork-toggle{cursor:pointer;margin-left:auto;padding:0 7px;font-size:9px;line-height:1.6;color:var(--ak-muted,#8b949e);' +
  'background:transparent;border:1px solid var(--ak-border,#30363d);border-radius:8px;text-transform:none;}' +
  '.ak-fork-toggle:hover{color:var(--ak-running,#58a6ff);border-color:#1f6feb;}' +
  '.ak-fork-collapsed{font-size:11px;color:var(--ak-muted,#8b949e);font-style:italic;}' +
  // Recursion-block interaction: hover lifts the subtree slightly.
  '.ak-pipeline-child:hover{filter:brightness(1.06);}' +
  // Active whole-subtree ring (a fork descendant is running).
  '.ak-module.ak-chain-active{border-color:var(--ak-running,#58a6ff);}' +
  '.ak-module.ak-chain-active:not(.ak-active){box-shadow:0 0 0 1px rgba(88,166,255,.35);}' +
  // Root module label pill + recursion depth tag.
  '.ak-root-label{display:inline-block;margin-right:4px;padding:0 6px;font-size:9px;font-weight:700;line-height:1.7;' +
  'color:var(--ak-needs-human,#d29922);background:#2d2410;border:1px solid #9e6a03;border-radius:8px;vertical-align:2px;}' +
  '.ak-depth-tag{font-size:9px;color:var(--ak-muted,#8b949e);letter-spacing:.03em;}' +
  // Round-card verdict accent colors + split/other kinds.
  '.ak-round-card-split{border-left:3px solid var(--ak-needs-human,#d29922);}' +
  '.ak-round-card-other{border-left:3px solid var(--ak-muted,#8b949e);}' +
  '.ak-round-verdict-ok{color:var(--ak-done,#3fb950);}' +
  '.ak-round-verdict-revise{color:var(--ak-needs-human,#d29922);}' +
  '.ak-round-verdict-block{color:var(--ak-block,#f85149);}' +
  '.ak-round-verdict-pending{color:var(--ak-running,#58a6ff);}' +
  '.ak-round-verdict-other{color:var(--ak-muted,#8b949e);}' +
  // In-flight round card pulse (CSS-only, no JS polling animation).
  '.ak-round-card.ak-round-active{border-color:#1f6feb;box-shadow:0 0 0 1px var(--ak-running,#58a6ff);}' +
  '.ak-round-card.ak-round-active::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:2px;' +
  'background:var(--ak-running,#58a6ff);animation:ak-flow-pulse 1.2s ease-in-out infinite;pointer-events:none;}' +
  '@keyframes ak-flow-pulse{0%,100%{opacity:.25;}50%{opacity:1;}}' +
  '.ak-round-card{position:relative;}';

/**
 * Inject the panel stylesheet into `doc` once. No-op when the document is
 * unavailable (e.g. the node test harness) or when already injected.
 * @param {Window} [win]
 * @param {Document} [doc]
 * @returns {boolean} true if a `<style>` element was actually injected
 */
function injectStyles(win, doc) {
  var theDoc = doc || (win && win.document);
  if (!theDoc || !theDoc.head || !theDoc.createElement) return false;
  if (theDoc.getElementById('ak-styles')) return true;
  var el = theDoc.createElement('style');
  el.setAttribute('id', 'ak-styles');
  el.setAttribute('data-ak-styles', '1');
  el.type = 'text/css';
  el.appendChild(theDoc.createTextNode(CSS));
  theDoc.head.appendChild(el);
  return true;
}

module.exports = {
  CSS: CSS,
  injectStyles: injectStyles
};
