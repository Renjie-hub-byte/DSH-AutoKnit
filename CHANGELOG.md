# framework-v1 CHANGELOG

分治执行框架的代码变更日志。每次改动记录：文件、改了什么、为什么、踩了什么坑。
详细踩坑故事见 `fw-tools/运行手册-首跑记录.md`；本文件是**代码层面**的变更台账。

---

## 2026-08-21 —— 首战（ai扩展资源库v2）修复台账

### 1. fw-runner/fw_runner/model.py —— DriverOutcome.substance 丢失
- **改动**：`from_mapping()` 补 `if "substance" in d: o.substance = bool(d["substance"])`
- **为什么**：executor outcome 写了 `"substance": true`，但解析时不读该字段（保持 None），
  心跳守护退回指纹判定 → 产物覆盖同名文件无变化 → 误判 stall → 模块回人
- **验证**：单元测试 + 71 项回归全绿

### 2. fw-runner/bin/fw-executor.sh + fw-auditor.sh —— 工作区必须绝对路径
- **改动**：任务指令由"当前目录"改为 `$MODULE_DIR` 绝对路径（heredoc 内变量展开）
- **为什么**：headless 会话 **不继承 shell 的 cwd**，说"当前目录"它找不到 → 卡死 0 输出
- **历史**：`<<'TASKEOF'`（不展开）→ `<<TASKEOF`（展开）才能注入绝对路径

### 3. fw-runner/bin/fw-spawn.py（新增）—— agent 子进程生命周期管理
- **改动**：新增统一 spawner：Popen **列表参数不走 shell**（防多行长文本/路径空格炸）、
  `start_new_session=True` 独立进程组、`killpg` 收 SIGTERM/SIGINT 清理进程组、macOS
  killpg PermissionError 忽略
- **为什么**：之前 `kill $JOB` 只杀 bash 包装器，**dsh headless Node 子进程没被杀 → 孤儿堆积**
  （实测 70 个）吃光资源 → 新会话起不动 → 全模块超时
- **配套**：executor/auditor 的执行段全部改用 fw-spawn（`-- <dsh> --profile headless <task> --out ...`）

### 4. fw-runner/bin/fw-auditor.sh（多次）—— auditor 输出解析修复
- **4a 输出文件轮次隔离**：`tmp/auditor_output.txt` → `tmp/auditor_output_${ROUND}.txt`
  （旧孤儿 spawn 与新轮次并发写同一文件 → 内容混合 → VERDICT 找不到）
- **4b 【重大】heredoc 单引号吞变量**：解析段 python heredoc 里写
  `open("tmp/auditor_output_${ROUND}.txt")`，但 `<<'PYEOF'` 不展开 → python 读到字面
  `${ROUND}` 文件（不存在）→ txt 为空 → 判"超时 block"。**改为通过 argv 传入文件名**
  （`python3 - "$OUTCOME" "tmp/auditor_output_${ROUND}.txt"`）
- **4c 【重大】智能兜底解析**：无 VERDICT= 行但输出 >200 字节（headless 真跑了只是没按格式）
  → 不再直接判超时 block，而是扫描 pass/block 关键词 + 结尾摘要（带给 executor 看）
- **4d 审计任务瘦身**：任务书只提取 objective+acceptance（pyyaml），不再灌整个 yaml；
  交付说明 grep -v 掉"未落档"占位垃圾（污染任务 → headless 读乱套 → 超时）
- **4e 浏览器禁令**：指令加"禁止 playwright/chromium/截图"，防 headless 起 Chromium
  弹钥匙串授权 → 卡死（实测 m03/m04 反复超时的环境因素之一）
- **4f 超时调整**：默认 180s → 300s（真实审计需 3-5 分钟）

### 5. fw-tools/fw-run.sh —— 相对路径 bug
- **改动**：TASK_DIR 校验后 `TASK_DIR="$(cd "$TASK_DIR" && pwd)"` 转绝对路径
- **为什么**：脚本内部 `cd "$FW1/fw-runner"` 后，相对路径任务根解析错 → task.yaml 找不到

### 6. 任务运行产物（非代码，状态记录）
- 4 模块全 done；m03/m04 靠"人工复核 pass + 快照标记 done"收官
  （审计结果真实存在，仅因解析 bug 被吞，非 agent 能力问题）
- 教训：**agent 框架的问题常常不是 agent 不行，是胶水层（shell/python 解析）吞掉了真实结果**

