# auditor 报告示例 —— pass（过程审计三步 + 结果对照 + 四段行）

## 过程审计
- 步骤1 事件流重放：filterEvents 显示 executor 调用 tool-fs-write 写入 src/data/orders.json（1 次），无跨模块路径写入。
- 步骤2 对照验收清单：验收项1「订单数据落盘为 JSON」→ 文件存在；验收项2「字段含 order_id/amount」→ 符合；只碰本模块。
- 步骤3 测试真伪：测试文件 test_parse.py 覆盖 JSON 解析与字段断言（非仅"测试通过"）。

## 结果对照
- contract.yaml output.artifacts = [src/data/orders.json] → 存在、可 JSON 解析。
- read_api 与契约区一致，无重复。
- 预测基线：will_have 1 项匹配 / 缺失 0；will_not_have 1 项 clean / 违反 0。

## 四段
AUDIT_RESULT|verdict=pass|blocker=|root=|confidence=0.95
