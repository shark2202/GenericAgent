# ga — GenericAgent Core Daemon + CLI

A single Rust binary providing daemon-mode orchestration, session management,
event streaming, and approval-gated tool execution for GenericAgent.

## Quick Start

```bash
# Build
cargo build

# Run (uses launch script — auto-finds binary)
./ga status

# Or directly
cargo run -- status
```

### Windows

```cmd
ga.cmd status
```

## Commands

| Command | Description |
|---------|-------------|
| `ga status` | Show active sessions and daemon state |
| `ga session new --runner=ga --name=N` | Create a GA agent session |
| `ga session new --runner=generic --name=N --command="bash"` | Create a PTY session |
| `ga session list [--json]` | List active sessions |
| `ga session watch ID` | Watch a session's event stream |
| `ga session send ID INPUT` | Send input to a session |
| `ga session archive ID` | Archive a session |
| `ga daemon --detach` | Start daemon in background |
| `ga daemon --foreground` | Start daemon in foreground |
| `ga daemon --stop` | Stop the daemon |
| `ga daemon-status` | Check if daemon is running |
| `ga approval list [--json]` | List pending approvals |
| `ga approval ID --approve` | Approve a pending request |
| `ga approval ID --deny` | Deny a pending request |
| `ga config set KEY VALUE` | Set a configuration value |

## Configuration

Key configuration paths (stored in `~/.config/ga/` or `%APPDATA%\ga\`):

| Key | Default | Description |
|-----|---------|-------------|
| `approval.yolo` | `false` | Auto-approve all tool calls |
| `approval.allowlist` | `[]` | Tools always approved (Low risk) |
| `approval.default_risk` | `Medium` | Default risk for unknown tools |

## Architecture

```
┌──────────┐     IPC      ┌──────────────┐
│  ga CLI  │◄────────────►│  ga daemon   │
└──────────┘              │              │
                          │ ┌──────────┐ │
                          │ │ Store    │ │  (SQLite)
                          │ └──────────┘ │
                          │ ┌──────────┐ │
                          │ │ Runner   │ │  (GA / subprocess)
                          │ │ Registry │ │
                          │ └──────────┘ │
                          │ ┌──────────┐ │
                          │ │ Approval │ │  (risk policy)
                          │ │ Gate     │ │
                          │ └──────────┘ │
                          └──────────────┘
```

- **IPC**: Unix domain socket (Unix) / named pipe (Windows)
- **Store**: SQLite via rusqlite (bundled)
- **Runners**: Pluggable trait-based runner system (GA adapter, subprocess adapter)
- **Approval**: Risk-classified tool execution with allowlist/YOLO bypass

## Testing

### Unit + Integration Tests

```bash
cargo test                    # All tests
cargo test --test e2e_daemon  # §9.1 daemon lifecycle
cargo test --test e2e_approval # §9.2 approval closed loop
```

### E2E Bash Scripts

```bash
# §9.1: Daemon lifecycle + reattach
bash scripts/e2e_daemon_lifecycle.sh [path/to/ga]

# §9.2: Approval closed loop
bash scripts/e2e_approval.sh [path/to/ga]

# §9.3: Cross-platform build + smoke test
bash scripts/cross_platform_smoke.sh [--release]
```

### Prerequisites for E2E Scripts

- `jq` (for JSON parsing in bash scripts)
- Built `ga` binary (scripts auto-detect `target/debug/ga`)

## Cross-Platform

Supported targets:

| Target | Notes |
|--------|-------|
| `x86_64-pc-windows-msvc` | Primary Windows target |
| `x86_64-unknown-linux-gnu` | Primary Linux target |
| `aarch64-apple-darwin` | macOS Apple Silicon |
| `x86_64-apple-darwin` | macOS Intel |

Build for a specific target:

```bash
rustup target add x86_64-unknown-linux-gnu
cargo build --target x86_64-unknown-linux-gnu
```

## Project Structure

```
src/
├── main.rs              # Entry point
├── cli/
│   ├── mod.rs
│   └── commands.rs      # CLI command dispatch (clap)
├── core/
│   ├── mod.rs
│   ├── config.rs        # Configuration management
│   ├── daemon.rs        # Daemon mode (PID file, IPC server)
│   ├── orchestrator.rs  # Session orchestration
│   └── approval.rs      # Risk policy + approval gate
├── ipc/
│   ├── mod.rs
│   ├── protocol.rs      # IPC message types
│   ├── server.rs        # IPC server (Unix socket / named pipe)
│   ├── event_stream.rs  # Event streaming to CLI
│   └── pty.rs           # PTY management
├── runner/
│   ├── mod.rs
│   ├── trait_def.rs     # Runner trait
│   ├── registry.rs      # Runner registry
│   ├── ga_adapter.rs    # GenericAgent adapter
│   └── subprocess_adapter.rs  # Generic subprocess adapter
└── store/
    ├── mod.rs
    ├── models.rs        # Data models
    └── schema.rs        # SQLite schema
tests/
├── e2e_daemon_lifecycle.rs  # §9.1 integration test
└── e2e_approval.rs         # §9.2 integration test
scripts/
├── e2e_daemon_lifecycle.sh # §9.1 bash E2E
├── e2e_approval.sh         # §9.2 bash E2E
└── cross_platform_smoke.sh # §9.3 cross-platform smoke
ga                          # §9.4 Unix launch script
ga.cmd                      # §9.4 Windows launch script
```

## License

GenericAgent Internal
