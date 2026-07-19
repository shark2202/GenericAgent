---
type: Architecture
title: Tool Dispatch System
description: "Dual-track tool dispatch mechanism in agent_loop.py: method-track (do_<name> on handler) with registry-track fallback, plus the drop-in tools/ directory for zero-edit extensibility."
resource: agent_loop.py
tags: [architecture, tool-dispatch, registry, extensibility, drop-in]
---

# Tool Dispatch System

GenericAgent uses a **dual-track dispatch** mechanism that routes LLM tool calls to implementations. The system was introduced in the `refactor-ga-extensibility` change and is formalized in `openspec/specs/tool-dispatch/spec.md`.

## Dual-Track Dispatch

When the LLM calls a tool, `BaseHandler.dispatch()` (line 49 in `agent_loop.py`) resolves it in two stages:

1. **Method-track (priority)**: Look for `do_<tool_name>` on the handler instance via `hasattr(self, method_name)`. If found, call it with `_index` and `_tool_num` injected.
2. **Registry-track (fallback)**: If no method exists, consult `_TOOL_REGISTRY`. If the tool name is registered, call the registered function with `(handler, args, response)`.
3. **Unknown tool**: If neither track matches, yield `"未知工具"` and return a `StepOutcome` with a "未知工具" next_prompt.

Both tracks share the same `_index`/`_tool_num` injection and `tool_before`/`tool_after` hooks.

```
dispatch(tool_name, args, response)
  ├── hasattr(self, "do_<tool_name>")? → YES → call self.do_<name>(args, response)  [method-track]
  ├── NO → get_tool(tool_name) exists?  → YES → call fn(handler, args, response)   [registry-track]
  └── NO → yield "未知工具"                                                          [unknown]
```

## Tool Registry API

The registry is a module-level `dict` in `agent_loop.py` with three functions:

| Function | Purpose |
|----------|---------|
| `register_tool(name)` | Decorator that binds a function under `name` in `_TOOL_REGISTRY` |
| `get_tool(name)` | Look up a registered tool by name, returns `None` if not found |
| `discover_tools(tools_dir)` | Scan a directory for non-underscore `.py` modules and import them (triggering `@register_tool` self-registration) |

### Signature Contract

Registry-track functions must have the signature `(handler, args, response) -> StepOutcome`, mirroring the method signature `do_*(self, args, response)` with `handler` replacing `self`. The handler parameter gives access to instance state like `handler.cwd` and `handler._pending_briefs`.

### Discovery Behavior

`discover_tools()` is called at startup from `agentmain.py`:

```python
discover_tools(os.path.join(os.path.dirname(__file__), 'tools'))
```

- Non-underscore `.py` files are imported
- Per-module import failures write to `stderr` (not silently swallowed)
- Missing directory is a silent no-op (allows running without `tools/`)

**Layering invariant**: `agent_loop.py` never imports business modules (`ga.py`, `tools/*`). Business modules are loaded at runtime through `discover_tools()`, keeping the engine pure.

## The `tools/` Directory

The `tools/` directory holds drop-in tool modules. Adding a new tool requires only:

1. Create a `.py` file in `tools/` (must not start with `_`)
2. Decorate a function with `@register_tool("tool_name")` from `agent_loop`
3. Implement `(handler, args, response) -> StepOutcome`

**Zero edits** to `ga.py` or the `GenericAgentHandler` class body.

### Current Drop-In Tools

| File | Tool Name | Purpose |
|------|-----------|---------|
| `tools/skill_manage.py` | `skill_manage` | Self-evolution skill CRUD (create/patch/retire/list_evolvable) |

`skill_manage` was migrated from `GenericAgentHandler.do_skill_manage` during the refactoring to demonstrate the drop-in loop. It is [the primary tool for Hermes self-evolution](/openwiki/architecture/self-evolution.md).

## Backward Compatibility

Existing `do_*` methods (e.g., `do_code_run`, `do_file_read`, `do_skill_manage` — the latter was migrated to registry but the method could coexist) continue to dispatch through the method-track with zero behavior change. The dual-track design ensures:

- Method-track always takes priority (even if a same-named registry tool exists)
- Registry-track is strictly additive — it only activates when no method exists
- Arg injection (`_index`, `_tool_num`) is identical across both tracks
- `StepOutcome` shape and side effects are unchanged for existing tools

## Tests

| Test File | What It Covers |
|-----------|---------------|
| `tests/test_tool_registry.py` | `register_tool`, `get_tool`, `discover_tools` correctness |
| `tests/test_agent_loop_dispatch.py` | Dual-track dispatch: method priority, registry fallback, unknown tool, arg injection |
| `tests/test_skill_manage_registry.py` | `skill_manage` via registry-track: create, patch, retire, list_evolvable |

Run: `pytest tests/test_tool_registry.py tests/test_agent_loop_dispatch.py tests/test_skill_manage_registry.py`

## Where to Start When Changing This Area

- **Adding a new drop-in tool**: Create `tools/my_tool.py` with `@register_tool("my_tool")`. The function receives `(handler, args, response)`.
- **Changing dispatch logic**: Edit `BaseHandler.dispatch()` in `agent_loop.py`. Run dispatch tests.
- **Adding registry features**: Edit the `_TOOL_REGISTRY` / `register_tool` / `discover_tools` section at the top of `agent_loop.py`. Run registry tests.
