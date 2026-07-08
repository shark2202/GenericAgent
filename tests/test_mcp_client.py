"""
tests/test_mcp_client.py — TDD tests for mcp_client.py core library

Tests mock the `mcp` SDK to avoid real MCP server dependencies.
Run: pytest tests/test_mcp_client.py -v
"""

import json, os, sys, time, tempfile, threading
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from pathlib import Path

import pytest

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def _mock_connect(name, cfg, mgr):
    """Mock _connect_server that creates a fake MCPServerConnection without subprocess."""
    from mcp_client import MCPServerConnection
    conn = Mock(spec=MCPServerConnection)
    conn.name = name
    conn.state = 'connected'
    conn.transport = cfg.get('transport', 'stdio')
    conn.tools = [{'name': 'mock_tool', 'description': 'A mock tool', 'inputSchema': {'type': 'object'}}]
    conn.call_tool = Mock(return_value={'result': 'mock_result'})
    conn.ping = Mock(return_value=True)
    conn.disconnect = Mock()
    conn.connect = Mock(return_value=True)
    mgr._connections[name] = conn
    mgr.registry.update(name, conn.tools)
    return conn


# ---------------------------------------------------------------------------
# Config Loading Tests
# ---------------------------------------------------------------------------
class TestConfigLoading:
    def test_load_config_valid(self, tmp_path):
        from mcp_client import load_config
        cfg_file = tmp_path / "mcp_servers.json"
        cfg_file.write_text(json.dumps({
            "mcpServers": {
                "fs": {"transport": "stdio", "command": "npx", "args": ["-y", "fs-server"]},
                "remote": {"transport": "sse", "url": "http://localhost:8080/sse"}
            }
        }))
        result = load_config(str(cfg_file))
        assert "fs" in result
        assert "remote" in result
        assert result["fs"]["command"] == "npx"

    def test_load_config_missing_file(self):
        from mcp_client import load_config
        result = load_config("/nonexistent/path/mcp_servers.json")
        assert result == {}

    def test_load_config_invalid_json(self, tmp_path):
        from mcp_client import load_config
        cfg_file = tmp_path / "mcp_servers.json"
        cfg_file.write_text("{invalid json!!!")
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_config(str(cfg_file))

    def test_load_config_missing_required_fields(self, tmp_path):
        from mcp_client import load_config
        cfg_file = tmp_path / "mcp_servers.json"
        cfg_file.write_text(json.dumps({
            "mcpServers": {
                "good": {"transport": "stdio", "command": "npx"},
                "bad_no_transport": {"command": "foo"},
                "bad_stdio_no_command": {"transport": "stdio"},
                "bad_sse_no_url": {"transport": "sse"}
            }
        }))
        result = load_config(str(cfg_file))
        assert "good" in result
        assert "bad_no_transport" not in result
        assert "bad_stdio_no_command" not in result
        assert "bad_sse_no_url" not in result


# ---------------------------------------------------------------------------
# MCPToolRegistry Tests
# ---------------------------------------------------------------------------
class TestMCPToolRegistry:
    def test_tool_registry_cache(self):
        from mcp_client import MCPToolRegistry
        reg = MCPToolRegistry()
        reg.update("srv-a", [{"name": "tool1", "description": "d1", "inputSchema": {}}])
        assert len(reg._tools["srv-a"]) == 1

    def test_tool_registry_get_tool(self):
        from mcp_client import MCPToolRegistry
        reg = MCPToolRegistry()
        reg.update("srv-a", [{"name": "tool1", "description": "d1", "inputSchema": {}}])
        t = reg.get_tool("srv-a", "tool1")
        assert t is not None
        assert t["name"] == "tool1"
        assert reg.get_tool("srv-a", "nonexistent") is None
        assert reg.get_tool("unknown-srv", "tool1") is None

    def test_tool_registry_get_all_tools_summary(self):
        from mcp_client import MCPToolRegistry
        reg = MCPToolRegistry()
        reg.update("srv-a", [{"name": "tool1", "description": "reads files", "inputSchema": {}}])
        reg.update("srv-b", [{"name": "tool2", "description": "writes files", "inputSchema": {}}])
        summary = reg.get_all_tools_summary()
        assert "[MCP Servers]" in summary
        assert "srv-a" in summary
        assert "srv-b" in summary
        assert "tool1" in summary
        assert "tool2" in summary

    def test_tool_registry_empty_summary(self):
        from mcp_client import MCPToolRegistry
        reg = MCPToolRegistry()
        assert reg.get_all_tools_summary() == ''

    def test_tool_registry_invalidate(self):
        from mcp_client import MCPToolRegistry
        reg = MCPToolRegistry()
        reg.update("srv-a", [{"name": "tool1", "description": "", "inputSchema": {}}])
        reg.invalidate("srv-a")
        assert "srv-a" not in reg._tools
        # Invalidate non-existent should not raise
        reg.invalidate("nonexistent")

    def test_tool_registry_invalidate_all(self):
        from mcp_client import MCPToolRegistry
        reg = MCPToolRegistry()
        reg.update("srv-a", [{"name": "t1", "description": "", "inputSchema": {}}])
        reg.update("srv-b", [{"name": "t2", "description": "", "inputSchema": {}}])
        reg.invalidate_all()
        assert len(reg._tools) == 0