### 7. fw-tools/fw-run.sh —— 退出自动清理孤儿
- **改动**：`trap cleanup EXIT`，退出时杀残留 fw-spawn/headless 孤儿
- **为什么**：今晚实测最多堆积 70 个孤儿进程吃光资源；不想每次手动 pkill

### 8. function calling 探索（结论已经验证）
- **已验证**：headless agent 工具系统完整（bash/read/write/edit/web_search/skill 等），
  function calling 基建就绪
- **方案 A（轻量，已用）**：智能兜底解析 = 输出长但无 VERDICT 行时扫 pass/block 词+摘要
- **方案 B（正解，待做）**：--patch 挂 `submit_audit_verdict` cordis 工具，模型必须调工具交判定
  （参数强类型，解析零脆弱）——需要写插件，30-60 分钟，有需要再做

---

## 待办/下一步

- [ ] **function calling 结构化判定（方案 B）**：给 headless 挂 `submit_audit_verdict` 工具，
      让 auditor 用工具调用交判定（参数强类型，解析零脆弱）——`--patch` 挂插件路径已验证可行
- [x] fw-run 结束时自动清理 spawn/headless 孤儿（trap EXIT）✅ 已完成
- [ ] token 统计接回（headless 子会话 token 用量，当前恒 0）
### 9. 结构化输出实验（验证通过，待落地）
- **实验**：让 auditor 把判定写 JSON 文件（{verdict,root,confidence,reason}），headless 只回复 "JSON_OK"
- **结果**：245 字节 JSON，json.loads 直接解析成功（pass/self/0.93 全对），零正则
- **结论**：模型"填表交卷"远稳于"写文本+正则解析"——根治 m03/m04 格式飘问题的方向
- **落地**（待做）：auditor/executor 改为写 tmp/result.json + schema 说明；scripts 直接 loads

### 10. reasoningEffort 实验（结论：当前 flash 模型不支持手动降）
- **实验**：settings 的 agent-default-model 加 `reasoningEffort: low`
- **结果**：报错 UNSUPPORTED_REASONING_EFFORT（volcengine deepseek-v4-flash 无 reasoning 元数据）
- **真相**：effort 支持由 adapter 硬编码；flash 本就是低思考模型，无需降
- **发现**：dsh 完整支持 reasoningEffort（settings.yaml agent-default-model 可配），
  但仅对声明了 reasoning 元数据的模型生效（思考型模型如 deepseek-v4-pro / qwen 某些）
- **结论**：executor/auditor 用 flash 已是最低成本档；若日后上 pro 且想省 token，
  可在 agent-default-model 配 reasoningEffort（视 adapter 支持而定）
- **已恢复**：settings 移除无效 effort，headless 恢复正常

### 11. 结构化输出落地（auditor 已改，executor 待跟）
- **auditor 改造**：判定改为写 tmp/audit-result.json（{verdict,root,confidence,reason}），
  脚本 json.loads 直接解析（零正则），headless 只回 "JSON_OK"
- **验证**：m01 手动审计 pass conf=0.97（JSON 闭环）
- **executor**：暂用原方式（交付说明.md + REVIEW），后续可同样改 JSON 交付

### 12. fw-new.sh —— PRD → 任务目录一键生成（新增）
- **功能**：headless planner 读 PRD → 产出标准 task.yaml → fw-protocol 校验 → fw-scaffold 生成任务目录
- **用法**：`fw-new <PRD.md> [--name 任务名] [--owner 负责人]` → 输出任务目录 → `fw-run <目录>`
- **意义**：补上"给个 PRD 就干"的最后一块（对标 lh 的 @任务.md）

### 13. 角色级模型配置（framework 低思考落地）
- **能力**：fw-run 支持 `--executor-model <模型>` / `--auditor-model <模型>`
  → 对应角色用 dsh --patch 覆盖 agent-default-model 到指定模型（如 doubao-seed-2.0-mini 低成本档）
- **作用域隔离**：只作用于 fw-run 的 executor/auditor，GUI 其他 agent 完全不受影响
- **原理**：headless 执行时生成 tmp/model-patch.yml，`--patch` 注入，不碰全局 settings
- **验证**：executor 识别模型参数、patch 生成正确、端到端跑通
- **背景**：dsh 对 volcengine 配 effort 报 UNSUPPORTED（adapter 不声明），
  因此用"换模型"（doubao-mini 天然低思考）替代"降 effort"，同达省 token 目的

