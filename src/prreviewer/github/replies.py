"""Replies inbox: detect replies to review comments you left on other people's PRs."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prreviewer.core.events import ReplyUpdated
from prreviewer.paths import env_path

if TYPE_CHECKING:
    from prreviewer.config import Settings
    from prreviewer.core.events import EventBus
    from prreviewer.github.client import GitHubClient

logger = logging.getLogger(__name__)

NOTIFICATIONS_URL = "https://api.github.com/notifications?participating=true&all=true&per_page=50"
REASONS = {"comment", "mention", "review_requested", "subscribed", "state_change"}
OPEN_STATUSES = {"pending", "verdict_ready", "verdict_error"}
CLOSED_STATUSES = {"done", "dismissed"}
PRUNE_DAYS = 14
MAX_PAGES = 5
CLASSIC_TOKEN_ERROR = "Replies needs a classic GitHub token (repo or notifications scope)."
INVALID_TOKEN_ERROR = "GitHub token was rejected — check it in Settings."

_SUBJECT_RE = re.compile(r"/repos/([^/]+)/([^/]+)/pulls/(\d+)$")

THREADS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      title url headRefOid author { __typename login }
      reviewThreads(first: 100) {
        nodes {
          id isResolved isOutdated path line originalLine
          comments(last: 20) {
            nodes { id databaseId author { __typename login } body bodyHTML createdAt url diffHunk }
          }
        }
      }
    }
  }
}
"""


def _iso(dt: datetime) -> str:
    """Format *dt* the way GitHub does (UTC, second precision, trailing Z)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso() -> str:
    return _iso(datetime.now(UTC))


def pr_key(owner: str, repo: str, number: int) -> str:
    """State key for a PR: ``owner/repo#number``."""
    return f"{owner}/{repo}#{number}"


def parse_subject_url(url: str) -> tuple[str, str, int] | None:
    """Extract (owner, repo, number) from a notification's ``subject.url``."""
    m = _SUBJECT_RE.search(url or "")
    if not m:
        return None
    return m.group(1), m.group(2), int(m.group(3))


def is_bot(author: dict | None) -> bool:
    """True for bot accounts and deleted (ghost) users, which never count as a reply."""
    if not author or not author.get("login"):
        return True
    return author.get("__typename") == "Bot" or author["login"].endswith("[bot]")


def _is_me(author: dict | None, login: str) -> bool:
    return bool(author and (author.get("login") or "").lower() == login.lower())


def has_my_comment(thread: dict, login: str) -> bool:
    """True when *login* wrote at least one comment in *thread*."""
    return any(_is_me(c.get("author"), login) for c in thread["comments"]["nodes"])


def reply_item_for(thread: dict, pr: dict, key: str, login: str) -> dict | None:
    """Build a reply item for *thread*, or None when it isn't waiting on me.

    A thread qualifies when it is unresolved, I have commented in it, and its
    latest human comment is someone else's and newer than my latest one.
    """
    if thread.get("isResolved"):
        return None
    human = [c for c in thread["comments"]["nodes"] if not is_bot(c.get("author"))]
    mine = [c for c in human if _is_me(c["author"], login)]
    if not mine or not human:
        return None
    last = human[-1]
    if _is_me(last["author"], login) or last["createdAt"] <= mine[-1]["createdAt"]:
        return None
    first_mine = mine[0]
    owner_repo, _, number = key.partition("#")
    return {
        "pr": key,
        "repo": owner_repo.split("/", 1)[1],
        "number": int(number),
        "pr_title": pr.get("title", ""),
        "pr_url": pr.get("url", ""),
        "pr_author": (pr.get("author") or {}).get("login", ""),
        "head_sha": pr.get("headRefOid", ""),
        "path": thread.get("path", ""),
        "line": thread.get("line") or thread.get("originalLine"),
        "thread_url": first_mine.get("url", ""),
        "last_comment_id": last.get("databaseId"),
        "last_comment_node_id": last["id"],
        "last_comment_at": last["createdAt"],
        "last_author": last["author"]["login"],
        "mine": [c["body"] for c in mine],
        "theirs": [c["body"] for c in human if not _is_me(c["author"], login)],
        "comments": [
            {
                "author": c["author"]["login"],
                "mine": _is_me(c["author"], login),
                "body": c["body"],
                "body_html": c.get("bodyHTML", ""),
                "created_at": c["createdAt"],
                "url": c.get("url", ""),
            }
            for c in human
        ],
        "code_then": first_mine.get("diffHunk", ""),
        "outdated": bool(thread.get("isOutdated")),
    }


