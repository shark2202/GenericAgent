#!/usr/bin/env python3
"""Goal-Runner Bridge (v0.1) — subprocess entry wrapping ``GenericAgent``.

Spawned by ``ga_cli.runner_process.RunnerProcess`` as::

    python -m ga_cli.runner_bridge --ga-path <GA> --session-id <id> [--cwd <dir>] [--llm-no <n>]

Reads JSON Lines *commands* (Run/Abort/Shutdown/Ping) on stdin and emits JSON
Lines *events* (Ready/TurnStart/ToolCallEnd/FinalAnswer/Error/Exited) on the
REAL stdout (fd 1).

Why the stdout dance: ``agentmain`` prints() unconditionally to ``sys.stdout``,
which would corrupt our event stream. So we (1) dup fd 1 into a private handle,
(2) redirect ``sys.stdout`` to ``/dev/null``. Event emission writes to the
captured handle; GA's chatter dies in devnull. (stderr is left intact so the
controller can capture diagnostics.)

R1 resolution: instead of monkeypatching ``GenericAgentHandler`` (galley's
approach), we use GA's *built-in* ``agent._turn_end_hooks`` dict (see
``ga.py:614``) to observe tool calls — cleaner and SDK-version-tolerant.
"""
import argparse
import os
import queue
import sys
import threading
import traceback

# Make sibling runner_ipc importable whether run as a module or a script.
_GA_CLI_DIR = os.path.dirname(os.path.abspath(__file__))
if _GA_CLI_DIR not in sys.path:
    sys.path.insert(0, _GA_CLI_DIR)

from runner_ipc import (  # noqa: E402
    Pong, Ready, TurnStart, ToolCallEnd, FinalAnswer, Error, Exited,
    encode_line, decode_line, IPCProtocolError,
)


# ---------------------------------------------------------------------------
# stdout capture / silencing
# ---------------------------------------------------------------------------
def _capture_real_stdout():
    """Duplicate the real fd 1 into a private, line-buffered text handle."""
    fd = os.dup(1)
    return os.fdopen(fd, "w", buffering=1)


