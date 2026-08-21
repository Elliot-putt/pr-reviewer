"""Slack Socket Mode listener that publishes PRDetected events."""

from __future__ import annotations

import logging
import threading

from prreviewer.config import Settings
from prreviewer.core.events import EventBus, PRDetected
from prreviewer.slack.parser import PRLinkParser

logger = logging.getLogger(__name__)


class SlackListener:
    """Watches a Slack channel for GitHub PR links via Socket Mode."""

    def __init__(self, settings: Settings, event_bus: EventBus, parser: PRLinkParser) -> None:
        self._settings = settings
        self._bus = event_bus
        self._parser = parser
        self._app = None
        self._handler = None

    def start(self) -> None:
        """Start the Socket Mode handler on a background daemon thread."""
        try:
            from slack_bolt import App
            from slack_bolt.adapter.socket_mode import SocketModeHandler
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
            if subtype:
                logger.info("Ignoring message — subtype=%s", subtype)
                return
            ts = float(event.get("ts", 0) or 0)
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

        handler = SocketModeHandler(app, settings.slack_app_token)
        self._handler = handler
        thread = threading.Thread(target=handler.start, daemon=True, name="slack-socket-mode")
        thread.start()
        logger.info("Slack Socket Mode listener started.")

    def stop(self) -> None:
        """Close the Socket Mode connection so no further events arrive."""
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

    def backfill(self, limit: int = 25) -> None:
        """Fetch the last *limit* messages and emit PRDetected for any PR links found.

        Runs on the calling thread — call this from a background thread so it
        does not block window startup.
        """
        if self._app is None:
            logger.warning("backfill called before start(); skipping.")
            return
        channel_id = self._settings.slack_channel_id
        if not channel_id:
            return
        try:
            result = self._app.client.conversations_history(channel=channel_id, limit=limit)
            messages = result.get("messages", [])
            seen: set[str] = set()
            # Messages are newest-first; reverse so oldest PR appears first in inbox
            for msg in reversed(messages):
                # Skip system messages (edits, joins, etc.) but allow bot-posted PR links
                if msg.get("subtype"):
                    continue
                text = msg.get("text", "")
                ts = float(msg.get("ts", 0) or 0)
                for ref in self._parser.parse_all(text):
                    if ref.id not in seen:
                        seen.add(ref.id)
                        logger.info("Backfill: found PR %s (ts=%s)", ref, ts)
                        self._bus.publish(PRDetected(ref=ref, slack_ts=ts, from_backfill=True))
            logger.info(
                "Backfill complete: scanned %d messages, found %d PR(s).",
                len(messages), len(seen),
            )
        except Exception:
            logger.exception("Backfill failed — continuing without historical messages.")
