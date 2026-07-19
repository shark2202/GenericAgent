"""Durable Agent Protocol v1 — stdio bridge (skeleton, Task 1).

Spawned by a non-Python frontend (Rust CLI/TUI/GUI) as a child process:
    python -m ga_stdio
Communicates via newline-delimited JSON on stdin/stdout. stderr is for
diagnostics only (not part of the protocol).

Design: docs/superpowers/specs/2026-07-19-durable-agent-protocol-design.md
Engine internals (agent_loop/ga/llmcore) are untouched; this module only
reuses existing extension points (plugins.hooks, GenericAgent SDK,
slash_cmds.prompt_for, MCPClientManager) — wired in later tasks.

Task 1 scope: stdio read/write loop + initialize/ready handshake + malformed
JSON error (E2) + not-initialized guard (E3). Business message handlers are
wired in Tasks 2-10; until then they return unknown_type.
"""
import sys
import os
import json
import queue
import threading
import time
import itertools

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERSION = "1"  # major only, LSP-style; breaking change bumps major
SERVER_CAPABILITIES = [
    "streaming", "multi-session", "autonomous", "approval",
    "mcp", "slash", "llm-switch", "session-resume",
]

# Error codes (design §7)
ERR_BAD_JSON = "bad_json"
ERR_NOT_INITIALIZED = "not_initialized"
ERR_CAPABILITY_UNSUPPORTED = "capability_unsupported"
ERR_UNKNOWN_TYPE = "unknown_type"
ERR_BAD_REQUEST = "bad_request"
ERR_INTERNAL_ERROR = "internal_error"

_REQUIRED_FIELDS = ("id", "type", "version")


def _parse_line(line):
    """Parse one stdin line into a dict, or None if malformed (E2).

    Blank lines return None too; the caller distinguishes by checking
    ``line.strip()`` first (see BridgeCore.serve).
    """
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(msg, dict):
        return None
    return msg


def _serialize(msg):
    """Serialize one message dict to a JSON line (with trailing newline)."""
    return json.dumps(msg, ensure_ascii=False) + "\n"


def _resolve_ga_from_ctx(ctx):
    """Recover the GenericAgent instance from a hook ctx dict.

    tool_before fires inside BaseHandler.dispatch (agent_loop.py:53/63) —
    ctx carries `self` = handler, GA = handler.parent (ga.py:30).
    turn_after fires inside agent_runner_loop (agent_loop.py:143) — ctx has
    no `self`, only `handler`; GA = handler.parent.
    Uses .get() so field drift in agent_loop doesn't crash the callback
    (design §6.3 resilience boundary).
    """
    handler = ctx.get("self") or ctx.get("handler")
    return getattr(handler, "parent", None)


class TaskCtx:
    """Per-task runtime: one GenericAgent instance + its display_queue + control.

    Task 2 scope: single-task serial (one GA, one task runs to completion).
    Per-task pooling / concurrency is wired in Task 6; autonomous loop in
    Task 7; approval flow in Task 8. Fields not used by Task 2 are reserved
    so later tasks can extend without reshaping the dataclass.
    """
    def __init__(self, ga, dq, task_id, thread, mode="single", budget=None):
        self.ga = ga
        self.dq = dq
        self.task_id = task_id
        self.thread = thread
        self.mode = mode            # "single" | "autonomous"
        self.budget = budget        # {seconds?, turns?} for autonomous; None for single
        self.start_time = time.time()
        self.turns_used = 0
        self.interrupted = False
        self._objective = None
        self.pending_approval = False
        self._last_approval_input_ref = None
        self._approval_input = None


