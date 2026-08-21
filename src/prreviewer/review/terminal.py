"""PTY session that spawns the claude CLI."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)


class PtySession:
    """Spawns a process in a pseudo-terminal and fans its output to registered readers."""

    def __init__(self, claude_bin: str, cwd: str, model: str = "") -> None:
        self._bin = claude_bin
        self._cwd = cwd
        self._model = model
        self._proc = None
        # Updated on every PTY output chunk and every keystroke — the idle
        # watchdog uses this to auto-close abandoned sessions.
        self.last_activity = time.time()
        self._readers: list[Callable[[bytes], None]] = []
        self._lock = threading.Lock()

    def start(self, dimensions: tuple[int, int] = (40, 220), initial_prompt: str = "") -> None:
        """Spawn the process in a PTY and begin the read loop."""
        import ptyprocess

        cmd = [self._bin, "--dangerously-skip-permissions"]
        if self._model:
            cmd += ["--model", self._model]
        if initial_prompt:
            cmd.append(initial_prompt)
        self._proc = ptyprocess.PtyProcess.spawn(
            cmd,
            cwd=self._cwd,
            dimensions=dimensions,
        )
        thread = threading.Thread(target=self._read_loop, daemon=True, name=f"pty-reader-{id(self)}")
        thread.start()
        logger.info("PTY session started (pid=%s).", self._proc.pid)

    def write(self, data: bytes) -> None:
        """Send *data* to the PTY (keyboard input)."""
        with self._lock:
            if self._proc and self._proc.isalive():
                self.last_activity = time.time()
                self._proc.write(data)

    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal window."""
        with self._lock:
            if self._proc and self._proc.isalive():
                self._proc.setwinsize(rows, cols)

    def add_reader(self, fn: Callable[[bytes], None]) -> None:
        """Register a callback that will be called with each chunk of PTY output."""
        self._readers.append(fn)

    def is_alive(self) -> bool:
        """Whether the underlying process is still running."""
        return bool(self._proc and self._proc.isalive())

    def wait(self) -> None:
        """Block until the PTY process exits."""
        if self._proc:
            self._proc.wait()

    def terminate(self) -> None:
        """Kill the underlying process."""
        with self._lock:
            if self._proc and self._proc.isalive():
                self._proc.terminate()

    def _read_loop(self) -> None:
        """Background thread: read PTY output and fan to all registered readers."""
        logger.info("PTY read loop started (pid=%s).", getattr(self._proc, "pid", "?"))
        while True:
            try:
                data = self._proc.read(4096)
                self.last_activity = time.time()
                logger.debug("PTY read_loop: got %d bytes.", len(data))
                for fn in list(self._readers):
                    try:
                        fn(data)
                    except Exception:
                        logger.exception("PTY reader callback raised an exception.")
            except EOFError:
                logger.info("PTY session ended (EOF).")
                break
            except Exception:
                logger.exception("Unexpected error in PTY read loop.")
                break
        logger.info("PTY read loop exited.")
