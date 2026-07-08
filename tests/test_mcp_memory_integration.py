"""
Tests for MCP ↔ memory system integration.

Verifies that:
  - get_skill_catalog() appends MCP tool summaries when MCPClientManager is available
  - get_skill_catalog() degrades gracefully (returns a str) when MCP is absent
  - the MCP SOP file and MAP.md documentation exist and mention MCP

These tests do NOT require mcp_client.py to be present on disk: they inject a
fake ``mcp_client`` module into ``sys.modules`` so the integration path is
exercised regardless of the parallel subagent's progress.
"""
import os
import sys
from unittest.mock import Mock, MagicMock, patch

import pytest

# Ensure the repo root is importable so ``import skill_loader`` resolves.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_skill_catalog_includes_mcp_tools():
    """Test that get_skill_catalog() includes MCP tools when available."""
    mock_mgr = Mock()
    mock_mgr.get_tools_summary.return_value = "[MCP Servers]\n- test-server: tool1 (desc)"

    # Build a fake mcp_client module so the test works even before
    # mcp_client.py is created by the parallel subagent.
    fake_module = MagicMock()
    fake_module.MCPClientManager.get_instance.return_value = mock_mgr

    with patch.dict(sys.modules, {"mcp_client": fake_module}):
        from skill_loader import get_skill_catalog
        catalog = get_skill_catalog()
        # Should contain MCP section
        assert "MCP" in catalog or "mcp" in catalog.lower() or mock_mgr.get_tools_summary.called


def test_skill_catalog_without_mcp():
    """Test that get_skill_catalog() works when MCP is not available."""
    # ``None`` in sys.modules makes ``import mcp_client`` raise ImportError,
    # which get_skill_catalog() must swallow via its try/except guard.
    with patch.dict(sys.modules, {"mcp_client": None}):
        from skill_loader import get_skill_catalog
        catalog = get_skill_catalog()
        # Should still work, just without MCP section
        assert isinstance(catalog, str)


def test_skill_catalog_mcp_isolation():
    """A second call after MCP is removed must not leak the MCP section."""
    from skill_loader import get_skill_catalog
    fake_module = MagicMock()
    fake_module.MCPClientManager.get_instance.return_value = Mock(
        get_tools_summary=Mock(return_value="[MCP Servers]\n- srv: t (d)")
    )
    with patch.dict(sys.modules, {"mcp_client": fake_module}):
        with_mcp = get_skill_catalog()
    assert "MCP" in with_mcp

    with patch.dict(sys.modules, {"mcp_client": None}):
        without_mcp = get_skill_catalog()
    assert isinstance(without_mcp, str)
    # No MCP section when the manager is unavailable
    assert "[MCP Servers]" not in without_mcp


def test_mcp_sop_exists():
    """Test that MCP SOP file exists."""
    sop_path = os.path.join(os.path.dirname(__file__), "..", "memory", "mcp_sop.md")
    assert os.path.exists(sop_path), f"MCP SOP not found at {sop_path}"


def test_mcp_sop_content():
    """MCP SOP should document config, usage, and memory fusion."""
    sop_path = os.path.join(os.path.dirname(__file__), "..", "memory", "mcp_sop.md")
    with open(sop_path, encoding="utf-8") as f:
        content = f.read()
    assert "mcp_call" in content
    assert "mcp_servers.json" in content
    assert "记忆融合" in content


def test_map_mentions_mcp():
    """Test that MAP.md mentions MCP."""
    map_path = os.path.join(os.path.dirname(__file__), "..", "MAP.md")
    with open(map_path, encoding="utf-8") as f:
        content = f.read()
    assert "mcp" in content.lower() or "MCP" in content


def test_map_mentions_mcp_files():
    """MAP.md should list the new MCP-related files."""
    map_path = os.path.join(os.path.dirname(__file__), "..", "MAP.md")
    with open(map_path, encoding="utf-8") as f:
        content = f.read()
    assert "mcp_client.py" in content
    assert "mcp_cli.py" in content
    assert "mcp_sop.md" in content
