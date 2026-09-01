"""pending —— 读取 human_pending（待决策信息）。

数据来源（可叠加）：
  * snapshot.needs_human（runner 在快照里标记的待人类处理项）；
  * 可选文件 human_pending.json（总日志/human_pending.json，或 FW_HUMAN_PENDING 指定）。

归一化为列表项 shape：{type, text, choices, module, seq}。只读不写。
"""

import json
import os


def _normalize_item(item, default_type="decision"):
    if item is None:
        return None
    if isinstance(item, str):
        return {"type": "text", "text": item, "choices": [], "module": None, "seq": None}
    if not isinstance(item, dict):
        return None
    text = item.get("text")
    if text is None:
        text = item.get("pending") or item.get("question") or ""
    return {
        "type": str(item.get("type") or default_type),
        "text": str(text or ""),
        "choices": list(item.get("choices", []) or []),
        "module": item.get("module"),
        "seq": item.get("seq"),
    }


def _from_file(path):
    """读取 human_pending 文件；不存在/非法返回 []。"""
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, ValueError, OSError):
        return []
    if isinstance(data, dict):
        if isinstance(data.get("pending"), list):
            data = data["pending"]
        else:
            data = [data]
    if not isinstance(data, list):
        return []
    return [_normalize_item(x) for x in data if _normalize_item(x) is not None]


def read_human_pending(snapshot, pending_path=None):
    """拼 human_pending 列表：needs_human + 可选文件（文件优先去重由调用方决定）。"""
    items = []
    if snapshot is not None:
        needs = snapshot.needs_human
        if isinstance(needs, list):
            items.extend(_normalize_item(x) for x in needs if _normalize_item(x) is not None)
    items.extend(_from_file(pending_path))
    return items
