"""Background poller: watches your own PRs for new review comments and fires Mac notifications."""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import TYPE_CHECKING

from prreviewer.models import PRStatus

if TYPE_CHECKING:
    from prreviewer.core.store import PRStore
    from prreviewer.github.client import GitHubClient

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 120  # seconds


def _esc(value: str) -> str:
    """terminal-notifier misparses values starting with '[' or '-' as flags; escape them."""
    return f"\\{value}" if value and value[0] in "[-" else value


def _notify(title: str, subtitle: str, message: str, pr_number: int, ui_port: int) -> None:
    url = f"http://127.0.0.1:{ui_port}/?pr={pr_number}"
    title, subtitle, message = _esc(title), _esc(subtitle), _esc(message)
    try:
        subprocess.run(
            [
                "terminal-notifier",
                "-title", title,
                "-subtitle", subtitle,
                "-message", message,
                "-open", url,
            ],
            check=False,
            timeout=5,
        )
    except FileNotFoundError:
        logger.warning("terminal-notifier not found; skipping notification.")
    except Exception:
        logger.exception("Notification failed.")


class PRPoller:
    """Polls GitHub every 2 minutes for new comments on your own PRs."""

    def __init__(self, github_client: GitHubClient, store: PRStore, ui_port: int, push_to_js=None, settings=None) -> None:
        self._client = github_client
        self._store = store
        self._ui_port = ui_port
        self._push_to_js = push_to_js
        self._settings = settings  # for native_notifications flag
        self._seen_counts: dict[str, int] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="pr-poller")
        self._thread.start()
        logger.info("PR poller started (interval=%ds).", _POLL_INTERVAL)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(_POLL_INTERVAL):
            self._poll()

    def _poll(self) -> None:
        try:
            # Resolved per cycle (cached per token) so PAT changes apply live.
            login = self._client.get_login()
            if not login:
                return
            _done = (PRStatus.MERGED, PRStatus.CLOSED, PRStatus.POSTED)
            my_prs = [
                pr for pr in self._store.list_all()
                if pr.author == login and pr.status not in _done
            ]
            for pr in my_prs:
                try:
                    count = self._client.get_unresolved_count(pr.ref.owner, pr.ref.repo, pr.ref.number)
                except Exception:
                    logger.debug("Failed to fetch unresolved count for %s.", pr.id)
                    continue

                prev = self._seen_counts.get(pr.id, pr.unresolved_count)
                _active = (PRStatus.CHECKING_OUT, PRStatus.REVIEWING)
                with self._store._lock:
                    if pr.id in self._store._prs:
                        self._store._prs[pr.id].unresolved_count = count
                        if count > 0 and self._store._prs[pr.id].status not in _active:
                            self._store._prs[pr.id].status = PRStatus.NEEDS_ATTENTION

                if (count > 0 or count != prev) and self._push_to_js:
                    updated = self._store.get(pr.id)
                    if updated:
                        self._push_to_js("pr-updated", updated.to_dict())

                if count > prev:
                    new = count - prev
                    logger.info("PR %s: %d new unresolved comment(s).", pr.id, new)
                    native = self._settings.native_notifications if self._settings else True
                    if native:
                        _notify(
                            title="New Review Comments",
                            subtitle=pr.title,
                            message=f"#{pr.number} has {count} unresolved comment(s)",
                            pr_number=pr.number,
                            ui_port=self._ui_port,
                        )

                self._seen_counts[pr.id] = count
        except Exception:
            logger.exception("Poller error.")
