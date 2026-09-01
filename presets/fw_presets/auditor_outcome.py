"""auditor 机器可解析判定：JSON（canonical）+ AUDIT_RESULT 四段行（日志投影）。

- validate_outcome(d)      -> (ok, errors)   按 protocol/auditor-outcome.schema.json +
                                               语义检查（verdict/root 枚举、confidence 范围、
                                               blocker 禁含 |、block 时 root 非空）
- load_outcome(path)       -> dict           读取并校验 tmp/auditor-outcome.json
- parse_four_segment_line(line) -> dict|None 解析 AUDIT_RESULT|... 单行
- build_four_segment_line(d)    -> str        由 outcome 生成四段行
- extract_four_segment_line(text) -> dict|None 从报告文本提取第一行四段
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ROOT_CAUSES, VERDICTS

_SCHEMA_REL = Path(__file__).resolve().parent.parent / "protocol" / "auditor-outcome.schema.json"
_LINE_PREFIX = "AUDIT_RESULT|"
_LINE_RE = re.compile(r"^AUDIT_RESULT\|(.+)$")


def _schema() -> Optional[Dict[str, Any]]:
    """加载 JSON Schema（jsonschema 存在时用于结构校验；缺失时降级为字段检查）。"""
    try:
        import json
        p = _SCHEMA_REL
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except Exception:
        return None


def validate_outcome(d: Any) -> tuple[bool, List[str]]:
    """校验 auditor 判定对象。返回 (ok, errors)。

    四段必填：verdict / blocker / root / confidence；并做枚举与范围语义检查。
    """
    errors: List[str] = []
    if not isinstance(d, dict):
        return False, ["outcome 必须是 JSON 对象（dict）"]

    for seg in ("verdict", "blocker", "root", "confidence"):
        if seg not in d:
            errors.append(f"缺少四段之一: {seg}")

    if "verdict" in d and d["verdict"] not in VERDICTS:
        errors.append(f"verdict 非法: {d['verdict']!r}（合法: {VERDICTS}）")
    if "root" in d and d["root"] not in ROOT_CAUSES:
        errors.append(f"root 非法: {d['root']!r}（合法: {ROOT_CAUSES}）")
    if "confidence" in d:
        try:
            c = float(d["confidence"])
            if not (0.0 <= c <= 1.0):
                errors.append(f"confidence 超出 0-1: {c}")
        except (TypeError, ValueError):
            errors.append(f"confidence 非数字: {d['confidence']!r}")
    if "blocker" in d and "|" in str(d.get("blocker", "")):
        errors.append("blocker 禁含 '|'（与四段行分隔符冲突；canonical 用 JSON 仍应规避）")
    if d.get("verdict") == "block" and not str(d.get("root") or "").strip():
        errors.append("block 时 root 必须非空（self|upstream|contract）")

    # JSON Schema 结构校验（若 jsonschema 可用）
    schema = _schema()
    if schema is not None:
        try:
            import jsonschema
        except ImportError:
            jsonschema = None  # type: ignore[assignment]
        if jsonschema is not None:
            try:
                jsonschema.validate(instance=d, schema=schema)
            except jsonschema.ValidationError as e:
                errors.append(f"schema: {e.message}")

    return (not errors), errors


def load_outcome(path: str | Path) -> Dict[str, Any]:
    """读取并校验 tmp/auditor-outcome.json；校验失败抛 ValueError（带全部错误）。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"auditor-outcome.json 不存在: {p}")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"auditor-outcome.json 不是合法 JSON: {e}") from e
    ok, errors = validate_outcome(d)
    if not ok:
        raise ValueError(f"auditor-outcome.json 校验失败: {errors}")
    return d


def parse_four_segment_line(line: str) -> Optional[Dict[str, str]]:
    """解析单行四段：AUDIT_RESULT|verdict=..|blocker=..|root=..|confidence=..
    返回 {verdict, blocker, root, confidence}；不合格式返回 None。
    """
    text = line.strip()
    if not text.startswith(_LINE_PREFIX):
        return None
    body = text[len(_LINE_PREFIX):]
    out: Dict[str, str] = {}
    for seg in body.split("|"):
        seg = seg.strip()
        if not seg:
            continue
        if "=" not in seg:
            return None
        key, _, value = seg.partition("=")
        key = key.strip()
        value = value.strip()
        if key not in ("verdict", "blocker", "root", "confidence"):
            return None
        if key in out:  # 重复段
            return None
        out[key] = value
    if set(out) != {"verdict", "blocker", "root", "confidence"}:
        return None
    return out


def build_four_segment_line(d: Mapping[str, Any]) -> str:
    """由 outcome（dict）生成四段行；段值不手工转义（blocker 禁含 | 由 validate 保证）。"""
    def _s(key: str) -> str:
        v = d.get(key, "")
        return "|" + key + "=" + (str(v) if v is not None else "")
    return "AUDIT_RESULT" + _s("verdict") + _s("blocker") + _s("root") + _s("confidence")


def extract_four_segment_line(text: str) -> Optional[Dict[str, str]]:
    """从报告文本提取第一行四段（逐行找 AUDIT_RESULT| 开头且可解析）。"""
    for line in text.splitlines():
        if line.strip().startswith(_LINE_PREFIX):
            parsed = parse_four_segment_line(line)
            if parsed is not None:
                return parsed
    return None
