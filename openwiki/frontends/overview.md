---
type: Architecture
title: Frontend Ecosystem
description: "All user-facing interfaces for GenericAgent: Python TUI (tui_v3, Textual v1/v2), Qt GUI, desktop pet, Streamlit web apps, FastAPI conductor, IM bots, Tauri desktop app, and Rust CLI/TUI components."
resource: frontends/
tags: [frontends, tui, gui, web, bots, rust, ui]
---

# Frontend Ecosystem

GenericAgent supports 16+ frontends, all consuming the same `GenericAgent` SDK from `agentmain.py`. Every frontend calls `agent.put_task(query)` and reads from the returned `display_queue` — the producer-consumer pattern is universal.

## Common API

All frontends share the same integration pattern:

```python
from agentmain import GenericAgent
agent = GenericAgent()
display_queue = agent.put_task("your query")
agent.run()  # runs in daemon thread
for chunk in display_queue:
    # render chunk in your UI
```

The `display_queue` yields `{'next': text, 'turn': N}` for incremental output and `{'done': full_resp}` on completion. See [Core Engine Architecture](/openwiki/architecture/overview.md) for protocol details.

## Python Frontends (`frontends/`)

### TUI (Terminal UI)

| File | Stack | Notes |
|------|-------|-------|
| `tui_v3.py` | prompt_toolkit + rich | **Recommended TUI**. ~6400-line single file, scrollback-first, most feature-complete |
| `tuiapp_v2.py` | Textual | V2 TUI |
| `tuiapp.py` | Textual | V1 TUI (legacy) |

`tui_v3.py` is the primary terminal interface. It uses `prompt_toolkit` for input handling and `rich` for formatted output. The scrollback-first design preserves full conversation history in the terminal.

### GUI (Graphical)

| File | Stack | Notes |
|------|-------|-------|
| `qtapp.py` | PySide6 | Qt desktop app with chat panel + floating button |
| `desktop_pet_v2.pyw` | PIL + HTTP Server | Desktop pet with skin system (`skins/`) |
| `desktop/` | Tauri | Tauri desktop application (`src-tauri/`) |

`qtapp.py` provides a native Qt window with a chat interface. `desktop_pet_v2.pyw` is a lightweight desktop companion. The Tauri desktop app (`frontends/desktop/`) is a work-in-progress cross-platform desktop application.

### Web

| File | Stack | Notes |
|------|-------|-------|
| `stapp.py` | Streamlit | Web chat UI |
| `stapp2.py` | Streamlit | Web chat UI v2 |
| `conductor.py` | FastAPI + WebSocket | Multi-session orchestrator with WebSocket API and `conductor.html` |

`conductor.py` is a multi-session FastAPI server that manages concurrent agent sessions over WebSocket connections. It serves `conductor.html` as the web client.

### IM Bots

All bot frontends inherit from `chatapp_common.AgentChatMixin`:

| File | Platform | Dependency |
|------|----------|------------|
| Telegram | `python-telegram-bot` | `frontends/` |
| QQ | `qq-botpy` | `frontends/` |
| WeChat Work | `wecom-aibot-sdk` | `frontends/` |
| Feishu/Lark | `lark-oapi` | `frontends/` |
| DingTalk | `dingtalk-stream` | `frontends/` |

## Rust Components

### `ga_rust_cli/` — Rust CLI

A Rust implementation of the GA CLI, providing a native binary alternative to the Python `ga_cli/` package. Built as a Cargo project with:

| Component | Path |
|-----------|------|
| CLI commands | `src/cli/commands.rs` |
| Core engine | `src/core/` (approval, config, daemon, goal, orchestrator) |
| IPC layer | `src/ipc/` (event stream, protocol, PTY, server) |
| Runner system | `src/runner/` (GA adapter, registry, subprocess adapter, trait) |
| Data store | `src/store/` (models, schema) |
| Entry point | `src/main.rs` |

Build: `cargo build` in `ga_rust_cli/`. Tests: `cargo test`.

### `ga_rust_tui/` — Rust TUI

A Rust-native terminal UI for GenericAgent, providing a TUI alternative to the Python `tui_v3.py`. Built as a Cargo project with:

| Component | Path |
|-----------|------|
| App state | `src/app.rs` |
| Approval UI | `src/approval.rs` |
| Configuration | `src/config.rs` |
| IPC client | `src/ipc.rs` |
| Panes | `src/pane.rs` |
| Theme | `src/theme.rs` |
| Timeline | `src/timeline.rs` |
| UI rendering | `src/ui.rs` |

Build: `cargo build` in `ga_rust_tui/`. Tests: `cargo test`.

Both Rust components were recently renamed: `ga_rust/` → `ga_rust_cli/` and `frontends/ga-tui/` → `ga_rust_tui/` to clarify their roles.

## `go-agents/`

Previously contained a Go implementation of the agent (deleted in recent churn). Currently contains only `.cache/` and `python-bundle/` — effectively empty. The Go agent experiment has been archived.

## Where to Start When Changing This Area

- **Adding a new frontend**: Import `GenericAgent` from `agentmain`, call `put_task()` / `run()`, consume `display_queue`
- **Modifying tui_v3.py**: The 6400-line single file is dense but self-contained; look for `GenericAgent` instantiation and the main loop
- **Modifying a bot**: All bots follow the same pattern via `AgentChatMixin`; check the `frontends/` directory for the specific platform
- **Modifying Rust components**: Each is a standard Cargo project with `src/main.rs` as entry point
