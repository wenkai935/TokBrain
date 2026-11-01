"""TokBrain 控制面内核。

纯标准库实现（``http.client`` / ``json`` / ``argparse``），不 import ``app.*``，
因此可以在 ``.venv``、``.venv-mcp`` 与系统解释器下同样运行。

两个入口共用同一份逻辑：

* ``python -m tokbrain_control ...``      面向人/脚本的 CLI
* ``mcp_server/mcp_control.py``           面向 Hermes 的 MCP 薄代理

唯一的可选依赖是 :mod:`tokbrain_control.search`（检索需要 SQLAlchemy 与
``app.services.knowledge``），它在被调用时才 import，缺失时给出明确降级。
"""

from __future__ import annotations

__version__ = "1.0.0"

# 克隆训练在产品侧尚未落地（全仓无 train/finetune 实现）。
# 这里只占位命名空间，不导出任何工具、不进任何 schema。
FUTURE_NAMESPACE = "tb_clone_"

from .errors import ControlError  # noqa: E402  (放在 version 之后是有意的)


def __getattr__(name: str):
    """惰性导出 Client，避免 import 本包时就把传输层拉起来。"""
    if name == "Client":
        from .client import Client

        return Client
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ControlError", "Client", "__version__", "FUTURE_NAMESPACE"]
