"""Goal-Runner Process lifecycle (v0.1) — controller-side wrapper around the
subprocess bridge.

Mirrors galley's Rust ``runner_manager::RunnerProcess``: spawn the bridge
subprocess, own its three pipes, run a dedicated stdout reader daemon thread
that decodes JSON Lines events into a queue, expose a locked ``send_command``
to write commands to the child's stdin, and guarantee ``kill_on_drop``-style
cleanup (no zombie/leaked processes) via ``close()`` / ``__del__`` / context
manager.

Lifecycle of a runner::

    with RunnerProcess(ga_path, session_id, cwd) as rp:
        rp.wait_ready()                 # blocks until first Ready event
        rp.send_command(Run(task="..."))
        for ev in rp.events():          # generator, yields decoded events
            if isinstance(ev, FinalAnswer):
                ...
"""

from __future__ import annotations

import atexit
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import weakref
from typing import Optional

# Make sibling runner_ipc importable whether run as a module or a script.
_GA_CLI_DIR = os.path.dirname(os.path.abspath(__file__))
if _GA_CLI_DIR not in sys.path:
    sys.path.insert(0, _GA_CLI_DIR)

from runner_ipc import (  # noqa: E402
    Command, Event,
    Run, Abort, Shutdown, Ping,
    Ready, Pong, TurnStart, ToolCallStart, ToolCallEnd,
    FinalAnswer, Error, Exited,
    encode_line, decode_event, IPCProtocolError,
)

GA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if GA not in sys.path:
    sys.path.insert(0, GA)


class RunnerNotReadyError(RuntimeError):
    """Raised when wait_ready() times out without a Ready event."""


