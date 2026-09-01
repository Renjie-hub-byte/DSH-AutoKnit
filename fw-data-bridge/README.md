# fw-api serve —— AutoKnit 面板数据桥（m01 数据桥）

把 fw-api 的 dsh.task.list / dsh.task.detail 数据以确定性、可空降级的方式暴露成
浏览器可调的 JSON HTTP 端点，并承载归档状态（任务目录约定文件）的读写。
纯 Python 标准库实现，无第三方运行时依赖。

## 目录结构

```
src/fwapi/
├── __init__.py       包入口（re-export serve / main）
├── __main__.py       python -m fwapi 入口（等价 fw-api serve）
├── serve.py          HTTP 服务 + 路由 + CLI（fw-api serve）
├── dsh/
│   ├── task.py       只读任务数据源：task.list / task.detail + 空降级
│   ├── usage.py      消耗汇总：dsh.usage.summary → /api/usage
│   └── events.py     task.update 事件桥接（可选，进程内轮询）→ /api/events
└── storage/
    └── archive.py    归档状态存取：总日志/archived.json（幂等）
```

## 启动

```bash
# 方式一：模块方式（推荐，无需安装）
export AUTOKNIT_TASK_DIR=/path/to/autoknit/task
.venv/bin/python -m fwapi serve --host 127.0.0.1 --port 8765 --task-dir /path/to/autoknit/task

# 方式二：脚本方式
.venv/bin/python src/fwapi/serve.py --port 8765 --task-dir /path/to/autoknit/task
```

请求级 `task_dir` 未携带时回落 `--task-dir` 或环境变量 `AUTOKNIT_TASK_DIR`。

## HTTP 端点

| 方法 | 路径 | 说明 | 响应 |
| --- | --- | --- | --- |
| GET | `/api/tasks?task_dir=...` | 任务列表，按紧急度降序，归档 run 不展示 | `[...]` |
| GET | `/api/tasks/{run_id}?task_dir=...` | 单 run 详情 | 对象 / `null` |
| GET | `/api/tasks/archived?task_dir=...` | 已归档 run_id 集合 | `[...]` |
| POST | `/api/tasks/archive` | 归档一个 run_id（幂等），body: `{"task_dir":..., "run_id":...}` | `{ok,run_id,archived}` |
| POST | `/api/tasks/{run_id}/archive` | 路径式归档一个 run_id（幂等），body: `{"task_dir":...}` | `{ok,run_id,archived}` |
| GET | `/api/usage?task_dir=...` | 消耗汇总（token/duration/cache，含按阶段分桶） | `{...}` |
| GET | `/api/events?task_dir=...&since=<seq>` | 任务状态增量更新（task.update 桥接），since 为游标 | `[...]` |
| GET | `/api/health` | 健康检查，恒 200 | `{status,service,version}` |
| GET | `/api/runs?task_dir=...` | run 列表：注册表聚合多 run（active 优先、updated_at 降序）；注册表缺失回落单 task_dir 快照 | `[...]` |
| GET | `/api/runs/{run_id}?task_dir=...` | run 详情（按注册表定位 task_dir，含 cause/task_dir/started_at）；未命中确定性 `null` | 对象 / `null` |
| GET | `/api/runs/{run_id}/tree?task_dir=...` | 执行树（按注册表定位 task_dir；modules/dependencies/per_module 全字段/split 子树/needs_human） | 对象 / `null` |
| GET | `/api/runs/{run_id}/timeline?task_dir=...` | 事件流（按注册表定位 task_dir）：dispatch.jsonl 按 seq 升序（契约枚举内事件，缺失确定性 `[]`） | `[...]` |
| GET | `/api/runs/{run_id}/usage?task_dir=...` | run 级 + per-module token 拆分（按注册表定位 task_dir，复用 fw-token.py 聚合会话） | `{run,per_module,no_split}` |
| POST | `/api/runs/{run_id}/reply` | 人工决策回复（按注册表定位 task_dir）：写 `needs_human/reply.md`（command 白名单，自定义必填 instruction） | `{success,detail}` |
| POST | `/api/runs/{run_id}/archive` | 注册表幂等归档（同 DELETE），列表不再显示该 run | `{run_id,status,ok}` |
| DELETE | `/api/runs/{run_id}/archive` | 注册表幂等归档（同 POST），列表不再显示该 run | `{run_id,status,ok}` |

> 所有 `?task_dir=...` 均可省略：请求级未携带时回落 `--task-dir` / `AUTOKNIT_TASK_DIR`。

