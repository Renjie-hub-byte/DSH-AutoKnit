# AutoKnit 架构（面向贡献者）

## 总览

```
                PRD
                 │
        ┌────────▼────────┐
        │     planner      │  按耦合拆分：高内聚的聚成一个模块
        └────────┬────────┘   + 首任务详细清单 + 模块间契约
                 ▼
        ┌────────▼────────┐
        │     executor     │  独立会话，只为本模块负责
        └────────┬────────┘
                 ▼
        ┌────────▼────────┐
        │     auditor      │  逐条核对验收清单 + 程序采证
        └────────┬────────┘
                 │ 剩余模块还大？
                 ▼ 大（阈值可调，建议 ≈1000 行）→ split 递归拆分
                   小 → executor 续做收官
                 ▼
            全部完成 ✅
```

**核心原则：程序负责调度（0 token），LLM 只出智力。** 拆分、派工、验收核对、递归决策全部是程序逻辑；LLM 只写代码、只判验收清单。

## 目录结构

```
framework-v1/
├── fw-tools/                  # CLI 入口（autoknit ≡ fw-*）
│   ├── autoknit               # 统一 CLI 分发
│   ├── fw-new.sh              # PRD → 任务（planner + normalize + scaffold）
│   ├── fw-run.sh              # 任务 → 执行（caffeinate 包裹 + runner）
│   ├── fw-status.py           # 事件驱动状态跟随
│   ├── fw-token.py            # token 账（--cwd 限定 + 多根扫描）
│   ├── fw-doctor.py           # 环境体检
│   └── fw-dashboard.py        # 数据桥面板
├── fw-runner/                 # 编排主循环（纯程序）
│   ├── fw_runner/runner.py    # 主循环：dispatch → executor → auditor → split
│   ├── fw_runner/registry.py  # run 注册表（dashboard 数据桥）
│   └── bin/
│       ├── fw-executor.sh     # executor 任务书模板（EXEC_TASK.md）
│       ├── fw-auditor.sh      # auditor 任务书模板（AUDIT_TASK.md + 程序采证）
│       ├── fw-spawn.py        # 会话 spawn（headless 调用）
│       └── fw-trace.py        # 路径级越界检测
├── fw-protocol/               # 任务书 schema 校验
├── fw-scaffold/               # 任务目录树生成
├── fw-data-bridge/            # fw-api serve（:8765 dashboard 数据源）
├── fw-merge/                  # 程序化合代码（零 LLM）
└── fw-planonly/               # plan-only 模式
```

## 四角色机制

### planner（LLM，一次）

- 读完整 PRD → 按**代码逻辑**分块（数据层/服务层/界面层、依赖方向），不按 PRD 目录分。
- **合并优先、宁大勿小**：预估 <600 行的小域并入最相关块。
- 产出：模块 id / 名称 / 依赖 / 预估行数 / first_block 范围 / 验收清单 / 契约（接口 + 数据 shape）。
- fw-normalize 程序接管结构：补全 id/layer/meta/默认值、字段归位、全角规范化。

### executor（LLM，每模块独立会话）

- 任务书信息分层：【总·任务全貌】【前·上游】【后·下游】【契约·本模块接口】【本·本轮唯一任务】。
- 读文件边界是硬墙：只允许读本模块目录内文件，契约/环境由程序注入。
- 跑代码/测试统一用 `.venv/bin/python`；分步推进逐条验收；空壳不算完成。
- REVIEW.md 先写「已做」小节再写单行，供续做定位。

### auditor（LLM，每模块独立会话）

- 对照验收清单逐条核验 src/ 产物，只判代码层。
- **程序预采证**（EVIDENCE_LAYER 注入）：pytest 实跑、semgrep、越界检查（realpath walk）——LLM 不需要重跑。
- 铁律：以 EXEC_TRACE.md 为唯一事实依据；已有的一律不重复 read/重跑。
- 证据等级：L1=测试实跑 / L2=文件取证 / L3=仅静态推断（无实证的 pass 不成立）。
- 判定写 `tmp/audit-result.json`：verdict（pass|partial|block）+ root + confidence + evidence_level + human_pending。

### split（程序）

- 剩余体量 > `split_exit_threshold`（默认 1000）→ 递归拆分新块。
- ≤ 阈值 → 当前 executor 续做收官（final block）。

## 契约体系

模块间不共享代码，只共享形状：

```yaml
# contracts/m01-task-state.yaml
interface:
  dsh.task.list:
    direction: F→R
    returns:
      tasks: "List[TaskSummary]"
data:
  snapshot.json:
    run_id: str
boundary:
  may_read: [contracts/, shared/]
  may_write: [modules/m01/]
```

executor 的全部世界 = 这份契约 + 自己的模块目录。它不需要、也不被允许了解全局。

## 事件流（dispatch.jsonl）

append-only 事件流，全链路留痕：

```
scaffold → run.start → module.dispatch → executor.round.start
→ executor.round.done → auditor.round.start → auditor.round(verdict)
→ module.final_block(remaining ≤ threshold) 或 module.blocked / module.partial
→ module.done → integration.check
```

- `module.final_block` 事件是"收官轮"铁证：`remaining ≤ threshold` + 前一 `auditor.round` 是 pass。
- `module.blocked` 表示 auditor 判定 block → executor 重跑（会有 `block_count` 递增）。

## 判定与重试语义

| 判定 | 含义 | runner 动作 |
|---|---|---|
| pass | 验收通过 | 剩余 ≤ 阈值 → 收官；否则 → split |
| partial | 部分通过 | 打回带反馈（`partial_count` 递增） |
| block | 严重失败 | 换 executor 重跑（`block_count` 递增） |
| parse_failed | auditor 输出格式没解析出来 | **轻量重试**：写 `.auditor_parse_retry` 标记重跑 auditor（不重跑 executor） |
| timeout / 空输出 | auditor 超时或空回复 | block（重跑 executor） |

> parse_failed 与 timeout/empty 的区分（2026-08-31 修复）：**非空但解析失败 → parse_failed 轻量重试；空/超时 → block**。此前统一误判为 block 导致整模块重跑烧 18 万 token。

## 会话与记账

- 独立 DSH_HOME：默认 `~/.fw-dsh`（`FW_DSH_HOME` 覆盖），不碰主 `~/.dsh`。
- 会话根多根扫描：`~/.fw-dsh` + `~/.fw-dsh-bench`（bench 隔离环境遗留），run 级 token 按任务目录（`--cwd`）+ 时间窗精确聚合。
- 计费口径：**未缓存输入 + 输出**（缓存读单独上报，不计费）——三方对比统一。
- 缓存友好：前缀冻结纪律（【本】部分在末尾、review_summary 不进前缀）+ 模块独立会话 → 命中率 94-98%。

## 数据桥（fw-api :8765）

- LaunchAgent `com.autoknit.fwapi-bridge` 保活；`autoknit run` 自动拉起，已有则复用。
- 接口：`/api/runs`（注册表）、`/api/runs/{id}/usage`（token 明细）、`/api/runs/{id}/tree`（模块进度链）。
- run 自动注册（runner.py 启动即登记），dashboard 数据桥可见。

## 已知边界

- 强全局状态 = 高耦合 → 按耦合拆分时本就该聚成一个模块（超阈值递归拆）。
- 需求本身没想清楚 → 契约驱动的前提是你能说清要什么（先 plan-only 审）。
- 精细 UI 打磨 → 分治产出"能用且健壮"，像素级调优请用交互式工具。
