# Architecture Review — 499eb19

- **Date:** 2026-07-16
- **Branch:** dev
- **Reviewed change:** `499eb19` `fix(mcp): inject MCP into system prompt, fix hot-reload locks, add L2 distill + real e2e`
- **Diff source:** `git show 499eb19 -- agentmain.py mcp_client.py plugins/mcp_distill.py pyproject.toml` (dry-run on a past commit; `gh` not installed, no PR)
- **Files reviewed (code):** `agentmain.py`, `mcp_client.py`, `plugins/mcp_distill.py` (new), `pyproject.toml`.
- **Baseline:** `docs/architecture.md` (engine vs SDK layer, `StepOutcome`, `display_queue`, 4 run modes, 5 design decisions), `MAP.md` (module map), `AGENTS.md` (conventions), `CONTRIBUTING.md` (let-it-crash, small change radius, no new deps for refactors).
- **Method:** custom 8-item architecture checklist (no existing arch skill). Blast-radius via static grep (no `.codegraph/` index).
- **Severity:** Critical / Important / Minor

## Verdict
**0 Critical · 1 Important · 3 Minor**

## Checklist findings

### Important

**A1. Layer coupling — SDK prompt assembly reaches into MCP infra singleton**
- **Checklist item:** #1 Layer separation, #6 Design-decision alignment.
- **Where:** `agentmain.py` `get_system_prompt()` → `MCPClientManager.get_instance().get_all_tools_summary()`.
- **What:** The system-prompt builder (SDK layer) now directly imports and calls the MCP client singleton, concatenating its output into the prompt.
- **Why:** `docs/architecture.md` separates the engine loop (`agent_loop.py`) from the SDK layer (`agentmain.py`); the system prompt is SDK-layer. Directly depending on `MCPClientManager` couples the SDK/prompt layer to a concrete MCP infra module — the prompt now "knows about" MCP specifics. The `if MCPClientManager is not None` guard mitigates **import-time** coupling (MCP optional), but not **runtime** coupling (SDK depends on MCP's API shape).
- **Suggested fix:** Introduce a tool-catalog abstraction the prompt layer queries (e.g., `get_external_tool_summary()`), with MCP as one provider behind it — so the prompt layer depends on an interface, not the MCP singleton.

### Minor

**A2. Implicit hook contract**
- **Checklist item:** #2 Abstraction boundaries.
- **Where:** `plugins/mcp_distill.py:40-46` reads `ctx['tool_name']`, `ctx['args']`, `ctx['ret']`, and `getattr(ret, 'data', None)`.
- **What:** The plugin encodes assumptions about the `tool_after` hook's `ctx` shape and the tool-return `.data` attribute.
- **Why:** If this contract isn't documented in the baseline (`MAP.md`/`AGENTS.md`/`docs/architecture.md`), it's implicit coupling — a change to the return shape silently breaks the plugin (it logs `FAIL` forever with no error).
- **Suggested fix:** Document the `tool_after` hook contract (ctx keys, return shape) in the baseline; have the plugin degrade visibly on contract mismatch.

**A3. Hot-reload concurrency window**
- **Checklist item:** #3 Blast radius, #1 Layer separation (concurrency invariant).
- **Where:** `mcp_client.py` `reload_config()` — `self._config` mutated under `_lock`, but `_disconnect_server`/`_connect_server` called **outside** `_lock`.
- **What:** Between releasing `_lock` (config now new) and reconnecting, `self._config[name]` reflects the new config while `self._connections[name]` still reflects the old connection.
- **Why:** A concurrent reader (`get_all_tools_summary`, a tool call) can observe inconsistent state during the window. Low risk (hot-reload is infrequent, window brief), but the sequencing invariant is now non-obvious.
- **Suggested fix:** Document the invariant ("config updated first, connections reconciled after; readers may see a brief mismatch during reload") or reconcile connections under the lock if deadlock can be avoided another way.

**A4. Test-config workaround masks deeper issue**
- **Checklist item:** #4 New dependencies / #5 Error handling.
- **Where:** `pyproject.toml` `-p no:langsmith_plugin`.
- **What:** Disabling the plugin masks a `packaging` namespace-shadowing root cause (same as security S5).
- **Why:** A shadowed `packaging` can break other imports, not just tests; the workaround hides it.
- **Suggested fix:** Fix the shadowing at source; keep the disable as a documented stopgap only.

## Positive
- **Test architecture (item #7):** `tests/test_mcp_e2e.py` adds a **real stdio MCP server** e2e (connect/list/call/disconnect/not-found + hot-reload add/remove), no mocks — matches `AGENTS.md`'s pytest conventions and tests the right layer (real integration, not unit-mocked).
- **Small change radius (item #8):** 6 files, all MCP-focused (prompt injection + hot-reload fix + distill + e2e) — focused, not sprawling.
- **Dead-code removal:** the meaningless `if self._lock.locked(): pass` guard is gone.

## Checkpoint
**Verdict: PASS** — approved by user (2026-07-16). 1 Important (A1, layer coupling) accepted as advisory; 3 Minor noted. Dry-run complete.
