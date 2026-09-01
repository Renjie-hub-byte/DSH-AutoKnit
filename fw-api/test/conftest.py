"""pytest 公共配置：注入 fw-api 的 src，提供任务目录/状态结果构造 fixture。

收敛自 dsh_cockpit m03/m04（合并）：fw-api 自包含，数据桥与各接口并入 fw_api 包。
测试统一用 fw_api / 兼容命名空间（dsh_task_list / fwr_detail）。
"""

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 兼容别名：把 fw_api 子模块注册为顶层包名，供历史测试（import dsh_task_list / fwr_detail）使用。
import fw_api  # noqa: E402
import fw_api.dsh_task_list as _dl  # noqa: E402,F401
import fw_api.fwr_detail as _fd  # noqa: E402,F401

sys.modules.setdefault("dsh_task_list", _dl)
sys.modules.setdefault("fwr_detail", _fd)

# 收敛后上游数据桥自包含在 fw_api 内，恒可用（历史集成测试引用此常量判 skip）。
UPSTREAM_OK = True


# ---------------------------------------------------------------------------
# 样例内容（字段为执行期涌现样例，仅用于测试）
# ---------------------------------------------------------------------------

TASK_YAML = """\
name: dsh_cockpit_m01_split验证
owner: 杰哥
created: '2026-08-23'
grade: B
"""

# 两个 run：executor 执行中（带模块级状态表）+ auditor 验收中（带模块级状态表）
SNAPSHOT_RUNS = [
    {
        "run_id": "run-20260823-001",
        "phase": "executor",
        "module": "m02",
        "status": "running",
        "updated_at": "2026-08-23T22:16:34+08:00",
        "modules": {
            "m01": {"stage": "done", "note": "任务目录解析完成"},
            "m02": {"stage": "executor 执行中", "executor_round": 2},
        },
    },
    {
        "run_id": "run-20260823-002",
        "phase": "auditor",
        "module": "m03",
        "status": "reviewing",
        "updated_at": "2026-08-23T22:17:00+08:00",
        "modules": {
            "m03": {"stage": "auditor 验收中", "auditor_round": 1},
        },
    },
]

DISPATCH_JSONL = [
    {"ts": "2026-08-23T22:16:34+08:00", "run_id": "run-20260823-001", "event": "dispatch", "to": "executor", "module": "m02"},
    {"ts": "2026-08-23T22:16:40+08:00", "run_id": "run-20260823-001", "event": "update", "phase": "executor", "status": "running"},
]

AUDITOR_SNAP = SNAPSHOT_RUNS[1]
EXECUTOR_SNAP = SNAPSHOT_RUNS[0]


@pytest.fixture
def build_task_dir(tmp_path):
    """构建一个可裁剪的 fw-runner 任务目录，返回目录 Path（对齐 m01 conftest 风格）。

    开关：with_task_yaml / with_snapshot / with_dispatch / with_modules 默认 True；
    传 False 模拟对应文件缺失。snapshot_runs 可注入自定义 run 快照列表。
    """

    def _build(
        base=None,
        snapshot_runs=None,
        dispatch_events=None,
        with_task_yaml=True,
        with_snapshot=True,
        with_dispatch=True,
        with_modules=True,
        runs=None,
    ):
        # runs 是 snapshot_runs 的历史别名（integration 测试用 runs=，detail 测试用 snapshot_runs=）
        if runs is not None:
            snapshot_runs = runs
        root = tmp_path / "task-dir" if base is None else base
        root.mkdir(parents=True, exist_ok=True)

        if with_task_yaml:
            (root / "task.yaml").write_text(TASK_YAML, encoding="utf-8")
        if with_snapshot:
            # snapshot_runs 为快照文件里的扁平 run 项（m01 读取后包装为 {run_id,index,snapshot}）
            runs = SNAPSHOT_RUNS if snapshot_runs is None else snapshot_runs
            (root / "snapshot.json").write_text(
                json.dumps({"runs": runs}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if with_dispatch:
            events = DISPATCH_JSONL if dispatch_events is None else dispatch_events
            (root / "dispatch.jsonl").write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
                encoding="utf-8",
            )
        if with_modules:
            for mod in ("m01", "m02", "m03"):
                (root / "modules" / mod / "tmp").mkdir(parents=True, exist_ok=True)
                (root / "modules" / mod / "tmp" / "state.json").write_text(
                    '{"stage": "active"}', encoding="utf-8"
                )
        return root

    return _build


# ---------------------------------------------------------------------------
# m02 状态结果构造（纯函数层测试，不依赖上游）
# ---------------------------------------------------------------------------


def make_status(ok=True, task_dir="/tmp/fake-task-dir", runs=None, errors=None):
    """构造 m02 `fwr.status.compute` 风格的结果 dict（字段对齐 m02 contract.yaml）。"""
    return {
        "ok": ok,
        "task_dir": task_dir,
        "runs": runs if runs is not None else [],
        "errors": errors if errors is not None else [],
    }


def make_run(
    run_id,
    index,
    stage,
    module="m01",
    module_states=None,
    status="running",
    **extra,
):
    """构造一条 m02 RunStatus 风格 dict；stage 决定阶段布尔标志与标签。"""
    run = {
        "run_id": run_id,
        "index": index,
        "phase": stage,
        "status": status,
        "module": module,
        "updated_at": "2026-08-23T22:00:00+08:00",
        "stage": stage,
        "stage_label": "label-%s" % stage,
        "executor_running": stage == "executor",
        "auditor_reviewing": stage == "auditor",
        "switch_in_progress": stage == "switch",
        "needs_human": stage == "needs_human",
        "module_states": module_states,
    }
    run.update(extra)
    return run