### curl 示例

```bash
# 任务列表 / 详情 / 已归档集合
curl 'http://127.0.0.1:8765/api/tasks?task_dir=/path/to/task'
curl 'http://127.0.0.1:8765/api/tasks/run-001?task_dir=/path/to/task'
curl 'http://127.0.0.1:8765/api/tasks/archived?task_dir=/path/to/task'

# 归档一个 run（body 式 与 路径式 二选一）
curl -X POST 'http://127.0.0.1:8765/api/tasks/archive' \
     -H 'Content-Type: application/json' \
     -d '{"task_dir":"/path/to/task","run_id":"run-001"}'
curl -X POST 'http://127.0.0.1:8765/api/tasks/run-001/archive' \
     -H 'Content-Type: application/json' \
     -d '{"task_dir":"/path/to/task"}'

# 消耗汇总 / 事件轮询 / 健康检查 / run 级 token 拆分
curl 'http://127.0.0.1:8765/api/usage?task_dir=/path/to/task'
curl 'http://127.0.0.1:8765/api/events?task_dir=/path/to/task&since=0'
curl 'http://127.0.0.1:8765/api/health'
curl 'http://127.0.0.1:8765/api/runs/run-001/usage?task_dir=/path/to/task'
```

## run 注册表（runs_registry，跨模块共享存储）

`/api/runs` 与 `/api/runs/{id}` 由注册表驱动多 run 聚合：

- 注册表文件：`~/.autoknit/runs.json`（可用环境变量 `AUTOKNIT_RUNS_REGISTRY` 覆盖绝对路径）。
- 文件格式：`{"runs": [ {record...}, ... ]}`；record 字段对齐 data_contract：
  `run_id / task_dir / task / status(active|complete|archived) / started_at / updated_at`
  （status 未知值确定性标 `unknown`；ts 为 ISO-8601 UTC，如 `2026-08-29T00:00:00+00:00`）。
- 写入主体是 fw-run.sh（程序包装器登记段，非 LLM 角色）；本桥只按契约读写同一文件。
- 注册表存在有效记录时：`/api/runs` 聚合所有 run（active 优先、updated_at 降序），
  每 run 从各自 `task_dir` 快照取详情；`/api/runs/{id}` 按注册表定位 task_dir 取详情。
- 注册表缺失/为空：确定性回落单 `task_dir`（请求 `task_dir` / `--task-dir` / `AUTOKNIT_TASK_DIR`），
  沿用现有单快照行为，不破坏旧端点。
- 归档（POST/DELETE `/api/runs/{id}/archive`）：把该 run 在注册表标记 `archived`（幂等），
  `/api/runs` 列表不再显示该 run；注册表未命中/空 run_id 确定性返回 `{ok:False}`。
- 注册表写入主体是 fw-run.sh 登记段：本模块在 `contrib/fw-run.sh.registry-segment.sh` 交付代码树
  （供 merge/apply 合入 fw-run.sh，run.start 登记 active、run 结束 complete|needs_human|exit 更新 complete），
  程序段、零加活，不修改任何调度逻辑。

## 数据布局（文档化约定）

- 任务列表数据源：`<task_dir>/总日志/runs.json`
  ```json
  {"runs": [
    {"run_id":"run-001","stage":"executor","stage_label":"执行中",
     "task_name":"示例","module_states":{"m01":{"status":"ok"}},
     "urgency":3,"needs_human":false,
     "consumption":{"token_input":100,"token_output":50,"cache_hit":"no","duration_sec":12}}
  ]}
  ```
  目录缺失 / 文件缺失或损坏 → 确定性空降级为 `[]`。仅旧 `/api/tasks*` 端点使用。
- 真实快照数据源：`<task_dir>/总日志/快照.json`（`/api/runs`、`/api/runs/{id}/tree` 使用）
  - 顶层含 `run_id / task / status / cause / updated_at / modules / dependencies /
    per_module / needs_human`；`per_module[module]` 携带 `parent_module / child_modules /
    split_depth` 以任意深度表达 split 子树。
  - 目录缺失 / 文件缺失或损坏 → `runs` 确定性 `[]`、`tree` 确定性 `null`；未知字段标 `unknown`。
- 事件流数据源：`<task_dir>/总日志/dispatch.jsonl`（`/api/runs/{id}/timeline` 使用）
  - 仅输出契约枚举事件（run.start / module.dispatch / executor.round.* / auditor.round.* /
    module.split / module.aggregated / module.final_block / module.done / integration.check）
    且带合法 seq 的事件，按 seq 升序；逐字段归一化，未知事件/无 seq/异 run 确定性过滤。
  - 目录/文件缺失 → 确定性 `[]`。
