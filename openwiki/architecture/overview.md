---
type: Architecture
title: Core Engine Architecture
description: "The layered architecture of GenericAgent's core engine: agent_loop.py (pure engine loop), agentmain.py (SDK layer), ga.py/ga_utils.py (tool implementations), llmcore.py (LLM communication), mcp_client.py (MCP client), and skill_loader.py (skill discovery)."
resource: agent_loop.py
tags: [architecture, engine, agent-loop, sdk, tools, llm]
---

# Core Engine Architecture

GenericAgent's core is built on a strict layered design: the engine loop (`agent_loop.py`) is pure framework, the SDK (`agentmain.py`) adds business logic, and tool implementations live in `ga.py` / `ga_utils.py` / `tools/`.

## Layer Diagram

```
┌─────────────────────────────────────────────────┐
│  Frontends (TUI, GUI, Web, IM Bots, CLI)        │  ← all use agentmain.GenericAgent
├─────────────────────────────────────────────────┤
│  agentmain.py — SDK Layer                       │
│  GenericAgent class, 4 run modes, task queue     │
├─────────────────────────────────────────────────┤
│  agent_loop.py — Pure Engine                    │
│  agent_runner_loop(), BaseHandler, tool dispatch │
├─────────────────────────────────────────────────┤
│  ga.py / ga_utils.py / tools/ — Tool Layer      │
│  do_* methods, utility functions, drop-in tools  │
├─────────────────────────────────────────────────┤
│  llmcore.py — LLM Communication                 │
│  Session types (Claude, OAI, native, mixin)      │
├─────────────────────────────────────────────────┤
│  mcp_client.py — MCP Client                     │
│  skill_loader.py — Skill Discovery              │
└─────────────────────────────────────────────────┘
```

## agent_loop.py — Pure Engine

`agent_loop.py` (~180 lines) is the framework-agnostic engine. It knows nothing about GenericAgent business logic (no memory, no skills, no frontends). Its sole job is the **LLM ↔ tool loop**.

### `agent_runner_loop()` (line 81)

A **generator** that callers consume incrementally:

1. LLM generates a response → yields text chunks
2. Parse `tool_calls` from the response
3. Dispatch each tool call through `handler.dispatch()` → returns a `StepOutcome`
4. Decide next action based on `StepOutcome`

### `StepOutcome` (line 6) — Control Flow Primitive

| Field | Purpose |
|-------|---------|
| `data` | Tool return value → becomes `tool_results` sent back to LLM |
| `next_prompt` | Next user message. **`None` = task complete** |
| `should_exit=True` | Break out of the loop immediately (used by `ask_user` to pause for human input) |

### Key Design Details

- **History management**: Only new messages are sent each turn (`messages = [{"role": "user", "content": next_prompt}]`). Full conversation history lives in the LLM session (e.g., `ClaudeSession`), not in the loop.
- **Exit conditions**: `should_exit` → `EXITED`; `next_prompt=None` → `CURRENT_TASK_DONE`; turn limit hit → `MAX_TURNS_EXCEEDED`; pending `_done_hooks` extend the loop.
- **Hooks**: Eight hook points (`tool_before/after`, `turn_before/after`, `llm_before/after`, `agent_before/after`) call `_hook()` from `plugins.hooks`. When no plugins are loaded, these are no-ops.
- **Tool dispatch**: Uses a [dual-track dispatch](/openwiki/architecture/tool-dispatch.md) mechanism — method-track first (`do_<name>` on handler), registry-track fallback.

See also: `/docs/architecture.md` (Chinese, deeper dive).

## agentmain.py — SDK Layer

`agentmain.py` provides the `GenericAgent` class — the thread-safe SDK consumed by all 16+ frontends.

### GenericAgent (line 51) — Producer-Consumer

- `put_task(query)` → returns a `display_queue` (line 117)
- `run()` runs in a daemon thread, pulling tasks from the queue (line 138)
- All frontends use this same API

### `run()` Execution Flow (lines 138-193)

1. Dequeue a task → handle slash commands (`/session.k=v`, `/resume`)
2. Long prompts (>2000 chars) are written to disk and referenced by path
3. Build `system_prompt` = base + global_memory + extra_prompts
4. Create a new `GenericAgentHandler`, **inheriting the previous handler's `key_info`** (working memory across tasks, lines 157-161)
5. Call `agent_runner_loop(yield_info=True)` and consume the generator:
   - `{'turn': N}` → update turn counter
   - Text chunks → accumulate, push every 30 chars to `display_queue`
   - Check `_stop` file or `stop_sig` → abort
6. On completion → push `{'done': full_resp}`, update `self.history`

### Four Run Modes

| Mode | Trigger | Use Case |
|------|---------|----------|
| **CLI** | Default (no flags) | Interactive chat |
| **--task** | `--task "query"` | Single-task, exits after completion |
| **--func** | `--func module.fn` | Call a Python function and return |
| **--reflect** | `--reflect` | Autonomous reflection loop |

