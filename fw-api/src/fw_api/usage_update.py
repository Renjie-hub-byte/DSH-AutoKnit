# -*- coding: utf-8 -*-
"""m04 —— 会话数据变更事件推送 dsh.usage.update

事件驱动的会话 / 消耗数据文件变更监听，变更时触发一次 `dsh.usage.update` 推送。

设计要点（对齐任务书验收）：
  - 事件驱动，禁止轮询：底层用 macOS/BSD 内核文件系统通知 `select.kqueue` +
    EVFILT_VNODE，在 `kq.control` 上阻塞等待变更，绝无定时扫描文件逻辑。
  - 只转发变更信号：不做消耗汇总计算（归 m03）、不解析 / 修复 / 重写会话文件本体（只读）。
  - 推送不崩：on_change 回调由 watcher 统一 try/except 包裹；文件瞬时损坏 / 并发写入
    仅导致一次变更事件，任何异常都不会让监听循环崩溃。
  - 可测：on_change 由调用方注入，单测通过真实写文件即可观察到推送产生。

接口契约（contract.yaml）：R→F 广播 `dsh.usage.update`，method post。
payload 形如：
  {"path": "…", "event": "modified", "ts": 1699999999.123, "kind": "session"}
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

try:  # macOS / BSD
    import select

    _HAS_KQUEUE = hasattr(select, "kqueue")
except Exception:  # pragma: no cover
    _HAS_KQUEUE = False


class UsageUpdateError(Exception):
    """监听器初始化 / 运行失败（确定性错误，供调用方兜底）。"""


@dataclass
class UsageUpdateEvent:
    """一次文件变更信号。event ∈ {created, modified, deleted}。"""
    path: str
    event: str
    ts: float = field(default_factory=time.time)
    kind: str = "session"

    def to_dict(self) -> dict:
        return {"path": self.path, "event": self.event, "ts": self.ts, "kind": self.kind}


# 变更回调签名：Callable[[UsageUpdateEvent], None]
ChangeHandler = Callable[[UsageUpdateEvent], None]

# 默认匹配的会话 / 消耗数据文件形态
DEFAULT_PATTERNS = ("*.jsonl", "*.jsonl.zstd", "*.zstd", "*.json")


def _matches(name: str, patterns: Sequence[str]) -> bool:
    import fnmatch

    return any(fnmatch.fnmatch(name, p) for p in patterns)


class UsageChangeWatcher:
    """基于 kqueue(EVFILT_VNODE) 的会话数据文件变更监听器（事件驱动，非轮询）。

    用法：:

        w = UsageChangeWatcher(dir_path, on_change=handler)
        w.start()
        # ... 对数据文件写入 / 修改 ...
        w.stop()

    也可作为上下文管理器：``with UsageChangeWatcher(...) as w:``。
    """

    def __init__(
        self,
        watch_path: str,
        on_change: Optional[ChangeHandler] = None,
        patterns: Sequence[str] = DEFAULT_PATTERNS,
        debounce_ms: float = 60.0,
    ) -> None:
        if not _HAS_KQUEUE:
            raise UsageUpdateError("select.kqueue unavailable on this platform")
        if not os.path.isdir(watch_path):
            raise UsageUpdateError(f"not a directory: {watch_path}")
        self.watch_path = os.path.abspath(watch_path)
        self.patterns = tuple(patterns)
        self.debounce = debounce_ms / 1000.0
        self.on_change = on_change
        self._kq: Optional["select.kqueue"] = None
        self._dir_fd: Optional[int] = None
        self._fds: dict[int, str] = {}          # fd -> abs path
        self._last_push: dict[str, float] = {}  # path -> last push ts
        self._running = False
        self._poller: Optional["threading.Thread"] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        import threading

        if self._running:
            return
        self._kq = select.kqueue()
        self._dir_fd = os.open(self.watch_path, os.O_RDONLY)
        # 目录 fd：监听目录内容变更（新建 / 删除 / 重命名）
        self._add_kevent(self._dir_fd, _DIR_NOTIFY, is_dir=True)
        # 目录内现有匹配文件：逐个注册
        for f in self._scan_matching_files():
            self._register_file(f)
        self._running = True
        self._poller = threading.Thread(target=self._run, name="usage-update-watcher", daemon=True)
        self._poller.start()

    def stop(self) -> None:
        self._running = False
        # 唤醒阻塞中的 kq.control
        if self._kq is not None:
            try:
                self._kq.close()
            except Exception:
                pass
            self._kq = None
        if self._dir_fd is not None:
            try:
                os.close(self._dir_fd)
            except OSError:
                pass
            self._dir_fd = None
        for fd in list(self._fds):
            self._close_fd(fd)
        self._fds.clear()
        if self._poller is not None:
            self._poller.join(timeout=1.0)
            self._poller = None

    def __enter__(self) -> "UsageChangeWatcher":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # kqueue 注册
    # ------------------------------------------------------------------
    def _add_kevent(self, fd: int, fflags: int, is_dir: bool) -> None:
        ev = select.kevent(
            fd,
            filter=select.KQ_FILTER_VNODE,
            flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
            fflags=fflags,
        )
        self._kq.control([ev], 0)  # type: ignore[union-attr]

    def _register_file(self, path: str) -> None:
        if path in self._fds.values():
            return
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return  # 文件瞬时不可读：跳过，不崩
        self._fds[fd] = path
        self._add_kevent(fd, _FILE_NOTIFY, is_dir=False)

    def _close_fd(self, fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass
        self._fds.pop(fd, None)

    def _scan_matching_files(self) -> List[str]:
        out = []
        for name in os.listdir(self.watch_path):
            if not _matches(name, self.patterns):
                continue
            full = os.path.join(self.watch_path, name)
            if os.path.isfile(full):
                out.append(full)
        return out

    # ------------------------------------------------------------------
    # 主循环（阻塞等待内核事件）
    # ------------------------------------------------------------------
    def _run(self) -> None:
        kq = self._kq
        if kq is None:
            return
        # 每轮最多取 32 个事件；KQ_EV_CLEAR 自动清除标记，无需重注册
        max_events = 32
        while self._running:
            try:
                events = kq.control([], max_events, None)  # 阻塞：无定时扫描
            except (OSError, ValueError):
                break  # kq 已关闭 / 异常 -> 结束监听
            if not self._running:
                break
            for ev in events:
                fd = ev.ident
                if fd == self._dir_fd:
                    self._handle_dir_event(ev.fflags)
                elif fd in self._fds:
                    self._handle_file_event(self._fds[fd], ev.fflags)
                else:
                    continue

    def _handle_dir_event(self, fflags: int) -> None:
        # 目录内出现新建 / 删除 / 重命名 -> 重新扫描并注册新文件
        if fflags & _DIR_NOTIFY:
            existing = set(self._fds.values())
            for f in self._scan_matching_files():
                if f not in existing:
                    self._register_file(f)
                    self._emit(f, "created")
            # 目录内容变化也可能是某文件被写（NOTE_WRITE 会带上来），
            # 文件本身的 EVFILT_VNODE 已独立注册，无需重复处理。

    def _handle_file_event(self, path: str, fflags: int) -> None:
        if fflags & select.KQ_NOTE_DELETE:
            self._emit(path, "deleted")
            # 删除后 fd 失效，关闭并从注册表移除
            for fd, p in list(self._fds.items()):
                if p == path:
                    self._close_fd(fd)
            return
        if fflags & (select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB):
            if not os.path.exists(path):
                self._emit(path, "deleted")
                return
            self._emit(path, "modified")

    # ------------------------------------------------------------------
    # 推送
    # ------------------------------------------------------------------
    def _emit(self, path: str, event: str) -> None:
        now = time.time()
        # 同一文件在 debounce 窗口内的重复事件合并为一次推送（一次写入 -> 一次推送）
        if now - self._last_push.get(path, 0.0) < self.debounce:
            self._last_push[path] = now
            return
        self._last_push[path] = now
        if self.on_change is None:
            return
        try:
            self.on_change(UsageUpdateEvent(path=path, event=event))
        except Exception:
            # 推送不崩：回调异常不影响监听循环继续工作
            pass


# 文件 / 目录监听掩码
_FILE_NOTIFY = (
    select.KQ_NOTE_WRITE
    | select.KQ_NOTE_EXTEND
    | select.KQ_NOTE_ATTRIB
    | select.KQ_NOTE_DELETE
) if _HAS_KQUEUE else 0
_DIR_NOTIFY = (
    select.KQ_NOTE_WRITE
    | select.KQ_NOTE_DELETE
    | select.KQ_NOTE_RENAME
) if _HAS_KQUEUE else 0


def build_payload(path: str, event: str, ts: Optional[float] = None) -> dict:
    """构造 `dsh.usage.update` 推送载荷（R→F 广播）。"""
    e = UsageUpdateEvent(path=path, event=event, ts=ts if ts is not None else time.time())
    return e.to_dict()


__all__ = [
    "UsageUpdateError",
    "UsageUpdateEvent",
    "UsageChangeWatcher",
    "build_payload",
    "DEFAULT_PATTERNS",
]
