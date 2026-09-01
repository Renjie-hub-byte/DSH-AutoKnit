# cordis-patch 挂载说明 —— 把 dsh-client-ui-autoknit 挂到 web profile

> 本文档说明如何把 AutoKnit client 插件挂到 DSH web profile。这是**踩坑修正后的正确
> 做法**（2026-08-27 前后，插件进 `dsh.profile.bundles` 导致重启即崩）。

---

## 0. 为什么"进 bundles 就崩"（历史崩因）

client 插件（`package.json` 带 `dsh.client` 声明，node 半负责服务端侧注册）**不是
`dsh.bundle` patch 包**。把它加进 `package.json` 的 `dsh.profile.bundles` 后：

1. cordis Loader 以 node 插件方式装载它 → 执行 node 半 `apply(ctx)`；
2. 旧版 node 半 `apply()` 里读 `ctx.layout` → 未 `inject` 就 get →
   `cannot get property "layout" without inject` → **DSH 启动即崩**。

即使 node 半是空函数，client 插件的 browser 半也不会通过 bundles 被 serve——
browser 半只走 `exports["./client"]` + dsh-client-modules 的 `/plugins/<id>/client.js`
路由。所以 client 插件必须走 **cordis.patch.yml 的 insert**，并满足：

- `dsh.client` 声明用官方格式：`platform`（必填，如 `"web"`）+ `inject`（数组）；
  m02 旧式 `{id/kind/slots/browser}` 字段 DSH 不认，激活扫描缺 `platform` 会 loud throw。
- `exports["./client"]` 必须指向**构建后的自包含 bundle**（factory 的 `require`
  只能解析平台种子词，没有相对路径分支）。

## 1. 三步正确挂法

### 步骤 1：构建 browser 半

```bash
cd framework-v1/fw-panel/plugin/dsh-client-ui-autoknit
node build.mjs          # 生成 dist/client.js（自包含单文件，无相对 require）
node verify_bundle.mjs  # 期望 ALL PASS（含 dist 物化检查）
```

### 步骤 2：插件包进 profile + file: 依赖

```bash
mkdir -p ~/.dsh/profiles/web/plugins
cp -R <插件包目录> ~/.dsh/profiles/web/plugins/dsh-client-ui-autoknit
```
profile 的 `~/.dsh/profiles/web/package.json` dependencies 加（**不要加进
dsh.profile.bundles**）：
```json
"dsh-client-ui-autoknit": "file:plugins/dsh-client-ui-autoknit"
```

### 步骤 3：cordis.patch.yml 加 insert

`~/.dsh/profiles/web/cordis.patch.yml` 顶层数组（与 mcp-playwright / delegation /
tdai-memory 条目并列）追加：
```yaml
- insert:
    - id: ui-autoknit
      name: 'dsh-client-ui-autoknit'
```

然后 `corepack pnpm install`（pnpm 不在 PATH，用 corepack）+ 重启 DSH。
**改前先备份** profile 的 package.json / cordis.patch.yml（.bak）。

## 2. 挂载自检清单

- [ ] `node build.mjs` 后 `dist/client.js` 存在，且无 `require('./` 相对引用
- [ ] `node verify_bundle.mjs` ALL PASS（node 半空函数 / package 官方声明 / dist 物化）
- [ ] profile package.json 的 `dsh.client` 用官方格式（platform + inject + exports）
- [ ] 插件在 `dsh.profile.bundles` **之外**，只在 cordis.patch.yml insert
- [ ] profile package.json dependencies 有 `file:plugins/dsh-client-ui-autoknit`
- [ ] `corepack pnpm install` 成功；重启后 DSH 正常启动，右侧 details 槽位出现面板
      （视觉交杰哥真人验收）

## 3. 边界

- 面板只做数据读 + 文件读写，不调 LLM。
- 视觉为代码级 + 单测 + 结构校验；真机浏览器验收需重启 DSH（影响当前会话，选时机）。