# ---------------------------------------------------------------------------
# AuditLogger Tests
# ---------------------------------------------------------------------------
class TestAuditLogger:
    def test_audit_log_format(self, tmp_path):
        from mcp_client import AuditLogger
        log_path = str(tmp_path / "audit.jsonl")
        logger = AuditLogger(log_path)
        logger.log("srv-a", "tool1", {"arg": "val"}, {"result": "ok"}, 15.3, True)
        with open(log_path) as f:
            entry = json.loads(f.readline())
        assert entry["server"] == "srv-a"
        assert entry["tool"] == "tool1"
        assert entry["duration_ms"] == 15.3
        assert entry["success"] is True
        assert "timestamp" in entry

    def test_audit_log_records_errors(self, tmp_path):
        from mcp_client import AuditLogger
        log_path = str(tmp_path / "audit.jsonl")
        logger = AuditLogger(log_path)
        logger.log("srv-a", "tool1", {}, "Error: connection refused", 5000.0, False)
        with open(log_path) as f:
            entry = json.loads(f.readline())
        assert entry["success"] is False
        assert "connection refused" in entry["result_summary"]

    def test_audit_log_truncation(self, tmp_path):
        from mcp_client import AuditLogger
        log_path = str(tmp_path / "audit.jsonl")
        logger = AuditLogger(log_path)
        long_arg = "x" * 1000
        logger.log("srv", "tool", {"data": long_arg}, "ok", 10, True)
        with open(log_path) as f:
            entry = json.loads(f.readline())
        assert len(entry["arguments_summary"]) <= 500


# ---------------------------------------------------------------------------
# MCPServerConnection Tests (mocked)
# ---------------------------------------------------------------------------
class TestMCPServerConnection:
    def _make_connection(self, name="test-srv", config=None):
        from mcp_client import MCPServerConnection, AuditLogger
        cfg = config or {"transport": "stdio", "command": "echo", "args": []}
        audit = AuditLogger(os.path.join(tempfile.gettempdir(), "test_audit.jsonl"))
        return MCPServerConnection(name, cfg, audit)

    def test_connection_initial_state(self):
        conn = self._make_connection()
        assert conn.state == 'disconnected'
        assert conn.tools == []

    def test_connection_connect_mocked(self):
        conn = self._make_connection()
        # Mock the async initialization
        conn._run_loop = Mock()
        conn.state = 'connected'
        # Just verify the object structure
        assert conn.name == 'test-srv'
        assert conn.transport == 'stdio'

    def test_connection_call_tool_not_connected(self):
        conn = self._make_connection()
        conn._loop = None
        with pytest.raises(RuntimeError, match="not connected"):
            conn.call_tool("tool1", {})

    def test_connection_ping_not_connected(self):
        conn = self._make_connection()
        conn._loop = None
        assert conn.ping() is False