class RunnerProcess:
    """Owns one bridge subprocess and its event stream."""

    # cold start of the SDK (import agentmain + GenericAgent()) is ~15s; give
    # a generous default so callers don't spuriously fail.
    DEFAULT_READY_TIMEOUT = 60.0

    def __init__(self, ga_path: str, session_id: str, cwd: Optional[str] = None,
                 llm_no: int = 0, env: Optional[dict] = None):
        self.ga_path = os.path.abspath(ga_path)
        self.session_id = session_id
        self.cwd = cwd
        self.llm_no = llm_no

        self._proc: Optional[subprocess.Popen] = None
        self._events: "queue.Queue[Optional[Event]]" = queue.Queue()
        self._stderr_file: Optional[tempfile._TemporaryFileWrapper] = None  # type: ignore[name-defined]
        self._reader: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._closed = False
        self._ready = threading.Event()
        self._exited = threading.Event()
        self._exit_code: Optional[int] = None
        self._env = env

        self._start()

    # ------------------------------------------------------------------ #
    # spawn / reader
    # ------------------------------------------------------------------ #
    def _start(self) -> None:
        self._stderr_file = tempfile.NamedTemporaryFile(
            mode="w+", prefix=f"runner_{self.session_id}_", suffix=".stderr",
            delete=False, encoding="utf-8")
        cmd = [
            sys.executable, "-u", "-m", "ga_cli.runner_bridge",
            "--ga-path", self.ga_path,
            "--session-id", self.session_id,
            "--llm-no", str(self.llm_no),
        ]
        if self.cwd:
            cmd += ["--cwd", self.cwd]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_file,
            # text=True + bufsize=1 → line-buffered reads are responsive
            text=True, bufsize=1,
            env=self._env,
        )
        self._reader = threading.Thread(
            target=self._read_loop, name=f"runner-{self.session_id}-reader",
            daemon=True)
        self._reader.start()
        # ensure cleanup even if the caller forgets to close()
        weakref.finalize(self, RunnerProcess._finalize_static, self._proc)

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = decode_event(line)
            except IPCProtocolError:
                # non-JSON/unknown line on stdout: log to stderr file, skip.
                # (the bridge should have silenced stray prints, but be robust)
                try:
                    self._stderr_file.write(
                        f"[unparsed stdout] {line}\n")
                    self._stderr_file.flush()
                except Exception:
                    pass
                continue
            if isinstance(ev, Ready):
                self._ready.set()
            elif isinstance(ev, Exited):
                self._exited.set()
            self._events.put(ev)
        # stdout closed → process is dying/finished
        self._exit_code = self._proc.poll()
        self._exited.set()
        self._events.put(None)  # sentinel: end of stream

    # ------------------------------------------------------------------ #
    # commands
    # ------------------------------------------------------------------ #
    def send_command(self, cmd: Command) -> None:
        """Write a command to the child's stdin (thread-safe)."""
        if self._closed:
            raise RuntimeError("RunnerProcess is closed")
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("runner has no stdin")
        data = encode_line(cmd)  # includes trailing newline
        with self._write_lock:
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError) as exc:
                raise RuntimeError(
                    f"runner stdin write failed: {exc}") from exc

    def send_run(self, task: str) -> None:
        self.send_command(Run(task=task))

    def send_abort(self) -> None:
        self.send_command(Abort())

    def send_ping(self) -> None:
        self.send_command(Ping())

    # ------------------------------------------------------------------ #
    # readiness / state
    # ------------------------------------------------------------------ #
    def wait_ready(self, timeout: Optional[float] = None) -> Ready:
        """Block until the bridge emits ``Ready``. Returns the event."""
        if timeout is None:
            timeout = self.DEFAULT_READY_TIMEOUT
        if self._ready.wait(timeout=timeout):
            # drain the Ready out of the queue so callers iterating events
            # don't re-encounter it — but keep it discoverable via the return.
            return self._peek_ready()
        if self._exited.is_set():
            raise RunnerNotReadyError(
                f"runner {self.session_id} exited before Ready "
                f"(code={self._exit_code}); see {self._stderr_path()}")
        raise RunnerNotReadyError(
            f"runner {self.session_id} not ready after {timeout}s; "
            f"see {self._stderr_path()}")

    def _peek_ready(self) -> Ready:
        # best-effort: scan the queue for the Ready (non-destructive by
        # re-queueing others). In practice wait_ready is the first consumer.
        drained = []
        found = None
        while True:
            try:
                ev = self._events.get_nowait()
            except queue.Empty:
                break
            if isinstance(ev, Ready) and found is None:
                found = ev
            else:
                drained.append(ev)
        for ev in drained:
            self._events.put(ev)
        return found or Ready(sessionId=self.session_id)

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def exit_code(self) -> Optional[int]:
        return self._exit_code if self._exit_code is not None else (
            self._proc.poll() if self._proc else None)

    def stderr_path(self) -> Optional[str]:
        return self._stderr_file.name if self._stderr_file else None

    # ------------------------------------------------------------------ #
    # event consumption
    # ------------------------------------------------------------------ #
    def next_event(self, timeout: Optional[float] = None) -> Optional[Event]:
        """Pop one event (None == end of stream / process gone)."""
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def iter_events(self, timeout: Optional[float] = None):
        """Yield events until the stream ends (None sentinel) or timeout fires
        between events."""
        while True:
            ev = self.next_event(timeout=timeout)
            if ev is None:
                return
            yield ev

    def wait_for_final(self, timeout: Optional[float] = 300.0) -> FinalAnswer:
        """Block consuming events until a FinalAnswer (terminal success)."""
        end = None if timeout is None else time.time() + timeout
        while True:
            now = time.time()
            if end is not None and now >= end:
                raise TimeoutError(
                    f"runner {self.session_id} no FinalAnswer within {timeout}s")
            to = None if end is None else max(0.1, end - now)
            ev = self.next_event(timeout=to)
            if ev is None:
                raise RuntimeError(
                    f"runner {self.session_id} stream ended before FinalAnswer")
            if isinstance(ev, FinalAnswer):
                return ev
            if isinstance(ev, Error) and ev.fatal:
                raise RuntimeError(f"runner fatal error: {ev.message}")
            if isinstance(ev, Exited) and ev.code != 0:
                raise RuntimeError(
                    f"runner exited code={ev.code}: {ev.reason}")

    # ------------------------------------------------------------------ #
    # teardown (kill_on_drop equivalent)
    # ------------------------------------------------------------------ #
    def shutdown(self, grace: float = 3.0) -> int:
        """Cooperative shutdown: send Shutdown, wait grace, then SIGKILL."""
        if self._closed:
            return self.exit_code if self.exit_code is not None else -1
        # Send Shutdown BEFORE marking closed: send_command refuses writes once
        # _closed is set, so flipping it first would never deliver the command
        # (observed: bridge hangs on readline -> grace expires -> SIGKILL -> -9).
        try:
            self.send_command(Shutdown())
        except RuntimeError:
            pass  # stdin already gone
        self._closed = True
        # wait for clean exit
        try:
            self._proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self._kill()
        self._join_reader()
        return self.exit_code if self.exit_code is not None else -1

    def terminate(self) -> None:
        """Forceful: SIGTERM then SIGKILL."""
        if self._proc is None:
            return
        self._closed = True
        try:
            self._proc.terminate()
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._kill()
        self._join_reader()

    def _kill(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.kill()
            self._proc.wait(timeout=2)
        except Exception:
            pass

    def _join_reader(self) -> None:
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=2)

    def close(self) -> None:
        """Release all resources (idempotent). Safe to call multiple times."""
        if self._closed and not self.is_alive:
            self._cleanup_files()
            return
        self.shutdown()
        self._cleanup_files()

    def _cleanup_files(self) -> None:
        # close the stderr tempfile handle; leave the file on disk for
        # diagnostics (caller may read stderr_path()).
        try:
            if self._stderr_file and not self._stderr_file.closed:
                self._stderr_file.flush()
                self._stderr_file.close()
        except Exception:
            pass

    @staticmethod
    def _finalize_static(proc: Optional[subprocess.Popen]) -> None:
        """weakref.finalize callback — last-resort kill to avoid zombies."""
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def __enter__(self) -> "RunnerProcess":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


# Register an atexit hook to clean up any RunnerProcess that might still be
# alive at interpreter shutdown (belt-and-suspenders for kill_on_drop).
_alive_runners: "weakref.WeakSet[RunnerProcess]" = weakref.WeakSet()


def _atexit_cleanup() -> None:
    for rp in list(_alive_runners):
        try:
            if rp.is_alive:
                rp._kill()
        except Exception:
            pass


atexit.register(_atexit_cleanup)
