"""Replies inbox: detect replies to review comments you left on other people's PRs."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
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
_API = "https://api.github.com"
CODE_CONTEXT_LINES = 15
DIFF_CAP_CHARS = 12_000

THREADS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      title url state headRefOid headRefName author { __typename login }
      reviews(last: 50) { nodes { author { login } state body commit { oid } } }
      reviewThreads(first: 100) {
        nodes {
          id isResolved isOutdated path line originalLine
          comments(last: 20) {
            nodes { id databaseId author { __typename login } body bodyHTML createdAt url diffHunk originalCommit { oid } }
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
        "base_sha": (first_mine.get("originalCommit") or {}).get("oid", ""),
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
        "verdictSha": item.get("verdict_sha", ""),
        "headSha": item.get("head_sha", ""),
        "evidence": item.get("evidence", ""),
        "note": item.get("note", ""),
        "action": item.get("action", ""),
        "actionError": item.get("action_error", ""),
        "auto": bool(item.get("auto")),
        "autoResolving": bool(item.get("auto_resolving")),
        "autoConfidence": item.get("auto_confidence"),
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
        self.claude_error = ""
        self.on_rereview: Callable[[str], None] | None = None
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

    def set_claude_error(self, message: str) -> None:
        """Set the user-visible verdict-worker error and notify listeners when it changes."""
        if message == self.claude_error:
            return
        self.claude_error = message
        self._bus.publish(ReplyUpdated(thread_id=""))

    def get(self, thread_id: str) -> dict | None:
        """Return a copy of one stored item."""
        with self._lock:
            item = self._state["items"].get(thread_id)
            return dict(item) if item else None

    def open_items(self, key: str | None = None) -> dict[str, dict]:
        """Copies of open (to-triage) items, optionally limited to one PR."""
        with self._lock:
            return {t: dict(i) for t, i in self._state["items"].items()
                    if i.get("status") in OPEN_STATUSES and (key is None or i.get("pr") == key)}

    def set_verdict(self, thread_id: str, judged_comment_id: Any, **fields: Any) -> bool:
        """Store a verdict result, unless a newer comment arrived while it was being judged."""
        with self._lock:
            item = self._state["items"].get(thread_id)
            if not item or item.get("last_comment_id") != judged_comment_id:
                return False
            if item.get("status") in CLOSED_STATUSES:
                return False
            item.update(fields)
            item["updated_at"] = _now_iso()
        self._bus.publish(ReplyUpdated(thread_id=thread_id))
        return True

    def update_item(self, thread_id: str, **fields: Any) -> None:
        """Merge *fields* into one item and notify listeners."""
        with self._lock:
            item = self._state["items"].get(thread_id)
            if not item:
                return
            item.update(fields)
            item["updated_at"] = _now_iso()
        self._bus.publish(ReplyUpdated(thread_id=thread_id))

    def touch_pr(self, key: str) -> None:
        """Notify listeners for every item on *key* (PR-level state changed)."""
        with self._lock:
            tids = [t for t, i in self._state["items"].items() if i.get("pr") == key]
        for tid in tids:
            self._bus.publish(ReplyUpdated(thread_id=tid))

    def set_thread_resolved(self, key: str, thread_id: str, resolved: bool) -> None:
        """Keep the PR's resolved-thread list in step with an action, without waiting for a refresh."""
        with self._lock:
            pr = self._state["prs"].setdefault(key, {})
            done = [t for t in pr.get("resolved", []) if t != thread_id]
            pr["resolved"] = [*done, thread_id] if resolved else done

    def pr_state(self, key: str) -> dict:
        """Copy of the per-PR state entry."""
        with self._lock:
            return dict(self._state["prs"].get(key, {}))

    def reset_verdict(self, thread_id: str) -> None:
        """Clear a verdict so the item is judged again."""
        with self._lock:
            item = self._state["items"].get(thread_id)
            if not item or item.get("status") in CLOSED_STATUSES:
                return
            item.update({"verdict": None, "verdict_sha": "", "evidence": "", "note": "",
                         "skipped_sha": "", "status": "pending", "auto_resolving": False})
        self._bus.publish(ReplyUpdated(thread_id=thread_id))

    def update_pr(self, key: str, **fields: Any) -> None:
        """Merge *fields* into the per-PR state entry."""
        with self._lock:
            self._state["prs"].setdefault(key, {}).update(fields)

    def upsert(self, thread_id: str, data: dict) -> bool:
        """Record a qualifying reply thread. Returns True when it is a new reply to triage.

        A new latest comment (re)opens the item as pending, even if it was done
        or dismissed. The same comment only refreshes display fields.
        """
        now = _now_iso()
        with self._lock:
            items = self._state["items"]
            existing = items.get(thread_id)
            is_new = not (existing and existing.get("last_comment_id") == data["last_comment_id"])
            if not is_new:
                if existing.get("status") in CLOSED_STATUSES:
                    return False
                changed = {k: v for k, v in data.items() if existing.get(k) != v}
                if not changed:
                    return False
                existing.update(changed)
            else:
                items[thread_id] = {**data, "verdict": None, "status": "pending",
                                    "first_seen_at": now, "updated_at": now}
        self._bus.publish(ReplyUpdated(thread_id=thread_id))
        return is_new

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

    def _js(self, thread_id: str, item: dict) -> dict:
        out = item_to_js(thread_id, item)
        pr = self._state["prs"].get(item.get("pr", ""), {})
        out["prThreads"] = len(pr.get("my_threads", []))
        out["prResolved"] = len(pr.get("resolved", []))
        out["prRereview"] = pr.get("rereview", "")
        out["prId"] = f"pr-{out['repo']}-{out['number']}"
        return out

    def item_js(self, thread_id: str) -> dict | None:
        """Frontend dict for one item, or None."""
        with self._lock:
            item = self._state["items"].get(thread_id)
            return self._js(thread_id, item) if item else None

    def list_js(self) -> list[dict]:
        """All items for the frontend, newest activity first."""
        with self._lock:
            out = [self._js(t, i) for t, i in self._state["items"].items()]
        return sorted(out, key=lambda i: i["lastCommentAt"], reverse=True)

    def rereview_sweep_keys(self) -> list[str]:
        """PRs where I have review threads and that aren't known to be merged or closed."""
        with self._lock:
            return sorted(k for k, pr in self._state["prs"].items()
                          if pr.get("my_threads") and pr.get("state") not in ("MERGED", "CLOSED"))

    def open_pr_keys(self) -> list[str]:
        """PRs that still have something to triage."""
        with self._lock:
            return sorted({i["pr"] for i in self._state["items"].values() if i.get("status") in OPEN_STATUSES})

    def log_calibration(self, record: dict) -> None:
        """Append one verdict/action record to ``replies-calibration.jsonl`` for tuning the threshold."""
        path = self._path.parent / "replies-calibration.jsonl"
        try:
            with path.open("a") as fh:
                fh.write(json.dumps({"ts": _now_iso(), **record}) + "\n")
        except OSError:
            logger.debug("Could not append to %s.", path)


