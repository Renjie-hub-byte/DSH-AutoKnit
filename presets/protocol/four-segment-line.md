# 四段行规范（AUDIT_RESULT 行）—— 机器可解析的日志友好投影

> 配套：`protocol/auditor-outcome.schema.json`（canonical 机器判定，JSON 文件）；
> 本文件定义**日志/聊天记录里的单行四段投影**，让 auditor 的人工语言报告末尾也可以被 grep / 解析。

## 格式

```
AUDIT_RESULT|verdict=<pass|block>|blocker=<无|具体文本>|root=<空|self|upstream|contract>|confidence=<0-1>
```

- 固定前缀 `AUDIT_RESULT`，段间以 `|` 分隔，段内为 `key=value`；
- 四段与 `auditor-outcome.json` 的 `verdict / blocker / root / confidence` 一一对应（判定 / blocker / 根因 / 置信度）；
- `blocker` 值**禁含 `|`**（用 `，`/`；`/空格 分隔长文本；canonical 全文以 JSON 为准）；
- `root` 合法值：`self | upstream | contract`（block 时必填）；pass 时为空；
- `confidence` 为 0-1 小数。

## 示例

```
AUDIT_RESULT|verdict=pass|blocker=|root=|confidence=0.95
AUDIT_RESULT|verdict=block|blocker=验收项2：cleaned_orders.json 字段缺失 valid 标志|root=self|confidence=0.6
AUDIT_RESULT|verdict=block|blocker=上游 m01 未交付 orders.json，本模块输入缺失|root=upstream|confidence=0.9
AUDIT_RESULT|verdict=block|blocker=契约冲突：m02 与 m01 重复声明 POST /api/order/*|root=contract|confidence=0.85
AUDIT_RESULT|verdict=block|blocker=证据不足，无法确认产物真实（低置信，建议 pro 重审）|root=self|confidence=0.4
```

## 解析规则

- 在报告文本中取**第一个**以 `AUDIT_RESULT|` 开头的行（`extract_four_segment_line`）；
- 按 `|` 切段 → 每段按第一个 `=` 切成 key/value → 四段齐全且值合法才算有效；
- 解析结果与 `auditor-outcome.json` 内容应一致（同一判定的两种投影）。

## 与 fw-runner 对齐

fw-runner 消费的是 `tmp/auditor-outcome.json`（`DriverOutcome.from_mapping`：verdict/root/confidence/
blocker/reason/tokens/detail）。四段行是**同一判定的日志投影**，两者字段名一致、语义一致；
preset 的测试同时验证 JSON 与行的可解析性（tests/test_auditor_outcome.py）。
