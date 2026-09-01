# dsh-client-ui-autoknit

AutoKnit 真人交互面板 —— DSH **client-plugin**，把任务列表/归档/详情切换渲染进
DSH web 的右侧 **details** 槽位（可折叠细长条）。

本插件由两半组成：

| 半 | 文件 | 职责 |
|----|------|------|
| node 半 | `lib/index.js` | `apply()` / `inject()` **空函数**，绝不访问 DSH `ctx.layout`（面板注册全部走 browser 半 + `package.json dsh.client` 声明） |
| browser 半 | `lib/client.js` | React + DSH 前端 slots 生态：`window.__ModuleLoader__.load` 注册 details 槽位；从 fw-api 数据桥拉取任务列表并渲染 `run_id / stage / module_states / consumption`；提供归档按钮、未归档任务切换详情、加载/空/错误(+重试)态、i18n 文案，并注入精致样式 |

纯数据读 + 文件读写，**不调用任何 LLM**。

## 数据流（只对接，不重做）

browser 半通过数据桥 HTTP 端点交互，不直接读框架内部文件：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/tasks` | GET | 任务列表（`run_id / stage / module_states / consumption`） |
| `/api/tasks/{run_id}` | GET | 单个任务详情 |
| `/api/tasks/archive` | POST | 归档任务（体：`{ "run_id": "..." }`） |
| `/api/tasks/archived` | GET | 已归档 run_id 列表，用于过滤列表 |

数据桥地址（baseURL）解析优先级：
`options.baseURL` → `window.__DSH__.config.autoknit.baseURL` → 默认 `/api`。
（trailing `/` 自动去掉）

## 视觉与体验（final-block polish）

- **精致样式**：面板样式在 `lib/styles.css`（与 `lib/style.js` 内嵌同源，二者保持同步）；
  browser 半加载时调用 `style.injectStyles(window)` 一次性注入 `<style>`（幂等、DOM 守卫）。
  选择器全部以 `.ak-` 为前缀，避免与 host 样式冲突。
- **状态 UI**：加载态（旋转 spinner + 「加载中…」）、空态（「暂无任务」+ 提示）、
  错误态（红框提示 + **重试**按钮）。
- **详情展示**：多任务详情按结构化分节渲染（meta / module_states / consumption），
  逐行 `key → value` 展示，嵌套对象自动 `JSON.stringify`，空值显示「（无）」——
  不再整块 `JSON.stringify` 原始对象。
- **i18n**：`lib/i18n.js` 维护 zh/en 文案目录（zh 为默认，未知 locale 回退 zh），
  支持 `{placeholder}` 占位符。locale 取 `options.locale` → `window.__DSH__.config.autoknit.locale`。

## 目录结构

```
dsh-client-ui-autoknit/
├── package.json          # DSH 官方格式 dsh.client 声明（platform:web + inject）→ exports["./client"] 指向 dist/client.js
├── lib/
│   ├── index.js          # node 半：空 apply/inject，不含 ctx.layout
│   ├── client.js         # browser 半源码：React + __ModuleLoader__.load 注册 details（build 原料）
│   ├── logic.js          # 纯逻辑：渲染载荷/归档过滤/详情分节/baseURL 解析（Node 可单测）
│   ├── data-bridge.js    # 数据桥 HTTP 客户端（fetch 可注入，baseURL 可配置）
│   ├── i18n.js           # i18n 文案目录（zh/en，占位符，回退）
│   ├── style.js          # 样式注入（内嵌 CSS，幂等 + DOM 守卫）
│   └── styles.css        # 精致面板样式（与 style.js 内嵌同源）
├── dist/
│   └── client.js         # build 产物：自包含单文件 bundle（唯一被 DSH 前端 serve 的文件）
├── build.mjs             # 无依赖 bundle 脚本：把 lib/*.js 内联成 dist/client.js
├── test/                 # node --test 单测 + 自检断言（helpers.mjs 为唯一事实源）
├── verify_bundle.mjs     # 一键自检（ALL PASS 且 exit 0）
└── README.md
```

> 关键：DSH 前端 client 模块只 serve `exports["./client"]` 指向的**一个自包含 bundle**，
> factory 的 `require` 只能解析平台种子词（react 等），**没有相对路径分支**。因此散文件
> 必须先用 `node build.mjs` 内联成 `dist/client.js`（相对 require 全部替换为内联变量），
> 再挂载。`verify_bundle.mjs` 会物化 dist/client.js 断言这一点。

## 挂载到 DSH web profile（正确做法，先构建后挂载）

> 本插件已收编进 framework-v1 的 `fw-panel/plugin/`。要挂到真机 DSH web profile，
> 走**构建 + cordis.patch.yml insert** 两步，**绝不把 client 插件加进
> `dsh.profile.bundles`**（bundle 是给 `dsh.bundle` patch 包的；client 插件进 bundles
> 会在启动时被 cordis 以 node 插件方式装载，node 半一旦碰 `ctx.layout` 就
> `cannot get property "layout" without inject` 崩溃——这是历史崩因）。

1. **构建 browser 半**：`node build.mjs` 产出 `dist/client.js`（自包含，无相对 require）。
2. **插件包进 profile**：把 `dsh-client-ui-autoknit` 目录放到
   `~/.dsh/profiles/web/plugins/` 下，并在 profile 的 `package.json` dependencies 加
   `"dsh-client-ui-autoknit": "file:plugins/dsh-client-ui-autoknit"`。
3. **cordis.patch.yml 加 insert**（顶层数组条目，与 mcp-playwright/delegation 并列）：
   ```yaml
   - insert:
       - id: ui-autoknit
         name: 'dsh-client-ui-autoknit'
   ```
4. **pnpm install + 重启 DSH**。`corepack pnpm install`（pnpm 不在 PATH，用 corepack）。
   改动前先备份 profile 的 package.json / cordis.patch.yml（.bak）。
5. **node 半安全**：`lib/index.js` 是空函数、不碰 `ctx.layout`；browser 半通过
   `window.__ModuleLoader__.load({id, factory})` 把面板注册进 details 槽位。
6. **数据桥地址**：默认 `/api`（与 m01 fw-data-bridge 数据桥同源）；可在
   `window.__DSH__.config.autoknit = { baseURL, locale }` 配置。

> 边界：面板只做数据读 + 文件读写，不调 LLM；视觉验收需真机浏览器（交杰哥真人看）。

## 自测命令

在 `dsh-client-ui-autoknit/` 目录内执行：

```bash
# 构建 browser 半 bundle（生成 dist/client.js）
node build.mjs

# 一键自检（node 结构 + 渲染载荷 + 归档/切换 + i18n + baseURL 配置 + 详情分节 + 样式注入
# + 源码注册 + dist bundle 物化 + package 官方声明 + 数据桥）
node verify_bundle.mjs
# 期望输出末尾： verify_bundle: ALL PASS  且 exit code 0

# 或跑 node --test 单测（test/*.test.mjs）
node --test test/*.test.mjs

# node 半源码 grep 校验（必须无输出）
grep -rn "ctx.layout" lib/index.js || echo "OK: no ctx.layout"

# package.json 官方声明校验
node -e "const p=require('./package.json'); console.log(p.dsh.client.platform, p.exports['./client'])"
```
