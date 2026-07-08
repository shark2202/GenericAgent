"""
Tests for MCP ↔ memory system integration.

Verifies that:
  - the MCP SOP file and MAP.md documentation exist and mention MCP

Note: get_skill_catalog() tests were removed when skill support was integrated
with the L1-L4 memory system (change: integrate-skills-with-memory).
"""
import os
import sys

import pytest

# Ensure the repo root is importable so ``import skill_loader`` resolves.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


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
