"""
mcp_client.py — MCP (Model Context Protocol) Client for GenericAgent

Connects to external MCP Servers (stdio + SSE), discovers tools, exposes them
via the mcp_call tool, and integrates with GA's memory/self-evolution system.

Architecture:
    MCPClientManager (singleton)
      ├── MCPServerConnection (async bridge per server)
      ├── MCPToolRegistry (tool name → server + schema cache)
      ├── HealthMonitor (background thread, list_tools heartbeat + backoff reconnect)
      ├── HotReloader (mtime poll + incremental diff)
      └── AuditLogger (jsonl audit log)
"""

import os, sys, json, time, threading, asyncio, atexit, traceback
from typing import Any, Optional
from dataclasses import dataclass, field
from pathlib import Path

script_dir = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------
class AuditLogger:
    """Append-only JSONL audit log for MCP tool calls."""

    def __init__(self, log_path: str = None):
        self.log_path = log_path or os.path.join(script_dir, 'temp', 'mcp_audit.jsonl')
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self._lock = threading.Lock()

    def log(self, server: str, tool: str, arguments: dict, result: Any, duration_ms: float, success: bool):
        entry = {
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'server': server,
            'tool': tool,
            'arguments_summary': str(arguments)[:500],
            'result_summary': str(result)[:500],
            'duration_ms': round(duration_ms, 1),
            'success': success,
        }
        with self._lock:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')


# ---------------------------------------------------------------------------
# MCPToolRegistry
# ---------------------------------------------------------------------------
class MCPToolRegistry:
    """Cache of discovered MCP tools, keyed by server name."""

    def __init__(self):
        self._tools: dict[str, list[dict]] = {}  # {server: [{name, description, inputSchema}]}
        self._lock = threading.Lock()

    def update(self, server: str, tools: list[dict]):
        with self._lock:
            self._tools[server] = tools

    def get_tool(self, server: str, tool_name: str) -> Optional[dict]:
        with self._lock:
            for t in self._tools.get(server, []):
                if t.get('name') == tool_name:
                    return t
        return None

    def get_all_tools_summary(self) -> str:
        with self._lock:
            if not self._tools:
                return ''
            lines = ['[MCP Servers]']
            for server, tools in self._tools.items():
                tool_strs = [f"{t['name']} ({t.get('description', '')[:40]})" for t in tools]
                lines.append(f"- {server}: {', '.join(tool_strs)}")
            return '\n'.join(lines) + '\n'

    def invalidate(self, server: str):
        with self._lock:
            self._tools.pop(server, None)

    def invalidate_all(self):
        with self._lock:
            self._tools.clear()

    def get_server_names(self) -> list[str]:
        with self._lock:
            return list(self._tools.keys())


# ---------------------------------------------------------------------------
# MCPServerConnection — async bridge
# ---------------------------------------------------------------------------
class MCPServerConnection:
    """Wraps a single MCP server connection with an asyncio event loop in a background thread."""

    def __init__(self, name: str, config: dict, audit: AuditLogger):
        self.name = name
        self.config = config
        self.audit = audit
        self.transport = config.get('transport', 'stdio')
        self.state = 'disconnected'  # disconnected | connecting | connected | reconnecting | failed
        self.tools: list[dict] = []
        self.last_ping: float = 0
        self._session = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._reconnect_attempts = 0
        self._lock = threading.Lock()

    # -- lifecycle --
    def connect(self) -> bool:
        """Start the async loop in a background thread and initialize the session."""
        if self._thread and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self.state = 'connecting'
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # Wait for initialization (up to 15s)
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.state == 'connected':
                return True
            if self.state == 'failed':
                return False
            time.sleep(0.1)
        self.state = 'failed'
        return False

    def disconnect(self):
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result(timeout=5)
        if self._thread:
            self._thread.join(timeout=5)
        self._loop = None
        self._session = None
        self.state = 'disconnected'

    # -- async core --
    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:
            self.state = 'failed'
        finally:
            self._loop.close()

    async def _main(self):
        try:
            await self._init_session()
            self.state = 'connected'
            self._reconnect_attempts = 0
            # Wait until stop
            while not self._stop_event.is_set():
                await asyncio.sleep(0.5)
        except Exception as e:
            self.state = 'failed'
        finally:
            await self._cleanup()

    async def _init_session(self):
        from mcp import ClientSession
        if self.transport == 'stdio':
            from mcp.client.stdio import stdio_client, StdioServerParameters
            params = StdioServerParameters(
                command=self.config['command'],
                args=self.config.get('args', []),
                env=self.config.get('env'),
                cwd=self.config.get('cwd'),
            )
            self._ctx = stdio_client(params)
        elif self.transport in ('sse', 'http', 'streamable_http'):
            from mcp.client.sse import sse_client
            self._ctx = sse_client(self.config['url'], headers=self.config.get('headers', {}))
        else:
            raise ValueError(f"Unknown transport: {self.transport}")

        transport_pair = await self._ctx.__aenter__()
        read_stream, write_stream = transport_pair[0], transport_pair[1]
        self._session = ClientSession(read_stream, write_stream)
        await self._session.initialize()
        # Discover tools
        await self._refresh_tools()

    async def _refresh_tools(self):
        if not self._session:
            return
        result = await self._session.list_tools()
        self.tools = [
            {
                'name': t.name,
                'description': t.description or '',
                'inputSchema': t.inputSchema if t.inputSchema else {'type': 'object', 'properties': {}},
            }
            for t in result.tools
        ]

    async def _call_tool(self, tool_name: str, arguments: dict) -> dict:
        if not self._session:
            raise RuntimeError(f"Server {self.name} not connected")
        result = await self._session.call_tool(tool_name, arguments or {})
        # CallToolResult has .content list and .is_error
        content = []
        for c in (result.content or []):
            if hasattr(c, 'text'):
                content.append(c.text)
            else:
                content.append(str(c))
        return {
            'content': content,
            'is_error': getattr(result, 'is_error', False),
        }

    async def _ping(self) -> bool:
        """Health check via list_tools (no ping method in SDK)."""
        try:
            await self._session.list_tools()
            return True
        except Exception:
            return False

    async def _shutdown(self):
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
        if hasattr(self, '_ctx') and self._ctx:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def _cleanup(self):
        await self._shutdown()

    # -- sync API (called from main thread) --
    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        if not self._loop or not self._loop.is_running():
            raise RuntimeError(f"Server {self.name} not connected")
        future = asyncio.run_coroutine_threadsafe(
            self._call_tool(tool_name, arguments), self._loop
        )
        return future.result(timeout=30)

    def ping(self) -> bool:
        if not self._loop or not self._loop.is_running():
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(self._ping(), self._loop)
            return future.result(timeout=10)
        except Exception:
            return False

    def refresh_tools(self):
        if not self._loop or not self._loop.is_running():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._refresh_tools(), self._loop)
            future.result(timeout=10)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HealthMonitor
