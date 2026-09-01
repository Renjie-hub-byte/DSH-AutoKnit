# AutoKnit FAQ

## 名词表

| 名词 | 含义 |
|---|---|
| 契约 | 模块间共享的接口形状（方法签名）+ 数据形状（字段/类型），不共享代码 |
| needs_human | 任务等待人工回复的状态（任务目录存在 `needs_human/` 子目录） |
| 打回（block） | auditor 判定严重失败，换 executor 重跑（`block_count` 递增） |
| 续做（final block） | 剩余 ≤ 阈值，当前 executor 把剩余做完（2 轮的正常收官路径） |
| 热续做 | executor 刚做完前一块，上下文还热着就续做，省 token 且质量连贯 |
| split | 程序递归拆分：剩余超阈值就拆新块 |
| 采证 | auditor 的证据采集（pytest/semgrep/越界由程序跑好注入，LLM 只引用） |
| evidence_level | 证据等级：L1=测试实跑 / L2=文件取证 / L3=仅静态推断 |

## 常见问题

### "我的任务每模块都跑了 2 轮，是不是被打回重做了？"

**不是。** 2 轮 = 首发块验收通过（auditor pass）→ 剩余 ≤ 阈值（默认 1000 行）→ 收官轮（final block）续做。看 `总日志/dispatch.jsonl`：

```
auditor.round  verdict=pass          ← 第一轮就通过
module.final_block  remaining ≤ 1000  ← 收官触发
executor.round2 → auditor pass → done
```

确认方法：`grep module.final_block 总日志/dispatch.jsonl`，看到 `remaining ≤ threshold` 就是正常收官；`block_count` 大于 0 才是打回。

### "dashboard 显示我的 run 消耗了 3000 万 token，是真的吗？"

**几乎肯定是聚合 bug（已修复，BUG-009）。** m01/m02/m03 是通用模块 id，历史每个 run 都有。旧版按模块 id 聚合时把跨 run 会话串进来了。修复后按 `--cwd`（run 任务目录）+ 时间窗精确聚合，并同时扫描 `~/.fw-dsh` 与 `~/.fw-dsh-bench` 两个历史会话根。验证：`autoknit token --cwd <任务目录> <模块id>` 与快照 `budget_used_tokens` 对账。

### "计费 token 和总输入为什么不一样？"

计费 = **未缓存输入 + 输出**（缓存读不重复计费）；总输入 = 未缓存 + 缓存读（平台仪表盘"总消耗"口径）。缓存命中率 94-98%，所以计费远小于总输入。

### "planner 拆得太碎 / 太粗怎么办？"

- 先 `autoknit plan-only` 审规划，不满意改 PRD 再 plan。
- 拆分粒度是最大成本杠杆（同任务三种粒度差 4.6×）。框架的默认倾向是**合并优先、宁大勿小**：预估 <600 行的小域并入最相关块；超阈值（约 1000 行）才递归拆。
- `split_exit_threshold` 可调（dflow.yaml 或 CLI flag）。

### "executor 为什么不允许读其他模块的代码？"

这是**防抄 + 低耦合**设计：模块间只通过契约说话，不读对方源码。executor 的全部世界 = 契约 + 自己的模块目录。读别人的实现会引入无关假设、浪费 token，还破坏独立性。

### "auditor 压力大吗？它怎么证明自己审对了？"

**不大——程序已把证据跑好了。** 改造后（2026-08-31）：pytest / semgrep / 越界检查由程序预采证注入【采证】层，auditor 只对照验收清单逐条核对引用，不重跑、不重复 read。铁律：以 EXEC_TRACE.md 为唯一事实依据，交付说明仅供参考。无实证的 pass 不成立（evidence_level=L3 时 confidence 打折）。

### "执行中电脑合盖睡眠了怎么办？"

不会冻死——`fw-run` 自动 `caffeinate -i` 包裹（macOS）。万一中断了，`autoknit run --resume` 从 checkpoint 干净续跑（不重复规划、不重跑已完成模块）。

### "某个模块老是打回怎么办？"

打回是 auditor 对照验收清单的正当反馈：
1. 看 `总日志/dispatch.jsonl` 里该模块的 `module.blocked` 事件和 auditor 的 reason。
2. 看 `modules/<模块>/tmp/auditor_output_*.txt` 的完整判定。
3. 多数是验收清单没写清（改 PRD 的验收标准）或模块边界模糊（改 planner 拆分）。

### "为什么我看到的 token 和 README 里不一样？"

README 是**冷启动基准**（同 PRD、同模型、同机器），n=1 单轮样本。你的任务规模/模型/并行度不同结果会不同。方向（大任务更省更快更厚）可引用，精确倍数勿外推。

### "AutoKnit 适合什么、不适合什么？"

| 适合 | 不适合 |
|---|---|
| 需求明确、可拆模块的中型代码生成（1,000–10,000 行） | 几百行脚本（杀鸡用牛刀） |
| 想省 token、要自动验收、要测试密度 | 需求频繁变更、要持续对话调整 |
| 能说清"要什么 + 验收标准" | 精细 UI 像素级调优 |
| 想并行加速（无依赖模块同时跑） | 低代码拖拽、非技术场景 |
