"""Tests for the tool registry mechanism (register_tool / get_tool / discover_tools).

Covers audit F26's sibling gap: the dispatch extensibility primitives had no tests.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent_loop import (  # noqa: E402
    _TOOL_REGISTRY,
    discover_tools,
    get_tool,
    register_tool,
)


def test_register_tool_stores_and_returns_fn():
    saved = _TOOL_REGISTRY.get("t_echo")
    try:
        @register_tool("t_echo")
        def fn(handler, args, response):
            return None
        assert _TOOL_REGISTRY["t_echo"] is fn
        assert get_tool("t_echo") is fn
    finally:
        if saved is None:
            _TOOL_REGISTRY.pop("t_echo", None)
        else:
            _TOOL_REGISTRY["t_echo"] = saved


def test_get_tool_returns_none_for_unknown():
    assert get_tool("definitely_not_registered_xyz") is None


def test_discover_tools_loads_dropin_modules(tmp_path, monkeypatch):
    """A non-underscore .py in tools_dir self-registers on import."""
    # Build a throwaway package dir on sys.path so importlib.import_module("pkg.<mod>") works.
    pkg = tmp_path / "tpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod_a.py").write_text(
        "from agent_loop import register_tool\n"
        "@register_tool('d_dropin')\n"
        "def _fn(handler, args, response):\n"
        "    return 'dropin-ok'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    saved = _TOOL_REGISTRY.get("d_dropin")
    try:
        discover_tools(str(pkg))
        assert get_tool("d_dropin")(None, None, None) == "dropin-ok"
    finally:
        if saved is None:
            _TOOL_REGISTRY.pop("d_dropin", None)
        else:
            _TOOL_REGISTRY["d_dropin"] = saved


def test_discover_tools_skips_underscore_files(tmp_path, monkeypatch):
    pkg = tmp_path / "tpkg2"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "_private.py").write_text(
        "from agent_loop import register_tool\n"
        "@register_tool('d_should_not_load')\n"
        "def _fn(handler, args, response):\n"
        "    return None\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        discover_tools(str(pkg))
        assert get_tool("d_should_not_load") is None
    finally:
        _TOOL_REGISTRY.pop("d_should_not_load", None)


def test_discover_tools_isolates_module_load_failure(tmp_path, monkeypatch, capsys):
    """One broken module must not kill sibling modules."""
    pkg = tmp_path / "tpkg3"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    (pkg / "good.py").write_text(
        "from agent_loop import register_tool\n"
        "@register_tool('d_survivor')\n"
        "def _fn(handler, args, response):\n"
        "    return 'survived'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    saved = _TOOL_REGISTRY.get("d_survivor")
    try:
        discover_tools(str(pkg))  # must not raise
        assert get_tool("d_survivor")(None, None, None) == "survived"
        err = capsys.readouterr().err
        assert "broken" in err and "boom" in err  # per-module stderr, not silent
    finally:
        if saved is None:
            _TOOL_REGISTRY.pop("d_survivor", None)
        else:
            _TOOL_REGISTRY["d_survivor"] = saved


def test_discover_tools_noop_when_dir_missing(tmp_path):
    missing = tmp_path / "does_not_exist"
    discover_tools(str(missing))  # must not raise
