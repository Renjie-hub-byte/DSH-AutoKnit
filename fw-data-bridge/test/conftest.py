"""共享测试工具：构造任务目录 / 驱动真实 HTTP 服务。

把本模块 src/ 加入 sys.path，使测试能 `import fwapi`，且不依赖安装。
"""
import json
import os
import sys
import threading
import uuid
from typing import Dict, List, Optional

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from fwapi.serve import _server_for

RUNS_FILE = os.path.join("总日志", "runs.json")
SNAPSHOT_FILE = os.path.join("总日志", "快照.json")


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch, tmp_path):
    """隔离注册表：默认指向不存在的临时路径，保证既有单快照回落测试不受宿主
    ~/.autoknit/runs.json 影响。注册表相关测试再显式覆盖 AUTOKNIT_RUNS_REGISTRY。
    """
    monkeypatch.setenv(
        "AUTOKNIT_RUNS_REGISTRY", str(tmp_path / "registry-isolated" / "runs.json")
    )


def make_registry(tmp_path, records: List[Dict]) -> str:
    """在临时目录写一个注册表文件（{"runs": [...]}），返回其绝对路径。

    调用方需用 AUTOKNIT_RUNS_REGISTRY 指向该路径（registry.read_records 才读到）。
    """
    reg_dir = tmp_path / f"registry-{uuid.uuid4().hex[:8]}"
    reg_dir.mkdir()
    path = reg_dir / "runs.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"runs": records}, fh, ensure_ascii=False)
    return str(path)


def make_task_dir(tmp_path, runs: List[Dict]) -> str:
    """在临时目录下构造一个任务目录并写入 runs.json，返回绝对路径。"""
    task_dir = tmp_path / f"task-{uuid.uuid4().hex[:8]}"
    task_dir.mkdir()
    log_dir = task_dir / "总日志"
    log_dir.mkdir()
    with open(log_dir / "runs.json", "w", encoding="utf-8") as fh:
        json.dump({"runs": runs}, fh, ensure_ascii=False)
    return str(task_dir)


def make_snapshot(tmp_path, snapshot: Dict) -> str:
    """在临时目录下构造一个任务目录并写入 总日志/快照.json，返回绝对路径。

    与 make_task_dir 互不冲突（写不同的约定文件），供 /api/runs 与 tree 测试使用。
    """
    task_dir = tmp_path / f"task-{uuid.uuid4().hex[:8]}"
    task_dir.mkdir()
    log_dir = task_dir / "总日志"
    log_dir.mkdir()
    with open(log_dir / "快照.json", "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False)
    return str(task_dir)


def make_archive(tmp_path, task_dir: str, run_ids: List[str]) -> str:
    """把指定 run_ids 写入任务目录的归档文件。"""
    log_dir = os.path.join(task_dir, "总日志")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "archived.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"archived": run_ids}, fh, ensure_ascii=False)
    return path


class Client:
    """对真实 fw-api serve 的迷你 HTTP 客户端（urllib，标准库）。"""

    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def get(self, endpoint: str):
        return self._request("GET", endpoint)

    def post(self, endpoint: str, payload: Optional[dict] = None):
        return self._request("POST", endpoint, payload)

    def delete(self, endpoint: str):
        return self._request("DELETE", endpoint)

    def _request(self, method: str, endpoint: str, payload: Optional[dict] = None):
        import urllib.error
        import urllib.request

        url = self.base + endpoint
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, json.loads(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            return exc.code, json.loads(body)


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    """启动一个真实服务（随机端口、无默认 task_dir），返回 (client, make_task_dir)。

    make_task_dir(runs) 已绑定 tmp_path，直接传 run 列表即可。
    """
    server = _server_for("", "127.0.0.1", 0)  # port 0 => 内核分配
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    client = Client(f"http://127.0.0.1:{port}")

    def make(runs: List[Dict], task_dir: Optional[str] = None) -> str:
        if task_dir:
            # 复用已存在的 task_dir，仅返回其路径
            return task_dir
        return make_task_dir(tmp_path, runs)

    yield client, make
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
