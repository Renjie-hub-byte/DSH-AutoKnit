"""fwapi —— AutoKnit 面板数据桥（m01 数据桥）。

把 fw-api 的 dsh.task.list/detail 数据以确定性、可空降级的方式暴露成浏览器
可调的 JSON HTTP 端点，并承载归档状态（任务目录约定文件 总日志/archived.json）的读写。

职责边界：
- 只读 fw-api 数据 + 只写归档文件，不写任务状态文件；
- 不调 LLM；
- 纯标准库实现（http.server + json），无第三方运行时依赖。
"""

from fwapi.serve import serve, main  # noqa: F401

__all__ = ["serve", "main"]
__version__ = "0.1.0"