# ---------------------------------------------------------------------------
# MCPClientManager Tests
# ---------------------------------------------------------------------------
class TestMCPClientManager:
    def test_manager_singleton(self):
        from mcp_client import MCPClientManager
        mgr1 = MCPClientManager("/tmp")
        mgr2 = MCPClientManager.get_instance()
        assert mgr1 is mgr2
        # Cleanup
        MCPClientManager._instance = None

    def test_manager_start_stop_empty_config(self, tmp_path):
        from mcp_client import MCPClientManager
        cfg_path = str(tmp_path / "mcp_servers.json")
        # No config file = empty config
        mgr = MCPClientManager(str(tmp_path), cfg_path)
        mgr.start()
        assert mgr._started is True
        assert mgr.get_status() == {}
        mgr.stop()
        assert mgr._started is False
        MCPClientManager._instance = None

    def test_manager_call_tool_unknown_server(self, tmp_path):
        from mcp_client import MCPClientManager
        mgr = MCPClientManager(str(tmp_path), str(tmp_path / "mcp_servers.json"))
        mgr.start()
        with pytest.raises(ValueError, match="Unknown MCP server"):
            mgr.call_tool("nonexistent", "tool", {})
        mgr.stop()
        MCPClientManager._instance = None

    def test_manager_call_tool_not_connected(self, tmp_path):
        from mcp_client import MCPClientManager
        cfg_path = str(tmp_path / "mcp_servers.json")
        Path(cfg_path).write_text(json.dumps({
            "mcpServers": {
                "srv-a": {"transport": "stdio", "command": "nonexistent_cmd_xyz"}
            }
        }))
        mgr = MCPClientManager(str(tmp_path), cfg_path)
        # Mock connect to simulate failure without real subprocess
        mgr._connect_server = lambda name, cfg: None
        mgr.start()
        # Server should be in config but not connected
        with pytest.raises((RuntimeError, ValueError)):
            mgr.call_tool("srv-a", "tool", {})
        mgr.stop()
        MCPClientManager._instance = None

    def test_manager_get_status(self, tmp_path):
        from mcp_client import MCPClientManager
        mgr = MCPClientManager(str(tmp_path), str(tmp_path / "mcp_servers.json"))
        mgr.start()
        status = mgr.get_status()
        assert isinstance(status, dict)
        mgr.stop()
        MCPClientManager._instance = None

    def test_manager_get_tools_summary_empty(self, tmp_path):
        from mcp_client import MCPClientManager
        mgr = MCPClientManager(str(tmp_path), str(tmp_path / "mcp_servers.json"))
        mgr.start()
        assert mgr.get_tools_summary() == ''
        mgr.stop()
        MCPClientManager._instance = None

    def test_manager_reload_config_invalid_json(self, tmp_path):
        from mcp_client import MCPClientManager
        cfg_path = str(tmp_path / "mcp_servers.json")
        Path(cfg_path).write_text("invalid json!!!")
        mgr = MCPClientManager(str(tmp_path), cfg_path)
        mgr._connect_server = Mock(return_value=None)
        mgr.start()
        assert mgr._config == {}
        Path(cfg_path).write_text(json.dumps({
            "mcpServers": {"srv-a": {"transport": "stdio", "command": "echo"}}
        }))
        mgr.reload_config()
        assert "srv-a" in mgr._config
        mgr.stop()
        MCPClientManager._instance = None

    def test_manager_start_stop_restart_server(self, tmp_path):
        from mcp_client import MCPClientManager
        cfg_path = str(tmp_path / "mcp_servers.json")
        Path(cfg_path).write_text(json.dumps({
            "mcpServers": {"srv-a": {"transport": "stdio", "command": "echo"}}
        }))
        mgr = MCPClientManager(str(tmp_path), cfg_path)
        # Mock _connect_server to store a mock connection without real subprocess
        def mock_connect(name, cfg=None):
            mock_conn = Mock()
            mock_conn.state = 'connected'
            mock_conn.tools = []
            mock_conn.transport = cfg.get('transport', 'stdio') if cfg else 'stdio'
            mock_conn.disconnect = Mock()
            mgr._connections[name] = mock_conn
            mgr.registry.update(name, mock_conn.tools)
        mgr._connect_server = mock_connect
        mgr.start()
        assert "srv-a" in mgr._connections
        # stop_server
        mgr.stop_server("srv-a")
        assert "srv-a" not in mgr._connections
        # start_server
        mgr.start_server("srv-a")
        assert "srv-a" in mgr._connections
        # restart_server
        mgr.restart_server("srv-a")
        assert "srv-a" in mgr._connections
        mgr.stop()
        MCPClientManager._instance = None