### 14. dsh 升级 + tencentdb 记忆通道修复（重要）
- **dsh 升级**：0.1.0-rc.6 → 0.1.1-rc.2（npm 全局，settings 已备份）
- **重大修复**：rc.8 "修复自定义 OpenAI 兼容网关请求格式 + 推理内容回传缺失"
  → **记忆通道×工具调用不兼容（断点B）验证修复**！
- 验证：rc.2 headless 走 tencentdb-memory provider，bash/多步工具链正常
- 遗留：proxy storage.enabled=False/tdai.enabled=False → 记忆"只注入不存档"（配置层，与 dsh 无关）

### 15. bash 3.2 空数组 unbound bug（验证跑抓到）
- **问题**：mac 默认 bash 3.2.57，`set -u` 下空数组 `${arr[@]}` 展开报 `unbound variable`
  → executor/auditor 秒败（agent 非零退出 124）
- **修复**：模型 patch 参数从数组改为字符串（`DSH_PATCH_ARG` 空字符串不带引号展开）
- **教训**：`set -u` + `${arr[@]}` 在 bash 3.2 有坑，避免用数组传可选参数；用普通字符串
- **来源**：fw-workflow skill 首次"无干预完整验证跑"抓到的（证明验证跑的价值）

### 16. auditor "超时"真相 = 报告写太重，判定没优先（工程设计优化）
- **现象**：便签本验证跑 m01 首轮 auditor "超时无判定"，但输出 2664 字节极专业
  （读了产物、跑了导入测试、逐条核验 4 条验收、给根因+下一轮 blocker）
- **真相**：auditor 不慢，是**把精力全花在写详细报告**，300s 内没走到"写 json"那步就被 kill
- **优化**：① audit 指令"json 判定最高优先级，先写它，报告可选" ② 超时 300→420s
- **教训**：给 agent 的产出物要有**优先级**——机器依赖的（判定 json）先做，锦上添花（报告）可后补
- **另外**：executor 首轮超时(124) → 只交付 __init__.py 空壳，auditor 正确打回——重试机制真实验证

### 17. 真实任务程序自主全 done + token 工具（决定性里程碑）
- **ai 拓展资源库完整重跑（08-22）**：m01/m02/m03/m04 全 done，回人 0
  - m03：5 轮迭代（打回4次→换E2→37/37通过）——重试+换 executor 机制自主工作
  - m04：auditor 明确"纯代码/命令验收，未用浏览器"（边界生效）
- **关键修复**：auditor 判定双保险——有 json 用 json，没有则文本智能解析
  （auditor 常把 pass/block 写进报告不写 json，导致"超时无判定"假失败）
- **fw-token.py 新增**：从会话文件读 provider usage，统计输入/输出/缓存命中
  （framework 快照 budget_used_tokens 恒 0，本工具补上真实消耗）
- **本轮真实消耗**：~93 万计费 tokens（367 次调用，缓存命中 96.2%）
  —— 4 模块全 done 的成本，缓存复用是省 token 核心

### 18. 假打回复盘：6/6 打回全是假的（100%），双保险已根治
- **统计**：ai 拓展重跑 dispatch 分析——m02/m03/m04 共 6 次 block，**100% 是"假 block"**
  （blocker = "auditor 未写 audit-result.json"，即 auditor 判 pass 但 json 没落盘 → 误判超时）
- **结论**：没有一次是 executor 真没做对——全是审计链路 bug（json 交付约定脆弱）
- **修复**：auditor 判定双保险（json 优先 + 文本智能解析兜底）——已验证稳定
  （无论模型写不写 json，判定都正确；假打回场景 + 稳定性测试均判 pass）
- **defineTool 评估**：dsh 官方"强制 JSON"正道，但 headless 子进程无 harness.defineTool
  （只在当前会话 Host 有）→ 需 MCP 才能注入自定义工具，工程重
- **决策**：双保险是"现实最优解"（不依赖模型自觉 + 无需动架构）；defineTool 为长期升级项

