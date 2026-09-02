# PyPI 打包（autoknit wheel）

- 组包脚本见 `../.github/workflows/publish.yml`（tag `v*` 触发）
- 结构：`autoknit_pkg/cli.py`（console_script 自安装 wrapper）+ `autoknit_pkg/framework/`（构建时从仓库 framework-v1 生成的完整快照，作为包数据）
- 发布：GitHub Actions + PyPI Trusted Publisher（无 token）；首次需在 PyPI 配置 pending publisher
