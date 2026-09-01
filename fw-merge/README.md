# fw-merge —— 程序化合代码 merge（纯程序，无 LLM）

> AutoKnit v2 需求2：把 AutoKnit 各 executor 独立产出的模块，按依赖图「树枝」维度
> 拼成可编译骨架 + 接线冲突清单。**机械活自动干，需判断的钉成交给人 / agent。**

## 是什么

每个 executor 产出的模块是独立、自包含的。合并是人的事——fw-merge 把「人」那摊
机械活做掉七八成，剩下需判断处（命名冲突 / 接口签名出入 / 语义融合）钉成清单交人。

## 用法

```bash
autoknit merge 任务-xxx [--output-dir OUT] [--db codegraph.db]
  # 落盘 skeleton.json + conflicts.json + compile_notes.json + wiring.json
  #   + interfaces/<module>/interface.json + wiring/<module>/wiring.json
```

子命令：`run`（全链路）/ `skeleton` / `conflicts` / `interfaces` / `wiring` / `notes` / `api`。

## 冲突清单（conflicts.json）四类

- `same_name`       同名单（文件/符号）落不同路径
- `naming_conflict` 命名不一致
- `signature_mismatch` 同一逻辑接口签名不一致（调用方难绑定）
- `semantic_merge`  同名文件定义公共符号（工具不做语义融合，需人工统一/去重）

## 约束（设计边界）

- **纯 python / 标准库 + pyyaml，不调 LLM**（`grep` 可确认无模型/网络调用）。
- 不做语义级融合（字段合并 / 逻辑去重 / 风格统一）——做不到处用 `needs_human` 标注。
- 依赖图优先读 codegraph.db（sqlite3），缺省回退模块 contract.yaml dependencies。

## 测试

```bash
cd fw-merge && PYTHONPATH=src python3.11 -m pytest test/ -q   # 54 passed
```