# ---------------------------------------------------------------------------
class HealthMonitor:
    """Background thread that pings servers and handles reconnection."""

    def __init__(self, manager: 'MCPClientManager'):
        self.manager = manager
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._backoff = {}  # {server: current_backoff_seconds}

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        while not self._stop_event.is_set():
            for name, conn in list(self.manager._connections.items()):
                if self._stop_event.is_set():
                    break
                if conn.state != 'connected':
                    self._handle_reconnect(name, conn)
                    continue
                # Ping check
                ok = conn.ping()
                conn.last_ping = time.time()
                if ok:
                    self._backoff.pop(name, None)
                else:
                    self._handle_reconnect(name, conn)
            # Check every 30s
            self._stop_event.wait(30)

    def _handle_reconnect(self, name: str, conn: MCPServerConnection):
        backoff = self._backoff.get(name, 2)
        if backoff > 30:
            print(f"[MCP WARN] Server {name} backoff={backoff}s, still unreachable")
        conn.state = 'reconnecting'
        time.sleep(backoff)
        # Disconnect old
        try:
            conn.disconnect()
        except Exception:
            pass
        # Reconnect
        ok = conn.connect()
        if ok:
            conn.refresh_tools()
            self.manager.registry.update(name, conn.tools)
            self._backoff.pop(name, None)
        else:
            self._backoff[name] = min(backoff * 2, 60)