### 19. 前端表格错位根因：td 挂 display 类脱离表格网格（测试工程师 agent 定位）
- **现象**：项目简介/公司简介两列内容重叠，后面整列错位、操作列留空
- **试错**（全不对症）：加宽表格/固定列宽/-webkit-line-clamp 单行多行……越修越乱
- **真根因**：`.cell-intro`(display:block) 和 `.row-actions`(display:flex) **直接挂在 <td> 上**，
  把表格单元格从 table-cell 改成块级/flex，脱离表格网格 → 简介 td 像 div 纵向堆叠，
  公司简介叠进项目简介下方 → 后面全错位
- **修复**：类移到 td 内部的新 <div> 上，td 保持纯 table-cell（m04 src/web 3 文件）
- **教训**：① <td> 只能做表格单元格，样式套内层元素，别改 td 的 display
  ② 前端渲染类 bug，纯代码验收（跑 API/数据）覆盖不到，需真实浏览器验证
  ③ executor 交付的功能测试通过 ≠ 渲染正确——auditor 应补浏览器渲染验收

## 下一任务规划（2026-08-22）

### 20. ai_cockpit × DSH 集成（待拆解）
- 规划草案：`~/projects-hold/projects/ai-cockpit/tasks/dsh-ai-cockpit-任务书-草案.md`
- 需求：任务流/通知合并监控 + 请示人工可回复 + 消耗面板 + 对话详情 + agent固定工作区
- m6 具体项：ai 拓展资源库 v2 工具入口（点开打开页面）
- 技术约束：relay-v2 按 openclaw 打造，接 DSH 需桥接方案
- 状态：待 fw-new 拆解成可执行任务书

### 21. heredoc 内 $VAR（ 全角黏连 → executor 全 124 超时（严重）
- **现象**：dsh_cockpit 任务 m01/m02 全 "agent 非零退出(124)"，executor_output 空
- **根因**：fw-executor.sh line 66（heredoc）`$MODULE_DIR（以此路径为准` 和 line 109
  `$FW_EXECUTOR_MODEL（` —— heredoc 展开时 bash set -u 把 `$MODULE_DIR（` 当变量名 → unbound
- **auditor 同样**：`$MODULE_DIR（` + `$FW_AUDITOR_MODEL（`
- **修复**：`$VAR（` → `${VAR}（`（4 处），全脚本扫描清除
- **教训**：heredoc 里变量后跟全角标点极易踩（bash 3.2 set -u bug），
  任何脚本改完都要跑 `re.findall(r'\$[A-Za-z_][A-Za-z0-9_]*[（）：。，、）]')` 自查

### 22. planner/executor 优化：按"轮数"拆模块 + 分步推进（dsh_cockpit 暴露的问题）
- **现象**：dsh_cockpit 任务 m01（数据桥）打回 4+ 次原地踏步——executor 每轮只写 models.py，
  auditor 反复 block"缺解析器"。不是 executor 笨，是**任务书把解析+列表+广播+兼容捆成一个 m01**，
  executor 一把抓做不完，每次在起步
- **根因**：planner（fw-new）按"功能抽象"拆，不懂"一个模块应该多大"→ 拆出超大模块
- **修复**：
  1. fw-new.sh planner 提示词加核心心智："模块大小 = 2-3 轮能做完；觉得要 5+ 轮就拆小；
     objective 必须有'第一步'；acceptance 必须增量可过；round_estimate 诚实填，>4 就是拆失败"
  2. fw-executor.sh 加"分步推进，禁止一把抓"纪律：先做核心第一步（哪怕过 1 条验收），
     明确写 REVIEW"本轮完成 X / 下轮做 Y"，不做空壳骨架
- **哲学**（借鉴 lh）：按"单次执行轮数"拆任务——做完就做完，没做完下轮续。
  但要让续做有效，必须：小模块 + 第一步明确 + 验收增量可过

### 23. planner 提示词重写：融入 prd-split 铁律 + lh 验收写死 + 按轮数拆（读透思想）
- **读到的思想**：
  1. **prd-split 四条铁律**（本地 skill，原框架设计就有但 fw-new 没用）：
     ① 按"变更隔离性"拆（改 A 不影响 B），不按大小平均
     ② 接口只到"前缀+方法级"，禁止定字段（字段执行期涌现）
     ③ 树深 ≤3，更深改流水线拆分
     ④ 骨架先行（每层先出整体骨架再分发）
  2. **lh 哲学**（本地 skill）：任务可粗但"验收标准必须写死"、喂结果不喂过程、轮数宁多勿少可续轮
  3. **杰哥观察**：按"单次执行轮数"拆（做完就做完，没做完下轮续）