### `display_queue` Protocol

- `{'next': text, 'turn': N, 'outputs': [...]}` — incremental or full output
- `{'done': full_resp}` — task complete signal

At startup, `agentmain.py` calls `discover_tools(os.path.join(..., 'tools'))` to load all [drop-in tools](/openwiki/architecture/tool-dispatch.md) into the registry.

## ga.py / ga_utils.py — Tool Implementations

### ga.py — `GenericAgentHandler(BaseHandler)`

The handler class (~560 lines after refactoring) defines all `do_*` tool methods. It inherits from `BaseHandler` in `agent_loop.py` and implements:

- **Execution tools**: `do_code_run`, `do_ask_user`
- **File tools**: `do_file_read`, `do_file_write`, `do_file_patch`
- **Web tools**: `do_web_scan`, `do_web_execute_js`
- **Memory tools**: `do_update_working_checkpoint`, `do_start_long_term_update`
- **Skill tools**: `do_get_skill_detail`
- **MCP tools**: `do_mcp_call`
- **Plan mode**: `_in_plan_mode`, `_exit_plan_mode`, `enter_plan_mode`, `_check_plan_completion`
- **Self-evolution helpers**: `_validate_skill_content`, `_set_frontmatter_flag`, `_build_skill_brief`

Module-level utilities originally in `ga.py` were extracted to `ga_utils.py`. `ga.py` re-exports them via named import so `from ga import smart_format, ...` remains backward-compatible.

### ga_utils.py — Utility Function Library

Extracted from `ga.py` during the `refactor-ga-extensibility` change. Organized by responsibility:

| Group | Functions |
|-------|-----------|
| **filetools** | `file_read`, `file_patch`, `_scan_files`, `expand_file_refs`, `log_memory_access` |
| **exectools** | `code_run`, `safe_print` |
| **webtools** | `web_scan`, `web_execute_js`, `first_init_driver` + `driver` global |
| **misc** | `ask_user`, `smart_format`, `consume_file`, `format_error` |

Globals: `script_dir`, `driver`, `_read_dirs`

Behavior, signatures, and logic are unchanged from the pre-extraction `ga.py` — a verbatim relocation.

## llmcore.py — LLM Communication Layer

`llmcore.py` (~77K, the largest file) manages all LLM provider communication through a session abstraction:

| Session Class | Protocol | Use Case |
|---------------|----------|----------|
| `ClaudeSession` | Anthropic Messages API (traditional) | Claude models |
| `LLMSession` | OpenAI-compatible chat API (traditional) | GPT, Gemini, Kimi, MiniMax |
| `NativeClaudeSession` | Anthropic native tool-use | Claude with native tool calling |
| `NativeOAISession` | OpenAI native tool-use | GPT with native tool calling |
| `MixinSession` | Failover across providers | High-availability setups |

The session keeps the full conversation history. `agent_loop.py` only sends the latest user message; the session appends it to its internal message array.

## mcp_client.py — MCP Client

`mcp_client.py` provides `MCPClientManager`, a singleton that manages MCP (Model Context Protocol) server connections. Supports **stdio** (subprocess) and **SSE** (Server-Sent Events) transports. MCP tools are surfaced through the `mcp_call` tool and merged into the skill catalog by `skill_loader.py`.

Referenced by: [Self-Evolution](/openwiki/architecture/self-evolution.md) (skills can depend on MCP tools).

## skill_loader.py — Skill Discovery

`skill_loader.py` manages the progressive-disclosure skill system:

- `sync_skills_to_l1()` — Indexes skills into L1 memory (`global_mem_insight.txt`)
- `get_skill_detail()` — Returns full skill content on demand
- `_get_skills_catalog()` — Returns the skill catalog (name → description + path)
- `backup_skill_prev()` / `revert_skill_prev()` — Backup/restore for safe self-evolution
- `parse_provenance()` — Reads provenance frontmatter from `SKILL.md`
- `validate_skill()` — Validates skill structure

Skills follow the [agentskills.io](https://agentskills.io) standard and live in `.agents/skills/<name>/SKILL.md`. The skill catalog is injected into the system prompt for LLM awareness.

## Where to Start When Changing This Area

- **Adding a tool to the handler**: Edit `ga.py`, add a `do_<name>` method
- **Adding a drop-in tool**: Drop a `.py` file into `tools/` with `@register_tool("name")` — see [Tool Dispatch](/openwiki/architecture/tool-dispatch.md)
- **Changing the engine loop**: Edit `agent_loop.py`; run `pytest tests/test_agent_loop_dispatch.py tests/test_tool_registry.py`
- **Changing LLM communication**: Edit `llmcore.py`; no dedicated tests, rely on integration/e2e
- **Adding a new LLM provider**: Add a session class in `llmcore.py`, configure in `mykey.py`
- **Build, release, or CLI changes**: See [Infrastructure](/openwiki/infrastructure/overview.md) for `ga_cli/`, runner subsystem, and release scripts
