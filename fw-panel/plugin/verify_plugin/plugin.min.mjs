// plugin.min.mjs —— 最小可跑的 DSH client-plugin 表示（注入机制验证用）
//
// 复刻 dsh-client-ui-autoknit 的 client-plugin 注入契约：
//   * package.json 里的 dsh.client 声明段（此处以 `client` 导出呈现）
//   * apply(ctx) / inject(ctx, host) 两个生命周期函数
//   * 挂到 layout 插件预留的 details 槽位（右侧可折叠细长条面板）
//   * 面板状态载荷 dsh.panel.state：进度/消耗/待决策 + 暂停态 + 提交/回复入口
//
// 自包含、零外部依赖，可在 node（ESM）下直接编译与运行。
// 面板交互全部走代码/文件读写，不调任何 LLM。

export const client = {
  id: "autoknit-panel",
  name: "dsh-client-ui-autoknit",
  // 挂到 layout 插件的 details 槽位（右侧可折叠细长条）
  slot: "details",
  open: "ctx.layout.openDetails()",
  close: "ctx.layout.closeDetails()",
  version: "0.1.0",
};

// 预定义选项（与 data_contract shared_enums.human_choice 对齐）
const HUMAN_CHOICES = ["A", "B", "C", "D", "text"];

/**
 * apply(ctx)：插件被 cordis 装载时执行，把面板注册进 ctx。
 * 这里把真实插件挂 details 槽位的最小结构做出来：
 *   - ctx.layout.openDetails()/closeDetails() 由宿主注入
 *   - 注册 buildState 供面板渲染进度/消耗/待决策/暂停态
 * 返回注册结果，便于校验"注入机制能跑"。
 */
export function apply(ctx) {
  if (!ctx || typeof ctx !== "object") {
    throw new Error("apply(ctx): ctx 必须是非空对象");
  }
  const layout = ctx.layout || {};
  const open = typeof layout.openDetails === "function" ? layout.openDetails : null;
  const close = typeof layout.closeDetails === "function" ? layout.closeDetails : null;
  ctx.panels = ctx.panels || [];
  ctx.panels.push({
    id: client.id,
    slot: client.slot,
    // 交互全部走代码/文件读写；此处仅登记能力入口
    actions: ["submit", "pause", "resume"],
  });
  return { registered: true, slot: client.slot, open: !!open, close: !!close };
}

/**
 * inject(ctx, host)：把构建好的面板状态注入宿主（示例：塞进 host.panelState）。
 * 返回注入后的状态对象。
 */
export function inject(ctx, host, state) {
  if (!host || typeof host !== "object") {
    throw new Error("inject(ctx, host): host 必须是非空对象");
  }
  host.panelState = state;
  return state;
}

/**
 * buildPanelState(input)：纯函数，把 快照/事件/待决策/暂停态 拼成 dsh.panel.state 载荷。
 * 与 Python 侧 autoknit_panel 保持同一 data_shape 语义。
 */
export function buildPanelState(input) {
  const src = input || {};
  const pending = Array.isArray(src.pending) ? src.pending : [];
  const blocked = pending.length > 0 && src.humanAnswered !== true;
  const paused = !!src.paused;
  return {
    stage: src.stage || "idle",
    roles: Array.isArray(src.roles) ? src.roles : [],
    consumption: src.consumption || {
      token_input: 0, token_output: 0, cache_hit: "-", duration_sec: 0,
    },
    pending: {
      blocked,
      count: pending.length,
      items: pending,
    },
    human_choices: [...HUMAN_CHOICES],
    control: {
      paused,
      state: paused ? "paused" : "resumed",
      may_start: !paused,
    },
  };
}

export const __api = { HUMAN_CHOICES, buildPanelState, apply, inject, client };