- 人工决策回复：`<task_dir>/needs_human/reply.md`（`POST /api/runs/{id}/reply` 写入）
  - command 白名单 continue/retry/revise/自定义，自定义必填 instruction；
  - 仅允许对「当前确需人工决策」的 run（快照 needs_human 非空）回复；成功/失败均 `{success,detail}`。
- 归档状态：`<task_dir>/总日志/archived.json`（`{"archived": [...]}`）
  - 可用环境变量 `AUTOKNIT_ARCHIVE_FILE` 覆盖归档文件绝对路径。
  - 文件缺失 → 确定性返回 `[]`；写入幂等。

## 空降级

- task_dir 无效 / 无活跃 run：列表 `[]`、详情 `null`、usage 全 0、events `[]`，HTTP 200，不抛异常。
- 单条 run 字段缺失：按契约默认值补齐（stage 落到 `unknown`，数值落到 0，布尔落到 false）。
- timeline：目录/文件缺失、run 未命中 → 确定性 `[]`；未知事件/无 seq 确定性过滤。
- reply：参数非法/非 needs_human/目录无效/写失败 → 确定性 `{success:false, detail}`（HTTP 400），不抛异常。
- 归档文件缺失/损坏：`archived` 返回 `[]`，不影响列表读取。
- `/api/health` 不依赖 task_dir，恒 200。

## 错误码

统一 JSON 信封 `{"error": code, "message": str}`：
- `404 not_found`：未知端点 / 方法不匹配。
- `400 bad_request`：参数非法（如空 run_id 归档）。

> `/api/runs/{id}/reply` 例外：语义失败（白名单外命令/自定义缺 instruction/非 needs_human/
> 写失败）返回 HTTP 400 + 契约体 `{"success": false, "detail": str}`。

## 消耗汇总（/api/usage）

对任务目录下所有活跃 run 的 `consumption` 聚合，输出：
`task_dir, total_runs, total_token_input, total_token_output, total_duration_sec,
cache_hit_runs, by_stage`（by_stage 按阶段分桶，含 runs/token_input/token_output）。

## run 级 token 拆分（/api/runs/{id}/usage）

对单个 run，按模块 id + run 时间窗 on-demand 复用 `fw-token.py --json` 聚合会话文件
（`$DSH_HOME/sessions/*/session-*/session.jsonl.zstd`），输出契约：
`{run: {input, output, cache_read, calls, billable}, per_module: {<module_id>: 同上}, no_split: str}`。

- 每个模块按快照 `per_module` 键（或 `modules` 键）枚举，逐模块调 `fw-token.py <module_id> --since <run起始ms>`
  聚合；模块 id 严格段匹配（m03 不串扰 m03a）；`--since` 用 run 时间窗起点（per_module 各模块
  started_at 最小值）过滤，run 开始前的会话不计入。
- run 级 = 各 per-module 之和；计费 billable = input + output（缓存命中不计费，单独上报）。
- 空降级：目录缺失 / run 未命中 / 无拆分数据（快照无模块可拆）→ `run` 全 0、`per_module` 空、
  `no_split` = 「无拆分数据」；`fw-token.py` 定位不到时数值确定性回落 0（保持结构）。
- fw-token.py 定位顺序：环境变量 `FW_TOKEN_PY` → PATH → `<framework-v1>/fw-tools/fw-token.py`。

## 事件推送（/api/events，可选）

dsh.task.update 桥接：进程内按 task_dir 分桶缓冲。每次拉取时对当前 run 快照与上次观测
做 diff，run 新增 / stage 变化 / run 消失分别产出 `{"type":"task.update","run_id",...,"seq",...}`。

桥内另补 runs 级事件（注册表驱动，全进程共享一份）：
- `run.start`：注册表新 run 出现（携带 `status`）；
- `run.archived`：注册表某 run 状态转为 `archived`（携带 `status`）。

`seq` 为全进程共享单调递增游标（跨桶唯一），`/api/events` 合并 task_dir 桶 + 注册表桶并按
`seq` 升序返回；浏览器用 `since=<seq>` 做增量轮询。服务重启即清空（进程内桥，不持久化）。
注册表文件路径变化时自动清空该桶（避免跨注册表串事件）。

## 测试

```bash
cd modules/m01-数据桥
.venv/bin/python -m pytest test/ -q
```