class RepliesPoller:
    """Background thread: GitHub notifications → per-PR thread query → RepliesStore."""

    def __init__(self, settings: Settings, github_client: GitHubClient, store: RepliesStore,
                 on_checked: Callable[[str], None] | None = None,
                 notify: Callable[[int], None] | None = None) -> None:
        self._settings = settings
        self._client = github_client
        self._store = store
        self._on_checked = on_checked
        self._notify = notify
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
        self.sweep_rereviews()
        next_watch = time.time() + self._watch_interval()
        while True:
            try:
                self.poll_once()
            except Exception:
                logger.exception("Replies poll failed.")
            if time.time() >= next_watch:
                self.watch_once()
                next_watch = time.time() + self._watch_interval()
            if self._stop.wait(self._interval()):
                return

    def _watch_interval(self) -> int:
        return max(int(self._settings.replies_watch_seconds or 300), 60)

    def sweep_rereviews(self) -> None:
        """One pass at startup: refresh PRs where I have threads, so ones the author resolved are noticed."""
        login = self._client.get_login()
        if not login or not self._settings.replies_enabled:
            return
        keys = self._store.rereview_sweep_keys()
        logger.info("Replies: checking %d PR(s) for re-reviews.", len(keys))
        for key in keys:
            if self._stop.is_set():
                return
            try:
                refresh_pr(self._client, self._store, key, login)
            except Exception:
                logger.exception("Replies: re-review check failed for %s.", key)
        self._store.save()

    def watch_once(self) -> None:
        """Re-check PRs with open items even without a notification (pushes and resolves don't notify)."""
        if not self._settings.replies_enabled or not self._on_checked:
            return
        for key in self._store.open_pr_keys():
            self._on_checked(key)

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
        first_run = not self._store.meta("last_seen")
        new_replies = 0
        failed = False
        for key, (owner, repo, number, notification_id) in flagged.items():
            try:
                new_replies += self._check_pr(key, owner, repo, number, notification_id, login)
                if self._on_checked:
                    self._on_checked(key)
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
        if new_replies and not first_run and self._notify:
            self._notify(new_replies)

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

    def _check_pr(self, key: str, owner: str, repo: str, number: int, notification_id: str, login: str) -> int:
        """Run the thread query for one PR and update reply items. Returns the number of new replies."""
        return refresh_pr(self._client, self._store, key, login, notification_id=notification_id)[1]


