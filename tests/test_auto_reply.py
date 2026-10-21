"""Tests for the Auto reply gate and the end-to-end auto 👍 + resolve flow (fake GitHub, fake Claude)."""

import json

import pytest

import prreviewer.review.verdict as verdict_mod
from prreviewer.core.events import EventBus
from prreviewer.github.replies import (
    ADD_REACTION,
    REMOVE_REACTION,
    RESOLVE_THREAD,
    UNRESOLVE_THREAD,
    RepliesStore,
    ReplyActions,
    refresh_pr,
    should_auto_resolve,
)
from prreviewer.review.verdict import VerdictWorker
from tests.test_reply_actions import KEY, FakeClient


class S:
    def __init__(self, **kw):
        self.auto_reply = True
        self.auto_reply_confidence = 0.85
        self.github_token = "t"
        self.replies_enabled = True
        self.replies_model = "sonnet"
        self.replies_max_concurrent = 1
        self.claude_bin = "claude"
        self.__dict__.update(kw)


def _ready(**over):
    item = {"status": "verdict_ready", "verdict": {"verdict": "addressed", "confidence": 0.92},
            "evidence": "checked", "code_changed": True, "theirs": ["Fixed."],
            "verdict_sha": "head", "head_sha": "head"}
    item.update(over)
    return item


def test_gate_passes_for_confident_addressed_with_code_change():
    assert should_auto_resolve(_ready(), S()) is True


@pytest.mark.parametrize("over,settings", [
    ({}, {"auto_reply": False}),
    ({"verdict": {"verdict": "addressed", "confidence": 0.84}}, {}),
    ({"verdict": {"verdict": "partial", "confidence": 0.99}}, {}),
    ({"verdict": {"verdict": "pushback_hold", "confidence": 0.99}}, {}),
    ({"evidence": "partial"}, {}),
    ({"code_changed": False}, {}),
    ({"theirs": ["??"]}, {}),
    ({"verdict_sha": "old"}, {}),
    ({"auto_undone": True}, {}),
    ({"status": "pending"}, {}),
    ({"verdict": None}, {}),
])
def test_gate_blocks(over, settings):
    assert should_auto_resolve(_ready(**over), S(**settings)) is False


def test_pushback_fair_needs_no_code_change_but_not_partial_rule():
    item = _ready(verdict={"verdict": "pushback_fair", "confidence": 0.9}, code_changed=False, evidence="partial")
    assert should_auto_resolve(item, S()) is True


def test_threshold_is_configurable():
    assert should_auto_resolve(_ready(verdict={"verdict": "addressed", "confidence": 0.7}),
                               S(auto_reply_confidence=0.7)) is True


def _fake_claude(verdict, confidence):
    def run(bin_, model, prompt, schema, checkout=None):
        return json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": {
            "verdicts": [{"thread": "T1", "verdict": verdict, "confidence": confidence,
                          "reason": "checked", "suggested_reply": ""}]}})
    return run


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr("requests.patch", lambda url, **kw: type("R", (), {"status_code": 205})())
    monkeypatch.setattr(verdict_mod, "fetch_changes", lambda *a, **k: ("--- a.php\n+fix", "full"))
    monkeypatch.setattr(verdict_mod, "fetch_code_now", lambda *a, **k: "3 | fixed")
    monkeypatch.setattr(verdict_mod, "load_taste", lambda: "")
    client = FakeClient()
    store = RepliesStore(EventBus(), tmp_path / "r.json")
    refresh_pr(client, store, KEY, "me", notification_id="N1")
    return client, store


def _worker(client, store, settings):
    return VerdictWorker(settings, client, store, git_repo=None, actions=ReplyActions(settings, client, store))


def test_worker_auto_resolves_confident_addressed(world, monkeypatch):
    client, store = world
    monkeypatch.setattr(verdict_mod, "run_claude", _fake_claude("addressed", 0.92))
    _worker(client, store, S())._process(KEY, set())
    item = store.get("T")
    assert [q for q, _ in client.calls] == [ADD_REACTION, RESOLVE_THREAD]
    assert (item["status"], item["action"], item["auto"], item["auto_confidence"]) == ("done", "resolved", True, 0.92)
    assert store.pr_state(KEY)["resolved"] == ["T"]
    log = (store._path.parent / "replies-calibration.jsonl").read_text().splitlines()
    assert json.loads(log[0])["event"] == "verdict" and json.loads(log[1])["auto"] is True


def test_worker_leaves_partial_for_me(world, monkeypatch):
    client, store = world
    monkeypatch.setattr(verdict_mod, "run_claude", _fake_claude("partial", 0.99))
    _worker(client, store, S())._process(KEY, set())
    assert client.calls == []
    assert store.get("T")["status"] == "verdict_ready"


def test_worker_never_posts_with_auto_reply_off(world, monkeypatch):
    client, store = world
    monkeypatch.setattr(verdict_mod, "run_claude", _fake_claude("addressed", 0.99))
    _worker(client, store, S(auto_reply=False))._process(KEY, set())
    assert client.calls == []
    assert store.get("T")["status"] == "verdict_ready"