class BridgeCore:
    """Protocol state machine + stdio reader loop.

    Task 1: handshake + error routing only. Pool, hooks, semaphore, and
    business handlers are wired in later tasks.
    """

    def __init__(self, stdin=None, stdout=None, max_concurrency=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._write_lock = threading.Lock()
        self.initialized = False
        # Task 6 (T3-M1/C2 lock收口): itertools.count.__next__ is a C-level
        # atomic under the GIL, so id generation is safe across the stdio
        # reader thread + multiple agent-run threads spawned by the pool
        # (semaphore max 4). Replaces the prior `x += 1` self-increment which
        # was a read-modify-write race once Task 6 introduced real GA-thread
        # concurrency. _next_id/_new_task_id/_tool_id_seq all draw from these.
        self._event_id_seq = itertools.count(1)
        self._task_id_seq = itertools.count(1)
        self._tool_id_seq = itertools.count(1)
        # Task 6: bounded concurrency (S2/Q8). max_concurrency defaults to the
        # GA_STDIO_MAX_CONCURRENCY env var (4) so operators can tune without
        # code changes. semaphore.acquire(blocking=False) is the gate;
        # overflow task/starts park on the FIFO _queued list and are woken
        # one-at-a-time from _release_task.
        if max_concurrency is None:
            max_concurrency = int(os.environ.get("GA_STDIO_MAX_CONCURRENCY", "4"))
        self.max_concurrency = max_concurrency
        self.semaphore = threading.Semaphore(max_concurrency)
        # Task 2: per-task pool + ga→task routing (hooks use the latter in Task 3)
        self.pool = {}                  # {task_id: TaskCtx}
        self.ga_to_task = {}            # {id(GA): task_id} for hook routing
        self._queued = []               # FIFO of (ctx, prompt, images, original_id)
        self._pool_lock = threading.Lock()
        self._hooks_registered = False
        # Task 5: approval loop state. _pending_approvals maps
        # (task_id, tool_id) → (Event, box); the patched ask_user blocks on
        # the Event, handle_approval_response fills the box + sets it.
        self._pending_approvals = {}   # {(task_id, tool_id): (Event, box)}
        self._ask_user_patched = False

    def _next_id(self):
        return next(self._event_id_seq)

    def send(self, msg):
        """Write one JSON line to stdout (thread-safe)."""
        line = _serialize(msg)
        with self._write_lock:
            self.stdout.write(line)
            self.stdout.flush()

    def send_error(self, code, message, original_id=None, **extra):
        """Emit an error frame.

        ``original_id`` is always present (null when the triggering line
        could not be parsed, e.g. bad_json) — design §7. Extra fields
        (e.g. ``missing=[...]`` for capability_unsupported) are merged in.
        """
        err = {"id": self._next_id(), "type": "error", "version": VERSION,
               "code": code, "message": message, "original_id": original_id}
        err.update(extra)
        self.send(err)

    def handle_initialize(self, msg):
        """Capability negotiation (design §4).

        Task 6: agent_info is filled with real values — llm_count via a
        throwaway GenericAgent probe (constructed + list_llms + shutdown
        right here, never enters the pool), mcp_connected via the singleton
        MCPClientManager's registry. Both probes are best-effort: any failure
        (no keys configured, MCP not started, agentmain import issues) falls
        back to 0 so the handshake never blocks on environment problems.
        """
        caps = msg.get("capabilities", []) or []
        missing = [c for c in caps if c not in SERVER_CAPABILITIES]
        if missing:
            self.send_error(ERR_CAPABILITY_UNSUPPORTED,
                            f"server lacks: {missing}",
                            original_id=msg.get("id"), missing=missing)
            return
        self.initialized = True
        llm_count = 0
        try:
            from agentmain import GenericAgent as _GA
            probe = _GA()
            llm_count = len(probe.list_llms())
            probe.shutdown()
        except Exception:
            llm_count = 0
        mcp_connected = 0
        try:
            from mcp_client import MCPClientManager
            mgr = MCPClientManager.get_instance()
            if mgr is not None:
                mcp_connected = len(mgr.registry.get_server_names())
        except Exception:
            mcp_connected = 0
        self.send({"id": msg["id"], "type": "ready", "version": VERSION,
                   "capabilities": SERVER_CAPABILITIES,
                   "agent_info": {"name": "GenericAgent",
                                  "mcp_connected": mcp_connected,
                                  "llm_count": llm_count}})

    def dispatch(self, msg):
        """Route one parsed client message.

        All messages must carry id/type/version (design §3). Business
        messages require a prior successful initialize (design §4, E3).
        """
        for field in _REQUIRED_FIELDS:
            if field not in msg:
                self.send_error(ERR_BAD_REQUEST,
                                f"missing required field: {field}",
                                original_id=msg.get("id"))
                return
        mtype = msg["type"]
        if mtype == "initialize":
            self.handle_initialize(msg)
            return
        if not self.initialized:
            self.send_error(ERR_NOT_INITIALIZED,
                            f"got {mtype!r} before initialize (E3)",
                            original_id=msg.get("id"))
            return
        # Task 2: single-task streaming path. task/start → handle_task_start
        # assigns a task_id, spawns a GA, drains display_queue → task/delta
        # + task/done. Other business types still fall through to unknown_type
        # until wired in later tasks (Task 3+ for tool events, Task 4 for
        # interrupt, Task 5 for approval, Task 7 for autonomous).
        if mtype == "task/start":
            self.handle_task_start(msg)
            return
        if mtype == "task/interrupt":
            self.handle_task_interrupt(msg)
            return
        if mtype == "approval/response":
            self.handle_approval_response(msg)
            return
        # Business handlers wired in later tasks; skeleton rejects as
        # unknown_type so the protocol contract is observable now.
        self.send_error(ERR_UNKNOWN_TYPE, f"{mtype} not implemented yet",
                        original_id=msg.get("id"))

    # ---- Task 2: single-task streaming path ----
    # ---- Task 6: per-task GA pool + bounded concurrency (S2/Q8) ----

    def _new_task_id(self):
        return f"t{next(self._task_id_seq)}"

    def handle_task_start(self, msg):
        """task/start → allocate task_id → maybe spawn GA + put_task → task/ack.

        Task 6 (S2/Q8): bounded-concurrency pool. The ctx is always registered
        in self.pool up front (so task/interrupt / approval can find it
        whether it's running or queued). Then a non-blocking semaphore
        acquire decides:
          - acquired → emit task/ack{status:running} + _start_task_thread
            (spawns a fresh GA, put_task, starts the drain worker).
          - not acquired → park (ctx, prompt, images, original_id) on the FIFO
            _queued list + emit task/ack{status:queued}. _release_task will
            pop + start it when a running task frees a slot.

        Task 7 wires the autonomous self-continuation loop; until then an
        autonomous task simply put_tasks once and drains to a single done
        (reason="completed", not "budget" — that's a Task 7 contract).
        """
        prompt = msg.get("prompt", "")
        mode = msg.get("mode", "single")
        budget = msg.get("budget")
        images = msg.get("images", [])
        task_id = self._new_task_id()
        if mode == "autonomous" and not self._validate_budget(budget):
            self.send_error("bad_budget",
                            "autonomous requires budget{seconds? and/or turns?}",
                            original_id=msg.get("id"))
            return
        ctx = TaskCtx(ga=None, dq=None, task_id=task_id, thread=None,
                      mode=mode, budget=budget)
        ctx._objective = prompt
        with self._pool_lock:
            self.pool[task_id] = ctx
        if self.semaphore.acquire(blocking=False):
            self.send({"id": msg["id"], "type": "task/ack", "version": VERSION,
                       "task_id": task_id, "status": "running"})
            self._start_task_thread(ctx, prompt, images)
        else:
            with self._pool_lock:
                self._queued.append((ctx, prompt, images, msg["id"]))
            self.send({"id": msg["id"], "type": "task/ack", "version": VERSION,
                       "task_id": task_id, "status": "queued"})

    def _validate_budget(self, budget):
        """autonomous budget shape: dict with at least one of seconds/turns.

        seconds ∈ (int|float), turns ∈ int. Used by handle_task_start to gate
        autonomous mode; the autonomous loop itself (budget enforcement +
        reason="budget" done) is wired in Task 7.
        """
        if not isinstance(budget, dict):
            return False
        seconds = budget.get("seconds")
        turns = budget.get("turns")
        if seconds is None and turns is None:
            return False
        if seconds is not None and not isinstance(seconds, (int, float)):
            return False
        if turns is not None and not isinstance(turns, int):
            return False
        return True

    def _start_task_thread(self, ctx, prompt, images):
        """Spawn a fresh GA for ctx + put_task + start the drain worker.

        Called both from handle_task_start (first run) and from _release_task
        (waking a queued task). GA is created here (not in handle_task_start)
        so queued tasks don't construct a GA they might never get to run —
        important because GenericAgent() is heavy (MCP/LLM init). ga_to_task
        is populated under _pool_lock so the tool_before / turn_after hook
        closures observe a consistent reverse-routing map.
        """
        ga = self._spawn_ga()
        ctx.ga = ga
        with self._pool_lock:
            self.ga_to_task[id(ga)] = ctx.task_id
        dq = ga.put_task(prompt, source="stdio", images=images)
        ctx.dq = dq
        t = threading.Thread(target=self._run_task, args=(ctx,), daemon=True)
        ctx.thread = t
        t.start()

    def _spawn_ga(self):
        """Create a fresh GenericAgent + run() daemon thread.

        Mirrors ga_httpapp.py:10 (``threading.Thread(target=agent.run,
        daemon=True).start()``). Task 6 wraps this in pool/semaphore.
        """
        from agentmain import GenericAgent
        ga = GenericAgent()
        ga.verbose = False
        ga.inc_out = True
        threading.Thread(target=ga.run, daemon=True).start()
        return ga

    def handle_task_interrupt(self, msg):
        """task/interrupt{task_id} → ctx.interrupted + ga.abort() (S3).

        Design §6.5 line 167. Reuses the engine's existing abort extension
        point (agentmain.py:137-141): abort() sets stop_sig + appends to
        handler.code_stop_signal, so run()'s ``if self.stop_sig: break``
        (agentmain.py:224) fires and run() still puts a ``done`` item on the
        display_queue (agentmain.py:235). The drain loop then reads
        ctx.interrupted to choose ``reason="interrupted"`` over "completed".

        We do NOT emit task/done here — the drain worker owns terminal
        frames for the task (single source of truth). We only ack.
        """
        task_id = msg.get("task_id")
        with self._pool_lock:
            ctx = self.pool.get(task_id)
        if ctx is None:
            self.send_error("unknown_task", f"no running task {task_id!r}",
                            original_id=msg.get("id"))
            return
        # Set ctx.interrupted ONLY AFTER abort() succeeds (I-1 fix). If abort
        # raises, the except branch emits interrupt_failed and returns; leaving
        # the flag True would make drain_display_queue mislabel the task's
        # natural done as reason="interrupted", contradicting the
        # interrupt_failed error the client already received. Moving the set
        # after the try (rather than rolling back in except) is safer: a
        # partially-applied abort (stop_sig set, handler.code_stop_signal
        # append raising) leaves the flag False, so drain reflects the task's
        # true outcome (completed/error) instead of a half-applied interrupt.
        try:
            ctx.ga.abort()
        except Exception as e:
            self.send_error("interrupt_failed", f"{type(e).__name__}: {e}",
                            original_id=msg.get("id"))
            return
        ctx.interrupted = True
        self.send({"id": msg["id"], "type": "task/ack", "version": VERSION,
                   "task_id": task_id, "status": "interrupting"})

    def _run_task(self, ctx):
        """Worker: drain display_queue for one task until done/interrupted."""
        try:
            if ctx.mode == "autonomous":
                self.run_autonomous(ctx)      # wired in Task 7
            else:
                self.drain_display_queue(ctx)
        except Exception as e:
            self.send({"id": self._next_id(), "type": "task/done", "version": VERSION,
                       "task_id": ctx.task_id, "reason": "error",
                       "error": f"{type(e).__name__}: {e}"})
        finally:
            self._release_task(ctx)

    def _release_task(self, ctx):
        """Remove ctx from the pool, shut its GA down, release the semaphore
        slot, and wake one queued task if any (FIFO).

        Called from _run_task's finally block. Wake-order matters: pool/ga_to_task
        cleanup + GA shutdown + semaphore.release() all happen BEFORE popping
        _queued, so the woken task sees a free slot and a clean reverse-routing
        map. The woken task's task/ack{status:running} is emitted here (not from
        the woken thread) so the client sees the running transition in order.
        """
        with self._pool_lock:
            self.pool.pop(ctx.task_id, None)
            if ctx.ga is not None:
                self.ga_to_task.pop(id(ctx.ga), None)
        try:
            if ctx.ga is not None:
                ctx.ga.shutdown()
        except Exception:
            pass
        self.semaphore.release()
        # wake one queued task if any
        next_item = None
        with self._pool_lock:
            if self._queued:
                next_item = self._queued.pop(0)
        if next_item is not None:
            nxt_ctx, nxt_prompt, nxt_images, _orig_id = next_item
            self._start_task_thread(nxt_ctx, nxt_prompt, nxt_images)
            self.send({"id": self._next_id(), "type": "task/ack", "version": VERSION,
                       "task_id": nxt_ctx.task_id, "status": "running"})

    def drain_display_queue(self, ctx):
        """Drain ctx.dq → emit task/delta + task/done. Used by single mode.

        Mirrors ga_httpapp.py:25 (``while "done" not in (item := dq.get(...))``).
        Items follow agentmain.py:229-235: ``{next|done, source, turn, outputs}``.
        The error-shaped done (agentmain.py:239) appends a fenced block
        ``\n```\n{format_error}\n````` to ``done``; we detect that to set
        reason="error" and surface the text via the ``error`` field.
        """
        while True:
            try:
                item = ctx.dq.get(timeout=2200)
            except queue.Empty:
                self.send({"id": self._next_id(), "type": "task/done", "version": VERSION,
                           "task_id": ctx.task_id, "reason": "error",
                           "error": "display_queue timeout (2200s)"})
                return
            if "next" in item:
                self.send({"id": self._next_id(), "type": "task/delta", "version": VERSION,
                           "task_id": ctx.task_id, "content": item.get("next", ""),
                           "turn": item.get("turn", 0)})
                continue
            if "done" in item:
                # Approval continuation: this done was caused by ask_user
                # (do_ask_user returns should_exit=True → agent_loop break → done).
                # Re-feed the client's approved input as a new prompt on the SAME
                # GA instance (history preserved, task_id unchanged, client sees
                # no task boundary).
                if getattr(ctx, "pending_approval", False):
                    ctx.pending_approval = False
                    cont = getattr(ctx, "_approval_input", None)
                    ctx._approval_input = None
                    if cont is not None and str(cont).strip() != "":
                        ctx.dq = ctx.ga.put_task(cont, source="stdio")
                        continue   # drain the new dq
                    # reject / empty input → fall through to normal done below
                # (normal done path below — unchanged from Task 2)
                done_text = item.get("done", "")
                # agentmain except branch (agentmain.py:239) appends a trailing
                # ```\n{format_error}\n``` block to done text on engine errors.
                is_error = False
                if "```" in done_text:
                    parts = done_text.split("```")
                    if len(parts) >= 3 and "Error" in parts[-2]:
                        is_error = True
                reason = "error" if is_error else (
                    "interrupted" if ctx.interrupted else "completed")
                done_msg = {"id": self._next_id(), "type": "task/done", "version": VERSION,
                            "task_id": ctx.task_id, "reason": reason,
                            "turn": item.get("turn", 0)}
                if reason == "error":
                    done_msg["error"] = done_text
                self.send(done_msg)
                return

    def run_autonomous(self, ctx):
        """Placeholder — wired in Task 7."""
        raise NotImplementedError("autonomous wired in Task 7")

    # ---- Task 3: hook routing — tool_before→tool/call, turn_after→tool/result ----
    #
    # Registers global plugin.hooks callbacks once at bridge startup. Coexists
    # with langfuse_tracing / skill_evolution callbacks: plugins.hooks.trigger
    # walks every registered callback (plugins/hooks.py:18). Our closure
    # reverse-looks-up task_id via ctx GA → ga_to_task[id(GA)] so tool events
    # route to the right task even when multiple GAs run (Task 6).

    def register_hooks(self):
        """Register tool_before/turn_after callbacks (idempotent per instance).

        Wired into serve() so the bridge publishes tool/call + tool/result
        frames as soon as it starts reading stdin. The _hooks_registered flag
        guards against double-registration on the same instance; cross-instance
        accumulation is bounded by the bridge's own lifetime.
        """
        if self._hooks_registered:
            return
        self._hooks_registered = True
        from plugins.hooks import register

        @register("tool_before")
        def _tool_before(ctx):
            ga = _resolve_ga_from_ctx(ctx)
            if ga is None:
                return
            with self._pool_lock:
                task_id = self.ga_to_task.get(id(ga))
            if task_id is None:
                return
            tool_name = ctx.get("tool_name", "?")
            args = dict(ctx.get("args", {}) or {})
            # agent_loop dispatch injects these internal keys (agent_loop.py:52/62)
            args.pop("_index", None)
            args.pop("_tool_num", None)
            # T3-M1/C2: atomic under GIL — safe across concurrent agent threads
            tool_id = f"tool_{next(self._tool_id_seq)}"
            if tool_name == "ask_user":
                # ask_user is intercepted by the approval path (Task 5), not tool/call.
                self._on_approval_request(task_id, tool_id, args)
                return
            self._emit_tool_call(task_id=task_id, tool_id=tool_id,
                                 name=tool_name, args=args)

        @register("turn_after")
        def _turn_after(ctx):
            self._on_turn_after(ctx)

    def _emit_tool_call(self, task_id, tool_id, name, args):
        self.send({"id": self._next_id(), "type": "tool/call", "version": VERSION,
                   "tool_id": tool_id, "task_id": task_id, "name": name, "args": args})

    def _on_turn_after(self, ctx):
        """Walk tool_results list → one tool/result per entry (agent_loop.py:137).

        Resolves GA + task_id internally (not passed in) so the helper is
        callable directly from tests and from the registered turn_after closure
        with the same signature.
        """
        ga = _resolve_ga_from_ctx(ctx)
        if ga is None:
            return
        with self._pool_lock:
            task_id = self.ga_to_task.get(id(ga))
        if task_id is None:
            return
        tool_results = ctx.get("tool_results", []) or []
        for tr in tool_results:
            tool_use_id = tr.get("tool_use_id", "")
            content = tr.get("content", "")
            self.send({"id": self._next_id(), "type": "tool/result", "version": VERSION,
                       "tool_id": tool_use_id, "task_id": task_id, "content": content})

    def _patch_ask_user(self):
        """Monkey-patch ga_utils.ask_user so do_ask_user calls our bridge version.

        NOT a source edit to ga_utils.py — bridge owns its own process namespace.
        ga_utils.ask_user(question, candidates=None) currently returns a non-blocking
        INTERRUPT dict (ga_utils.py:30-33); we replace it with a blocking call that
        waits on the approval response from the stdio reader thread, then returns
        the client's input so do_ask_user's StepOutcome carries it.
        """
        if self._ask_user_patched:
            return
        self._ask_user_patched = True
        import ga_utils

        def _bridge_ask_user(question, candidates=None):
            """Blocking: emit approval/request, wait for approval/response, return input."""
            ga = _bridge_ask_user._current_ga
            with self._pool_lock:
                task_id = self.ga_to_task.get(id(ga)) if ga is not None else None
            if task_id is None:
                # GA not in pool — fall back to old non-blocking behavior
                return {"status": "INTERRUPT", "intent": "HUMAN_INTERVENTION",
                        "data": {"question": question, "candidates": candidates or []}}
            # T3-M1/C2: atomic under GIL — safe across concurrent agent threads
            tool_id = f"ask_{next(self._tool_id_seq)}"
            ev = threading.Event()
            box = {}
            self._pending_approvals[(task_id, tool_id)] = (ev, box)
            self.send({"id": self._next_id(), "type": "approval/request", "version": VERSION,
                       "task_id": task_id, "tool_id": tool_id,
                       "prompt": question, "options": candidates or []})
            ev.wait()  # blocks the agent thread until approval/response arrives
            decision = box.get("decision", "reject")
            if decision == "approve":
                return box.get("input", "")   # becomes do_ask_user's StepOutcome.data
            return {"status": "REJECTED", "data": {"question": question}}

        _bridge_ask_user._current_ga = None
        ga_utils.ask_user = _bridge_ask_user
        # C1: ga.py:8-12 binds `ask_user` into ga module globals via
        # `from ga_utils import ask_user`; do_ask_user (ga.py:79) looks it up
        # there (LOAD_GLOBAL), so patching ga_utils.ask_user alone never reaches
        # the real do_ask_user path. Reroute ga.ask_user too.
        import ga
        ga.ask_user = _bridge_ask_user
        self._bridge_ask_user = _bridge_ask_user

    def _on_approval_request(self, task_id, tool_id, args):
        """tool_before callback for ask_user: stamp the current GA onto the patched
        ask_user so it can route, and set pending_approval so drain_display_queue
        knows the upcoming done is ask_user-caused and should continue instead of
        emitting task/done."""
        ga = None
        with self._pool_lock:
            ctx = self.pool.get(task_id)
            if ctx is not None:
                ga = ctx.ga
                ctx.pending_approval = True
                ctx._last_approval_input_ref = (task_id, tool_id)
        if self._ask_user_patched and ga is not None:
            self._bridge_ask_user._current_ga = ga
        # The actual approval/request is emitted by the patched ask_user when
        # do_ask_user calls it (next in dispatch, same agent thread) — avoids a
        # race where we emit before ev.wait() is armed.

    def handle_approval_response(self, msg):
        task_id = msg.get("task_id")
        tool_id = msg.get("tool_id")
        slot = self._pending_approvals.pop((task_id, tool_id), None)
        if slot is None:
            self.send_error("stale_approval",
                            f"no pending approval for {tool_id} on {task_id}",
                            original_id=msg.get("id"))
            return
        ev, box = slot
        box["decision"] = msg.get("decision", "reject")
        box["input"] = msg.get("input", "")
        # stash input on ctx so drain_display_queue can re-feed as continuation
        with self._pool_lock:
            ctx = self.pool.get(task_id)
        if ctx is not None:
            ctx._approval_input = box.get("input", "")
        ev.set()  # wake the blocked agent thread
        self.send({"id": msg["id"], "type": "approval/ack", "version": VERSION,
                   "task_id": task_id, "tool_id": tool_id, "status": "accepted"})

    def serve(self):
        """Main stdio reader loop. One JSON line per stdin line.

        E2: malformed JSON emits bad_json and continues (does not break).
        Blank lines are skipped silently. On stdin EOF the loop returns
        and the child process exits; the client detects stdout EOF (E1).
        """
        self.register_hooks()   # Task 3: wire tool_before/turn_after once
        self._patch_ask_user()  # Task 5: intercept ga_utils.ask_user → approval loop
        for line in self.stdin:
            stripped = line.strip()
            if not stripped:
                continue  # tolerate blank lines without an error frame
            msg = _parse_line(line)
            if msg is None:
                self.send_error(ERR_BAD_JSON,
                                f"unparseable line: {stripped[:80]}",
                                original_id=None)
                continue
            try:
                self.dispatch(msg)
            except Exception as e:
                self.send_error(ERR_INTERNAL_ERROR,
                                f"{type(e).__name__}: {e}",
                                original_id=msg.get("id"))


if __name__ == "__main__":
    BridgeCore().serve()
