"""Tests for the Slack connection watchdog's decisions."""

from prreviewer.slack.listener import WAKE_GAP_SECONDS, SlackListener


def _fresh():
    return SlackListener(settings=None, event_bus=None, parser=None)


def test_healthy_connection_does_nothing():
    lst = _fresh()
    assert [lst.health_action(True, 30) for _ in range(5)] == ["", "", "", "", ""]


def test_one_blip_is_ignored_two_misses_reconnect():
    lst = _fresh()
    assert lst.health_action(False, 30) == ""
    assert lst.health_action(True, 30) == ""
    assert lst.health_action(False, 30) == ""
    assert lst.health_action(False, 30) == "reconnect"


def test_waking_from_sleep_refreshes_even_if_it_says_connected():
    lst = _fresh()
    assert lst.health_action(True, WAKE_GAP_SECONDS + 1) == "refresh"
    assert lst.health_action(True, 30) == ""


def test_message_text_handles_plain_broadcast_and_edits():
    from prreviewer.slack.listener import message_text

    assert message_text({"text": "see https://github.com/o/r/pull/1", "ts": "1.5"}) == ("see https://github.com/o/r/pull/1", 1.5)
    assert message_text({"subtype": "thread_broadcast", "text": "x", "ts": "2"}) == ("x", 2.0)
    assert message_text({"subtype": "message_changed", "message": {"text": "now with link", "ts": "3"}}) == ("now with link", 3.0)
    assert message_text({"subtype": "channel_join", "text": "joined"}) is None
    assert message_text({"subtype": "bot_message", "text": "x"}) is None
