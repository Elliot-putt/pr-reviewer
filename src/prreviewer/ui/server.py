"""Asyncio websocket server + HTTP static-file server for the UI assets."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import websockets

if TYPE_CHECKING:
    from prreviewer.review.runner import ReviewRunner

logger = logging.getLogger(__name__)

# Ensure common web types are recognised on macOS
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".jsx")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("image/svg+xml", ".svg")


class UiServer:
    """Combined HTTP (static) + WebSocket server running on one asyncio event loop."""

    def __init__(self, port: int, asset_dir: Path) -> None:
        self._port = port
        self._asset_dir = asset_dir
        self._runner: "ReviewRunner | None" = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_runner(self, runner: "ReviewRunner") -> None:
        """Inject the ReviewRunner after construction (breaks circular dep)."""
        self._runner = runner

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The asyncio event loop used by this server (available after run_in_thread)."""
        if self._loop is None:
            raise RuntimeError("Server has not started yet.")
        return self._loop

    # ------------------------------------------------------------------
    # HTTP static file handler
    # ------------------------------------------------------------------

    async def _http_handler(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Minimal HTTP/1.1 static-file server."""
        try:
            raw = await reader.read(4096)
            first_line = raw.split(b"\r\n")[0].decode(errors="replace")
            parts = first_line.split()
            if len(parts) < 2:
                writer.close()
                return

            path = parts[1].split("?")[0]
            if path == "/":
                path = "/index.html"

            file_path = self._asset_dir / path.lstrip("/")
            # Prevent directory traversal
            try:
                file_path.resolve().relative_to(self._asset_dir.resolve())
            except ValueError:
                writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                await writer.drain()
                writer.close()
                return

            if file_path.is_file():
                content = file_path.read_bytes()
                mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
                header = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: {mime}\r\n"
                    f"Content-Length: {len(content)}\r\n"
                    f"Cache-Control: no-cache\r\n"
                    f"\r\n"
                ).encode()
                writer.write(header + content)
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")

            await writer.drain()
        except Exception:
            logger.exception("Error in HTTP handler.")
        finally:
            writer.close()

    # ------------------------------------------------------------------
    # WebSocket handler
    # ------------------------------------------------------------------

    async def _ws_handler(self, websocket) -> None:
        """Handle websocket connections for /terminal/{pr_id}."""
        path = websocket.request.path if hasattr(websocket, "request") else getattr(websocket, "path", "/")
        logger.info("WebSocket connection received: path=%s remote=%s", path, getattr(websocket, "remote_address", "?"))

        # Extract pr_id from path: /terminal/{pr_id}
        parts = path.strip("/").split("/")
        logger.info("WebSocket path parts: %s", parts)
        if len(parts) >= 2 and parts[0] == "terminal":
            pr_id = "/".join(parts[1:])
            logger.info("Routing to terminal handler for pr_id=%r", pr_id)
            await self._handle_terminal_ws(websocket, pr_id)
        else:
            logger.warning("WebSocket path %r did not match /terminal/{pr_id} — closing 1008.", path)
            await websocket.close(1008, "Unknown path")

    async def _handle_terminal_ws(self, websocket, pr_id: str) -> None:
        """Attach the websocket to the TerminalBridge for *pr_id*."""
        logger.info("_handle_terminal_ws: pr_id=%r runner=%s", pr_id, self._runner)
        bridge = self._runner.get_bridge(pr_id) if self._runner else None
        logger.info("Bridge lookup for pr_id=%r: %s", pr_id, "found" if bridge is not None else "NOT FOUND")
        if bridge is None:
            logger.warning("No active bridge for pr_id=%r — sending error and closing.", pr_id)
            await websocket.send('{"type":"error","message":"No active session for this PR."}')
            await websocket.close()
            return

        await bridge.attach_and_replay(websocket)
        logger.info("WebSocket attached to terminal bridge for %s.", pr_id)
        try:
            async for message in websocket:
                await bridge.handle_message(message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            bridge.detach_websocket(websocket)
            logger.info("WebSocket detached from terminal bridge for %s.", pr_id)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def _start(self) -> None:
        """Launch HTTP and WebSocket servers on the configured port."""
        # HTTP on port ui_port
        http_server = await asyncio.start_server(
            self._http_handler, "127.0.0.1", self._port
        )
        # WebSocket on port ui_port + 1
        ws_server = await websockets.serve(self._ws_handler, "127.0.0.1", self._port + 1)
        logger.info(
            "HTTP server: http://127.0.0.1:%d  |  WS server: ws://127.0.0.1:%d",
            self._port,
            self._port + 1,
        )
        async with http_server, ws_server:
            await asyncio.Future()  # run forever

    def run_in_thread(self) -> None:
        """Start the asyncio event loop in a background daemon thread."""
        loop = asyncio.new_event_loop()
        self._loop = loop

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._start())

        thread = threading.Thread(target=_run, daemon=True, name="ui-server")
        thread.start()
        logger.info("UI server thread started.")