def _silence_python_stdout():
    """Send GA's uncontrolled print() output to /dev/null."""
    sys.stdout = open(os.devnull, "w")


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------
class Bridge:
    def __init__(self, real_out, session_id, llm_no):
        self.real_out = real_out
        self.session_id = session_id
        self.llm_no = llm_no
        self.agent = None
        self.agentmain = None
        self.worker = None
        self._shutdown_requested = False
        self._lock = threading.Lock()  # serialise writes to real_out

    # ---- lifecycle --------------------------------------------------------
    def setup(self, ga_path):
        sys.path.insert(0, ga_path)
        import agentmain  # noqa: WPS433 (deferred import: needs sys.path tweak)
        self.agentmain = agentmain

        agent = agentmain.GenericAgent()
        agent.llm_no = self.llm_no
        agent.inc_out = True    # produce incremental 'next' frames (for TurnStart)
        agent.verbose = False   # chatter goes to devnull anyway; minimise work
        # Register a turn-end hook via GA's built-in mechanism (R1 resolution).
        if not getattr(agent, "_turn_end_hooks", None):
            agent._turn_end_hooks = {}
        agent._turn_end_hooks["bridge"] = self._on_turn_end
        self.agent = agent

        # run() consumes task_queue forever; daemon so the process can exit.
        self.worker = threading.Thread(
            target=self._agent_worker, name="ga-worker", daemon=True)
        self.worker.start()

    def _agent_worker(self):
        try:
            self.agent.run()
        except Exception as exc:  # pragma: no cover - defensive
            traceback.print_exc()
            self._emit(Error(message="agent worker crashed: %s" % exc, fatal=True))

    # ---- event emission ---------------------------------------------------
    def _emit(self, event):
        with self._lock:
            self.real_out.write(encode_line(event))
            self.real_out.flush()

    # ---- hook -> ToolCallEnd (best-effort) --------------------------------
    def _on_turn_end(self, loc):
        """Called by GA at end of each turn with the handler's locals() dict."""
        try:
            tcs = loc.get("tool_calls") or []
            for tc in tcs:
                name = ""
                if isinstance(tc, dict):
                    name = str(tc.get("name") or tc.get("id")
                               or tc.get("call_id") or "tool")
                elif isinstance(tc, str):
                    name = tc
                self._emit(ToolCallEnd(callId=name or "tool", ok=True))
        except Exception:  # pragma: no cover - hook must never crash GA
            pass

    # ---- command loop -----------------------------------------------------
    def command_loop(self):
        while True:
            raw = sys.stdin.readline()
            if not raw:
                break  # stdin closed (controller gone)
            raw = raw.strip()
            if not raw:
                continue
            try:
                cmd = decode_line(raw)
            except IPCProtocolError as exc:
                self._emit(Error(message="bad command: %s" % exc, fatal=False))
                continue
            except Exception as exc:  # e.g. TypeError for a Run missing 'task'
                self._emit(Error(message="malformed command: %s" % exc,
                                 fatal=False))
                continue
            if cmd is None:
                self._emit(Error(message="undecodable command", fatal=False))
                continue
            kind = getattr(cmd, "kind", "")
            if kind == "run":
                self._handle_run(cmd)
            elif kind == "abort":
                self._handle_abort(cmd)
            elif kind == "shutdown":
                self._shutdown_requested = True
                self._emit(Exited(code=0, reason="shutdown command"))
                return
            elif kind == "ping":
                self._emit(Pong(sessionId=self.session_id))
            else:
                self._emit(Error(message="unknown command kind: %s" % kind,
                                 fatal=False))

    def _handle_run(self, cmd):
        task = getattr(cmd, "task", "") or ""
        try:
            disp_q = self.agent.put_task(task)
        except Exception as exc:
            self._emit(Error(message="put_task failed: %s" % exc, fatal=False))
            return
        # Drain this task's display queue off the command-loop thread.
        threading.Thread(target=self._drain, args=(disp_q,),
                         name="drain", daemon=True).start()

    def _drain(self, disp_q):
        """Translate display_queue frames into events until the task is done.

        Frame contract (agentmain.py:218-240):
          * {'next': <delta>, 'source':.., 'turn': N, 'outputs':..} — incremental
          * {'done': <full_resp>, ...}                              — terminal
          * {'done':.., 'error':..}                                 — terminal+err
        """
        last_turn = 0
        while True:
            try:
                msg = disp_q.get(timeout=3600)
            except queue.Empty:
                # Idle for an hour with no frame; keep waiting (controller will
                # time out / kill us if the agent truly hung).
                continue
            if not isinstance(msg, dict):
                continue
            if "next" in msg:
                turn = msg.get("turn", 0) or 0
                if turn != last_turn:
                    last_turn = turn
                    self._emit(TurnStart(turn=turn))
            elif "done" in msg:
                text = msg.get("done")
                if not isinstance(text, str):
                    text = "" if text is None else str(text)
                err = msg.get("error")
                if err:
                    self._emit(Error(message=str(err), fatal=False))
                self._emit(FinalAnswer(text=text))
                return  # this task is finished; loop keeps listening for more

    def _handle_abort(self, cmd):
        try:
            self.agent.abort()
        except Exception as exc:
            self._emit(Error(message="abort failed: %s" % exc, fatal=False))


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="GA goal-runner bridge")
    parser.add_argument("--ga-path", required=True,
                        help="path to the GenericAgent root (contains agentmain.py)")
    parser.add_argument("--session-id", required=True, help="runner session id")
    parser.add_argument("--cwd", default=None, help="working dir for the agent")
    parser.add_argument("--llm-no", type=int, default=0, help="LLM index to use")
    args = parser.parse_args()

    real_out = _capture_real_stdout()
    _silence_python_stdout()

    if args.cwd:
        try:
            os.chdir(args.cwd)
        except OSError as exc:
            real_out.write(encode_line(
                Error(message="chdir failed: %s" % exc, fatal=True)))
            real_out.flush()
            sys.exit(1)

    bridge = Bridge(real_out, args.session_id, args.llm_no)
    try:
        bridge.setup(args.ga_path)
    except Exception as exc:
        traceback.print_exc()
        bridge._emit(Error(message="setup failed: %s" % exc, fatal=True))
        bridge._emit(Exited(code=1, reason="setup failure"))
        os._exit(1)

    bridge._emit(Ready(sessionId=args.session_id))

    try:
        bridge.command_loop()
    except KeyboardInterrupt:
        pass
    # Emit a terminal Exited only if shutdown wasn't requested (the shutdown
    # handler already emitted one). This covers the controller-died (stdin EOF)
    # case so the controller always sees exactly one Exited.
    if not bridge._shutdown_requested:
        bridge._emit(Exited(code=0, reason="stdin closed"))
    # Force-kill: GA spawns non-daemon threads (MCP client _run_loop) that keep
    # the interpreter alive past sys.exit(). os._exit bypasses them so the
    # parent controller sees a deterministic EOF/exit (galley uses the same
    # trick, e.g. desktop_bridge.py:1703).
    os._exit(0)


if __name__ == "__main__":
    main()
