"""autoknit console_script —— 首次运行自动安装，之后转调框架 launcher。

pip install autoknit 之后：
  - 第一次运行任意子命令 → 从包内资源铺 ~/.autoknit/framework-v1，
    并执行框架自带 install.sh（venv + 按依赖顺序 editable 安装子包 +
    launcher + 数据桥 LaunchAgent），与手动安装用户得到完全一致的环境
  - 之后每次运行 → 直接转调框架 launcher（FW1 指向安装目录）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__

HOME = Path.home()
AK_HOME = HOME / ".autoknit"
FW_HOME = AK_HOME / "framework-v1"
VENV = AK_HOME / "venv"
VERSION_FILE = AK_HOME / ".autoknit-pkg-version"


def _pkg_framework() -> Path:
    return Path(__file__).resolve().parent / "framework"


def _install_framework() -> None:
    print(f"[autoknit] 首次安装（v{__version__}）：铺设框架到 {AK_HOME} …")
    AK_HOME.mkdir(parents=True, exist_ok=True)
    if FW_HOME.exists():
        shutil.rmtree(FW_HOME)
    shutil.copytree(_pkg_framework(), FW_HOME,
                    ignore=shutil.ignore_patterns("__pycache__"))
    # wheel 打包会丢可执行位，统一补回
    for p in FW_HOME.rglob("*"):
        if p.is_file() and (p.suffix == ".sh" or p.name in
                            ("autoknit", "fw-runner", "fw-env", "fw-env-bootstrap",
                             "fw-executor", "fw-executor.sh", "fw-auditor", "fw-auditor.sh",
                             "fw-panorama", "fw-trace", "fw-spawn", "fw-new", "fw-run",
                             "fw-status", "fw-token", "fw-scaffold", "run-bridge-demo",
                             "fw-merge", "fw-planonly", "fw-budget", "fw-integrate")):
            p.chmod(p.stat().st_mode | 0o111)
    # 交给框架自带 install.sh：venv + editable 子包（与手动安装完全一致，幂等）
    env = os.environ.copy()
    env["AUTOKNIT_HOME"] = str(AK_HOME)
    r = subprocess.run(["bash", str(FW_HOME / "install.sh")], env=env)
    if r.returncode != 0:
        sys.exit("[autoknit] install.sh 执行失败——见上方输出")
    VERSION_FILE.write_text(__version__, encoding="utf-8")
    print(f"[autoknit] ✅ 安装完成（v{__version__}）")


def main() -> None:
    if "--pkg-setup" not in sys.argv:
        marker = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.is_file() else ""
        if not FW_HOME.is_dir() or marker != __version__:
            _install_framework()
        elif not (VENV / "bin" / "python").exists():
            _install_framework()
    launcher = FW_HOME / "fw-tools" / "autoknit"
    env = os.environ.copy()
    env["FW1"] = str(FW_HOME)
    env["AUTOKNIT_HOME"] = str(AK_HOME)
    env["PATH"] = f"{VENV / 'bin'}:{env.get('PATH', '')}"
    os.execve("/bin/bash", ["/bin/bash", str(launcher)] + sys.argv[1:], env)


if __name__ == "__main__":
    main()
