"""Slack Socket Mode listener that publishes PRDetected events."""

from __future__ import annotations

import logging
import threading
import time

from slack_sdk.errors import SlackApiError

from prreviewer.config import Settings
from prreviewer.core.events import EventBus, PRDetected
from prreviewer.slack.parser import PRLinkParser

logger = logging.getLogger(__name__)

WATCHDOG_SECONDS = 30
LIVE_SUBTYPES = {"thread_broadcast"}


def message_text(event: dict) -> tuple[str, float] | None:
    """(text, ts) of a channel message worth scanning for PR links, or None to ignore it.

    Plain messages and thread replies sent to the channel count; an edit counts as its
    new text, so a link added by editing a message is still seen.
    """
    subtype = event.get("subtype")
    if subtype == "message_changed":
        edited = event.get("message") or {}
        return edited.get("text", ""), float(edited.get("ts", 0) or 0)
    if subtype and subtype not in LIVE_SUBTYPES:
        return None
    return event.get("text", ""), float(event.get("ts", 0) or 0)
WAKE_GAP_SECONDS = 120


class SlackListener:
    """Watches a Slack channel for GitHub PR links via Socket Mode."""

    def __init__(self, settings: Settings, event_bus: EventBus, parser: PRLinkParser) -> None:
        self._settings = settings
        self._bus = event_bus
        self._parser = parser
        self._app = None
        self._handler = None
        self._stop = threading.Event()
        self._last_tick = time.monotonic()
        self._last_wall = time.time()
        self._misses = 0

    def start(self) -> None:
        """Start the Socket Mode handler on a background daemon thread."""
        try:
            from slack_bolt import App
        except ImportError:
            logger.error("slack-bolt is not installed; Slack listener disabled.")
            return

        settings = self._settings
        bus = self._bus
        parser = self._parser

        app = App(token=settings.slack_bot_token)
        self._app = app

        def _process_message(event: dict, logger: logging.Logger) -> None:
            channel = event.get("channel")
            subtype = event.get("subtype")
            text = event.get("text", "")
            bot_id = event.get("bot_id")
            logger.info(
                "Slack message: channel=%s subtype=%s bot_id=%s text=%.120r",
                channel, subtype, bot_id, text,
            )
            if channel != settings.slack_channel_id:
                logger.info("Ignoring message — wrong channel (%s != %s)", channel, settings.slack_channel_id)
                return
            found = message_text(event)
            if found is None:
                logger.info("Ignoring message — subtype=%s", subtype)
                return
            text, ts = found
            refs = parser.parse_all(text)
            if not refs:
                logger.info("No PR links found in message.")
                return
            for ref in refs:
                logger.info("Detected PR %s — publishing event", ref)
                bus.publish(PRDetected(ref=ref, slack_ts=ts))

        # Register for both bare "message" and the channel-specific subtype.
        # Public channels emit "message" with event_type "message" but
        # slack_bolt also dispatches "message.channels" — registering both
        # ensures we catch messages regardless of which variant arrives.
        app.event("message")(_process_message)

        self._connect()
        self._stop.clear()
        threading.Thread(target=self._watchdog, daemon=True, name="slack-watchdog").start()
        logger.info("Slack Socket Mode listener started.")

    def _connect(self) -> None:
        """(Re)create the Socket Mode handler and start it on a daemon thread."""
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        handler = SocketModeHandler(self._app, self._settings.slack_app_token)
        self._handler = handler
        threading.Thread(target=handler.start, daemon=True, name="slack-socket-mode").start()

    def health_action(self, connected: bool, wall_gap: float) -> str:
        """Decide what one watchdog tick should do: ``""``, ``"reconnect"`` or ``"refresh"``.

        A wall-clock gap much longer than the tick means the Mac slept, so the socket may be
        stale even if it reports connected. Two disconnected ticks in a row mean a real drop.
        """
        if wall_gap > WAKE_GAP_SECONDS:
            self._misses = 0
            return "refresh"
        self._misses = 0 if connected else self._misses + 1
        return "reconnect" if self._misses >= 2 else ""

    def _watchdog(self) -> None:
        while not self._stop.wait(WATCHDOG_SECONDS):
            now = time.time()
            gap, self._last_wall = now - self._last_wall, now
            handler = self._handler
            if handler is None:
                continue
            try:
                connected = handler.client.is_connected()
            except Exception:
                connected = False
            action = self.health_action(connected, gap)
            if not action:
                continue
            try:
                if action == "refresh":
                    logger.info("Slack: woke after %.0fs — refreshing the connection and catching up.", gap)
                    handler.client.connect_to_new_endpoint(force=True)
                else:
                    logger.warning("Slack: connection lost — reconnecting and catching up.")
                    try:
                        handler.close()
                    except Exception:
                        logger.debug("Closing the dead Socket Mode handler failed.")
                    self._connect()
                self._misses = 0
                self.backfill(limit=25, catch_up=True)
            except Exception:
                logger.exception("Slack reconnect failed; will retry.")

    def stop(self) -> None:
        """Close the Socket Mode connection so no further events arrive."""
        self._stop.set()
        if self._handler is None:
            return
        try:
            self._handler.close()
            logger.info("Slack Socket Mode listener stopped.")
        except Exception:
            logger.exception("Error closing Slack Socket Mode handler.")
        finally:
            self._handler = None
            self._app = None

    def backfill(self, limit: int = 25, catch_up: bool = False) -> str:
        """Fetch the last *limit* messages and emit PRDetected for any PR links found.

        Runs on the calling thread — call this from a background thread so it
        does not block window startup. Returns a user-facing error, or "" on success.
        """
        if self._app is None:
            logger.warning("backfill called before start(); skipping.")
            return ""
        channel_id = self._settings.slack_channel_id
        if not channel_id:
            return ""
        try:
            result = self._app.client.conversations_history(channel=channel_id, limit=limit)
            messages = result.get("messages", [])
            seen: set[str] = set()
            # Messages are newest-first; reverse so oldest PR appears first in inbox
            for msg in reversed(messages):
                # Skip system messages (edits, joins, etc.) but allow bot-posted PR links
                if msg.get("subtype") and msg.get("subtype") not in LIVE_SUBTYPES:
                    continue
                text = msg.get("text", "")
                ts = float(msg.get("ts", 0) or 0)
                for ref in self._parser.parse_all(text):
                    if ref.id not in seen:
                        seen.add(ref.id)
                        logger.info("Backfill: found PR %s (ts=%s)", ref, ts)
                        self._bus.publish(PRDetected(ref=ref, slack_ts=ts, from_backfill=True, catch_up=catch_up))
            logger.info(
                "Backfill complete: scanned %d messages, found %d PR(s).",
                len(messages), len(seen),
            )
            return ""
        except SlackApiError as exc:
            code = exc.response.get("error", "")
            if code == "not_in_channel":
                message = self._not_in_channel_message(channel_id)
                logger.warning("Backfill skipped: %s", message)
                return message
            if code == "channel_not_found":
                logger.warning("Backfill skipped: channel %s not found.", channel_id)
                return "Slack channel not found — pick it again in Settings."
            logger.warning("Backfill failed: %s", code or exc)
            return f"Couldn't read the Slack channel ({code or 'unknown error'})."
        except Exception:
            logger.exception("Backfill failed — continuing without historical messages.")
            return "Couldn't read the Slack channel — see the log."

    def _not_in_channel_message(self, channel_id: str) -> str:
        """Explain how to add the bot to the channel, using real names where Slack gives them."""
        name, bot = channel_id, "the bot"
        try:
            name = "#" + self._app.client.conversations_info(channel=channel_id)["channel"]["name"]
            bot = "@" + self._app.client.auth_test()["user"]
        except Exception:
            logger.debug("Could not resolve channel/bot names for the invite hint.")
        return f"The bot isn't in {name}. In Slack, run /invite {bot} in that channel."
