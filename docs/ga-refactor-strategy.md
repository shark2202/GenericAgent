# ga.py Refactor Strategy — analysis (pre-Comet)

> **Status:** analysis note, **not** a Comet change. Produced 2026-07-16 from a
> structural read of `ga.py` + `agent_loop.py` + the repo's existing audit/review
> artifacts. Goal: decide *how* to optimize/refactor `ga`, scoped to three user
> picks — **split the giant file · tool-dispatch architecture · readability**.
> If we later formalize, this becomes the seed of a Comet `full` change's design.
>
> **Read alongside:** the audit is the primary prior art — this doc references
> its finding IDs (F1, F2, F3, F9, F26, F27…) rather than re-deriving them.

## Inputs consumed (don't re-derive)

| Artifact | What it gave |
|---|---|
| `docs/audits/audit-report-genericagent-2026-07-16.html` | **58-finding audit** (dev @ 8d78780). ga.py findings F1(Critical)/F2/F3/F9/F26/F27 + maintainability callouts on `do_skill_manage` & `turn_end_callback`. Scores: Security D(2.5), Maintainability C(4.5), Design C(4.5), Testing C(3.0), Overall C 3.9. |
| `docs/reviews/2026-07-16-dev-499eb19-architecture.md` / `-security.md` | MCP-change reviews (A1 layer-coupling, S1 prompt-injection, S3 swallowed-except, S5 namespace-shadow). Patterns, not ga.py-scoped. |
| `docs/architecture.md` | Baseline layering: `agent_loop.py` (pure engine) / `agentmain.py` (SDK) / `ga.py` (tools). Decision #4: BaseHandler reusable, `do_<name>` convention. |
| `MAP.md` | Module map (last updated 2026-07-08 — **stale**: predates hermes `do_skill_manage` + subagents). |
| `CONTRIBUTING.md` | **Hard governors**: net line count ≤0 for refactors · small change radius · let-it-crash (no blanket try/except) · no new deps · self-documenting/minimal comments. PR checklist: "can I modify this locally without reading the whole codebase?" |
| `docs/plugins.md` | Hook system: `discover_and_load()` auto-imports `plugins/`. Plugins = event subscribers; `do_*` = explicitly-invoked tools. |
| `docs/superpowers/specs/2026-07-16-add-first-class-subagents-design.md` | **In-flight (build, 0/31)** — adds `do_task`/`do_submit_result` + root-level `subagent_manager.py` to ga.py. Establishes the "root service class + do_* mirror of do_skill_manage" pattern. **A refactor of ga.py must coordinate with this.** |

## ga.py current shape (~840 lines)

Three concerns crammed in one file:

1. **Module-level pure utilities (lines 13–264, ~250 lines)** — `safe_print`,
   `code_run`(+`stream_reader`), `ask_user`, `first_init_driver`, `web_scan`,
   `web_execute_js`, `format_error`, `log_memory_access`, `expand_file_refs`,
   `file_patch`, `_scan_files`, `file_read`, `smart_format`, `consume_file`.
   Globals: `driver=None`, `_read_dirs=set()`, `script_dir`. Stateless w.r.t.
   the handler (except the `driver` web global). Heavy top-level imports
   (`subprocess, traceback, webbrowser, importlib, …`) only some utils need.
2. **`class GenericAgentHandler(BaseHandler)` (266–822, ~28 methods)** —
   tool dispatchers (`do_code_run/ask_user/web_scan/web_execute_js/file_patch/
   file_write/file_read/update_working_checkpoint/get_skill_detail/skill_manage/
   mcp_call/no_tool/start_long_term_update`), plan-mode quartet
   (`_in_plan_mode/_exit_plan_mode/enter_plan_mode/_check_plan_completion`),
   hermes helpers (`_validate_skill_content/_set_frontmatter_flag/_build_skill_brief`),
   memory/prompt (`_fold_earlier/_get_anchor_prompt/turn_end_callback/export_history/
   _retry_or_exit`), path/code helpers (`_get_abs_path/_extract_code_block`).
3. **`get_global_memory()` (823)** — memory-system prompt builder.

**Dispatch mechanism:** `BaseHandler.dispatch` (`agent_loop.py:18-29`) does
`getattr(self, f"do_{tool_name}")` — already convention-based (not if/elif),
with `tool_before`/`tool_after` hooks around it. Falls back to `bad_json` /
"未知工具".

## The reframe that matters (audit-driven)

