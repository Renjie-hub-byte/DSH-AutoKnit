"""agent 驱动（executor / auditor）—— "spawn executor（cwd=模块目录）" 的落地层。

- AgentDriver 协议：run_round(ctx) -> DriverOutcome
- ScriptedAgentDriver：子进程 spawn（cwd=模块目录）—— 真实进程边界，模拟 dsh
  sessions.fork 下的 agent 会话；CLI 默认驱动。
- InlineAgentDriver：进程内回调（测试用，保证调度逻辑确定性可复现）。

驱动契约：
- 退出码 0        → 正常完成；结果读 tmp/{role}-outcome.json（没有则按 REVIEW 兜底）
- 退出码 13       → interrupted（RunInterrupted → runner checkpoint 后退出 130）
- 其他非零退出码   → agent_error（按 block/self 路由进升级链）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Protocol

from .model import DriverOutcome, ModuleSpec

EXIT_INTERRUPTED = 13  # driver 约定的"被中断"退出码（与 130 区分：13 是子进程侧，130 是 CLI 侧）


@dataclass
class AgentContext:
    """一次 agent 轮次的上下文（传给驱动）；env 为子进程环境变量（超集）。"""

    module: ModuleSpec
    run_id: str
    role: str                 # executor | auditor
    round_no: int             # 该角色第几轮（1-based，模块级累计）
    executor_id: str          # 当前 executor 标识（E1/E2/...）
    task_root: Path
    mode: str
    env: Dict[str, str] = field(default_factory=dict)

    def to_env(self) -> Dict[str, str]:
        env = dict(self.env)
        env.update({
            "MODULE_ID": self.module.id,
            "MODULE_DIR": str(self.module.dir),
            "TASK_ROOT": str(self.task_root),
            "RUN_ID": self.run_id,
            "ROUND": str(self.round_no),
            "ROLE": self.role,
            "EXECUTOR_ID": self.executor_id,
            "MODE": self.mode,
        })
        return env


class AgentDriver(Protocol):
    def run_round(self, ctx: AgentContext) -> DriverOutcome:
        ...


class InlineAgentDriver:
    """进程内回调驱动（测试用）。fn(ctx) -> DriverOutcome。"""

    def __init__(self, fn: Callable[[AgentContext], DriverOutcome]) -> None:
        self.fn = fn

    def run_round(self, ctx: AgentContext) -> DriverOutcome:
        return self.fn(ctx)


def _read_outcome_json(module_dir: Path, role: str) -> Optional[Mapping[str, Any]]:
    p = module_dir / "tmp" / f"{role}-outcome.json"
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


# BUG-003 修复（2026-08-25）：兼容 executor 的「remaining N 行」写法。
# 原正则只认「剩余行数 ≈ N」，executor 写「（remaining 1400 行）」不匹配 → rem=None →
# 出口判定静默 done → split 永不触发。现兼容两种写法 + 行字后缀。
_REMAINING_LINES_RE = re.compile(
    r"(?:剩余行数|remaining)\s*[≈约~:：]?\s*(\d+)\s*(?:行)?", re.M | re.I)


def _extract_remaining_lines(module_dir: Path, role: str) -> Optional[int]:
    """executor 出口自报剩余行数：优先 outcome.json 的 remaining_lines，其次扫 REVIEW「待办」。

    正则匹配 `剩余行数 ≈ N` / `剩余行数: N` / `remaining N 行` 等
    （executor 铁律要求写 REVIEW 的格式；框架侧兼容变体写法）。
    """
    data = _read_outcome_json(module_dir, role)
    if data and data.get("remaining_lines") is not None:
        try:
            return int(data["remaining_lines"])
        except (TypeError, ValueError):
            pass
    try:
        rp = module_dir / "REVIEW.md"
        if rp.is_file():
            txt = rp.read_text(encoding="utf-8", errors="replace")
            m = _REMAINING_LINES_RE.search(txt)
            if m:
                return int(m.group(1))
    except OSError:
        pass
    return None


def classify_env_error(text: str, exit_code: int) -> str:
    """客观环境错误分类（限流/断网/服务端）→ root 值；非环境错误 → None。

    对齐 dsh-llm-retry 的失败 code（RATE_LIMIT/TRANSPORT/SERVER/TIMEOUT）：
    这些都是「上游客观原因」，executor 换人无用，应退避重试而不是甩锅给 executor。
    返回 upstream（环境类）或 None（其它）。
    """
    low = (text or "").lower()
    # 限流：429 / rate limit / tokenplan quota
    if any(s in low for s in ("rate limit", "rate_limit", "429", "quota exceeded",
                              "too many requests", "retry_after")):
        return "upstream"
    # 断网/连接：connection / transport / network / econnrefused / dns
    if any(s in low for s in ("connection error", "connection refused", "econnrefused",
                              "transport", "network error", "socket hang up",
                              "failed to fetch", "unreachable", "dns")):
        return "upstream"
    # 服务端 5xx / 超时
    if any(s in low for s in ("500", "502", "503", "504", "internal server error",
                              "timeout", "timed out", "request failed")):
        return "upstream"
    return ""


def _fallback_outcome(module: ModuleSpec, role: str) -> DriverOutcome:
    """无 outcome.json 时按 REVIEW.md 兜底（auditor 判定键由 runner 统一写回，
    此处读 REVIEW 判定键作为兜底来源，保持脚本最简）。"""
    out = DriverOutcome()
    try:
        from .review import read_review
        doc = read_review(module.review_path)
        status = doc.kv.get("status", "")
        if role == "auditor":
            out.verdict = "pass" if status == "done" else "block"
            out.root = doc.kv.get("root", "")
            try:
                out.confidence = float(doc.kv.get("confidence") or 0.0)
            except ValueError:
                out.confidence = 0.0
            out.reason = doc.kv.get("detail", "")
        else:
            # executor 无 substance 声明 → None，由 runner 用 REVIEW 指纹判定
            out.substance = None
    except Exception:
        pass
    return out


class ScriptedAgentDriver:
    """子进程驱动：spawn agent（cwd=模块目录），模拟 dsh sessions.fork 的会话边界。

    cmd 支持占位符：{module_dir} {task_root} {run_id} {round} {role} {executor_id} {mode}
    不填占位符时也通过环境变量传递（to_env）。
    """

    def __init__(self, cmd: str, role: str, env: Optional[Dict[str, str]] = None,
                 timeout: Optional[float] = None) -> None:
        self.cmd = cmd
        self.role = role
        self.env = dict(env or os.environ)
        self.timeout = timeout

    def run_round(self, ctx: AgentContext) -> DriverOutcome:
        cmd = self.cmd.format(
            module_dir=ctx.module.dir,
            task_root=ctx.task_root,
            run_id=ctx.run_id,
            round=ctx.round_no,
            role=self.role,
            executor_id=ctx.executor_id,
            mode=ctx.mode,
        )
        env = dict(self.env)            # 驱动自带 env（如测试注入的 FW_EXIT_INTERRUPT）
        env.update(ctx.to_env())        # runner 注入的标准变量（优先级更高）
        env.setdefault("PYTHONPATH", self._pythonpath())
        # P1（packaging-p0）：spawn 前清掉上一轮遗留的 outcome.json，防止 stale 读
        # （上一轮的 tmp/{role}-outcome.json 若未清理，本轮 agent 崩溃退出 0 时会被误读为本轮产物）
        stale_outcome = ctx.module.dir / "tmp" / f"{self.role}-outcome.json"
        try:
            stale_outcome.unlink()
        except OSError:
            pass
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(ctx.module.dir), env=env,
                capture_output=True, text=True, errors="replace",
                encoding="utf-8", timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return DriverOutcome(status="error", root="self", reason=f"agent 超时(>{self.timeout}s)")
        except UnicodeDecodeError:
            # 输出含非法 UTF-8 字节（agent 打 emoji/二进制）→ 降级为子进程结果不可读
            return DriverOutcome(status="error", root="self",
                                 reason="agent 输出无法解码(非法 UTF-8 字节)",
                                 detail={"exit": -1, "stderr": "UnicodeDecodeError: 输出含非 UTF-8 字节"})
        if proc.returncode == EXIT_INTERRUPTED:
            return DriverOutcome(status="interrupted", reason="driver 报告中断")
        if proc.returncode != 0:
            # 环境类错误识别（限流/断网/服务端 5xx）→ root=upstream（客观原因，不是 executor 不行）
            joined = (proc.stderr or "") + (proc.stdout or "")
            err_code = classify_env_error(joined, proc.returncode)
            return DriverOutcome(
                status="error", root=err_code or "self",
                reason=f"agent 非零退出({proc.returncode})",
                detail={"exit": proc.returncode, "stderr": (proc.stderr or "")[-2000:]},
            )
        data = _read_outcome_json(ctx.module.dir, self.role)
        if data is not None:
            out = DriverOutcome.from_mapping(data)
            out.status = "ok"
            if self.role == "executor" and out.remaining_lines is None:
                out.remaining_lines = _extract_remaining_lines(ctx.module.dir, self.role)
            return out
        out = _fallback_outcome(ctx.module, self.role)
        if self.role == "executor" and out.remaining_lines is None:
            out.remaining_lines = _extract_remaining_lines(ctx.module.dir, self.role)
        return out

    def _pythonpath(self) -> str:
        """子进程 PYTHONPATH：保证源码树（未 pip install）直跑时 `import fw_runner` 可用。

        fw-protocol / fw-scaffold 是正规 pip 依赖（pyproject.toml 声明），随运行环境
        自带，不再往 PYTHONPATH 塞 monorepo 兄弟包路径（packaging-p0）。
        """
        from .context import _RUNNER_SOURCE_ROOT
        parts = []
        existing = self.env.get("PYTHONPATH")
        if existing:
            parts.append(existing)
        parts.append(str(_RUNNER_SOURCE_ROOT))
        return os.pathsep.join(parts)
