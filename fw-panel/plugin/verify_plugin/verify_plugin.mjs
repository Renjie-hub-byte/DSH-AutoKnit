#!/usr/bin/env node
// verify_plugin.mjs —— client-plugin 注入机制「编译 + 运行」自检脚本
//
// 校验三件事：
//   1) bundle.json（模拟 web profile bundle 列表）登记了 autoknit 插件与 details 槽位
//   2) client-plugin ESM 模块能编译（动态 import 成功 = ESM 语法/加载通过）
//   3) 注入机制能跑：apply(ctx) 挂 details 槽位 + inject 注入宿主 + buildPanelState 拼面板载荷
//
// 任一步失败 → 打印 FAIL 并以非零码退出（可作为 CI 门禁）。
// 用法：node verify_plugin/verify_plugin.mjs

import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
let failures = 0;

function check(name, ok, detail = "") {
  const mark = ok ? "PASS" : "FAIL";
  console.log(`[${mark}] ${name}${detail ? " :: " + detail : ""}`);
  if (!ok) failures += 1;
}

// ---- 1) bundle 列表登记校验 ----
const bundlePath = join(HERE, "bundle.json");
check("bundle.json 存在", existsSync(bundlePath));
let bundle = null;
try {
  bundle = JSON.parse(readFileSync(bundlePath, "utf8"));
  check("bundle.json 为合法 JSON", true);
} catch (err) {
  check("bundle.json 为合法 JSON", false, String(err));
}
if (bundle) {
  const hasPlugin = (bundle.bundles || []).some(
    (b) => b.id === "autoknit-panel" && b.slot === "details" && b.enabled === true,
  );
  check("bundle 列表登记 autoknit-panel + details 槽位", hasPlugin);
}

// ---- 2) 编译校验：动态 import 成功即 ESM 可编译/可加载 ----
let mod = null;
try {
  mod = await import(join(HERE, "plugin.min.mjs"));
  check("client-plugin ESM 模块可编译/可加载", true);
} catch (err) {
  check("client-plugin ESM 模块可编译/可加载", false, String(err));
}

if (mod) {
  const { client, apply, inject, buildPanelState } = mod;

  // client 声明段：details 槽位
  check(
    "client 声明挂 details 槽位",
    client.slot === "details" && !!client.open && !!client.close,
  );

  // ---- 3) 运行校验：注入机制能跑 ----
  // 3a) apply 挂 details 槽位（mock 宿主提供 openDetails/closeDetails）
  const ctx = {
    layout: {
      openDetails: () => true,
      closeDetails: () => true,
    },
  };
  let applied = null;
  try {
    applied = apply(ctx);
  } catch (err) {
    applied = { error: String(err) };
  }
  check(
    "apply(ctx) 注册 details 槽位面板",
    !!applied && applied.registered === true && applied.slot === "details",
    JSON.stringify(applied || {}),
  );
  check("apply 可调用 openDetails/closeDetails", !!applied && applied.open && applied.close);

  // 3b) inject 注入宿主
  const host = {};
  const state = { stage: "exec", roles: ["executor", "auditor"] };
  let injected = null;
  try {
    injected = inject(ctx, host, state);
  } catch (err) {
    injected = { error: String(err) };
  }
  check(
    "inject(ctx, host) 注入 panelState",
    !!injected && host.panelState === state,
  );

  // 3c) buildPanelState 拼面板载荷（含待决策 + 暂停态 + 选项 A/B/C/D/text）
  const sample = {
    stage: "split",
    roles: ["executor", "auditor"],
    consumption: { token_input: 120, token_output: 340, cache_hit: "5%", duration_sec: 42 },
    pending: [{ kind: "split_ambiguity", options: ["A", "B", "C", "D"] }],
    humanAnswered: false,
    paused: true,
  };
  const payload = buildPanelState(sample);
  check("buildPanelState 产出 dsh.panel.state 载荷", !!payload && !!payload.pending);
  check(
    "待决策 blocked + 选项 A/B/C/D",
    payload.pending.blocked === true &&
      Array.isArray(payload.human_choices) &&
      JSON.stringify(payload.human_choices) === JSON.stringify(["A", "B", "C", "D", "text"]),
  );
  check(
    "暂停态写入 control.may_start=false",
    payload.control.paused === true && payload.control.may_start === false,
  );
  check(
    "消耗字段（输入/输出/缓存/耗时）",
    payload.consumption.token_input === 120 &&
      payload.consumption.token_output === 340 &&
      payload.consumption.duration_sec === 42,
  );

  // 3d) 未回复时阻塞、回复后解除（阻塞即停语义）
  const answered = buildPanelState({ ...sample, humanAnswered: true });
  check(
    "未回复 blocked=true / 已回复 blocked=false",
    payload.pending.blocked === true && answered.pending.blocked === false,
  );
}

// ---- 汇总 ----
console.log(`\nverify_plugin: ${failures === 0 ? "ALL PASS" : failures + " FAILURE(S)"}`);
process.exit(failures === 0 ? 0 : 1);