- **fw-new planner 提示词重写为 A/B/C 三段**：
  A. 为什么这么拆（prd-split 铁律）
  B. 拆多大（2-3 轮能做完 + objective 第一步 + acceptance 增量可过 + 验收写死 + round_estimate）
  C. 其他（依赖无环/分层）
- **核心思想**：模块边界按"变更隔离"定（不头痛医头），大小按"轮数"控（续做有效），
  验收"写死+增量可过"（executor 自检 + auditor 验收 + 续做有进展）

## 设计沉淀（2026-08-23，仅参考未实施）

### 24. Manager 角色设计参考（DeepSeek 评审，先不做）
- 三份文档：工作流还原-v1.md / Manager设计参考-DeepSeek评审.md / 设计-v0.5-递归分层planner.md
- 核心：Manager 按需唤醒（程序规则到头才叫，不每轮参与）；全景由程序汇编；Auditor 加 partial 三态；
  阈值表（70%/2/8/15）；指令格式（retry/split/human/continue）；human_question/answer 通道
- 状态：杰哥拍板"先参考，不做"。要动工时按 Manager设计参考-DeepSeek评审.md 第十二节顺序落地

---

## 2026-08-23 —— framework-v1 v0.4 → v1.0 简化版升级（SPLIT 递归拆分 + pro 兜底 + Auditor 三态，已交付）

> 设计事实源：`设计-v1.0-简化版.md` + `工程对接清单-v1.0.md`。全部改动限 `fw-runner/` 内，逐轮经 auditor（round_001–round_012）实读源码+实测确认，全量测试 `python3.11 -m pytest tests/ -x` 最终 **157 passed** 全绿。

### 1. 数据模型（fw_runner/model.py）—— A1–A6
- **改动**：`VERDICTS` 二态 → 三态 `("pass","partial","block")`；`MODULE_STATUS_OPTIONS` 增 `"split"`；
  `ModuleAgentState` 增 `split_depth/parent_module/child_modules/partial_count/aggregated/model_tier`；
  `DriverOutcome` 增 `passed_count/total_count/remaining_items`；
  `RunConfig` 增 `enable_split/split_max_depth/split_min_deliverables/split_merge_after_fails/enable_fallback_model/fallback_model/model_tiers`
- **为什么**：Auditor 三态判定（partial 需计数）+ SPLIT 递归拆分 + pro 兜底需要状态承载；
  `from_dict` 缺字段回落默认值，旧 v3 快照不破坏（A6 兼容）
- **验证**：`tests/test_model_v1.py` 10 项 + round_002 78 passed

### 2. 升级链（fw_runner/upgrade.py）—— B1–B4
- **改动**：新增 `SPLIT="split"`、`UPGRADE_MODEL="upgrade"` 常量；
  `route_verdict` 升级链改为 **retry → switch → SPLIT → UPGRADE_MODEL → HUMAN**（顺序不能乱）；
  新增 `route_partial`（前置检查 `total_count<=split_min_deliverables`→RETRY、完成度≥70% 且剩余≤2→RETRY、
  `enable_split and split_depth<max`→SPLIT、否则 HUMAN）；新增 `should_merge_back`（`partial_count>=split_merge_after_fails`）
- **为什么**：模块太大 executor 吞不下时先拆再换 pro，仍不行才回人；防小模块（≤2 交付物）误拆打架
- **验证**：`tests/test_upgrade_v1.py` 13 项（含全链顺序）+ round_003 91 passed

### 3. Split 落地逻辑（fw_runner/split.py，新增）—— D1–D5
- **改动**：`collect_split_context`（objective+完整 deliverables+auditor 判定+REVIEW+文件列表，5 项输入）、
  `call_split_agent`（dsh headless 一次性调用，解析并 `validate_split_json` 校验）、
  `scaffold_children`（modules/ 平级子模块：任务书-mXXa.yaml/REVIEW.md/contract.yaml/交付说明.md/src/test）、
  `generate_shared_context`（父目录写 SHARED_CONTEXT.md）、
  `insert_children_into_order`（子模块插父后 + 依赖图继承防环）
- **为什么**：flash 模型拆不动的大模块交给 split agent 递归拆解，拆解 JSON 校验失败→`split_failed` 回人不硬拆
- **验证**：`tests/test_split_v1.py` 23 项 + round_004 114 passed；round_011 修 `FW_SPLIT_MODEL`
  （注入真实模型名 `deepseek-v4-flash`，不再用档位名覆盖）

