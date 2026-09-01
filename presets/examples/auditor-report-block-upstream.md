# auditor 报告示例 —— block（root=upstream，直接回人不重试）

## 过程审计
- 步骤1 事件流重放：executor 在本模块内动作正常，未越界。
- 步骤2 对照验收清单：验收项1「基于 m01 交付的 orders.json 清洗」→ 输入文件不存在。
- 步骤3 测试真伪：无法运行（输入缺失）。

## 结果对照
- m02 contract.yaml input.from=[m01]，但 m01 未交付 src/data/orders.json。

## 根因分类
上游未交付（非 executor 自身问题）→ root=upstream。按 v0.4 失败根因分流：
upstream/contract 直接抛人不重试，不消耗 executor 重试 token。

## 四段
AUDIT_RESULT|verdict=block|blocker=上游 m01 未交付 orders.json，本模块输入缺失|root=upstream|confidence=0.9
