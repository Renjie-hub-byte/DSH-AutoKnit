# auditor 报告示例 —— block（root=self，回同 executor 修）

## 过程审计
- 步骤1 事件流重放：executor 写入 src/data/cleaned_orders.json，但未运行 test_clean.py。
- 步骤2 对照验收清单：验收项2「每条记录含 valid 标志」→ 产物缺少 valid 字段。
- 步骤3 测试真伪：测试文件未覆盖 valid 字段断言。

## 结果对照
- contract.yaml output.artifacts = [src/data/cleaned_orders.json] → 存在但格式不满足验收项2。
- 预测基线：will_have 1 项匹配 / 1 项缺失（验收项2）。

## 根因分类
executor 自身实现漏做 → root=self，回同 executor 修（附 REVIEW.md 反馈）。

## 四段
AUDIT_RESULT|verdict=block|blocker=验收项2：cleaned_orders.json 字段缺失 valid 标志|root=self|confidence=0.6
