"""共享数据契约（对齐 contracts/data.yaml 唯一事实源）。

plan-only 产出的 task.yaml 需携带这份数据契约，供下游模块（m03 面板、run 接续）
对齐存储/枚举。本模块内部不得自定义表名/路径/格式，全部以这里为准。
"""

from __future__ import annotations

from typing import Any

# 与任务根 contracts/data.yaml 字节级一致的共享契约（只读，勿改）。
DATA_CONTRACT: dict[str, Any] = {
    "stores": [
        {
            "name": "snapshot_store",
            "kind": "file",
            "path": "总日志/快照.json",
            "env_var": "FW_SNAPSHOT_PATH",
            "owners": ["plan-only 模式"],
            "readers": ["DSH 可折叠面板"],
            "note": "运行状态快照，plan-only 写 checkpoint（规划完即停），面板读它拼进度/消耗/待决策；阶段与角色枚举全模块一致",
            "columns": {
                "stage": "text",
                "roles": "list[text]",
                "token_input": "int",
                "token_output": "int",
                "cache_hit": "text",
                "pending": "text",
            },
        },
        {
            "name": "human_answer_store",
            "kind": "file",
            "path": "总日志/human_answer.json",
            "env_var": "FW_HUMAN_ANSWER",
            "owners": ["DSH 可折叠面板"],
            "readers": [],
            "note": "真人决策落盘文件；面板提交决策/文本写入，既有框架 human.py/runner 读它 resume 接续；格式对齐既有 human.py 的 human_answer.json",
            "columns": {"choice": "text", "text": "text"},
        },
    ],
    "shared_enums": {
        "stage": ["planning", "exec", "audit", "split", "idle"],
        "roles": ["planner", "executor", "auditor"],
        "human_choice": ["A", "B", "C", "D", "text"],
        "merge_conflict_kind": ["same_name", "naming_conflict", "signature_mismatch", "semantic_merge"],
    },
}

# 本模块对外只读接口（契约 dsh.plan-only.summary）。
PLAN_ONLY_SUMMARY_INTERFACE: dict[str, Any] = {
    "path": "dsh.plan-only.summary",
    "method": ["get"],
    "direction": "F→R",
    "note": "拉取 plan-only 的规划摘要（模块数/每模块预计行数/每个大模块首个 executor 任务行数），供真人或另一 agent 单独审；任务目录缺 task.yaml 时确定性空降级并报错",
    "data_shape": {
        "request": {},
        "response": {
            "type": "list",
            "item": {"module_name": "str", "estimated_lines": "int", "first_block_lines": "int"},
            "extendable": True,
        },
    },
}