The audit's systemic finding is a complete **injection → unsandboxed code-exec →
credential-exfil** chain. **Every link routes through `BaseHandler.dispatch`**:
- F1 (Critical): `do_code_run` `inline_eval` runs `eval()`/`exec()` in-process
  with `parent` (→ `api_key`) in the namespace. No per-tool authz gate.
- F2: `do_file_*` / `_get_abs_path` have no path containment.
- F3: `do_skill_manage` create has unsanitized `name` → arbitrary file write.
- F27: unescaped tool results fed back as user-role messages.
- F9: `locals()` (incl. `client`/`api_key`/`messages`) handed to every hook.

**Therefore the "tool-dispatch architecture" goal is not cosmetic.** It is the
single best leverage point to close F1/F2/F3/F27 at their common root: a
**per-tool authorization gate + path-containment policy at the dispatch boundary**,
plus an **additive tool registry** so feature-tools (hermes `do_skill_manage`,
subagents `do_task`) can register from their own modules without bloating the
handler class. The refactor and the Critical security fix are the same work.

## Constraints governing HOW (from CONTRIBUTING.md + state)

- **Net line count ≤ 0** — extraction must be pure moves + deletion of
  boilerplate, not new wrapper layers. A registry that adds indirection
  without removing code violates this.
- **Small change radius** — each step independently safe and reviewable.
- **Let-it-crash** — the bare `except: pass` sites (`safe_print`, `stream_reader:56`,
  `log_memory_access`, `first_init_driver`, and audit S3 in `mcp_distill`) must
  be narrowed, not preserved.
- **No new deps.**
- **Coordinate with `add-first-class-subagents`** (build, 0/31) — it will add
  `do_task`/`do_submit_result`/`_subagent_mgr` to ga.py. A handler-split done
  mid-flight collides. Either sequence after it merges, or keep early steps
  off the handler methods it adds.
- **Unblock the repo first** — 3 changes are 100% task-complete but stalled
  (`add-llm-slash-cmd`=archive, `skill-lazy-load-mtime-cache`=build,
  `fix-skill-loader-home-windows`=build) + large uncommitted churn + uncommitted
  hermes v1. A big refactor on a dirty base compounds risk.

## Phased plan (each step: net ≤0, small radius, ruff-clean, tests added where the audit says none exist)

### Phase 0 — unblock & guardrails (do first, not optional)
0a. Verify+archive the 3 stalled-complete changes; commit/branch hermes v1.
0b. **Add CI** (audit F13): `.github/workflows/ci.yml` on push/PR →
    `ruff check .` + `pytest tests/` + `shellcheck scripts/*.sh`, as a required
    gate. Without this, refactor steps have no safety net (audit F26: core hot
    paths have zero tests).
0c. **Core-path tests** (audit F26): a `test_agent_loop_dispatch.py` with a mock
    client + canned responses + a `do_echo` test handler covering single-tool→done,
    MAX_TURNS, `should_exit`, `no_tool`, `_done_hooks`, `tool_results` assembly.
    This is the regression net every later step leans on.

### Phase 1 — split the giant file (Goal 1)
1a. **Extract pure utils → `ga_utils.py`** (or `ga_tools/` package, grouped:
    `filetools` = `file_read/file_patch/_scan_files/expand_file_refs/log_memory_access`,
    `exectools` = `code_run/stream_reader/safe_print`, `webtools` =
    `web_scan/web_execute_js/first_init_driver` + the `driver` global +
    `simphtml`/`TMWebDriver` imports, `misc` = `ask_user/smart_format/consume_file/
    format_error`). Pure move; `ga.py` does `from ga_utils import *` (or named).
    Net ~0 lines; `script_dir` moves to `ga_utils` or is passed in. **Does not
    touch any `do_*` method** → safe to do now, even alongside subagents.
1b. **Decompose the handler by responsibility via mixins** —
    `GenericAgentHandler(FileTools, WebTools, CodeTools, PlanMode, MemoryPrompt,
    BaseHandler)`, each mixin its own module. Herder methods stay methods (no
    dispatch change). `ga.py` shrinks to composition + `__init__` + the parts
    that genuinely belong together (`turn_end_callback` until 2a). Update the
    stale `MAP.md`.
    - *Sequencing:* do this **after** `add-first-class-subagents` merges (it adds
      handler methods), or scope 1b to mixins that subagents doesn't touch.

