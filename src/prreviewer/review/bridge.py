"""Bidirectional bridge between a PtySession and a websocket connection."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prreviewer.review.terminal import PtySession

logger = logging.getLogger(__name__)

_MAX_SCROLLBACK_BYTES = 512 * 1024  # 512 KB


class TerminalBridge:
    """Connects PTY bytes to a websocket client.

    Wire protocol (JSON messages):
    - ``{"type": "data",   "data": "<base64>"}``  — PTY output → browser
    - ``{"type": "input",  "data": "<base64>"}``  — keyboard input ← browser
    - ``{"type": "resize", "cols": N, "rows": N}`` — terminal resize ← browser
    """

    def __init__(self, pty: "PtySession", loop: asyncio.AbstractEventLoop) -> None:
        self._pty = pty
        self._loop = loop
        self._websockets: set = set()
        self._connected = threading.Event()
        self._dims: tuple[int, int] = (40, 80)  # (rows, cols)
        self._resized = threading.Event()
        # Scrollback: list of already-encoded JSON message strings.
        # Sent to new connections before they join the broadcast set.
        self._scrollback: list[str] = []
        self._scrollback_bytes = 0

    def wait_for_resize(self, timeout: float = 5.0) -> tuple[int, int]:
        """Block until xterm sends its first resize (or timeout). Returns (rows, cols)."""
        self._resized.wait(timeout)
        return self._dims

    def detach_websocket(self, ws) -> None:
        """Unregister *ws*."""
        self._websockets.discard(ws)
        logger.info("detach_websocket: now %d websockets attached", len(self._websockets))

    def on_pty_data(self, data: bytes) -> None:
        """Called from PTY reader thread; appends to scrollback and broadcasts."""
        msg = json.dumps({"type": "data", "data": base64.b64encode(data).decode()})
        # Append to scrollback (thread-safe: GIL protects list.append)
        self._scrollback.append(msg)
        self._scrollback_bytes += len(msg)
        # Trim from front if over limit
        while self._scrollback_bytes > _MAX_SCROLLBACK_BYTES and self._scrollback:
            removed = self._scrollback.pop(0)
            self._scrollback_bytes -= len(removed)
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    async def attach_and_replay(self, ws) -> None:
        """Replay scrollback to *ws*, then add it to the live broadcast set.

        Replay happens BEFORE joining the set so the ws never receives a message
        via both replay and broadcast (which was the old duplication bug).
        """
        snapshot = list(self._scrollback)
        if snapshot:
            logger.info("Replaying %d scrollback messages to new ws", len(snapshot))
            for msg in snapshot:
                try:
                    await ws.send(msg)
                except Exception:
                    logger.warning("ws closed during scrollback replay — aborting")
                    return
            # Send any messages that arrived while we were replaying
            tail = self._scrollback[len(snapshot):]
            for msg in tail:
                try:
                    await ws.send(msg)
                except Exception:
                    return

        # Now join the broadcast set — no await between here and the add,
        # so no other coroutine can slip a broadcast in for this ws.
        self._websockets.add(ws)
        self._connected.set()
        logger.info("attach_websocket: now %d websockets attached", len(self._websockets))

    async def _broadcast(self, msg: str) -> None:
        """Send *msg* to all attached websockets."""
        dead = set()
        for ws in list(self._websockets):
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        self._websockets -= dead

    async def handle_message(self, raw: str) -> None:
        """Process an incoming websocket message from the browser."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Received non-JSON message from websocket: %r", raw)
            return

        kind = msg.get("type")
        if kind == "input":
            data = base64.b64decode(msg.get("data", ""))
            self._pty.write(data)
        elif kind == "resize":
            cols = int(msg.get("cols", 80))
            rows = int(msg.get("rows", 24))
            self._dims = (rows, cols)
            self._resized.set()
            self._pty.resize(rows, cols)
        else:
            logger.debug("Unknown websocket message type: %s", kind)
