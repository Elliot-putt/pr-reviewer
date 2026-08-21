"""Simple pub/sub event bus backed by asyncio queues."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Type

from prreviewer.models import PRRef, PRStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

@dataclass
class PRDetected:
    """A new PR link was found in Slack."""

    ref: PRRef
    slack_ts: float = 0.0  # Unix timestamp from the Slack message (0 = unknown)
    from_backfill: bool = False  # True when emitted by backfill/refresh, never triggers auto-review

    def to_dict(self) -> dict:
        return {"owner": self.ref.owner, "repo": self.ref.repo, "number": self.ref.number}


@dataclass
class PRUpdated:
    """A PR's status changed."""

    pr_id: str
    status: PRStatus

    def to_dict(self) -> dict:
        return {"pr_id": self.pr_id, "status": self.status.value}


@dataclass
class ReviewReady:
    """Claude finished producing review comments."""

    pr_id: str
    comments: list  # list[ReviewComment]

    def to_dict(self) -> dict:
        return {"pr_id": self.pr_id, "comment_count": len(self.comments)}


@dataclass
class ReviewPosted:
    """Review comments were posted to GitHub."""

    pr_id: str
    count: int

    def to_dict(self) -> dict:
        return {"pr_id": self.pr_id, "count": self.count}


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

class EventBus:
    """Thread-safe pub/sub hub. Subscribers receive events via synchronous callbacks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[Type, list[Callable]] = {}

    def subscribe(self, event_type: Type, handler: Callable) -> None:
        """Register *handler* to be called when an event of *event_type* is published."""
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(handler)

    def publish(self, event: Any) -> None:
        """Dispatch *event* to all registered handlers (synchronous, caller's thread)."""
        with self._lock:
            handlers = list(self._subscribers.get(type(event), []))
        for h in handlers:
            try:
                h(event)
            except Exception:
                logger.exception("Error in event handler for %s", type(event).__name__)
