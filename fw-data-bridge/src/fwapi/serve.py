"""fwapi.serve —— fw-api serve JSON HTTP 数据桥。

用 Python 标准库 http.server 提供浏览器可调的 JSON 端点：

    GET  /api/tasks                    任务列表（按紧急度降序；归档 run 不展示）
    GET  /api/tasks/{run_id}           单 run 详情
    GET  /api/tasks/archived           已归档 run_id 集合
    POST /api/tasks/archive            归档一个 run_id（幂等，run_id 在 body）
    POST /api/tasks/{run_id}/archive   归档一个 run_id（幂等，run_id 在路径）
    GET  /api/usage                    消耗汇总（dsh.usage.summary）
    GET  /api/events                   任务状态增量更新轮询（dsh.task.update 桥接）
    GET  /api/health                   健康检查（恒 200）
    GET  /api/runs                     run 列表（注册表聚合多 run / 缺失回落单快照）
    GET  /api/runs/{run_id}            run 详情（按注册表定位 task_dir；未命中 null）
    GET  /api/runs/{run_id}/tree       执行树（modules/dependencies/per_module/split 子树/needs_human）
    POST /api/runs/{run_id}/archive    注册表幂等归档（同 DELETE），列表不再显示
    DELETE /api/runs/{run_id}/archive  注册表幂等归档（同 POST），列表不再显示

请求契约（对齐 contract.yaml fwapi.tasks.*）：
- task_dir：浏览器在 query 或 POST JSON body 中携带；未携带时回落服务端默认值
  （CLI --task-dir 或环境变量 AUTOKNIT_TASK_DIR）。
- 归档文件路径：<task_dir>/总日志/archived.json，可用 AUTOKNIT_ARCHIVE_FILE 覆盖。

确定性空降级：task_dir 无效 / 无活跃 run → 列表 []、详情 null、usage 全 0、events [],
HTTP 200，不抛异常。

错误码约定（统一 JSON 信封 {"error": code, "message": str}）：
- 404 not_found：未知端点/方法不匹配；400 bad_request：参数非法（如空 run_id）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import fwapi
from fwapi import registry as registry_source
from fwapi.dsh import events as event_source
from fwapi.dsh import reply as reply_source
from fwapi.dsh import task as task_source
from fwapi.dsh import usage as usage_source
from fwapi.storage import archive

ENV_TASK_DIR = "AUTOKNIT_TASK_DIR"

# 标准 JSON 响应头：杜绝 MIME 抖动，方便浏览器直接 fetch。
_JSON_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Cache-Control": "no-store",
}


def resolve_task_dir(req_task_dir: str, default_task_dir: str) -> str:
    """请求级 task_dir 优先，否则回落服务端默认（CLI/环境变量）。"""
    if req_task_dir:
        return req_task_dir
    if default_task_dir:
        return default_task_dir
    return ""


def _query_params(path: str) -> Dict[str, str]:
    """解析 URL query 为 {k: v}（多值取最后一个）。"""
    parsed = urlsplit(path)
    return {k: vs[-1] for k, vs in parse_qs(parsed.query).items()}


def _parse_body_json(body: bytes) -> Dict[str, Any]:
    """解析 POST JSON body；空体/非法 JSON 返回 {}（绝不抛异常）。"""
    if not body:
        return {}
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def route(method: str, path: str) -> Tuple[str, Optional[str]]:
    """路由到 (endpoint, run_id)；无法匹配返回 (None, None)。"""
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] != "api":
        return (None, None)

    # CORS 预检（OPTIONS）：浏览器跨源请求（data-bridge 连 GET 都带 Content-Type:
    # application/json → 触发预检）需对任何 /api 路径统一放行，否则新 runs 命名空间
    # 的 GET 预检会 404 → 面板 "Failed to fetch"。修复 2026-08-29。
    if method == "OPTIONS":
        return ("options", None)

    # 系统级端点（位于 /api/tasks 命名空间之外，仅 GET）
    if len(parts) == 2 and parts[1] in ("usage", "health", "events"):
        return (parts[1], None) if method == "GET" else (None, None)

    # runs 命名空间（只读 timeline/tree + 决策 reply；在 tasks 判定之前，避免误落入 tasks 分支）
    if len(parts) >= 2 and parts[1] == "runs":
        sub = parts[2:]
        if not sub:
            return ("runs", None) if method == "GET" else (None, None)
        if len(sub) == 1 and method == "GET":
            return ("run_detail", sub[0])
        if len(sub) == 2:
            run_id = sub[0]
            if method == "GET" and sub[1] == "tree":
                return ("run_tree", run_id)
            if method == "GET" and sub[1] == "timeline":
                return ("run_timeline", run_id)
            if method == "GET" and sub[1] == "usage":
                return ("run_usage", run_id)
            if method == "POST" and sub[1] == "reply":
                return ("run_reply", run_id)
            if method in ("POST", "DELETE") and sub[1] == "archive":
                return ("run_archive", run_id)  # POST/DELETE 均幂等归档
        return (None, None)

    if len(parts) < 2 or parts[1] != "tasks":
        return (None, None)
    sub = parts[2:]

    if method == "GET":
        if not sub:
            return ("tasks", None)
        if sub == ["archived"]:
            return ("archived", None)
        if len(sub) == 1:
            return ("detail", sub[0])

    if method == "POST":
        if sub == ["archive"]:
            return ("archive", None)  # run_id 走 body
        if len(sub) == 2 and sub[1] == "archive":
            return ("archive", sub[0])  # run_id 走路径

    if method == "OPTIONS":
        return ("options", None)

    return (None, None)


class FwApiHandler(BaseHTTPRequestHandler):
    """处理 /api/tasks* JSON 端点。"""

    server_version = "fwapi-serve"
    sys_version = ""

    # ---- 服务端配置（由 serve() 注入） ----
    default_task_dir: str = ""

    # ---- 生命周期 ----
    def log_message(self, fmt: str, *args: Any) -> None:
        # 保持简洁的访问日志，避免无关噪声；生产可替换为结构化日志。
        print("[fwapi] " + (fmt % args), file=sys.stderr)

    # ---- 通用 JSON 应答 ----
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        for key, value in _JSON_HEADERS.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, code: str, message: str) -> None:
        """统一错误信封：{"error": code, "message": str}。"""
        self._send_json({"error": code, "message": message}, status=status)

    # ---- HTTP 入口 ----
    def _handle(self) -> None:
        endpoint, run_id = route(self.command, urlsplit(self.path).path)
        if endpoint is None:
            self._send_error(
                404,
                "not_found",
                f"unknown endpoint: {self.command} {self.path}",
            )
            return

        params = _query_params(self.path)
        if self.command == "POST":
            body = _parse_body_json(self._read_body())
            params.update(body)

        task_dir = resolve_task_dir(params.get("task_dir", ""), self.default_task_dir)

        if endpoint == "tasks":
            self._handle_tasks(task_dir)
        elif endpoint == "detail":
            self._handle_detail(task_dir, run_id or "")
        elif endpoint == "archived":
            self._handle_archived(task_dir)
        elif endpoint == "archive":
            # run_id 优先取路径（/api/tasks/{run_id}/archive），否则回落 body。
            self._handle_archive(task_dir, run_id or params.get("run_id", ""))
        elif endpoint == "usage":
            self._handle_usage(task_dir)
        elif endpoint == "events":
            self._handle_events(task_dir, _query_params(self.path).get("since", "0"))
        elif endpoint == "health":
            self._handle_health()
        elif endpoint == "run_archive":
            self._handle_run_archive(run_id or "")
        elif endpoint == "runs":
            self._handle_runs(task_dir)
        elif endpoint == "run_detail":
            self._handle_run_detail(task_dir, run_id or "")
        elif endpoint == "run_tree":
            self._handle_run_tree(task_dir, run_id or "")
        elif endpoint == "run_timeline":
            self._handle_run_timeline(task_dir, run_id or "")
        elif endpoint == "run_usage":
            self._handle_run_usage(task_dir, run_id or "")
        elif endpoint == "run_reply":
            self._handle_run_reply(task_dir, run_id or "", params)
        elif endpoint == "options":
            self._send_json({"ok": True})

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(length)

    # ---- 各端点 ----
    def _handle_tasks(self, task_dir: str) -> None:
        tasks = task_source.list_tasks(task_dir)
        archived_ids = archive.list_archived(task_dir)
        visible = [t for t in tasks if t["run_id"] not in archived_ids]
        self._send_json(visible)

    def _handle_detail(self, task_dir: str, run_id: str) -> None:
        run = task_source.get_task_detail(task_dir, run_id)
        self._send_json(run)  # 未命中返回 JSON null

    def _handle_archived(self, task_dir: str) -> None:
        self._send_json(archive.list_archived(task_dir))

    def _handle_archive(self, task_dir: str, run_id: str) -> None:
        result = archive.archive_run(task_dir, run_id)
        status = 200 if result["ok"] else 400
        self._send_json(result, status=status)

    def _handle_usage(self, task_dir: str) -> None:
        """GET /api/usage：消耗汇总，目录缺失时全 0。"""
        self._send_json(usage_source.summary(task_dir))

    def _handle_events(self, task_dir: str, since_raw: str) -> None:
        """GET /api/events：先探测增量，再返回 seq > since 的事件列表。

        长轮询（2026-09-01）：带 wait=N（秒，上限 60）时，无新事件则服务端
        hold 请求，每 0.5s 探测一次（快照 diff + dispatch.jsonl 增量行 + 注册表
        diff），任一出现新事件立即返回——模块/轮次结束毫秒级到达面板；空闲时
        每 wait 秒才一个心跳请求。wait=0 保持旧的立即返回语义（兼容旧客户端）。
        """
        try:
            since = int(since_raw)
        except (TypeError, ValueError):
            since = 0
        try:
            wait = float(_query_params(self.path).get("wait", "0") or 0)
        except (TypeError, ValueError):
            wait = 0.0
        wait = max(0.0, min(wait, 60.0))
        deadline = time.time() + wait
        while True:
            event_source.check_task_updates(task_dir)
            event_source.check_dispatch_events(task_dir)
            event_source.check_runs_updates()
            events = event_source.events_since(task_dir, since)
            if events or wait <= 0 or time.time() >= deadline:
                self._send_json(events)
                return
            time.sleep(0.5)

    def _handle_health(self) -> None:
        """GET /api/health：健康检查，恒 200（不依赖 task_dir）。"""
        self._send_json(
            {
                "status": "ok",
                "service": "fwapi",
                "version": getattr(fwapi, "__version__", "0.0.0"),
                "at": time.time(),
            }
        )

    def _handle_runs(self, task_dir: str) -> None:
        """GET /api/runs：注册表聚合多 run；注册表缺失回落单 task_dir；目录缺失确定性 []。"""
        self._send_json(task_source.list_runs(task_dir))

    def _handle_run_detail(self, task_dir: str, run_id: str) -> None:
        """GET /api/runs/{id}：按注册表定位 task_dir 取详情；未命中/目录无效确定性 JSON null。"""
        self._send_json(task_source.get_run_detail(task_dir, run_id))

    def _handle_run_archive(self, run_id: str) -> None:
        """POST/DELETE /api/runs/{id}/archive：注册表幂等标记 archived。

        响应恒为契约 {run_id, status, ok}；成功 200，空 run_id/未命中/写失败 400。
        """
        result = registry_source.archive_run(run_id)
        status = 200 if result["ok"] else 400
        self._send_json(result, status=status)

    def _handle_run_tree(self, task_dir: str, run_id: str) -> None:
        """GET /api/runs/{run_id}/tree：执行树；未命中确定性 JSON null。"""
        self._send_json(task_source.get_run_tree(task_dir, run_id))

    def _handle_run_timeline(self, task_dir: str, run_id: str) -> None:
        """GET /api/runs/{run_id}/timeline：dispatch.jsonl 事件流按 seq 升序；缺失确定性 []。"""
        self._send_json(task_source.get_run_timeline(task_dir, run_id))

    def _handle_run_usage(self, task_dir: str, run_id: str) -> None:
        """GET /api/runs/{run_id}/usage：复用 fw-token.py 聚合会话；空降级确定性结构。

        契约 {run, per_module, no_split}；run 未命中/目录缺失/无拆分数据仍 HTTP 200。
        """
        self._send_json(usage_source.run_usage(task_dir, run_id))

    def _handle_run_reply(self, task_dir: str, run_id: str, params: Dict[str, Any]) -> None:
        """POST /api/runs/{run_id}/reply：写 needs_human/reply.md；成功 200 / 失败 400。

        响应恒为契约 {success, detail}（确定性 JSON）；失败用 detail 描述原因。
        """
        result = reply_source.reply(task_dir, run_id, params)
        status = 200 if result["success"] else 400
        self._send_json(result, status=status)

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._handle()


def _server_for(default_task_dir: str, host: str, port: int) -> ThreadingHTTPServer:
    FwApiHandler.default_task_dir = default_task_dir
    server = ThreadingHTTPServer((host, port), FwApiHandler)
    return server


def serve(host: str = "127.0.0.1", port: int = 8765, task_dir: str = "") -> None:
    """启动 fw-api serve，阻塞直到被中断。

    启动时读注册表路径（跨模块共享 runs_registry）：注册表有记录时 /api/runs 聚合多 run，
    否则确定性回落单 task_dir。请求处理时实时读取注册表文件（供 fw-run.sh 登记/更新反映）。

    会话索引（2026-09-01 数据流重写）：启动即建后台索引线程（全量一次 +
    定时增量），/api/runs/{id}/usage 毫秒级直查，不再每次请求全扫会话。
    首次索引完成前 usage 查询确定性空降级（不阻塞监听）。
    """
    default_task_dir = task_dir or os.environ.get(ENV_TASK_DIR, "")
    registry_path = registry_source.resolve_registry_path()
    from fwapi.dsh.session_index import get_index
    idx = get_index()
    idx.start()
    httpd = _server_for(default_task_dir, host, port)
    print(
        f"[fwapi] serving on http://{host}:{port} "
        f"(task_dir={default_task_dir or '<per-request>'})"
    )
    print(f"[fwapi] runs_registry={registry_path}")
    print(f"[fwapi] session index building in background ({idx.ready and 'ready' or 'pending'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        idx.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fw-api",
        description="AutoKnit 面板数据桥：把 dsh.task 暴露成浏览器可调 JSON 端点。",
    )
    sub = parser.add_subparsers(dest="command", metavar="{serve}")
    serve_p = sub.add_parser("serve", help="启动 fw-api 数据桥 HTTP 服务")
    serve_p.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    serve_p.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    serve_p.add_argument(
        "--task-dir",
        default="",
        help="默认任务目录（请求未携带 task_dir 时使用；也可用 %s 环境变量）" % ENV_TASK_DIR,
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        # 裸 `python -m fwapi`：按 serve 默认值启动。
        host, port, task_dir = "127.0.0.1", 8765, os.environ.get(ENV_TASK_DIR, "")
    else:
        host, port, task_dir = args.host, args.port, args.task_dir
    serve(host=host, port=port, task_dir=task_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