# ---------------------------------------------------------------------------
# Hot Reload Tests
# ---------------------------------------------------------------------------
class TestHotReload:
    def test_hot_reload_add_server(self, tmp_path):
        from mcp_client import MCPClientManager
        cfg_path = str(tmp_path / "mcp_servers.json")
        Path(cfg_path).write_text(json.dumps({"mcpServers": {}}))
        mgr = MCPClientManager(str(tmp_path), cfg_path)
        mgr._connect_server = lambda name, cfg: _mock_connect(name, cfg, mgr)
        mgr.start()
        assert len(mgr._config) == 0
        Path(cfg_path).write_text(json.dumps({
            "mcpServers": {"srv-new": {"transport": "stdio", "command": "echo"}}
        }))
        mgr.reload_config()
        assert "srv-new" in mgr._config
        assert "srv-new" in mgr._connections
        mgr.stop()
        MCPClientManager._instance = None

    def test_hot_reload_remove_server(self, tmp_path):
        from mcp_client import MCPClientManager
        cfg_path = str(tmp_path / "mcp_servers.json")
        Path(cfg_path).write_text(json.dumps({
            "mcpServers": {"srv-a": {"transport": "stdio", "command": "echo"}}
        }))
        mgr = MCPClientManager(str(tmp_path), cfg_path)
        mgr._connect_server = lambda name, cfg: _mock_connect(name, cfg, mgr)
        mgr.start()
        assert "srv-a" in mgr._config
        Path(cfg_path).write_text(json.dumps({"mcpServers": {}}))
        mgr.reload_config()
        assert "srv-a" not in mgr._config
        mgr.stop()
        MCPClientManager._instance = None

    def test_hot_reload_invalid_json_preserves_existing(self, tmp_path):
        from mcp_client import MCPClientManager
        cfg_path = str(tmp_path / "mcp_servers.json")
        Path(cfg_path).write_text(json.dumps({
            "mcpServers": {"srv-a": {"transport": "stdio", "command": "echo"}}
        }))
        mgr = MCPClientManager(str(tmp_path), cfg_path)
        mgr._connect_server = lambda name, cfg: _mock_connect(name, cfg, mgr)
        mgr.start()
        original_config = dict(mgr._config)
        Path(cfg_path).write_text("{{invalid")
        mgr.reload_config()
        assert mgr._config == original_config
        mgr.stop()
        MCPClientManager._instance = None


# ---------------------------------------------------------------------------
# Health Monitor Tests (mocked)
# ---------------------------------------------------------------------------
class TestHealthMonitor:
    def test_health_check_pass(self):
        from mcp_client import HealthMonitor, MCPClientManager
        mgr = Mock(spec=MCPClientManager)
        mgr._connections = {}
        monitor = HealthMonitor(mgr)
        # No connections = no ping calls
        monitor._run = Mock()
        monitor.start()
        time.sleep(0.5)
        monitor.stop()
        # Should not crash

    def test_reconnect_on_failure(self):
        from mcp_client import HealthMonitor
        mgr = Mock()
        conn = Mock()
        conn.state = 'connected'
        conn.ping.return_value = False
        conn.connect.return_value = True
        conn.tools = []
        mgr._connections = {"srv-a": conn}
        monitor = HealthMonitor(mgr)
        monitor._backoff = {"srv-a": 0.1}
        # Simulate one reconnect cycle
        monitor._handle_reconnect("srv-a", conn)
        assert conn.disconnect.called
        assert conn.connect.called

    @patch('time.sleep')
    def test_reconnect_exponential_backoff(self, mock_sleep):
        from mcp_client import HealthMonitor
        mgr = Mock()
        conn = Mock()
        conn.state = 'connected'
        conn.ping.return_value = False
        conn.connect.return_value = False  # reconnect fails
        conn.tools = []
        mgr._connections = {"srv-a": conn}
        monitor = HealthMonitor(mgr)
        # First failure: backoff=2 (default), sleep(2), connect fails → backoff=2*2=4
        monitor._backoff = {}
        monitor._handle_reconnect("srv-a", conn)
        assert monitor._backoff["srv-a"] == 4  # 2*2
        # Second failure: backoff=4, sleep(4), connect fails → backoff=4*2=8
        monitor._handle_reconnect("srv-a", conn)
        assert monitor._backoff["srv-a"] == 8  # 4*2
        # Keep doubling until cap=60
        monitor._backoff["srv-a"] = 32
        monitor._handle_reconnect("srv-a", conn)
        assert monitor._backoff["srv-a"] == 60  # capped at 60


# ---------------------------------------------------------------------------
# In-Memory Integration Test (uses mcp SDK's InMemoryTransport)
# ---------------------------------------------------------------------------
class TestInMemoryIntegration:
    """Integration test using mcp SDK's create_connected_server_and_client_session."""

    def test_in_memory_tool_call(self):
        """Test a full MCP tool call cycle using in-memory transport."""
        import asyncio
        from mcp.server import Server
        from mcp import ClientSession
        from mcp.types import Tool, TextContent

        async def test():
            app = Server("test-server")

            @app.list_tools()
            async def list_tools():
                return [Tool(
                    name="echo",
                    description="Echo back the input",
                    inputSchema={
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    }
                )]

            @app.call_tool()
            async def call_tool(name: str, arguments: dict):
                if name == "echo":
                    return [TextContent(type="text", text=f"Echo: {arguments.get('message', '')}")]

            from mcp.shared.memory import create_connected_server_and_client_session
            async with create_connected_server_and_client_session(
                app
            ) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                assert len(tools_result.tools) == 1
                assert tools_result.tools[0].name == "echo"
                call_result = await session.call_tool("echo", {"message": "hello"})
                assert len(call_result.content) == 1
                assert "hello" in call_result.content[0].text

        asyncio.run(test())
