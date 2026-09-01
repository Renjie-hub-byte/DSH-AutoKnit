#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fw-doctor.py —— AutoKnit 环境体检：缺什么、怎么装，人话报错。

检查项（全部只读，不修改任何配置）：
  1. Python 3.11+（含 yaml）
  2. dsh 二进制（DeepSeek Harness）
  3. dsh 凭据（~/.fw-dsh/credentials* 或环境变量 DEEPSEEK_API_KEY）
  4. 模型路由（dsh settings.yaml 的 provider/model 可解析）
  5. fw-api 数据桥连通性（:8765，dashboard 数据源）
  6. 防睡眠（macOS caffeinate 可用性）

用法:
  fw-doctor            # 全量体检，输出人话报告
  fw-doctor --json     # 机器可读（{item, ok, detail} 数组）
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
FW_DSH_HOME = Path(os.environ.get("FW_DSH_HOME") or os.environ.get("DSH_HOME") or HOME / ".fw-dsh")
FW1 = Path(os.environ.get("FW1") or HOME / "projects-hold" / "projects" / "dsh-workflow" / "framework-v1")


def _py_ok() -> tuple:
    if sys.version_info < (3, 11):
        return False, f"Python {sys.version_info.major}.{sys.version_info.minor}（需 3.11+）"
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False, f"Python {sys.version_info.major}.{sys.version_info.minor} 缺 pyyaml（pip install pyyaml）"
    return True, f"Python {sys.version_info.major}.{sys.version_info.minor} + pyyaml"


def _dsh_ok() -> tuple:
    dsh = shutil.which("dsh")
    if dsh:
        return True, f"dsh: {dsh}"
    for cand in (
        HOME / "Library" / "Application Support" / "QClaw" / "npm-global" / "bin" / "dsh",
        HOME / ".npm-global" / "bin" / "dsh",
        "/usr/local/bin/dsh",
    ):
        if cand.exists():
            return True, f"dsh: {cand}"
    return False, "dsh 未找到（DeepSeek Harness）。安装见 docs/quickstart.md 附录 A"


def _cred_ok() -> tuple:
    # 1) 环境变量直供（fw-run 同款）
    if os.environ.get("DEEPSEEK_API_KEY"):
        return True, "凭据：环境变量 DEEPSEEK_API_KEY"
    # 2) dsh 凭据文件（profiles/headless 或根级）
    for p in (
        FW_DSH_HOME / "credentials.yaml",
        FW_DSH_HOME / "credentials.yml",
        FW_DSH_HOME / "credentials.json",
        FW_DSH_HOME / "profiles" / "headless" / "credentials.yaml",
        FW_DSH_HOME / "profiles" / "headless" / "credentials.yml",
    ):
        if p.exists():
            return True, f"凭据：{p.relative_to(HOME) if str(p).startswith(str(HOME)) else p}"
    # 3) dsh login 状态（~/.dsh 或 ~/.fw-dsh 的 profile 目录）
    for root in (FW_DSH_HOME, HOME / ".dsh"):
        if (root / "profiles" / "headless").is_dir():
            return True, f"凭据：dsh profile 就绪（{root.name}/profiles/headless）"
    return False, "dsh 凭据未就绪：设置 DEEPSEEK_API_KEY 或 dsh login（docs/quickstart.md）"


def _model_ok() -> tuple:
    settings = FW_DSH_HOME / "settings.yaml"
    if settings.exists():
        try:
            import yaml
            doc = yaml.safe_load(settings.read_text(encoding="utf-8"))
            mdl = (doc or {}).get("agent-default-model") or {}
            if mdl.get("provider") and mdl.get("model"):
                return True, f"模型路由: {mdl['provider']} / {mdl['model']}"
        except Exception:
            pass
    return True, "模型路由: dsh 默认（settings.yaml 未覆盖，走 dsh 默认模型）"


def _bridge_ok() -> tuple:
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/api/runs", timeout=3) as r:
            return r.status == 200, f"数据桥 :8765 连通（HTTP {r.status}）"
    except Exception as e:
        return False, f"数据桥 :8765 未运行（{type(e).__name__}）。autoknit run 会自动拉起；或 launchctl kickstart -k gui/{os.getuid()}/com.autoknit.fwapi-bridge"


def _caffeinate_ok() -> tuple:
    if sys.platform != "darwin":
        return True, "非 macOS，无需防睡眠"
    c = shutil.which("caffeinate")
    return bool(c), "caffeinate 可用（防睡眠）" if c else "caffeinate 未找到（macOS 应自带，/usr/bin/caffeinate）"


def run(json_out: bool = False) -> list:
    checks = [
        ("python", _py_ok),
        ("dsh", _dsh_ok),
        ("credentials", _cred_ok),
        ("model", _model_ok),
        ("bridge", _bridge_ok),
        ("caffeinate", _caffeinate_ok),
    ]
    results = []
    for name, fn in checks:
        ok, detail = fn()
        results.append({"item": name, "ok": ok, "detail": detail})
    if json_out:
        return results
    print("AutoKnit doctor —— 环境体检")
    print("-" * 70)
    all_ok = True
    for r in results:
        mark = "✅" if r["ok"] else "❌"
        all_ok = all_ok and r["ok"]
        print(f"  {mark} {r['item']:<13} {r['detail']}")
    print("-" * 70)
    if all_ok:
        print("全部就绪，可以开跑：autoknit plan-only <任务目录> 或 autoknit run")
    else:
        print("有项未就绪，按上面 ❌ 提示补齐后重跑 autoknit doctor")
    return results


if __name__ == "__main__":
    run(json_out="--json" in sys.argv)