def item_to_js(thread_id: str, item: dict) -> dict:
    """Camel-cased copy of a stored item for the frontend."""
    return {
        "id": thread_id,
        "pr": item.get("pr", ""),
        "repo": item.get("repo", ""),
        "number": item.get("number", 0),
        "prTitle": item.get("pr_title", ""),
        "prUrl": item.get("pr_url", ""),
        "prAuthor": item.get("pr_author", ""),
        "path": item.get("path", ""),
        "line": item.get("line"),
        "threadUrl": item.get("thread_url", ""),
        "lastCommentAt": item.get("last_comment_at", ""),
        "lastAuthor": item.get("last_author", ""),
        "comments": [
            {"author": c["author"], "mine": c["mine"], "body": c["body"],
             "bodyHtml": c.get("body_html", ""), "createdAt": c["created_at"], "url": c.get("url", "")}
            for c in item.get("comments", [])
        ],
        "outdated": item.get("outdated", False),
        "verdict": item.get("verdict"),
        "status": item.get("status", "pending"),
        "updatedAt": item.get("updated_at", ""),
    }


class RepliesStore:
    """Persisted Replies state (``replies.json`` next to the .env). Thread-safe."""

    def __init__(self, event_bus: EventBus, path: Path | None = None) -> None:
        self._bus = event_bus
        self._path = path or env_path().parent / "replies.json"
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"last_poll": "", "last_modified": "", "last_seen": "",
                                       "prs": {}, "items": {}}
        self.error = ""
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text())
                self._state.update({k: v for k, v in data.items() if k in self._state})
        except Exception:
            logger.exception("Could not read %s; starting with empty Replies state.", self._path)

    def save(self) -> None:
        """Write state to disk atomically."""
        with self._lock:
            payload = json.dumps(self._state, indent=2)
        tmp = self._path.with_suffix(".json.tmp")
        try:
            tmp.write_text(payload)
            os.replace(tmp, self._path)
        except Exception:
            logger.exception("Could not write %s.", self._path)

    def meta(self, key: str) -> str:
        """Return a top-level scalar (``last_poll``, ``last_modified``, ``last_seen``)."""
        with self._lock:
            return self._state.get(key, "") or ""

    def set_meta(self, **values: str) -> None:
        """Set top-level scalars."""
        with self._lock:
            self._state.update(values)

    def set_error(self, message: str) -> None:
        """Set the user-visible Replies error and notify listeners when it changes."""
        if message == self.error:
            return
        self.error = message
        self._bus.publish(ReplyUpdated(thread_id=""))

    def update_pr(self, key: str, **fields: Any) -> None:
        """Merge *fields* into the per-PR state entry."""
        with self._lock:
            self._state["prs"].setdefault(key, {}).update(fields)

    def upsert(self, thread_id: str, data: dict) -> None:
        """Record a qualifying reply thread.

        A new latest comment (re)opens the item as pending, even if it was done
        or dismissed. The same comment only refreshes display fields.
        """
        now = _now_iso()
        with self._lock:
            items = self._state["items"]
            existing = items.get(thread_id)
            if existing and existing.get("last_comment_id") == data["last_comment_id"]:
                if existing.get("status") in CLOSED_STATUSES:
                    return
                changed = {k: v for k, v in data.items() if existing.get(k) != v}
                if not changed:
                    return
                existing.update(changed)
            else:
                items[thread_id] = {**data, "verdict": None, "status": "pending",
                                    "first_seen_at": now, "updated_at": now}
        self._bus.publish(ReplyUpdated(thread_id=thread_id))

    def close(self, thread_id: str) -> None:
        """Mark an open item done (resolved on GitHub, or I replied last)."""
        with self._lock:
            item = self._state["items"].get(thread_id)
            if not item or item.get("status") not in OPEN_STATUSES:
                return
            item["status"] = "done"
            item["updated_at"] = _now_iso()
        self._bus.publish(ReplyUpdated(thread_id=thread_id))

    def prune(self) -> None:
        """Drop done/dismissed items older than PRUNE_DAYS."""
        cutoff = _iso(datetime.now(UTC) - timedelta(days=PRUNE_DAYS))
        with self._lock:
            items = self._state["items"]
            for tid in [t for t, i in items.items()
                        if i.get("status") in CLOSED_STATUSES and i.get("updated_at", "") < cutoff]:
                del items[tid]

    def item_js(self, thread_id: str) -> dict | None:
        """Frontend dict for one item, or None."""
        with self._lock:
            item = self._state["items"].get(thread_id)
            return item_to_js(thread_id, item) if item else None

    def list_js(self) -> list[dict]:
        """All items for the frontend, newest activity first."""
        with self._lock:
            out = [item_to_js(t, i) for t, i in self._state["items"].items()]
        return sorted(out, key=lambda i: i["lastCommentAt"], reverse=True)


