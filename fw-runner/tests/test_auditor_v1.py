"""F1-F3：bin/fw-auditor.sh 三态判定 + 计数字段 + 文本兜底 partial。

覆盖（本轮主目标，只测真实脚本子进程，不 mock 脚本内部）：
- F1 判定三态（pass/partial/block）：demo 模式快速冒烟三种判定
- F2 计数字段写入：audit-result.json 的 passed_count/total_count/remaining_items
  被读入并写入 auditor-outcome.json（真实 dsh 路径，fake DSH_BIN 只写 json）
- F3 文本兜底解析 partial + 计数：fake dsh 只写文本、不写 json → 解析出 partial + 计数
- 边界：文本兜底 pass/block、超时/无输出 → block（回归 F1）
契约：outcome 经 DriverOutcome.from_mapping + runner._valid 三态校验可被接受（round_005 使能）。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
AUDITOR = BIN / "fw-auditor.sh"


def _write_module(tmp_path: Path, mid: str, acceptance: list,
                  artifacts: list, done: list, todo: list | None = None) -> Path:
    mdir = tmp_path / mid
    (mdir / "src").mkdir(parents=True)
    (mdir / f"任务书-{mid}.yaml").write_text(
        "task:\n  name: 测试\nmodules:\n"
        + f"  - id: {mid}\n    name: 测试模块\n    objective: 目标\n"
        + "    acceptance:\n"
        + "".join(f"      - {a}\n" for a in acceptance),
        encoding="utf-8")
    for a in artifacts:
        (mdir / "src" / a).write_text("content", encoding="utf-8")
    if not artifacts:
        (mdir / "src" / ".gitkeep").write_text("", encoding="utf-8")
    review = ["# REVIEW", "## 已做"]
    review += [f"- {d}" for d in done] if done else ["- （占位）"]
    review.append("## 待办")
    review += [f"- [ ] {t}" for t in (todo or [])]
    (mdir / "REVIEW.md").write_text("\n".join(review) + "\n", encoding="utf-8")
    return mdir


def _write_fake_dsh(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    p.chmod(0o755)
    return str(p)


def _run_auditor(mdir: Path, *, mode: str, dsh_bin: str | None = None,
                 timeout: int = 90) -> dict:
    env = dict(os.environ)
    env.update({
        "MODULE_DIR": str(mdir),
        "TASK_ROOT": str(mdir),
        "ROUND": "1",
        "EXECUTOR_ID": "E1",
        "MODE": "speed_first",
        "FW_AUDITOR_MODE": mode,
        "FW_AUDITOR_TIMEOUT": str(timeout),
    })
    if dsh_bin:
        env["DSH_BIN"] = dsh_bin
    proc = subprocess.run(["bash", str(AUDITOR)], cwd=str(mdir), env=env,
                          capture_output=True, text=True, encoding="utf-8")
    out = mdir / "tmp" / "auditor-outcome.json"
    assert out.is_file(), f"无 auditor-outcome.json rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))


# ---------- F1：三态判定（demo 模式） ----------

def test_demo_partial_three_state(tmp_path):
    mdir = _write_module(tmp_path, "m01", ["项A", "项B", "项C"],
                         artifacts=["main.py"], done=["项A"], todo=["项B", "项C"])
    res = _run_auditor(mdir, mode="demo")
    assert res["verdict"] == "partial"
    assert res["passed_count"] == 2          # 1 产物 + 1 已做
    assert res["total_count"] == 3
    assert res["root"] == ""


def test_demo_pass(tmp_path):
    mdir = _write_module(tmp_path, "m01", ["项A", "项B", "项C"],
                         artifacts=["a.py", "b.py", "c.py"],
                         done=["项A", "项B", "项C"])
    res = _run_auditor(mdir, mode="demo")
    assert res["verdict"] == "pass"
    assert res["passed_count"] == res["total_count"] == 3


def test_demo_block(tmp_path):
    mdir = _write_module(tmp_path, "m01", ["项A", "项B", "项C"], artifacts=[], done=[])
    res = _run_auditor(mdir, mode="demo")
    assert res["verdict"] == "block"
    assert res["root"] == "self"
    assert res["passed_count"] == 0
    assert res["total_count"] == 3


# ---------- F2：计数字段写入（真实 dsh 路径，fake DSH_BIN 只写 json） ----------

def test_dsh_json_counts_written(tmp_path):
    mdir = _write_module(tmp_path, "m01", ["a", "b", "c", "d"],
                         artifacts=["main.py"], done=["项A"])
    fake = _write_fake_dsh(tmp_path, "fake_json.sh", '''
cat > tmp/audit-result.json <<'EOF'
{"verdict": "partial", "root": "", "confidence": 0.8, "reason": "部分通过",
 "passed_count": 2, "total_count": 4, "remaining_items": ["API", "测试"]}
EOF
echo "JSON_OK"
''')
    res = _run_auditor(mdir, mode="dsh", dsh_bin=fake)
    assert res["verdict"] == "partial"
    assert res["passed_count"] == 2
    assert res["total_count"] == 4
    assert res["remaining_items"] == ["API", "测试"]
    assert res["confidence"] == 0.8


# ---------- F3：文本兜底解析 partial + 计数 ----------

def test_dsh_text_fallback_partial(tmp_path):
    mdir = _write_module(tmp_path, "m01", ["a", "b", "c", "d", "e"],
                         artifacts=["main.py"], done=["项A"])
    fake = _write_fake_dsh(tmp_path, "fake_text.sh", '''
cat <<'EOF'
验收结论：3/5 通过，部分满足。
剩余：项D
剩余：项E
confidence: 0.75
EOF
''')
    res = _run_auditor(mdir, mode="dsh", dsh_bin=fake)
    assert res["verdict"] == "partial"
    assert res["passed_count"] == 3
    assert res["total_count"] == 5
    assert res["remaining_items"] == ["项D", "项E"]
    assert res["confidence"] == 0.75


def test_dsh_text_fallback_pass(tmp_path):
    mdir = _write_module(tmp_path, "m01", ["a", "b"],
                         artifacts=["main.py"], done=["项A"])
    fake = _write_fake_dsh(tmp_path, "fake_pass.sh", '''
cat <<'EOF'
验收结论：**通过**，全部通过，4/4 项满足。
confidence: 0.9
EOF
''')
    res = _run_auditor(mdir, mode="dsh", dsh_bin=fake)
    assert res["verdict"] == "pass"
    assert res["passed_count"] == res["total_count"] == 4


def test_dsh_text_fallback_block(tmp_path):
    mdir = _write_module(tmp_path, "m01", ["a", "b"],
                         artifacts=["main.py"], done=["项A"])
    fake = _write_fake_dsh(tmp_path, "fake_block.sh", '''
cat <<'EOF'
验收失败：B 不通过，缺实现。
EOF
''')
    res = _run_auditor(mdir, mode="dsh", dsh_bin=fake)
    assert res["verdict"] == "block"
    assert res["root"] == "self"


# ---------- 边界：无输出/超时 → block（回归 F1 兜底） ----------

def test_dsh_timeout_fallback_block(tmp_path):
    mdir = _write_module(tmp_path, "m01", ["a", "b"],
                         artifacts=["main.py"], done=["项A"])
    fake = _write_fake_dsh(tmp_path, "fake_empty.sh", "sleep 1\n")
    res = _run_auditor(mdir, mode="dsh", dsh_bin=fake)
    assert res["verdict"] == "block"
    assert res["root"] == "self"
    assert res["passed_count"] == 0
    assert res["total_count"] == 0