### 4. 运行主循环（fw_runner/runner.py）—— C1–C8 + F4 + H
- **改动**：`_run_module` 三分支（pass→done / partial→route_partial / block→route_verdict）；
  `_upgrade_model`（model_tier+1、block_count 清零、executor_id=E{n}_pro、emit module.model_upgrade）；
  `_do_split`（调 split agent→scaffold→SHARED_CONTEXT→父标 split→emit module.split）；
  `_aggregate_parents`（子全 done→父聚合 done，while 收敛嵌套）；`_check_merge_back`（连续失败合并回父，产出保留）；
  每批结束调 `_aggregate_parents`；`_valid` 三态使能（F4）；
  10 处 HUMAN 出口全部接 `human_escalate`（H）
- **为什么**：让 flash 模型 + 递归拆分 + pro 兜底能真实跑通；pro 只兜底当前叶子模块不改全局默认（铁律）
- **验证**：`tests/test_runner_v1.py` + round_005 128 passed、round_010 154 passed

### 5. Split Agent 脚本（bin/fw-split.sh，新增）—— E1–E4
- **改动**：仿 fw-executor.sh 子进程包装（cwd=模块目录）；拼装 split 指令（`fw-runner/prompts/split.md`）；
  调 dsh headless（flash）→ 输出 `tmp/split-outcome.json`（detail.split 含拆解 JSON）；
  退出码 0 + outcome 存在 → 成功；叶子（交付物≤2）守卫 exit 3 不拆
- **为什么**：split agent 真身 = dsh headless 子进程，与 `drivers.py` outcome 契约对齐
- **验证**：round_007 demo 端到端 exit 0、叶子 exit 3、无 dsh exit 2 均实测

### 6. Auditor 三态（bin/fw-auditor.sh）—— F1–F3
- **改动**：判定扩展为 pass/partial/block；`auditor-outcome.json` 写 `passed_count/total_count/remaining_items`；
  文本兜底解析支持 partial（has_partial + n/m 解析 + remaining 收集）
- **为什么**：partial 是拆分/续做/合并回父的路由依据；dsh json 与文本兜底两路径都按真实计数
- **验证**：`tests/test_auditor_v1.py` 8 项 + round_008 136 passed（自建 /tmp 模块实测 demo/dsh/文本三态）

### 7. 事件与快照（fw_runner/events.py + checkpoint.py）—— G1–G4
- **改动**：5 个 v1.0 事件常量（module.split / split_failed / aggregated / merge_back / model_upgrade）
  + round_012 补登 `module.human_abandoned`/`module.human_rerun`（V1_EVENT_TYPES 共 7 项）；
  `SNAPSHOT_SCHEMA_VERSION` 3→4（保留 `SNAPSHOT_SCHEMA_V3` 识别旧快照）；
  per_module 含 split/模型字段；恢复时 running→pending 崩溃重跑
- **为什么**：快照向前兼容 + 崩溃恢复不再依赖人工排查
- **验证**：`tests/test_events_checkpoint_v1.py` + round_009 140 passed、round_012 157 passed

### 8. 人机通道（fw_runner/human.py，新增）—— H1–H2
- **改动**：`prompt_text` 输出「模块 mXX 需要人工决策」+ 四选项 [A]放弃 [B]改方案 [C]暂停 [D]自定义；
  stdin 读取（input()）；回复写 `总日志/human_answer.json`，resume 读回收敛（A 放弃/B·D 重跑/C 暂停）；
  `interactive_human_enabled` 保证 pytest/headless 自动非交互不挂起
- **为什么**：HUMAN 出口不再干等，真人可离线给答案，resume 续跑
- **验证**：`tests/test_human_v1.py` 14 项（含 3 个 run→resume 端到端）+ round_010 154 passed

### 9. 测试（I1–I2）
- **改动**：新增 `test_model_v1/test_upgrade_v1/test_split_v1/test_runner_v1/test_auditor_v1/test_events_checkpoint_v1/test_human_v1`
- **验证**：全量 `python3.11 -m pytest tests/ -x` 最终 **157 passed in 15.60s，退出码 0**（68 基线 → 157，逐轮不回退）
- **遗留**：真 dsh 端到端拆分 + 真终端 human 交互属交付后人工验证（约束 3，GUI/交互交真人）
