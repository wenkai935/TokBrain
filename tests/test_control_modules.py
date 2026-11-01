"""把所有控制面模块编译一遍。

存在的理由很具体：`search.py` 是惰性导入的，只有真调 `search` 才会加载。
它一旦有语法错误，单测全绿、CLI 一跑就炸。这条测试把这类问题挡在提交前。
"""

from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "tokbrain_control"
MCP_FILES = [
    ROOT / "mcp_server" / "_http_guard.py",
    ROOT / "mcp_server" / "mcp_control.py",
    ROOT / "mcp_server" / "tokbrain_mcp.py",
    ROOT / "mcp_server" / "tokbrain_hermes_stdio.py",
    ROOT / "scripts" / "probe_control_mcp.py",
]


def _sources() -> list[Path]:
    files = sorted(PACKAGE.glob("*.py"))
    files += [path for path in MCP_FILES if path.is_file()]
    return files


def test_sources_discovered():
    names = {path.name for path in _sources()}
    assert "search.py" in names
    assert "commands.py" in names
    assert "mcp_control.py" in names


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_module_compiles(path: Path):
    py_compile.compile(str(path), cfile=None, doraise=True)


def test_package_imports_without_optional_deps():
    """`tokbrain_control` 顶层不能被 SQLAlchemy 之类的可选依赖卡住。"""
    import tokbrain_control

    assert tokbrain_control.__version__
    assert tokbrain_control.FUTURE_NAMESPACE == "tb_clone_"


def test_search_module_imports_lazily():
    """导入 search 本身不该触发 app.* 加载。"""
    import tokbrain_control.search as module

    assert callable(module.search)
    assert callable(module.transcript)
