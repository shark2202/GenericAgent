---
type: Reference
title: Infrastructure and Operations
description: "Build, release, CLI tooling, the goal/runner subsystem, testing infrastructure, and development conventions for GenericAgent."
resource: ga_cli/
tags: [infrastructure, cli, build, testing, release, runners, ci]
---

# Infrastructure and Operations

This page covers the operational infrastructure around GenericAgent's core: the Python CLI (`ga_cli/`), the goal/runner subsystem, build and release tooling, testing conventions, and CI. All CLI and runner components interact with the [Core Engine](/openwiki/architecture/overview.md) through the `GenericAgent` SDK.

## ga_cli/ — Python CLI Package

The `ga_cli/` package provides the `ga` command-line interface. It is installed via `pip install -e .` and exposes `ga` as a console script (see `pyproject.toml` `[project.scripts]`).

| File | Purpose |
|------|---------|
| `cli.py` | Multi-subcommand CLI: `ga list` (list frontends/reflect), `ga status`, `ga update` |
| `mcp_cli.py` | MCP subcommands: `ga mcp list/start/stop/restart` — manage MCP server lifecycle |
| `__main__.py` | `python -m ga_cli` entry point, calls `cli.main()` |
| `setup_wizard.py` | Interactive setup wizard for first-time configuration |

### Goal/Runner Subsystem (New — Uncommitted)

Four new files in `ga_cli/` implement a **goal-oriented runner** that manages isolated agent subprocesses:

| File | Purpose |
|------|---------|
| `goal_cli.py` | CLI interface for goal-oriented task management |
| `goal_controller.py` | Controller that orchestrates goal execution across runner processes |
| `runner_bridge.py` | Bridge between controller and runner subprocess (stdin/stdout JSON Lines protocol) |
| `runner_ipc.py` | IPC protocol types: `Command`, `Event`, `Run`, `Abort`, `Ready`, `TurnStart`, `FinalAnswer`, etc. |
| `runner_process.py` | `RunnerProcess` class — owns a bridge subprocess, manages its three pipes, decodes event stream |

The runner subsystem uses a JSON Lines protocol over stdin/stdout pipes. Key events include `Ready`, `TurnStart`, `ToolCallStart`, `ToolCallEnd`, `FinalAnswer`, `Error`, and `Exited`. The `RunnerProcess` class provides a context-manager lifecycle with `kill_on_drop` cleanup.

Tests: `tests/test_goal_cli_e2e.py`, `tests/test_goal_controller.py`, `tests/test_goal_e2e.py`

## Build and Release

### Python Package

Managed via `pyproject.toml`:
- Package name: `genericagent`
- Python: `>=3.10,<3.14` (3.14 incompatible with `pywebview`)
- Extras: `[ui]` for GUI/TUI, `[all-frontends]` for bot dependencies
- Lint: `ruff check .` (E, F, W, I, UP, B; E501 ignored)
- Test: `pytest tests/`

### Release CI

- **Portable distribution**: Push `v*` tag → `.github/workflows/release.yml` (4-architecture cross-build, produces self-contained CPython portable archive). Planned deprecation in favor of local installer.
- **Local assembly**: `scripts/assemble-dist-local.sh`
- **Python bundling**: `scripts/bundle-python.sh <arch>` (win-x64, mac-arm64, mac-x64, linux-x64)
- **Local release**: `scripts/release-local.sh <arch> [--dry-run]`
- **Installer build**: `scripts/build-installer.sh <arch>` (Win=Inno `.exe`, mac=`.pkg`, Linux=`.deb`+`.rpm`)
- **Installer testing**: `scripts/installers/TESTING.md` (per-version manual checklist)

### OpenWiki CI

`.github/workflows/openwiki-update.yml` is a newly added scheduled workflow for automatic OpenWiki documentation refreshes.

## Testing Infrastructure

All tests live in `tests/` and use pytest (configured in `pyproject.toml`).

### Test Categories

| Area | Test Files |
|------|-----------|
| **Tool Registry** | `test_tool_registry.py`, `test_agent_loop_dispatch.py` |
| **Skill Evolution** | `test_skill_evolution.py`, `test_skill_evolution_plugin.py`, `test_skill_manage_registry.py` |
| **Goal/Runner** | `test_goal_cli_e2e.py`, `test_goal_controller.py`, `test_goal_e2e.py` |
| **MCP** | `test_mcp_client.py`, `test_mcp_e2e.py`, `test_mcp_ga_integration.py`, `test_mcp_memory_integration.py` |
| **Skill Loader** | `test_skill_loader_l1.py` |
| **Utilities** | `test_ga_utils_import.py` |

### Running Tests

```bash
# All tests
pytest tests/

# Specific area
pytest tests/test_tool_registry.py tests/test_agent_loop_dispatch.py
pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py
pytest tests/test_goal_controller.py tests/test_goal_e2e.py

# With coverage
pytest tests/ --cov
```

### Test Configuration

From `pyproject.toml`:
- `testpaths = ["tests"]`
- `addopts = "-ra --strict-markers -p no:langsmith_plugin"`
- Coverage source: `ga_cli`, `mcp_client`, `agent_loop`, `agentmain`, `ga`, `llmcore`, `simphtml`, `TMWebDriver`, `skill_loader`

### Known Gaps

- **No type checking**: mypy/pyright not configured
- **No dev CI**: Only release CI (tag-triggered) exists; dev/test/staging CI not yet landed
- **Historical lint violations**: Existing code has known ruff violations; new code should not introduce new ones
- **Go subproject**: `go-agents/` has been deleted; no Go tests to run

## Development Conventions

From `AGENTS.md` and `CONTRIBUTING.md`:

- **Python**: 3.11 or 3.12 (not 3.14)
- **Package manager**: `uv`
- **Install**: `uv pip install -e ".[ui]"`
- **Branches**: `main` (primary), `dev` (integration). Tag `v*` on `dev` after verification.
- **Net line count**: Refactors should aim for ≤0 net line increase
- **Small change radius**: Prefer narrow, focused changes
- **Let-it-crash**: No blanket try/except; let errors surface
- **No new deps**: Avoid adding dependencies without strong justification
- **Self-documenting**: Minimal comments; code should explain itself

## Where to Start When Changing This Area

- **Adding a CLI command**: Edit `ga_cli/cli.py`, add a new subcommand
- **Adding an MCP subcommand**: Edit `ga_cli/mcp_cli.py`
- **Modifying the runner protocol**: Edit `ga_cli/runner_ipc.py` (types) and `ga_cli/runner_process.py` (lifecycle)
- **Modifying release CI**: `.github/workflows/release.yml` (tag-triggered) or `scripts/release-local.sh`
- **Adding tests**: Follow existing patterns in `tests/`; use pytest conventions
- **Build/installer**: Start with `scripts/assemble-dist-local.sh` and `scripts/build-installer.sh`
