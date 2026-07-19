---
type: Overview
title: GenericAgent Quickstart
description: Entry point for the GenericAgent OpenWiki knowledge base. Covers the high-level architecture, design philosophy, directory map, and links to all major documentation sections.
tags: [quickstart, overview, genericagent]
---

# GenericAgent — OpenWiki

**GenericAgent** is a minimal, self-evolving autonomous agent framework. Its core is **~3K lines of seed code** — through **9 atomic tools** and a **~100-line Agent Loop**, it grants any LLM system-level control over a local computer (browser, terminal, filesystem, keyboard/mouse, screen vision, and mobile devices via ADB).

> Design philosophy: **don't preload skills, evolve them.** Every time GenericAgent solves a new task, it automatically crystallizes the execution path into a reusable Skill.

## Repository at a Glance

| Layer | Purpose | Key Files |
|-------|---------|-----------|
| **Engine** | LLM↔tool loop, tool dispatch | `agent_loop.py` |
| **SDK** | Multi-mode entry, thread-safe task queue | `agentmain.py` |
| **Tools** | Handler methods + drop-in registry tools | `ga.py`, `ga_utils.py`, `tools/` |
| **LLM Comms** | Multi-protocol session management | `llmcore.py` |
| **Self-Evolution** | Post-task skill distillation, provenance | `plugins/skill_evolution.py`, `tools/skill_manage.py` |
| **Frontends** | 16+ interfaces (TUI, GUI, Web, IM Bots) | `frontends/`, `ga_rust_cli/`, `ga_rust_tui/` |
| **Infrastructure** | CLI, runners, build/release, testing | `ga_cli/`, `scripts/`, `tests/` |

## Documentation Map

- **[Architecture: Core Engine](/openwiki/architecture/overview.md)** — `agent_loop.py`, `agentmain.py`, `ga.py`/`ga_utils.py`, `llmcore.py`, `mcp_client.py`, `skill_loader.py`. The layered design that makes GA tick.
- **[Architecture: Tool Dispatch](/openwiki/architecture/tool-dispatch.md)** — Dual-track dispatch (method + registry), drop-in tool registration, the `tools/` directory, and the `refactor-ga-extensibility` change that introduced it all.
- **[Architecture: Self-Evolution](/openwiki/architecture/self-evolution.md)** — Hermes post-task skill distillation, the `skill_manage` tool, the `skill_evolution` plugin, provenance tracking, circuit breakers, and the L1-L4 memory system.
- **[Frontends](/openwiki/frontends/overview.md)** — All user-facing interfaces: TUI (Python + Rust), GUI, Web, IM Bots, and the Rust CLI.
- **[Infrastructure](/openwiki/infrastructure/overview.md)** — CLI tools (`ga_cli/`), the goal subsystem, runner IPC, build/release pipelines, and test infrastructure.

## Existing Documentation

This wiki complements, not replaces, the repo's existing documentation:

- `README.md` — Project overview, quick start, demos, comparison
- `MAP.md` — Module map with file roles and directory structure (Chinese)
- `docs/architecture.md` — Deep dive on engine + SDK layering (Chinese)
- `docs/ga-refactor-strategy.md` — Analysis behind the refactor-ga-extensibility work
- `HANDOFF.md` — Hermes self-evolution state, artifacts, and ecosystem map
- `hermes/NOTES.md` — Reference research on hermes-agent and jiuwenswarm
- `hermes/workflows/post-task-skill-distillation.md` — FINAL v1 buildable spec
- `AGENTS.md` — Build/verify commands for agent collaboration

## Key Design Principles

1. **Minimal seed, emergent capability** — ~3K lines of core code, capabilities grow through skill crystallization
2. **Layered separation** — `agent_loop.py` is pure engine (no GA business logic); `agentmain.py` is SDK; `ga.py` is tools
3. **Drop-in extensibility** — Add a tool by dropping a `@register_tool`-decorated `.py` file into `tools/`; zero edits to `ga.py` or handler class
4. **Controlled self-evolution** — Agent-authored skill changes are provenance-tracked (`ContextVar`), gated behind `GA_SKILL_EVOLUTION_ENABLED`, and circuit-broken
5. **Thread-safe SDK** — All 16+ frontends consume the same `GenericAgent` producer-consumer API

## Backlog

- Detailed per-tool documentation (9 atomic tools in `ga.py`) — deferred: well-covered by inline source and `MAP.md`
- Langfuse tracing plugin internals — deferred: operational concern, not architectural
- Desktop Tauri app (`frontends/desktop/`) — deferred: secondary frontend, in flux
- Reflect/scheduled automation layer — deferred: already covered in `docs/reflect.md`