def refresh_pr(client: GitHubClient, store: RepliesStore, key: str, login: str,
               **pr_fields: Any) -> tuple[dict | None, int]:
    """Re-query one PR's review threads and sync its reply items.

    Returns (PR node or None, number of new replies to triage).
    """
    owner_repo, _, number = key.partition("#")
    owner, repo = owner_repo.split("/", 1)
    data = client.graphql(THREADS_QUERY, {"owner": owner, "repo": repo, "number": int(number)})
    pr = (data.get("repository") or {}).get("pullRequest")
    if not pr:
        logger.info("Replies: %s not found; skipping.", key)
        return None, 0
    if _is_me(pr.get("author"), login):
        return None, 0
    threads = pr["reviewThreads"]["nodes"]
    mine = [t for t in threads if has_my_comment(t, login)]
    store.update_pr(
        key,
        my_threads=[t["id"] for t in mine],
        resolved=[t["id"] for t in mine if t.get("isResolved")],
        head_sha=pr.get("headRefOid", ""),
        state=pr.get("state", ""),
        my_last_review_sha=last_review_sha(pr, login),
        **pr_fields,
    )
    new = 0
    for thread in mine:
        item = reply_item_for(thread, pr, key, login)
        if item:
            new += store.upsert(thread["id"], item)
        else:
            store.close(thread["id"])
    check_rereview(store, key)
    return pr, new


def last_review_sha(pr: dict, login: str) -> str:
    """Commit of my last real review: approve/request-changes or one with a summary.

    Inline comments and thread replies show up as empty COMMENTED reviews, so they
    don't count. Falls back to my earliest review when there's no real one.
    """
    mine = [r for r in (pr.get("reviews") or {}).get("nodes", [])
            if _is_me(r.get("author"), login) and (r.get("commit") or {}).get("oid")]
    real = [r for r in mine if r.get("state") in ("APPROVED", "CHANGES_REQUESTED") or (r.get("body") or "").strip()]
    pick = real[-1] if real else (mine[0] if mine else None)
    return pick["commit"]["oid"] if pick else ""


def rereview_due(state: dict) -> bool:
    """All my threads on the PR are resolved, there are commits since my last review, and it's still open."""
    threads = set(state.get("my_threads", []))
    head = state.get("head_sha", "")
    return bool(
        threads
        and threads <= set(state.get("resolved", []))
        and head
        and state.get("my_last_review_sha")
        and head != state.get("my_last_review_sha")
        and head != state.get("rereviewed_sha")
        and (state.get("state") or "OPEN") == "OPEN"
    )


def check_rereview(store: RepliesStore, key: str) -> bool:
    """Mark the PR ready for re-review the first time it becomes due at this head. Returns True when newly marked."""
    state = store.pr_state(key)
    due = rereview_due(state)
    if not due:
        if state.get("rereview") == "ready":
            store.update_pr(key, rereview="")
            store.touch_pr(key)
        return False
    if state.get("rereview") == "ready" and state.get("rereview_head") == state.get("head_sha"):
        return False
    store.update_pr(key, rereview="ready", rereview_head=state.get("head_sha"))
    store.save()
    store.touch_pr(key)
    if store.on_rereview:
        store.on_rereview(key)
    return True