### Phase 2 — tool-dispatch architecture (Goal 2, security-rooted)
2a. **Per-tool authz gate at `BaseHandler.dispatch`** (closes F1/F2/F3/F27 root).
    Add an authorization hook at the chokepoint: tools declared
    `destructive`/`exec`/`write`/`network` (`inline_eval`, `code_run`,
    `file_write`, `file_patch`, `shell`, `mcp_call`, `skill_manage create`)
    require human approve/deny; default-deny when a configured workspace root
    is set. A **path-containment policy** in `_get_abs_path`/`skill_manage`
    (realpath+commonpath vs workspace root; reject `..`/absolute/separator in
    `name`) closes F2/F3. This is the audit's priority-fix ① and is small-radius
    (one boundary, not scattered).
2b. **Additive `register_tool(name)` registry** — `dispatch` first tries
    `do_<name>` (existing, unchanged), then a module-level `_TOOL_REGISTRY`.
    Lets `do_skill_manage` (hermes), `do_task`/`do_submit_result` (subagents),
    `do_mcp_call` move into their own modules and self-register, **without**
    forcing existing `do_*` to move. Small, additive, backward-compatible.
    Aligns with subagents' "root service class" pattern and CONTRIBUTING's
    "new features add implementations, not modify old logic."
2c. **Harden the hook surface** (F9): stop passing `locals()`; pass an explicit
    minimal ctx (no `client`/`api_key`/raw `messages`). Document the `tool_after`
    contract (audit A2) so plugins fail visibly, not silently.

### Phase 3 — readability & structure (Goal 3)
3a. **Decompose `turn_end_callback` god-method** (ga.py:787–821) into helpers:
    `_extract_summary`, `_turn_nudges`, `_inject_master`, `_flush_briefs`. No
    behavior change; audit flagged it as oversized.
3b. **Let-it-crash cleanup**: narrow the bare `except: pass` sites listed above
    (CONTRIBUTING + audit S3). On failure write a one-line stderr note.
3c. **Drop now-unused top-level imports** after 1a (subprocess/traceback/
    webbrowser/importlib move with their utils) — shrinks ga.py's import surface.
3d. **`do_no_tool`** (679–728, ~50 lines, nested regex) — extract the
    code-block-detection + plan-completion-intercept into named helpers.

## What this refactor does NOT do (scope guard)
- **`llmcore.py` god-module** (1964 lines, 30 bare excepts, audit's biggest
  maintainability target) — separate refactor, out of scope here.
- **168 ruff baseline** — not selected by the user; AGENTS.md flags
  single→double-quote normalization as a repo-wide future task (huge unrelated
  diff). Leave alone.
- **Audit findings not on the dispatch path** — CI/lockfile (F13/F14/F18),
  MCP races (F19–F24), `llmcore` failover (F21–F24), remote-key/verify (F7/F8),
  sche_tasks/L4-cron RCE (F11) — tracked by the audit, owned by other work,
  not this refactor.
- **`simphtml.py` JS blob** — flagged by audit, separate.

## Coordination & risks
- **`add-first-class-subagents` (build, 0/31)** is the live collision. Plan: do
  Phase 0 + 1a (utils extraction, no handler touch) now; defer Phase 1b/2b
  handler-split until subagents merges, then refactor the combined result.
  Phase 2a (dispatch authz) is additive to `agent_loop.py` and can proceed in
  parallel — subagents reuses `should_exit`/dispatch, so a gate there benefits
  it too.
- **`hermes` is uncommitted + not a Comet change.** Its `do_skill_manage` is
  exactly what Phase 2b would extract — fold it into the registry plan rather
  than refactoring around it.
- **No tests on the hot path (F26)** is the binding constraint on every step:
  Phase 0c must land before Phases 1–3, or regressions go undetected.

## Suggested next step (decision point)
This doc is analysis. To act, choose:
1. **Start a Comet `full` change** `refactor-ga` — open phase: narrow to Phase 0+1a
   (unblock + utils extraction) as the first change; Phase 2 (dispatch authz) as
   a second change coordinated with subagents. This respects the small-radius rule.
2. **Address the audit's Critical first** — F1 (`inline_eval` namespace) is a
   standalone Critical fix that doesn't need the full refactor; ship it as a
   `hotfix` (the audit's priority ①) before any restructuring.
3. **Hold** until `add-first-class-subagents` merges, then refactor the combined
   ga.py (avoids collision entirely).

Recommendation: **(2) then (1) Phase 0+1a** — close the Critical, establish CI +
dispatch tests, extract utils (no handler touch). Defer the handler split until
subagents lands.
