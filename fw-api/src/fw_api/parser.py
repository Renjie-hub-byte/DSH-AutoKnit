# -*- coding: utf-8 -*-
"""m01 —— DSH 会话数据源解析器（纯函数解析器）

从 DSH 会话文件 / 目录或 fw-token 输出中抽取结构化字段：
  轮数(rounds) / 步数(steps) / token 速度(token_speed) / 缓存命中(cache_hit) / token 量(tokens)。

数据源格式：
  DSH 会话文件为 JSONL（每行一个事件），事件含 type/seq/time/data。
  真实会话常以 zstd 压缩（`session.jsonl.zstd`）。
  用法例:  {"type":"turn/start","seq":0,"time":1000,"data":{...}}
  token usage 上报于 assistant/chunk 事件: data.chunk.usage
    {"inputTokens":N,"outputTokens":N,"cacheReadTokens":N}

设计约束（对齐任务书验收）：
  - 纯函数：核心解析(parse_session_lines / parse_session_bytes)不读全局状态、不读环境，
    单测可独立复现；仅 *_file / *_dir 负责 I/O。
  - 只读消费：不修改 / 重写 / 修复会话文件本体。
  - 损坏兜底：zstd 解压失败 / JSON 行损坏时抛 SessionParseError（确定性错误），
    由调用方决定空对象或错误；绝不返回伪造数据、不抛未捕获异常。
  - 不依赖 zstandard python 包（当前环境未装）；解压走系统 `zstd` CLI（与 fw-token 一致）。
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field


class SessionParseError(Exception):
    """DSH 会话解析失败（确定性错误，供调用方兜底）。"""


@dataclass
class SessionStats:
    """单个 DSH 会话的解析结果。所有字段均非 None，缺失时取 0/空。"""
    rounds: int = 0                 # 轮数：turn/start 事件数
    steps: int = 0                  # 步数：step/start 事件数
    input_tokens: int = 0           # 输入 token 量
    output_tokens: int = 0          # 输出 token 量
    cache_hit_tokens: int = 0       # 缓存命中 token 量（cacheReadTokens）
    calls: int = 0                  # 带 usage 的上报次数（调用次数）
    duration_ms: int = 0            # 首个事件到末个事件的时间跨度(ms)
    token_speed: float = 0.0        # token 速度：output_tokens / duration(秒)
    cache_hit_ratio: float = 0.0    # 缓存命中占比：cache / (input + cache)

    def to_dict(self) -> dict:
        return {
            "rounds": self.rounds,
            "steps": self.steps,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "token_speed": round(self.token_speed, 3),
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_hit_ratio": round(self.cache_hit_ratio, 4),
            "calls": self.calls,
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# 纯函数核心解析
# ---------------------------------------------------------------------------

def parse_session_lines(lines) -> SessionStats:
    """从事件行迭代器解析一个 DSH 会话。纯函数：无全局状态、无 I/O。

    损坏的 JSON 行会被跳过（不影响其他行）；若整个流没有任何可解析事件，
    返回确定性空 SessionStats。任何 JSON 结构异常均不抛未捕获异常。
    """
    st = SessionStats()
    times: list[int] = []
    saw_event = False
    for raw in lines:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            # 单行损坏：跳过，不中断，不伪造
            continue
        if not isinstance(ev, dict):
            continue
        saw_event = True
        t = ev.get("time")
        if isinstance(t, (int, float)):
            times.append(int(t))
        etype = ev.get("type")
        data = ev.get("data")
        if etype == "turn/start":
            st.rounds += 1
        elif etype == "step/start":
            st.steps += 1
        # token usage 上报于 assistant/chunk 的 data.chunk.usage
        if etype == "assistant/chunk" and isinstance(data, dict):
            chunk = data.get("chunk")
            if isinstance(chunk, dict):
                usage = chunk.get("usage")
                if isinstance(usage, dict):
                    st.calls += 1
                    st.input_tokens += int(usage.get("inputTokens", 0) or 0)
                    st.output_tokens += int(usage.get("outputTokens", 0) or 0)
                    st.cache_hit_tokens += int(usage.get("cacheReadTokens", 0) or 0)
    # token 速度 = 输出 token / 时长(秒)；时长 = 末事件-首事件
    if not saw_event:
        return st
    if times:
        st.duration_ms = max(times) - min(times)
    duration_s = st.duration_ms / 1000.0
    if duration_s > 0:
        st.token_speed = st.output_tokens / duration_s
    if (st.input_tokens + st.cache_hit_tokens) > 0:
        st.cache_hit_ratio = st.cache_hit_tokens / (st.input_tokens + st.cache_hit_tokens)
    return st


def _decompress_zstd(raw: bytes) -> str:
    """用系统 zstd CLI 解压。失败抛 SessionParseError（确定性错误）。"""
    if not raw:
        raise SessionParseError("empty zstd data")
    try:
        proc = subprocess.run(
            ["zstd", "-dc"],
            input=raw,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # 如 zstd 未安装
        raise SessionParseError(f"zstd CLI unavailable: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise SessionParseError(f"zstd decompress failed: {err or 'unknown'}")
    return proc.stdout.decode("utf-8", errors="replace")


def parse_session_bytes(raw: bytes, *, is_zstd: bool = False) -> SessionStats:
    """解析原始字节。纯函数：输入即字节，无外部状态。is_zstd 时先解压。"""
    if not raw:
        return SessionStats()
    text = _decompress_zstd(raw) if is_zstd else raw.decode("utf-8", errors="replace")
    return parse_session_lines(text.splitlines())


# ---------------------------------------------------------------------------
# 文件 / 目录 I/O
# ---------------------------------------------------------------------------

def parse_session_file(path: str) -> SessionStats:
    """解析单个 DSH 会话文件（.jsonl 或 .jsonl.zstd）。只读。

    文件损坏/不可读时抛 SessionParseError（确定性错误），不返回伪造数据。
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise SessionParseError(f"cannot read {path}: {exc}") from exc
    is_zstd = str(path).endswith(".zstd")
    return parse_session_bytes(raw, is_zstd=is_zstd)


