# auditor 报告示例 —— block（root=contract，直接回人重排契约）

## 过程审计
- 步骤1 事件流重放：executor 正常完成本模块产物。
- 步骤2 对照验收清单：验收项本身可通过。
- 步骤3 测试真伪：测试通过。

## 结果对照
- contracts/api.yaml 中 m01 与 m02 均声明 POST /api/order/*（接口前缀+方法重复）。
- 跨模块数据依赖：B 需要的输入在 A 的 output 中未声明（input_not_declared）。

## 根因分类
契约/验收标准本身问题（不是 executor 实现问题）→ root=contract。按 v0.4 失败根因分流：直接回人，
由人工定优先级 / 修契约，不进入 executor 重试链。

## 四段
AUDIT_RESULT|verdict=block|blocker=契约冲突：m02 与 m01 重复声明 POST /api/order/*|root=contract|confidence=0.85
