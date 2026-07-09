"""Real end-to-end MCP tests — replaces mock-only §12 tests.

Starts a real stdio MCP server (echo tool) and exercises the full
config → connect → list_tools → call_tool → disconnect chain.
No mocks. Validates that GA's MCP client can talk to a real MCP server.
"""
import os
import sys
import json
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

SERVER_SCRIPT = r'''
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

server = Server("echo-server")

@server.list_tools()
async def list_tools():
    return [types.Tool(
        name="echo",
        description="Echo back the msg argument",
        inputSchema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
    )]

@server.call_tool()
async def call_tool(name, arguments):
    if name == "echo":
        return [types.TextContent(type="text", text=f"echo: {arguments.get('msg', '')}")]
    raise ValueError(f"Unknown tool: {name}")

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

asyncio.run(main())
'''


@pytest.mark.skipif(sys.platform != 'win32', reason="Uses _WindowsStdioMCPClient (Win32 stdio workaround)")
def test_e2e_stdio_connect_list_call(tmp_path):
    """Real stdio MCP server: connect → list_tools → call_tool → disconnect."""
    from mcp_client import _WindowsStdioMCPClient

    script = tmp_path / 'echo_server.py'
    script.write_text(SERVER_SCRIPT, encoding='utf-8')

    client = _WindowsStdioMCPClient(
        command=sys.executable,
        args=[str(script)],
        timeout=15.0,
    )
    try:
        assert client.connect(), "Failed to connect to real echo server"

        tool_names = [t['name'] for t in client._tools]
        assert 'echo' in tool_names, f"echo tool not found in {tool_names}"

        result = client.call_tool('echo', {'msg': 'hello'})
        assert result is not None, "call_tool returned None"
        content = result.get('content', [])
        texts = [c.get('text', '') if isinstance(c, dict) else str(c) for c in content]
        assert any('hello' in t for t in texts), f"echo result missing 'hello': {texts}"
    finally:
        client.disconnect()


@pytest.mark.skipif(sys.platform != 'win32', reason="Uses _WindowsStdioMCPClient (Win32 stdio workaround)")
def test_e2e_stdio_disconnect_cleanup(tmp_path):
    """After disconnect, the server subprocess is terminated."""
    from mcp_client import _WindowsStdioMCPClient

    script = tmp_path / 'echo_server.py'
    script.write_text(SERVER_SCRIPT, encoding='utf-8')

    client = _WindowsStdioMCPClient(
        command=sys.executable,
        args=[str(script)],
        timeout=15.0,
    )
    assert client.connect()
    proc = client._proc
    client.disconnect()
    assert not client.connected
    if proc:
        assert proc.poll() is not None, "Server subprocess still alive after disconnect"


@pytest.mark.skipif(sys.platform != 'win32', reason="Uses _WindowsStdioMCPClient (Win32 stdio workaround)")
def test_e2e_stdio_tool_not_found(tmp_path):
    """Calling a non-existent tool returns an error."""
    from mcp_client import _WindowsStdioMCPClient

    script = tmp_path / 'echo_server.py'
    script.write_text(SERVER_SCRIPT, encoding='utf-8')

    client = _WindowsStdioMCPClient(
        command=sys.executable,
        args=[str(script)],
        timeout=15.0,
    )
    try:
        assert client.connect()
        result = client.call_tool('nonexistent_tool', {})
        is_err = result.get('isError', result.get('is_error', False))
        content = result.get('content', [])
        assert is_err or any('error' in str(c).lower() or 'unknown' in str(c).lower() for c in content), \
            f"Expected error for nonexistent tool, got: {result}"
    finally:
        client.disconnect()


@pytest.mark.skipif(sys.platform != 'win32', reason="Uses _WindowsStdioMCPClient (Win32 stdio workaround)")
def test_e2e_hot_reload_add_server(tmp_path):
    """Real hot-reload: write empty config -> start -> add server to config -> HotReloader picks it up."""
    from mcp_client import MCPClientManager

    config = tmp_path / 'mcp_servers.json'
    server_script = tmp_path / 'echo_server.py'
    server_script.write_text(SERVER_SCRIPT, encoding='utf-8')

    def write_config(servers):
        config.write_text(json.dumps({"mcpServers": servers}), encoding='utf-8')

    write_config({})
    mgr = MCPClientManager(config_path=str(config))
    mgr.start()
    try:
        assert len(mgr._connections) == 0, "should start with no servers"
        write_config({"echo-srv": {"transport": "stdio", "command": sys.executable, "args": [str(server_script)]}})
        for _ in range(20):
            conn = mgr._connections.get('echo-srv')
            if conn and conn.state == 'connected':
                break
            time.sleep(0.5)
        assert 'echo-srv' in mgr._connections, f"hot-reload did not add server: {list(mgr._connections.keys())}"
        assert mgr._connections['echo-srv'].state == 'connected', f"state: {mgr._connections['echo-srv'].state}"
    finally:
        mgr.stop()
        MCPClientManager._instance = None


@pytest.mark.skipif(sys.platform != 'win32', reason="Uses _WindowsStdioMCPClient (Win32 stdio workaround)")
def test_e2e_hot_reload_remove_server(tmp_path):
    """Real hot-reload: start with server -> remove from config -> HotReloader disconnects it."""
    from mcp_client import MCPClientManager

    config = tmp_path / 'mcp_servers.json'
    server_script = tmp_path / 'echo_server.py'
    server_script.write_text(SERVER_SCRIPT, encoding='utf-8')

    def write_config(servers):
        config.write_text(json.dumps({"mcpServers": servers}), encoding='utf-8')

    write_config({"echo-srv": {"transport": "stdio", "command": sys.executable, "args": [str(server_script)]}})
    mgr = MCPClientManager(config_path=str(config))
    mgr.start()
    try:
        assert 'echo-srv' in mgr._connections, "server should be connected at start"
        write_config({})
        for _ in range(20):
            if 'echo-srv' not in mgr._connections:
                break
            time.sleep(0.5)
        assert 'echo-srv' not in mgr._connections, f"hot-reload did not remove server: {list(mgr._connections.keys())}"
    finally:
        mgr.stop()
        MCPClientManager._instance = None
