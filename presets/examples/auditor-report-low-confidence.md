# auditor 报告示例 —— 低置信（confidence < 0.7 → pro 重审/仲裁）

## 过程审计
- 步骤1 事件流重放：session-query 事件缺失 tool/result 细节，无法确证 executor 实际动作。
- 步骤2 对照验收清单：产物存在但事件链证据不完整。
- 步骤3 测试真伪：测试文件存在但内容未覆盖关键逻辑。

## 结果对照
无法确认产物真实 → 判定 block，但证据不足以高置信定根因。

## 置信度
confidence=0.4（< 0.7 低置信）→ 依 v0.4 设计 9.4 触发 pro 模型重审；与 flash 结论一致才定案，冲突则人工仲裁。

## 四段
AUDIT_RESULT|verdict=block|blocker=证据不足，无法确认产物真实（低置信，建议 pro 重审）|root=self|confidence=0.4
