"""Tests for reply-item detection, store transitions and evidence helpers."""

from prreviewer.core.events import EventBus, ReplyUpdated
from prreviewer.github.replies import (
    RepliesStore,
    cap_patches,
    code_window,
    mentioned_files,
    parse_subject_url,
    reply_item_for,
)

A, B, C = "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"
PR = {"title": "x", "url": "u", "headRefOid": "sha", "author": {"login": "tom"}}


def _c(login, at, typ="User"):
    return {"id": f"n-{login}-{at}", "databaseId": hash((login, at)) % 10**6,
            "author": {"__typename": typ, "login": login}, "body": f"{login} says", "bodyHTML": "",
            "createdAt": at, "url": "u", "diffHunk": "@@", "originalCommit": {"oid": "base"}}


def _thread(comments, resolved=False):
    return {"id": "T", "isResolved": resolved, "isOutdated": False, "path": "a.php", "line": 3,
            "originalLine": 3, "comments": {"nodes": comments}}


def _item(thread):
    return reply_item_for(thread, PR, "o/r#1", "me")


def test_reply_after_mine_is_an_item():
    item = _item(_thread([_c("Me", A), _c("tom", B)]))
    assert item and item["last_author"] == "tom" and item["base_sha"] == "base"


def test_not_an_item_when_i_replied_last():
    assert _item(_thread([_c("me", A), _c("tom", B), _c("me", C)])) is None


def test_not_an_item_when_resolved():
    assert _item(_thread([_c("me", A), _c("tom", B)], resolved=True)) is None


def test_not_an_item_without_my_comment():
    assert _item(_thread([_c("sam", A), _c("tom", B)])) is None


def test_bots_are_ignored():
    assert _item(_thread([_c("me", A), _c("copilot", B, "Bot")])) is None
    assert _item(_thread([_c("me", A), _c("tom", B), _c("coderabbitai[bot]", C)])) is not None


def test_parse_subject_url():
    assert parse_subject_url("https://api.github.com/repos/o/r/pulls/12") == ("o", "r", 12)
    assert parse_subject_url("https://api.github.com/repos/o/r/issues/12") is None


def test_store_transitions_and_persistence(tmp_path):
    bus = EventBus()
    events = []
    bus.subscribe(ReplyUpdated, lambda e: events.append(e.thread_id))
    store = RepliesStore(bus, tmp_path / "r.json")
    item = _item(_thread([_c("me", A), _c("tom", B)]))
    store.upsert("T", item)
    store.upsert("T", item)
    store.close("T")
    store.upsert("T", item)
    assert store.get("T")["status"] == "done"
    store.upsert("T", {**item, "last_comment_id": 999})
    assert store.get("T")["status"] == "pending"
    assert events == ["T", "T", "T"]
    store.save()
    assert RepliesStore(bus, tmp_path / "r.json").get("T")["status"] == "pending"


def test_set_verdict_ignores_stale_comment(tmp_path):
    store = RepliesStore(EventBus(), tmp_path / "r.json")
    item = _item(_thread([_c("me", A), _c("tom", B)]))
    store.upsert("T", item)
    assert store.set_verdict("T", "old-id", status="verdict_ready") is False
    assert store.set_verdict("T", item["last_comment_id"], status="verdict_ready") is True
    assert store.get("T")["status"] == "verdict_ready"


def test_code_window_numbers_lines_and_clamps():
    text = "\n".join(f"line{i}" for i in range(1, 51))
    window = code_window(text, 3, context=2)
    assert window.splitlines()[0].endswith("| line1")
    assert window.splitlines()[-1].endswith("| line5")
    assert code_window(text, 999, context=1).splitlines()[-1].endswith("| line50")


def test_cap_patches_orders_thread_file_first_and_truncates():
    files = [{"filename": "b.php", "patch": "B" * 50}, {"filename": "a.php", "patch": "A" * 50}]
    out = cap_patches(files, ["a.php"], cap=80)
    assert out.startswith("--- a.php")
    assert "b.php" not in out
    assert "truncated" in out


def test_mentioned_files_matches_basenames():
    assert mentioned_files(["see ConnectionType.php"], ["app/Enums/ConnectionType.php", "x.php"]) == [
        "app/Enums/ConnectionType.php"]
