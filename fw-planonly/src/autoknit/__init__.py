"""autoknit — plan-only 模式.

一个自包含、确定性的 CLI，把"人在环上"的规划环节独立出来给真人/另一 agent 审：
- ``autoknit plan-only <dir>``：只跑 planner，产出 task.yaml、写 checkpoint、输出摘要，
  规划完即停且退出码 0；全程不产生任何 executor / auditor / split 事件，也不发起 LLM 请求。
- ``autoknit summary <dir>``：读取已有 task.yaml 打印摘要（对外只读接口的命令行对应）。
- ``autoknit run <dir> --resume-from-checkpoint``：用同一份 task.yaml 接续已规划任务，
  识别 plan checkpoint、不重复规划。

外部可对接的只读接口（供真人 / 另一 agent / DSH 面板单独审）：
    dsh.plan-only.summary (get) -> :func:`autoknit.api.get_plan_summary`
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
