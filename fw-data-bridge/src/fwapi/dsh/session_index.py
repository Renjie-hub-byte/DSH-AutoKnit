"""fwapi.dsh.session_index —— dsh 会话索引层（一次索引 + 增量刷新）。

重写数据流的核心新件，替代「每次请求全扫所有会话」的补丁债（需求重定义 2026-09-01
已知问题 3「刷新慢」与 4「数据时有时无」）：

- 启动后由后台线程对全部会话根建一次索引，每个 session.jsonl.zstd 只解压一次；
- 之后每轮 refresh 只 stat 对比 (mtime_ns, size)，仅重新解析新增/变化的文件；
- 单文件解析失败（run 进行中读到写一半的 zstd 文件）不进索引、保留旧值并标记，
  待下次 mtime 变化时自愈 → 根治「数据跳变」；
- 索引只存原始事实（逐条 usage 行 + 会话 cwd 目录名），run 归属/时间窗/模块拆分
  由查询侧（usage.py）判定，索引层不做任何聚合口径决策。

纯标准库实现；线程安全（ThreadingHTTPServer 多线程并发查询）。
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# 会话根解析：与 fw-token.py 同一套规则（FW_DSH_HOME → DSH_HOME → 默认候选），
# 显式自定义根严格单根、默认根补扫历史根（BUG-009 配套语义，保持不变）。
_DEFAULT_ROOTS = ("~/.fw-dsh", "~/.fw-dsh-bench", "~/.dsh")

# 模块段匹配：模块 id 形如 m01/m02a（快照 per_module 键为准，索引侧不假设格式）。
_MODULE_SEG_SPLIT = re.compile(r"[-/]")


def sess_roots() -> List[str]:
    """全部需要扫描的会话根（去重保序）：与 fw-token.py _sess_roots 同规则。"""
    defaults = tuple(
        os.path.normpath(os.path.expanduser(p)) for p in _DEFAULT_ROOTS
    )
    explicit = [os.environ.get("FW_DSH_HOME"), os.environ.get("DSH_HOME")]
    explicit = [os.path.normpath(p) for p in explicit if p]

    def _add(sp: str, seen: set, roots: List[str]) -> None:
        sp = os.path.normpath(sp)
        if sp not in seen and os.path.isdir(sp):
            seen.add(sp)
            roots.append(sp)

    seen: set = set()
    roots: List[str] = []
    if explicit:
        for p in explicit:
            _add(os.path.join(p, "sessions"), seen, roots)
        if all(p in defaults for p in explicit):
            for d in defaults:
                _add(os.path.join(d, "sessions"), seen, roots)
    else:
        for d in defaults:
            _add(os.path.join(d, "sessions"), seen, roots)
    return roots


def decode_dirname(name: str) -> str:
    """解码 dsh 会话目录名：非 ASCII 码点 ~HHHH（UTF-16 hex）、/ 编码为 -。

    会话目录名 = "--" + 会话 cwd 的编码路径 + "--"。与 fw-token.py 同规则。
    """
    out: List[str] = []
    i, n = 0, len(name)
    while i < n:
        ch = name[i]
        if ch == "~" and i + 5 <= n and all(c in "0123456789abcdefABCDEF" for c in name[i + 1:i + 5]):
            try:
                out.append(chr(int(name[i + 1:i + 5], 16)))
                i += 5
                continue
            except (ValueError, OverflowError):
                pass
        out.append(ch)
        i += 1
    return "".join(out)


def encode_task_dir(task_dir: str) -> str:
    """把任务目录按会话目录名同规则编码（/ → -），供前缀匹配。

    注意：目录名编码把 / 与 - 都映射为 -，存在固有歧义（任务A 与 任务A-b 的
    编码前缀相同）；查询侧用「前缀 + 结尾边界 + 时间窗」三重判定把误报压到
    实际碰撞概率以下（任务目录名均带日期后缀）。
    """
    return (task_dir or "").replace("/", "-").strip("-")


def module_seg_of(decoded_dirname: str) -> str:
    """从解码后的会话 cwd 目录名提取模块段：紧跟 modules 段之后的那一段。

    会话目录名形如 "--<task>-modules-m01-名称--"（/ 编码为 -）。
    无 modules 段 → ""（框架根级会话：planner / integration / 总检等）。
    """
    segs = [s for s in _MODULE_SEG_SPLIT.split(decoded_dirname) if s]
    for i, seg in enumerate(segs[:-1]):
        if seg == "modules":
            return segs[i + 1]
    return ""


class _FileEntry:
    """单个 session.jsonl.zstd 的索引条目（只存原始事实，不做聚合）。"""

    __slots__ = ("mtime_ns", "size", "decoded", "module_seg", "rows")

    def __init__(self, mtime_ns: int, size: int, decoded: str, module_seg: str,
                 rows: List[Tuple[int, int, int, int]]) -> None:
        self.mtime_ns = mtime_ns
        self.size = size
        self.decoded = decoded            # 解码后的 cwd 目录名（路径分隔符为 -）
        self.module_seg = module_seg      # modules 段后一段；"" = 根级会话
        # 逐条 usage 行：(time_ms, inputTokens, outputTokens, cacheReadTokens)。
        # 窗口过滤精确到行 → 同 task_dir 多 run 的时间窗上下界可精确切分。
        self.rows = rows


class SessionIndex:
    """全部会话文件的增量索引。后台线程定时 refresh，查询直接读。"""

    def __init__(self, refresh_interval: float = 2.0) -> None:
        self._interval = refresh_interval
        self._lock = threading.RLock()
        self._files: Dict[str, _FileEntry] = {}   # path → entry
        self._ready = False
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------- 生命周期 ===

    def start(self) -> None:
        """启动后台索引线程（首次全量建索引 + 定时增量刷新）。"""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="fwapi-session-index", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                # 索引线程绝不带崩 serve；下一轮重试。
                pass
            self._stop.wait(self._interval)

    def wait_ready(self, timeout: float = 60.0) -> bool:
        """阻塞等待首次索引完成（serve 启动后调用一次；超时返回 False 不抛异常）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._ready:
                    return True
            time.sleep(0.2)
        with self._lock:
            return self._ready

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    # ------------------------------------------------------------- 索引维护 ===

    def refresh(self) -> int:
        """增量刷新：stat 全部会话文件，仅重新解析新增/变化者。返回当前文件数。

        - 单文件解析失败 → 保留旧条目（若有），本轮跳过；run 进行中写一半的
          zstd 文件等 mtime 稳定后自愈，绝不把半截数据当全量。
        - 消失的文件从索引剔除。
        """
        changed = False
        seen_paths: set = set()
        for sess_root in sess_roots():
            for path in glob.glob(os.path.join(sess_root, "*", "session-*", "session.jsonl.zstd")):
                seen_paths.add(path)
                try:
                    st = os.stat(path)
                    sig = (st.st_mtime_ns, st.st_size)
                except OSError:
                    continue
                with self._lock:
                    old = self._files.get(path)
                if old is not None and (old.mtime_ns, old.size) == sig:
                    continue  # 未变化：零成本跳过（索引热路径）
                entry = self._parse_file(path, sig)
                if entry is None:
                    continue  # 解析失败：保留旧值，自愈待下次
                with self._lock:
                    self._files[path] = entry
                changed = True
        with self._lock:
            for gone in set(self._files) - seen_paths:
                del self._files[gone]
                changed = True
            self._ready = True
        _ = changed
        with self._lock:
            return len(self._files)

    def _parse_file(self, path: str, sig: Tuple[int, int]) -> Optional[_FileEntry]:
        """解析单个会话文件为 _FileEntry；解压/读失败返回 None（保留旧值自愈）。"""
        try:
            proc = subprocess.run(["zstd", "-dc", path], capture_output=True, timeout=60)
            if proc.returncode != 0:
                return None
            txt = proc.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return None
        dirname = path.split(os.sep + "sessions" + os.sep, 1)[-1].split(os.sep + "session-")[0]
        decoded = decode_dirname(dirname)
        rows: List[Tuple[int, int, int, int]] = []
        for line in txt.splitlines():
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
                u = (d.get("data") or {}).get("chunk") or {}
                usage = u.get("usage")
                if not isinstance(usage, dict):
                    continue
                # dsh 会话格式兼容：新版嵌套两层 {usage:{usage:{...}}}，旧版平铺。
                if "usage" in usage and isinstance(usage["usage"], dict):
                    usage = usage["usage"]
                t = d.get("time", 0)
                if not isinstance(t, (int, float)) or t <= 0:
                    continue
                rows.append((
                    int(t),
                    int(usage.get("inputTokens", 0) or 0),
                    int(usage.get("outputTokens", 0) or 0),
                    int(usage.get("cacheReadTokens", 0) or 0),
                ))
            except Exception:
                continue
        if not rows:
            # 无 usage 的会话也入索引（空事实），避免每轮反复重解析。
            return _FileEntry(sig[0], sig[1], decoded, module_seg_of(decoded), [])
        return _FileEntry(sig[0], sig[1], decoded, module_seg_of(decoded), rows)

    # ------------------------------------------------------------- 查询接口 ===

    def iter_entries(self) -> List[_FileEntry]:
        """当前全部索引条目快照（浅拷贝列表；entry 不可变，线程安全）。"""
        with self._lock:
            return list(self._files.values())


# 进程内单例（serve 启动时 start()；测试可自建实例注入路径环境）。
_index: Optional[SessionIndex] = None
_index_lock = threading.Lock()


def get_index() -> SessionIndex:
    """进程内共享索引单例（懒创建 + 自动启动后台线程）。"""
    global _index
    with _index_lock:
        if _index is None:
            _index = SessionIndex()
            _index.start()
        return _index


def reset_index() -> None:
    """重置单例（测试用：换隔离会话根环境后调用）。"""
    global _index
    with _index_lock:
        if _index is not None:
            _index.stop()
        _index = None
