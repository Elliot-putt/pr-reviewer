"""Tests for posting actions against a fake GitHub client (nothing leaves the machine)."""

import pytest

from prreviewer.core.events import EventBus
from prreviewer.github import replies
from prreviewer.github.replies import (
    ADD_REACTION,
    REPLY_TO_THREAD,
    RESOLVE_THREAD,
    THREADS_QUERY,
    RepliesStore,
    ReplyActions,
    refresh_pr,
)

KEY = "o/r#1"


def _comment(cid, login, at):
    return {"id": f"node-{cid}", "databaseId": cid, "author": {"__typename": "User", "login": login},
            "body": f"{login} {cid}", "bodyHTML": "", "createdAt": at, "url": f"u{cid}",
            "diffHunk": "@@", "originalCommit": {"oid": "base"}}


class FakeClient:
    def __init__(self):
        self.comments = [_comment(1, "me", "2026-01-01T00:00:00Z"), _comment(2, "tom", "2026-01-02T00:00:00Z")]
        self.resolved = False
        self.calls = []
        self.fail = {}

    def get_login(self):
        return "me"

    def graphql(self, query, variables, strict=False):
        if query == THREADS_QUERY:
            thread = {"id": "T", "isResolved": self.resolved, "isOutdated": False, "path": "a.php",
                      "line": 3, "originalLine": 3, "comments": {"nodes": list(self.comments)}}
            return {"repository": {"pullRequest": {"title": "x", "url": "u", "headRefOid": "head",
                                                   "author": {"login": "tom"},
                                                   "reviewThreads": {"nodes": [thread]}}}}
        self.calls.append((query, variables))
        if query in self.fail:
            raise RuntimeError(self.fail[query])
        if query == RESOLVE_THREAD:
            self.resolved = True
        return {}


class FakeSettings:
    github_token = "t"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    patched = []
    monkeypatch.setattr("requests.patch", lambda url, **kw: patched.append(url) or type("R", (), {"status_code": 205})())
    client = FakeClient()
    store = RepliesStore(EventBus(), tmp_path / "r.json")
    refresh_pr(client, store, KEY, "me", notification_id="N1")
    return client, store, ReplyActions(FakeSettings(), client, store), patched


def test_resolve_reacts_then_resolves_and_marks_notification_read(setup):
    client, store, actions, patched = setup
    actions.run("resolve", "T")
    assert [q for q, _ in client.calls] == [ADD_REACTION, RESOLVE_THREAD]
    assert client.calls[0][1] == {"subject": "node-2"}
    item = store.get("T")
    assert (item["status"], item["action"], item["action_error"]) == ("done", "resolved", "")
    assert patched == [f"{replies._API}/notifications/threads/N1"]


def test_reply_and_resolve_posts_trimmed_body(setup):
    client, store, actions, _ = setup
    actions.run("reply_resolve", "T", "  Fair enough 👍  ")
    assert [q for q, _ in client.calls] == [REPLY_TO_THREAD, RESOLVE_THREAD]
    assert client.calls[0][1] == {"thread": "T", "body": "Fair enough 👍"}
    assert store.get("T")["action"] == "replied_resolved"


def test_reply_only_does_not_resolve(setup):
    client, store, actions, _ = setup
    actions.run("reply", "T", "Can you check line 3?")
    assert [q for q, _ in client.calls] == [REPLY_TO_THREAD]
    assert store.get("T")["action"] == "replied"


def test_aborts_when_a_new_reply_arrived(setup):
    client, store, actions, patched = setup
    client.comments.append(_comment(3, "tom", "2026-01-03T00:00:00Z"))
    before = store.get("T")
    store_item = dict(before)
    actions.run("resolve", "T")
    assert client.calls == []
    item = store.get("T")
    assert item["status"] == "pending"
    assert item["last_comment_id"] == 3
    assert store_item["last_comment_id"] == 2
    assert patched == []


def test_aborts_when_already_resolved_on_github(setup):
    client, store, actions, _ = setup
    client.resolved = True
    actions.run("resolve", "T")
    assert client.calls == []
    assert store.get("T")["status"] == "done"
    assert "Already resolved" in store.get("T")["action_error"]


def test_github_error_is_shown_and_status_unchanged(setup):
    client, store, actions, patched = setup
    client.fail[ADD_REACTION] = "GitHub: Resource not accessible by personal access token"
    actions.run("resolve", "T")
    item = store.get("T")
    assert item["status"] == "pending"
    assert "Resource not accessible" in item["action_error"]
    assert patched == []


def test_reply_posted_but_resolve_failed(setup):
    client, store, actions, _ = setup
    client.fail[RESOLVE_THREAD] = "GitHub: boom"
    actions.run("reply_resolve", "T", "done")
    item = store.get("T")
    assert (item["status"], item["action"]) == ("done", "replied")
    assert "Reply posted, but resolving failed" in item["action_error"]


def test_empty_reply_is_rejected_without_calling_github(setup):
    client, store, actions, _ = setup
    actions.run("reply", "T", "   ")
    assert client.calls == []
    assert store.get("T")["action_error"] == "Reply is empty"


def test_dismiss_is_local_only(setup):
    client, store, actions, patched = setup
    actions.run("dismiss", "T")
    assert client.calls == []
    assert store.get("T")["status"] == "dismissed"
    assert patched == [f"{replies._API}/notifications/threads/N1"]