def parse_session_dir(path: str, *, pattern: str = "*.jsonl.zstd") -> dict:
    """解析一个 DSH 会话目录，返回 {相对标识: SessionStats.to_dict()}。

    pattern 默认 *.jsonl.zstd（真实 DSH 会话文件形态）；也可传 "*.jsonl" 匹配明文。
    目录不存在时抛 SessionParseError；单个文件损坏时以确定性错误对象占位，
    不影响其余文件（不崩、不伪造）。
    """
    if not os.path.isdir(path):
        raise SessionParseError(f"not a directory: {path}")
    result: dict = {}
    for dirpath, _dirs, files in os.walk(path):
        for name in sorted(files):
            if not name.endswith(pattern.lstrip("*")):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, path)
            try:
                st = parse_session_file(full)
                result[rel] = st.to_dict()
            except SessionParseError as exc:
                result[rel] = {"error": str(exc)}
    return result


# ---------------------------------------------------------------------------
# fw-token CLI 输出解析
# ---------------------------------------------------------------------------

def parse_fw_token_output(text: str) -> dict:
    """解析 fw-token CLI 输出文本，抽出汇总数值。

    输出形如：
      会话 ... 输入 输出 缓存 调用
      ------...
      <key>  N N N N
      ...
      ------...
      合计(103 会话)  输入: 3,974,464  输出: 1,124,482  计费: 5,098,946
      缓存命中: 66,066,432（占 94.3%）
      平均每次调用: 输入 2,273 / 输出 643

    返回：{sessions, input_tokens, output_tokens, billed_tokens, cache_hit_tokens,
           cache_hit_ratio, avg_input_per_call, avg_output_per_call, rows:[...]}
    无法解析时抛 SessionParseError；不返回伪造数据。
    """
    rows = []
    tot = {"i": 0, "o": 0, "c": 0, "n": 0}
    n_sessions = 0
    cache_hit_ratio = 0.0
    avg_in = avg_out = 0

    def _num(s: str) -> int:
        return int(s.replace(",", "").strip())

    header_seen = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 合计行
        if line.startswith("合计("):
            n_sessions = int(line.split("合计(")[1].split(" 会话")[0])
            for part in line.split("  "):
                part = part.strip()
                if part.startswith("输入:"):
                    tot["i"] = _num(part.split(":", 1)[1])
                elif part.startswith("输出:"):
                    tot["o"] = _num(part.split(":", 1)[1])
                elif part.startswith("计费:"):
                    pass  # billed = i + o，见下
            continue
        # 缓存命中行
        if line.startswith("缓存命中:"):
            c = _num(line.split("缓存命中:", 1)[1].split("（")[0])
            tot["c"] = c
            if "占 " in line and "%" in line:
                try:
                    cache_hit_ratio = float(line.split("占 ")[1].split("%")[0]) / 100.0
                except ValueError:
                    cache_hit_ratio = 0.0
            continue
        # 平均每次调用行
        if line.startswith("平均每次调用:"):
            body = line.split("平均每次调用:", 1)[1]
            for part in body.split("/"):
                part = part.strip()
                if part.startswith("输入"):
                    avg_in = _num(part.split("输入")[1].strip().split()[0])
                elif part.startswith("输出"):
                    avg_out = _num(part.split("输出")[1].strip().split()[0])
            continue
        # 分隔线 / 表头
        if line.startswith("-"):
            continue
        if line.startswith("会话") and "输入" in line and "调用" in line:
            header_seen = True
            continue
        # 数据行（含 k/v 会话，可能带空格路径）
        if header_seen:
            parts = line.split()
            # 会话标识含空格时按末 4 个数值列截取
            if len(parts) >= 5:
                num_cols = parts[-4:]
                try:
                    n = int(num_cols[3])
                    i = _num(num_cols[0])
                    o = _num(num_cols[1])
                    c = _num(num_cols[2])
                except ValueError:
                    continue
                key = " ".join(parts[:-4])
                rows.append({"session": key, "input": i, "output": o, "cache": c, "calls": n})
                tot["i"] += i
                tot["o"] += o
                tot["c"] += c
                tot["n"] += n
                n_sessions = max(n_sessions, len(rows))

    if not rows and tot["i"] == 0 and tot["o"] == 0:
        raise SessionParseError("no fw-token data parsed")
    billed = tot["i"] + tot["o"]
    if (tot["i"] + tot["c"]) > 0:
        cache_hit_ratio = cache_hit_ratio or tot["c"] / (tot["i"] + tot["c"])
    return {
        "sessions": n_sessions,
        "input_tokens": tot["i"],
        "output_tokens": tot["o"],
        "billed_tokens": billed,
        "cache_hit_tokens": tot["c"],
        "cache_hit_ratio": round(cache_hit_ratio, 4),
        "calls": tot["n"],
        "avg_input_per_call": avg_in,
        "avg_output_per_call": avg_out,
        "rows": rows,
    }


__all__ = [
    "SessionParseError",
    "SessionStats",
    "parse_session_lines",
    "parse_session_bytes",
    "parse_session_file",
    "parse_session_dir",
    "parse_fw_token_output",
]
