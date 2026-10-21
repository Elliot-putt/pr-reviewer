"""Tests for spotting when a PR is due a re-review, and the re-review prompt."""

from types import SimpleNamespace

from prreviewer.core.events import EventBus
from prreviewer.github.replies import (
    RepliesStore,
    ReplyActions,
    check_rereview,
    last_review_sha,
    refresh_pr,
    rereview_due,
    rereview_prompt,
)
from prreviewer.review.runner import ReviewRunner
from tests.test_reply_actions import KEY, FakeClient


def _review(login, state, sha, body=""):
    return {"author": {"login": login}, "state": state, "body": body, "commit": {"oid": sha}}


def test_last_review_ignores_inline_comment_and_reply_reviews():
    pr = {"reviews": {"nodes": [
        _review("me", "COMMENTED", "c1"), _review("me", "APPROVED", "c2", "Approving"),
        _review("tom", "APPROVED", "c3"), _review("me", "COMMENTED", "c4"),
    ]}}
    assert last_review_sha(pr, "me") == "c2"


def test_last_review_counts_a_comment_review_with_a_summary():
    pr = {"reviews": {"nodes": [_review("me", "APPROVED", "c1"), _review("me", "COMMENTED", "c5", "Round two")]}}
    assert last_review_sha(pr, "me") == "c5"


def test_last_review_falls_back_to_earliest_when_only_inline_comments():
    pr = {"reviews": {"nodes": [_review("me", "COMMENTED", "c1"), _review("me", "COMMENTED", "c2")]}}
    assert last_review_sha(pr, "me") == "c1"
    assert last_review_sha({"reviews": {"nodes": []}}, "me") == ""


def _state(**over):
    st = {"my_threads": ["A", "B"], "resolved": ["A", "B"], "head_sha": "new", "my_last_review_sha": "old",
          "rereviewed_sha": "", "state": "OPEN"}
    st.update(over)
    return st


def test_due_when_all_resolved_with_new_commits():
    assert rereview_due(_state()) is True


def test_not_due_cases():
    assert rereview_due(_state(resolved=["A"])) is False
    assert rereview_due(_state(head_sha="old")) is False
    assert rereview_due(_state(rereviewed_sha="new")) is False
    assert rereview_due(_state(state="MERGED")) is False
    assert rereview_due(_state(my_threads=[])) is False
    assert rereview_due(_state(my_last_review_sha="")) is False


def test_check_marks_ready_once_and_calls_back_once(tmp_path):
    store = RepliesStore(EventBus(), tmp_path / "r.json")
    fired = []
    store.on_rereview = fired.append
    store.update_pr(KEY, **_state())
    assert check_rereview(store, KEY) is True
    assert check_rereview(store, KEY) is False
    assert fired == [KEY]
    assert store.pr_state(KEY)["rereview"] == "ready"
    store.update_pr(KEY, resolved=["A"])
    check_rereview(store, KEY)
    assert store.pr_state(KEY)["rereview"] == ""


def test_resolving_the_last_thread_makes_the_pr_ready(tmp_path, monkeypatch):
    monkeypatch.setattr("requests.patch", lambda url, **kw: type("R", (), {"status_code": 205})())
    client = FakeClient()
    store = RepliesStore(EventBus(), tmp_path / "r.json")
    fired = []
    store.on_rereview = fired.append
    monkeypatch.setattr("prreviewer.github.replies.last_review_sha", lambda pr, login: "old")
    refresh_pr(client, store, KEY, "me")
    assert store.pr_state(KEY).get("rereview", "") == ""
    ReplyActions(SimpleNamespace(github_token="t"), client, store).run("resolve", "T")
    assert store.pr_state(KEY)["rereview"] == "ready"
    assert fired == [KEY]


def test_prompt_scopes_to_changes_since_last_review():
    p = rereview_prompt("/code-review", "https://github.com/o/r/pull/1", "abc123", True)
    assert p.splitlines()[0] == "/code-review https://github.com/o/r/pull/1"
    assert "git diff abc123..HEAD" in p and "approve the PR" in p and "resolved threads" in p


def test_prompt_falls_back_to_full_diff_after_rebase():
    p = rereview_prompt("/code-review", "https://github.com/o/r/pull/1", "abc123", False)
    assert "full PR diff" in p and "git diff abc123" not in p


def test_runner_keeps_extra_instructions_and_does_not_repeat_the_url(tmp_path):
    url = "https://github.com/o/r/pull/1"
    (tmp_path / ".claude" / "skills" / "code-review").mkdir(parents=True)
    runner = ReviewRunner.__new__(ReviewRunner)
    runner._settings = SimpleNamespace(code_root=str(tmp_path), skills_repo="nope")
    prompt = runner._build_prompt(rereview_prompt("/code-review", url, "abc", True), url, str(tmp_path))
    assert prompt.startswith(f"/code-review {url}\n\nThis is a re-review")
    assert prompt.count(url) == 1
    assert runner._build_prompt("/code-review", url, str(tmp_path)) == f"/code-review {url}"