def test_undo_reverses_and_is_not_redone(world, monkeypatch):
    client, store = world
    settings = S()
    monkeypatch.setattr(verdict_mod, "run_claude", _fake_claude("addressed", 0.92))
    worker = _worker(client, store, settings)
    worker._process(KEY, set())
    client.calls.clear()
    ReplyActions(settings, client, store).run("undo", "T")
    assert [q for q, _ in client.calls] == [UNRESOLVE_THREAD, REMOVE_REACTION]
    client.resolved = False
    item = store.get("T")
    assert (item["status"], item["auto"], item["auto_undone"]) == ("verdict_ready", False, True)
    assert store.pr_state(KEY)["resolved"] == []
    client.calls.clear()
    worker._process(KEY, set())
    assert client.calls == []


def test_undo_refuses_manual_resolves(world):
    client, store = world
    actions = ReplyActions(S(), client, store)
    actions.run("resolve", "T")
    client.calls.clear()
    actions.run("undo", "T")
    assert client.calls == []
    assert "Only auto-resolved" in store.get("T")["action_error"]


def test_stale_verdict_is_rejudged_before_acting(world, monkeypatch):
    client, store = world
    monkeypatch.setattr(verdict_mod, "run_claude", _fake_claude("partial", 0.5))
    worker = _worker(client, store, S())
    worker._process(KEY, set())
    store.update_item("T", verdict_sha="older-head")
    monkeypatch.setattr(verdict_mod, "run_claude", _fake_claude("addressed", 0.95))
    worker._process(KEY, set())
    item = store.get("T")
    assert item["verdict"]["verdict"] == "addressed" and item["verdict_sha"] == "head"
    assert item["auto"] is True


class FakeGit:
    def __init__(self, tmp_path, changed=True):
        self.path, self.changed, self.checkouts = tmp_path, changed, 0

    def verdict_checkout(self, repo, number, branch, head, bases):
        self.checkouts += 1
        return self.path

    def has_changes_since(self, checkout, base):
        return self.changed

    def remove_verdict_checkout(self, repo, number):
        pass


@pytest.mark.parametrize("changed,resolved", [(True, True), (False, False)])
def test_old_verdict_without_code_changed_is_backfilled_then_acted_on(world, tmp_path, monkeypatch, changed, resolved):
    client, store = world
    store.update_item("T", status="verdict_ready", verdict={"verdict": "addressed", "confidence": 0.97, "reason": "r",
                                                            "suggested_reply": ""},
                      verdict_sha="head", evidence="checked")
    assert store.get("T").get("code_changed") is None
    monkeypatch.setattr(verdict_mod, "run_claude", lambda *a, **k: pytest.fail("should not re-run Claude"))
    settings = S()
    git = FakeGit(tmp_path, changed)
    VerdictWorker(settings, client, store, git_repo=git, actions=ReplyActions(settings, client, store))._process(KEY, set())
    item = store.get("T")
    assert item["code_changed"] is changed
    assert (item["status"] == "done") is resolved
    assert git.checkouts == 1


def test_partial_evidence_verdict_is_rejudged_with_a_checkout(world, tmp_path, monkeypatch):
    client, store = world
    store.update_item("T", status="verdict_ready", verdict={"verdict": "addressed", "confidence": 0.9, "reason": "r",
                                                            "suggested_reply": ""},
                      verdict_sha="head", evidence="partial")
    calls = []

    def fake(bin_, model, prompt, schema, checkout=None):
        calls.append(checkout)
        return _fake_claude("addressed", 0.9)(bin_, model, prompt, schema, checkout)

    monkeypatch.setattr(verdict_mod, "run_claude", fake)
    monkeypatch.setattr(verdict_mod, "_local_code", lambda *a: "3 | fixed")
    settings = S()
    VerdictWorker(settings, client, store, git_repo=FakeGit(tmp_path), actions=ReplyActions(settings, client, store))._process(KEY, set())
    item = store.get("T")
    assert calls == [tmp_path]
    assert (item["evidence"], item["status"], item["auto"]) == ("checked", "done", True)


def test_partial_evidence_left_alone_without_a_clone(world, monkeypatch):
    client, store = world
    store.update_item("T", status="verdict_ready", verdict={"verdict": "addressed", "confidence": 0.9, "reason": "r",
                                                            "suggested_reply": ""},
                      verdict_sha="head", evidence="partial")
    monkeypatch.setattr(verdict_mod, "run_claude", lambda *a, **k: pytest.fail("should not re-run Claude"))
    _worker(client, store, S())._process(KEY, set())
    assert store.get("T")["status"] == "verdict_ready"


def test_item_is_marked_auto_resolving_while_posting(world, monkeypatch):
    client, store = world
    monkeypatch.setattr(verdict_mod, "run_claude", _fake_claude("addressed", 0.92))
    seen = []
    settings = S()
    actions = ReplyActions(settings, client, store)
    real_run = actions.run

    def spy(action, tid, body="", auto=False):
        seen.append(store.get(tid)["auto_resolving"])
        real_run(action, tid, body, auto)

    actions.run = spy
    VerdictWorker(settings, client, store, git_repo=None, actions=actions)._process(KEY, set())
    assert seen == [True]
    assert store.get("T")["auto_resolving"] is False
    assert store.get("T")["status"] == "done"