# ---------------------------------------------------------------------------
# HotReloader
# ---------------------------------------------------------------------------
class HotReloader:
    """Polls config file mtime and applies incremental changes."""

    def __init__(self, manager: 'MCPClientManager'):
        self.manager = manager
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_mtime: float = 0

    def start(self):
        self._stop_event.clear()
        self._init_mtime()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _init_mtime(self):
        try:
            self._last_mtime = os.path.getmtime(self.manager.config_path)
        except OSError:
            self._last_mtime = 0

    def _run(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(2)
            if self._stop_event.is_set():
                break
            try:
                mtime = os.path.getmtime(self.manager.config_path)
            except OSError:
                continue
            if mtime != self._last_mtime:
                self._last_mtime = mtime
                self.manager.reload_config()

    def force_reload(self):
        self._init_mtime()
        self.manager.reload_config()


# ---------------------------------------------------------------------------
# Config Loader
# ---------------------------------------------------------------------------
def load_config(config_path: str) -> dict:
    """Load and validate MCP server config. Returns {server_name: config_dict}."""
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {config_path}: {e}")

    servers = raw.get('mcpServers', raw)  # support both {"mcpServers": {}} and {}
    if not isinstance(servers, dict):
        return {}

    valid = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        transport = cfg.get('transport')
        if transport not in ('stdio', 'sse', 'http', 'streamable_http'):
            print(f"[MCP WARN] Server '{name}': invalid or missing transport, skipping")
            continue
        if transport == 'stdio' and not cfg.get('command'):
            print(f"[MCP WARN] Server '{name}': stdio requires 'command', skipping")
            continue
        if transport in ('sse', 'http', 'streamable_http') and not cfg.get('url'):
            print(f"[MCP WARN] Server '{name}': {transport} requires 'url', skipping")
            continue
        valid[name] = cfg
    return valid


# ---------------------------------------------------------------------------
# MCPClientManager (singleton)
# ---------------------------------------------------------------------------
class MCPClientManager:
    """Singleton manager for all MCP server connections."""

    _instance: Optional['MCPClientManager'] = None
    _instance_lock = threading.Lock()

    def __init__(self, project_root: str = None, config_path: str = None):
        self.project_root = project_root or script_dir
        self.config_path = config_path or os.path.join(self.project_root, '.ga', 'mcp_servers.json')
        self.audit = AuditLogger()
        self.registry = MCPToolRegistry()
        self._connections: dict[str, MCPServerConnection] = {}
        self._config: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._health: Optional[HealthMonitor] = None
        self._hot: Optional[HotReloader] = None
        self._started = False
        MCPClientManager._instance = self

    @classmethod
    def get_instance(cls) -> Optional['MCPClientManager']:
        return cls._instance

    def start(self):
        if self._started:
            return
        self._started = True
        try:
            self._config = load_config(self.config_path)
        except ValueError as e:
            print(f"[MCP ERROR] Config load failed: {e}")
            self._config = {}
        # Connect all servers
        for name, cfg in self._config.items():
            self._connect_server(name, cfg)
        # Start monitors
        self._health = HealthMonitor(self)
        self._health.start()
        self._hot = HotReloader(self)
        self._hot.start()
        atexit.register(self.stop)

    def stop(self):
        if not self._started:
            return
        self._started = False
        if self._health:
            self._health.stop()
        if self._hot:
            self._hot.stop()
        with self._lock:
            for conn in self._connections.values():
                try:
                    conn.disconnect()
                except Exception:
                    pass
            self._connections.clear()
            self.registry.invalidate_all()

    def _connect_server(self, name: str, cfg: dict):
        with self._lock:
            if name in self._connections:
                return  # already connected
            conn = MCPServerConnection(name, cfg, self.audit)
            self._connections[name] = conn
        ok = conn.connect()
        if ok:
            self.registry.update(name, conn.tools)
        else:
            print(f"[MCP WARN] Failed to connect to server '{name}'")

    def _disconnect_server(self, name: str):
        with self._lock:
            conn = self._connections.pop(name, None)
        if conn:
            conn.disconnect()
            self.registry.invalidate(name)

    def call_tool(self, server: str, tool: str, arguments: dict = None) -> dict:
        conn = self._connections.get(server)
        if not conn:
            raise ValueError(f"Unknown MCP server: '{server}'. Available: {list(self._connections.keys())}")
        if conn.state != 'connected':
            raise RuntimeError(f"Server '{server}' is {conn.state}, not connected")
        # Check tool exists
        tool_info = self.registry.get_tool(server, tool)
        if tool_info is None:
            available = [t['name'] for t in self.registry._tools.get(server, [])]
            raise ValueError(f"Unknown tool '{tool}' on server '{server}'. Available: {available}")
        start = time.time()
        try:
            result = conn.call_tool(tool, arguments or {})
            duration = (time.time() - start) * 1000
            self.audit.log(server, tool, arguments, result, duration, True)
            return result
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.audit.log(server, tool, arguments, str(e), duration, False)
            raise

    def get_status(self) -> dict:
        status = {}
        for name, conn in self._connections.items():
            status[name] = {
                'state': conn.state,
                'transport': conn.transport,
                'tools_count': len(conn.tools),
                'last_ping': conn.last_ping,
            }
        return status

    def get_tools_summary(self) -> str:
        return self.registry.get_all_tools_summary()

    def start_server(self, name: str):
        cfg = self._config.get(name)
        if not cfg:
            print(f"[MCP] No config for server '{name}'")
            return
        self._connect_server(name, cfg)

    def stop_server(self, name: str):
        self._disconnect_server(name)

    def restart_server(self, name: str):
        self._disconnect_server(name)
        cfg = self._config.get(name)
        if cfg:
            self._connect_server(name, cfg)

    def reload_config(self):
        """Hot-reload: diff new config against current, apply incremental changes."""
        with self._lock:
            if self._lock.locked():
                pass  # already locked, prevent concurrent reload
        try:
            new_config = load_config(self.config_path)
        except ValueError as e:
            print(f"[MCP ERROR] Hot-reload failed (invalid JSON): {e}. Existing connections preserved.")
            return

        old_names = set(self._config.keys())
        new_names = set(new_config.keys())

        # Removed servers
        for name in old_names - new_names:
            print(f"[MCP] Hot-reload: removing server '{name}'")
            self._disconnect_server(name)
            self._config.pop(name, None)

        # New servers
        for name in new_names - old_names:
            print(f"[MCP] Hot-reload: adding server '{name}'")
            self._config[name] = new_config[name]
            self._connect_server(name, new_config[name])

        # Changed servers
        for name in old_names & new_names:
            if self._config[name] != new_config[name]:
                print(f"[MCP] Hot-reload: restarting server '{name}' (config changed)")
                self._disconnect_server(name)
                self._config[name] = new_config[name]
                self._connect_server(name, new_config[name])

    # Alias for skill_loader compatibility
    def get_all_tools_summary(self) -> str:
        return self.registry.get_all_tools_summary()
