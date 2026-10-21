"""Entry point: wire up all components and launch the window."""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

_handlers: list = [logging.StreamHandler(sys.stderr)]
if getattr(sys, "frozen", False):
    # Finder swallows stderr — keep a real log file for the bundled app.
    from logging.handlers import RotatingFileHandler
    _log_dir = Path.home() / "Library" / "Logs" / "PR Reviewer"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handlers.append(RotatingFileHandler(_log_dir / "app.log", maxBytes=2_000_000, backupCount=2))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger(__name__)

_src = Path(__file__).parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


def main() -> None:
    """Bootstrap and launch the PR Reviewer app."""
    import time as _time
    _startup_time = _time.time()

    from prreviewer.paths import asset_dir as _asset_dir, fix_path_env
    fix_path_env()  # Finder-launched apps lack /opt/homebrew/bin on PATH

    from prreviewer.config import Settings
    from prreviewer.core.events import EventBus, PRDetected, PRUpdated, ReplyUpdated
    from prreviewer.core.store import PRStore
    from prreviewer.models import PRStatus
    from prreviewer.github.client import GitHubClient
    from prreviewer.github.pull_requests import PullRequestService
    from prreviewer.github.reviews import ReviewPublisher
    from prreviewer.review.git import GitRepo
    from prreviewer.review.runner import ReviewRunner
    from prreviewer.slack.listener import SlackListener
    from prreviewer.slack.parser import PRLinkParser
    from prreviewer.github.poller import PRPoller
    from prreviewer.github.replies import RepliesPoller, RepliesStore, ReplyActions
    from prreviewer.review.verdict import VerdictWorker
    from prreviewer.ui.server import UiServer
    from prreviewer.ui.window import AppWindow

    settings = Settings()
    event_bus = EventBus()
    store = PRStore(event_bus)
    replies_store = RepliesStore(event_bus)
    parser = PRLinkParser()
    github_client = GitHubClient(settings)
    pr_service = PullRequestService(github_client)
    review_publisher = ReviewPublisher(github_client)
    git_repo = GitRepo(settings, login_fn=github_client.get_login)

    ui_server = UiServer(port=settings.ui_port, asset_dir=_asset_dir())
    ui_server.run_in_thread()

    for _ in range(20):
        try:
            loop = ui_server.loop
            break
        except RuntimeError:
            time.sleep(0.05)
    else:
        logger.warning("UI server loop not ready.")
        loop = None

    runner = ReviewRunner(
        settings=settings,
        store=store,
        event_bus=event_bus,
        git_repo=git_repo,
        loop=loop,
    )
    ui_server.set_runner(runner)

    window = AppWindow(
        settings=settings,
        store=store,
        ui_server=ui_server,
        pr_service=pr_service,
        review_publisher=review_publisher,
        git_repo=git_repo,
        runner=runner,
        github_client=github_client,
        replies_store=replies_store,
    )

    # Notify the UI whenever a claude session actually spawns (including
    # queued auto-reviews starting later, and manual/resumed sessions).
    runner.on_session_started = lambda pr_id: window.push_to_js("real-session-started", {"prId": pr_id})

    # Wire store events → JS
    def _on_pr_updated(e: PRUpdated) -> None:
        # Push the full PR dict when available so fields like sessionDeadline
        # reach the frontend, not just the status.
        pr = store.get(e.pr_id)
        window.push_to_js("pr-updated", pr.to_dict() if pr else e.to_dict())

    def _on_pr_detected(e: PRDetected) -> None:
        if not window.listening:
            logger.info("Listening paused — ignoring PR %s.", e.ref)
            return
        existing = store.get(f"pr-{e.ref.repo}-{e.ref.number}")
        pr = pr_service.fetch(e.ref, detected_at=e.slack_ts)
        if pr is None:
            return
        if existing is not None:
            if pr.status in (PRStatus.MERGED, PRStatus.CLOSED) and existing.status != pr.status:
                store.update_status(existing.id, pr.status)
            return
        store.add(pr)
        window.push_to_js("pr-detected", pr.to_dict())

        is_live = e.slack_ts > _startup_time and (not e.from_backfill or e.catch_up)
        my_login = github_client.get_login()
        is_others_pr = not my_login or pr.author != my_login

        # Mac notification for live PRs that aren't yours
        if is_live and is_others_pr and settings.native_notifications:
            from prreviewer.github.poller import _notify
            _notify(
                title="New PR waiting for review",
                subtitle=f"#{pr.number} · {pr.author}",
                message=pr.title,
                pr_number=pr.number,
                ui_port=settings.ui_port,
            )

        # Auto-review: start Claude Code immediately in the background.
        # Never auto-review on backfill/refresh — only on live Slack socket events.
        if is_live and is_others_pr and settings.auto_review:
            try:
                # The runner's on_session_started callback notifies the UI when a
                # session actually spawns — including queued ones starting later.
                runner.start_review(pr, auto=True)
            except Exception:
                logger.exception("Auto-review failed for %s.", pr.id)

    event_bus.subscribe(PRUpdated, _on_pr_updated)
    event_bus.subscribe(PRDetected, _on_pr_detected)

    def _on_reply_updated(e: ReplyUpdated) -> None:
        item = replies_store.item_js(e.thread_id) if e.thread_id else None
        window.push_to_js("reply-updated", {"item": item, "error": replies_store.error,
                                            "claudeError": replies_store.claude_error})

    event_bus.subscribe(ReplyUpdated, _on_reply_updated)

    # Start Slack if credentials present — track listener on window so sidebar
    # shows Connected and connect_slack() from JS stays idempotent.
    listener: SlackListener | None = None
    if settings.slack_app_token and settings.slack_bot_token:
        listener = SlackListener(settings=settings, event_bus=event_bus, parser=parser)
        listener.start()
        window._slack_listener = listener  # so get_settings returns slackConnected=True
    else:
        logger.info("Slack tokens not configured; listener skipped.")

    # Backfill in background AFTER window opens — don't block startup
    def _backfill_after_ready() -> None:
        # Give the window a moment to load and call get_prs()
        time.sleep(3)
        if listener:
            logger.info("Starting backfill of last 25 Slack messages…")
            window.push_to_js("backfill-start", {})
            error = listener.backfill(limit=25)
            window.push_to_js("backfill-done", {"error": error})

    threading.Thread(target=_backfill_after_ready, daemon=True, name="backfill").start()

    # Update check: compare our version against the latest GitHub release and
    # prompt in the UI when a newer one exists. Re-checked every 6 hours.
    def _update_check_loop() -> None:
        from prreviewer.paths import is_frozen
        from prreviewer.version import check_for_update
        time.sleep(5)  # let the window load first
        while True:
            info = check_for_update()
            if info:
                logger.info("Update available: v%s", info["latest"])
                # One-click self-update only works for the bundled .app
                info["canSelfUpdate"] = bool(is_frozen() and info.get("zipUrl"))
                window.push_to_js("update-available", info)
            time.sleep(6 * 3600)

    threading.Thread(target=_update_check_loop, daemon=True, name="update-check").start()

    # Start the PR poller (notifies on new review comments for your own PRs).
    # Always started — it resolves the GitHub login lazily each cycle, so adding
    # or changing the PAT in settings takes effect without a restart.
    poller = PRPoller(
        github_client=github_client,
        store=store,
        ui_port=settings.ui_port,
        push_to_js=window.push_to_js,
        settings=settings,
    )
    poller.start()

    reply_actions = ReplyActions(settings=settings, github_client=github_client, store=replies_store)

    def _on_rereview_due(pr_key: str) -> None:
        if settings.auto_rereview:
            threading.Thread(target=window.run_rereview, args=(pr_key, True), daemon=True,
                             name="auto-rereview").start()

    replies_store.on_rereview = _on_rereview_due
    verdict_worker = VerdictWorker(settings=settings, github_client=github_client, store=replies_store,
                                   git_repo=git_repo, actions=reply_actions)
    window._verdict_worker = verdict_worker
    window._reply_actions = reply_actions
    verdict_worker.start()
    verdict_worker.enqueue_open()

    def _notify_replies(count: int) -> None:
        if not settings.native_notifications:
            return
        from prreviewer.github.poller import _notify
        noun = "reply" if count == 1 else "replies"
        _notify(title="Replies", subtitle="", message=f"{count} new {noun} to your review comments",
                pr_number=0, ui_port=settings.ui_port, query="view=replies")

    RepliesPoller(settings=settings, github_client=github_client, store=replies_store,
                  on_checked=verdict_worker.enqueue, notify=_notify_replies).start()

    logger.info("Launching window on http://127.0.0.1:%d/", settings.ui_port)
    window.start()


if __name__ == "__main__":
    main()