class RepliesPoller:
    """Background thread: GitHub notifications → per-PR thread query → RepliesStore."""

    def __init__(self, settings: Settings, github_client: GitHubClient, store: RepliesStore) -> None:
        self._settings = settings
        self._client = github_client
        self._store = store
        self._min_interval = 60
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the daemon polling thread."""
        self._thread = threading.Thread(target=self._loop, daemon=True, name="replies-poller")
        self._thread.start()
        logger.info("Replies poller started.")

    def stop(self) -> None:
        """Ask the polling thread to exit."""
        self._stop.set()

    def _interval(self) -> int:
        return max(int(self._settings.replies_poll_seconds or 60), self._min_interval)

    def _loop(self) -> None:
        if self._stop.wait(5):
            return
        while True:
            try:
                self.poll_once()
            except Exception:
                logger.exception("Replies poll failed.")
            if self._stop.wait(self._interval()):
                return

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self._settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        last_modified = self._store.meta("last_modified")
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return headers

    def poll_once(self) -> None:
        """Run one notifications poll and process any PRs with new activity."""
        if not self._settings.replies_enabled or not self._settings.github_token:
            return
        login = self._client.get_login()
        if not login:
            return
        fetched = self._fetch_notifications()
        if fetched is None:
            return
        flagged, last_modified, newest = fetched
        failed = False
        for key, (owner, repo, number, notification_id) in flagged.items():
            try:
                self._check_pr(key, owner, repo, number, notification_id, login)
            except Exception:
                logger.exception("Replies: thread check failed for %s.", key)
                failed = True
        if not failed:
            updates = {"last_poll": _now_iso(), "last_modified": last_modified}
            if newest:
                updates["last_seen"] = newest
            self._store.set_meta(**updates)
        self._store.prune()
        self._store.save()

    def _fetch_notifications(self) -> tuple[dict, str, str] | None:
        """GET the notifications list. None on 304 or error; else (flagged PRs, Last-Modified, newest updated_at)."""
        import requests

        resp = requests.get(NOTIFICATIONS_URL, headers=self._headers(), timeout=15)
        poll_interval = resp.headers.get("X-Poll-Interval")
        if poll_interval and poll_interval.isdigit():
            self._min_interval = int(poll_interval)
        if resp.status_code == 304:
            self._store.set_error("")
            return None
        if resp.status_code == 401:
            self._store.set_error(INVALID_TOKEN_ERROR)
            return None
        if resp.status_code in (403, 404):
            logger.warning("Notifications API returned %s: %s", resp.status_code, resp.text[:200])
            self._store.set_error(CLASSIC_TOKEN_ERROR)
            return None
        resp.raise_for_status()
        self._store.set_error("")

        last_modified = resp.headers.get("Last-Modified", "")
        last_seen = self._store.meta("last_seen") or _iso(
            datetime.now(UTC) - timedelta(days=PRUNE_DAYS))
        notifications = list(resp.json())
        next_url = resp.links.get("next", {}).get("url")
        pages = 1
        while next_url and pages < MAX_PAGES and notifications and notifications[-1].get("updated_at", "") > last_seen:
            page = requests.get(next_url, headers={k: v for k, v in self._headers().items()
                                                   if k != "If-Modified-Since"}, timeout=15)
            page.raise_for_status()
            notifications.extend(page.json())
            next_url = page.links.get("next", {}).get("url")
            pages += 1

        flagged: dict[str, tuple[str, str, int, str]] = {}
        newest = ""
        for n in notifications:
            updated = n.get("updated_at", "")
            subject = n.get("subject") or {}
            if updated <= last_seen or subject.get("type") != "PullRequest" or n.get("reason") not in REASONS:
                continue
            parsed = parse_subject_url(subject.get("url", ""))
            if not parsed:
                continue
            newest = max(newest, updated)
            key = pr_key(*parsed)
            flagged.setdefault(key, (*parsed, str(n.get("id", ""))))
        logger.info("Replies: %d notification(s), %d PR(s) with new activity.", len(notifications), len(flagged))
        return flagged, last_modified, newest

    def _check_pr(self, key: str, owner: str, repo: str, number: int, notification_id: str, login: str) -> None:
        """Run the thread query for one PR and update reply items."""
        data = self._client.graphql(THREADS_QUERY, {"owner": owner, "repo": repo, "number": number})
        pr = (data.get("repository") or {}).get("pullRequest")
        if not pr:
            logger.info("Replies: %s not found; skipping.", key)
            return
        if _is_me(pr.get("author"), login):
            return
        threads = pr["reviewThreads"]["nodes"]
        mine = [t for t in threads if has_my_comment(t, login)]
        self._store.update_pr(
            key,
            notification_id=notification_id,
            my_threads=[t["id"] for t in mine],
            resolved=[t["id"] for t in mine if t.get("isResolved")],
            head_sha=pr.get("headRefOid", ""),
        )
        for thread in mine:
            item = reply_item_for(thread, pr, key, login)
            if item:
                self._store.upsert(thread["id"], item)
            else:
                self._store.close(thread["id"])