def rereview_prompt(review_command: str, pr_url: str, since_sha: str, since_exists: bool) -> str:
    """The review command plus re-review instructions (first line stays ``<command> <url>``)."""
    if since_exists:
        scope = (f"Only review what changed since my last review, at commit {since_sha}: "
                 f"use `git diff {since_sha}..HEAD` (fetch it first if needed).")
    else:
        scope = (f"My last review was at commit {since_sha}, which no longer exists after a rebase or force-push, "
                 "so review the full PR diff.")
    return (
        f"{review_command.strip()} {pr_url}\n\n"
        "This is a re-review: all of my earlier review threads on this PR are resolved and new commits have landed. "
        f"{scope} Don't re-raise anything my resolved threads already covered. "
        "If nothing Critical or Important is left, approve the PR."
    )


def _rest_headers(token: str, accept: str = "application/vnd.github+json") -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}


def code_window(text: str, line: int | None, context: int = CODE_CONTEXT_LINES) -> str:
    """Return ``line ± context`` of *text*, each line prefixed with its number."""
    lines = text.splitlines()
    if not lines:
        return ""
    center = min(max(int(line or 1), 1), len(lines))
    start, end = max(center - context, 1), min(center + context, len(lines))
    width = len(str(end))
    return "\n".join(f"{n:>{width}} | {lines[n - 1]}" for n in range(start, end + 1))


def fetch_code_now(token: str, owner: str, repo: str, path: str, ref: str, line: int | None) -> str:
    """Current code around *line* of *path* at *ref*, or a short note when unavailable."""
    import requests

    resp = requests.get(f"{_API}/repos/{owner}/{repo}/contents/{path}", params={"ref": ref},
                        headers=_rest_headers(token, "application/vnd.github.raw"), timeout=20)
    if resp.status_code == 404:
        return "(file no longer exists at the PR head)"
    resp.raise_for_status()
    return code_window(resp.text, line)


def cap_patches(files: list[dict], first_paths: list[str], cap: int = DIFF_CAP_CHARS) -> str:
    """Join per-file patches, *first_paths* first (in that order), stopping at *cap* characters."""
    rank = {p: i for i, p in enumerate(first_paths)}
    ordered = sorted(files, key=lambda f: rank.get(f.get("filename", ""), len(rank)))
    out: list[str] = []
    used = 0
    for f in ordered:
        patch = f.get("patch")
        if not patch:
            continue
        chunk = f"--- {f['filename']}\n{patch}\n"
        if used + len(chunk) > cap:
            out.append(f"(diff truncated at {cap} characters)")
            break
        out.append(chunk)
        used += len(chunk)
    return "".join(out)


def mentioned_files(texts: list[str], candidates: list[str]) -> list[str]:
    """Changed files whose basename appears in any of *texts*."""
    blob = "\n".join(texts)
    return [c for c in candidates if Path(c).name and Path(c).name in blob]


def fetch_changes(token: str, owner: str, repo: str, number: int, base: str, head: str,
                  path: str, texts: list[str], cache: dict) -> tuple[str, str]:
    """What changed since my comment: (diff text, evidence), evidence being ``full`` or ``partial``.

    Uses compare base...head. A 404/422 or a diverged history (rebases, force-pushes)
    falls back to the PR's current diff for the thread's file plus any file named in
    the thread text, marked ``partial``.
    """
    import requests

    if base and base == head:
        return "", "full"
    if base:
        ck = ("compare", base, head)
        if ck not in cache:
            resp = requests.get(f"{_API}/repos/{owner}/{repo}/compare/{base}...{head}",
                                headers=_rest_headers(token), timeout=30)
            cache[ck] = resp.json() if resp.status_code == 200 else None
        cmp = cache[ck]
        if cmp and cmp.get("status") != "diverged":
            return cap_patches(cmp.get("files", []), [path]), "full"
    fk = ("files", head)
    if fk not in cache:
        resp = requests.get(f"{_API}/repos/{owner}/{repo}/pulls/{number}/files", params={"per_page": 100},
                            headers=_rest_headers(token), timeout=30)
        resp.raise_for_status()
        cache[fk] = resp.json()
    files = cache[fk]
    wanted = [path] + [f for f in mentioned_files(texts, [x["filename"] for x in files]) if f != path]
    return cap_patches([f for f in files if f.get("filename") in wanted], wanted), "partial"


