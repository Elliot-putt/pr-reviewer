"""In-memory PR store with event emission on mutations."""

from __future__ import annotations

import logging
import threading
from typing import Optional

from prreviewer.models import PRStatus, PullRequest
from prreviewer.core.events import EventBus, PRUpdated

logger = logging.getLogger(__name__)


class PRStore:
    """Thread-safe in-memory store for tracked pull requests."""

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus
        self._lock = threading.Lock()
        self._prs: dict[str, PullRequest] = {}

    def add(self, pr: PullRequest) -> None:
        """Add a new PR to the store and emit PRUpdated."""
        with self._lock:
            self._prs[pr.id] = pr
        logger.info("Stored PR %s (%s)", pr.id, pr.title)
        self._bus.publish(PRUpdated(pr_id=pr.id, status=pr.status))

    def update_status(self, pr_id: str, status: PRStatus) -> None:
        """Update the status of an existing PR and emit PRUpdated."""
        with self._lock:
            if pr_id not in self._prs:
                logger.warning("update_status: unknown PR %s", pr_id)
                return
            self._prs[pr_id].status = status
        self._bus.publish(PRUpdated(pr_id=pr_id, status=status))

    def set_session_deadline(self, pr_id: str, deadline: float) -> None:
        """Update the idle-watchdog deadline for a PR and emit PRUpdated."""
        with self._lock:
            pr = self._prs.get(pr_id)
            if not pr or pr.session_deadline == deadline:
                return
            pr.session_deadline = deadline
            status = pr.status
        self._bus.publish(PRUpdated(pr_id=pr_id, status=status))

    def get(self, pr_id: str) -> Optional[PullRequest]:
        """Return the PR with *pr_id*, or None if not found."""
        with self._lock:
            return self._prs.get(pr_id)

    def list_all(self) -> list[PullRequest]:
        """Return all stored PRs, most-recently-added first."""
        with self._lock:
            return list(reversed(list(self._prs.values())))