ADD_REACTION = """
mutation($subject: ID!) { addReaction(input: {subjectId: $subject, content: THUMBS_UP}) { clientMutationId } }
"""
UNRESOLVE_THREAD = """
mutation($thread: ID!) { unresolveReviewThread(input: {threadId: $thread}) { thread { isResolved } } }
"""
REMOVE_REACTION = """
mutation($subject: ID!) { removeReaction(input: {subjectId: $subject, content: THUMBS_UP}) { clientMutationId } }
"""
RESOLVE_THREAD = """
mutation($thread: ID!) { resolveReviewThread(input: {threadId: $thread}) { thread { isResolved } } }
"""
REPLY_TO_THREAD = """
mutation($thread: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, body: $body}) { comment { url } }
}
"""


class ReplyActionError(RuntimeError):
    """An action couldn't be applied; the message is shown on the card."""


class ReplyActions:
    """Post to GitHub on my behalf for one reply item. Every method runs on a background thread."""

    def __init__(self, settings: Settings, github_client: GitHubClient, store: RepliesStore) -> None:
        self._settings = settings
        self._client = github_client
        self._store = store

    def run(self, action: str, thread_id: str, body: str = "", auto: bool = False) -> None:
        """Apply *action* and record the outcome.

        Actions: ``resolve`` | ``reply`` | ``reply_resolve`` | ``dismiss`` | ``undo``.
        *auto* marks a resolve made by Auto reply rather than a click.
        """
        before = self._store.get(thread_id) or {}
        try:
            if action == "dismiss":
                self._store.update_item(thread_id, status="dismissed", action="dismissed", action_error="")
            elif action == "undo":
                self._undo(thread_id)
            else:
                self._post(action, thread_id, body)
                if auto:
                    confidence = (before.get("verdict") or {}).get("confidence")
                    self._store.update_item(thread_id, auto=True, auto_confidence=confidence)
        except ReplyActionError as exc:
            self._store.update_item(thread_id, action_error=str(exc))
        except Exception as exc:
            logger.exception("Reply action %s failed for %s.", action, thread_id)
            self._store.update_item(thread_id, action_error=f"Failed: {exc}")
        item = self._store.get(thread_id)
        if item:
            verdict = before.get("verdict") or {}
            self._store.log_calibration({
                "thread": thread_id, "pr": item.get("pr"), "action": action, "auto": auto,
                "verdict": verdict.get("verdict"), "confidence": verdict.get("confidence"),
                "evidence": before.get("evidence"), "result": item.get("action_error") or "ok",
                "edited_reply": bool(body and verdict.get("suggested_reply")
                                     and body.strip() != verdict["suggested_reply"].strip()),
            })
            if action != "undo":
                self.mark_read_if_handled(item["pr"])
            if action in ("resolve", "reply_resolve") and not item.get("action_error"):
                check_rereview(self._store, item["pr"])
        self._store.save()

    def _undo(self, thread_id: str) -> None:
        """Reverse an Auto reply resolve: unresolve the thread and remove the 👍."""
        item = self._store.get(thread_id)
        if not item or not item.get("auto") or item.get("action") != "resolved":
            raise ReplyActionError("Only auto-resolved threads can be undone")
        self._client.graphql(UNRESOLVE_THREAD, {"thread": thread_id}, strict=True)
        self._client.graphql(REMOVE_REACTION, {"subject": item["last_comment_node_id"]}, strict=True)
        self._store.set_thread_resolved(item["pr"], thread_id, False)
        self._store.update_item(thread_id, status="verdict_ready", action="", auto=False,
                                auto_undone=True, action_error="")

    def _fresh_item(self, thread_id: str) -> dict:
        """Re-fetch the thread and refuse to act if it changed since the card was shown."""
        before = self._store.get(thread_id)
        if not before:
            raise ReplyActionError("Unknown reply")
        login = self._client.get_login()
        if not login:
            raise ReplyActionError("GitHub login unavailable — check the token in Settings")
        refresh_pr(self._client, self._store, before["pr"], login)
        now = self._store.get(thread_id)
        if not now or now.get("status") in CLOSED_STATUSES:
            raise ReplyActionError("Already resolved on GitHub — nothing to do")
        if now.get("last_comment_id") != before.get("last_comment_id"):
            raise ReplyActionError("A new reply arrived — read it before acting")
        return now


    def _post(self, action: str, thread_id: str, body: str) -> None:
        if action not in ("resolve", "reply", "reply_resolve"):
            raise ReplyActionError(f"Unknown action: {action}")
        if action != "resolve" and not body.strip():
            raise ReplyActionError("Reply is empty")
        item = self._fresh_item(thread_id)
        if action == "resolve":
            self._client.graphql(ADD_REACTION, {"subject": item["last_comment_node_id"]}, strict=True)
            self._client.graphql(RESOLVE_THREAD, {"thread": thread_id}, strict=True)
            self._store.set_thread_resolved(item["pr"], thread_id, True)
            self._store.update_item(thread_id, status="done", action="resolved", action_error="")
            return
        self._client.graphql(REPLY_TO_THREAD, {"thread": thread_id, "body": body.strip()}, strict=True)
        if action == "reply":
            self._store.update_item(thread_id, status="done", action="replied", action_error="")
            return
        import requests

        try:
            self._client.graphql(RESOLVE_THREAD, {"thread": thread_id}, strict=True)
        except (RuntimeError, requests.RequestException) as exc:
            self._store.update_item(thread_id, status="done", action="replied",
                                    action_error=f"Reply posted, but resolving failed: {exc}")
            return
        self._store.set_thread_resolved(item["pr"], thread_id, True)
        self._store.update_item(thread_id, status="done", action="replied_resolved", action_error="")

    def mark_read_if_handled(self, key: str) -> None:
        """Mark the PR's GitHub notification read once none of its items are left to triage."""
        if self._store.open_items(key):
            return
        notification_id = self._store.pr_state(key).get("notification_id")
        if not notification_id or not self._settings.github_token:
            return
        import requests

        try:
            resp = requests.patch(f"{_API}/notifications/threads/{notification_id}",
                                  headers=_rest_headers(self._settings.github_token), timeout=15)
            if resp.status_code not in (200, 205, 304, 404):
                logger.warning("Marking notification %s read returned %s.", notification_id, resp.status_code)
        except requests.RequestException:
            logger.warning("Could not mark notification %s read.", notification_id)


_QUESTION_ONLY = re.compile(r"^[\s?!.]*$")


def should_auto_resolve(item: dict, settings: Any) -> bool:
    """True only when Auto reply may 👍 + resolve this item without a click.

    All must hold: Auto reply on; verdict ``addressed`` or ``pushback_fair`` at or above
    the threshold; for ``addressed``, evidence isn't partial and the code changed since
    the comment; their latest reply isn't just punctuation; the verdict was made against
    the current head; and I haven't undone an auto-resolve on this comment.
    """
    if not getattr(settings, "auto_reply", False) or item.get("status") != "verdict_ready":
        return False
    verdict = item.get("verdict") or {}
    if verdict.get("verdict") not in ("addressed", "pushback_fair"):
        return False
    if float(verdict.get("confidence") or 0) < float(getattr(settings, "auto_reply_confidence", 0.85)):
        return False
    if verdict["verdict"] == "addressed" and (item.get("evidence") == "partial" or not item.get("code_changed")):
        return False
    latest = (item.get("theirs") or [""])[-1]
    if _QUESTION_ONLY.match(latest or ""):
        return False
    if not item.get("verdict_sha") or item.get("verdict_sha") != item.get("head_sha"):
        return False
    return not item.get("auto_undone")
