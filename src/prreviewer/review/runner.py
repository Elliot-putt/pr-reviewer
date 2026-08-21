"""Orchestrates checkout → PTY session → review for a single PR."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

from prreviewer.config import Settings
from prreviewer.core.events import EventBus
from prreviewer.core.store import PRStore
from prreviewer.models import PRStatus, PullRequest
from prreviewer.review.git import GitRepo
from prreviewer.review.terminal import PtySession

if TYPE_CHECKING:
    from prreviewer.review.bridge import TerminalBridge

logger = logging.getLogger(__name__)


class ReviewRunner:
    """Runs the full review pipeline for a single pull request."""

    def __init__(
        self,
        settings: Settings,
        store: PRStore,
        event_bus: EventBus,
        git_repo: GitRepo,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._settings = settings
        self._store = store
        self._bus = event_bus
        self._git = git_repo
        self._loop = loop
        self._sessions: dict[str, PtySession] = {}
        self._bridges: dict[str, "TerminalBridge"] = {}

    def start_review(self, pr: PullRequest, command: str | None = None, auto: bool = False) -> "TerminalBridge":
        """Start the review pipeline for *pr* in a background thread.

        Returns a TerminalBridge so the websocket server can attach clients.
        *auto* skips waiting for xterm resize — used for background auto-reviews.
        """
        from prreviewer.review.bridge import TerminalBridge

        # Kill any previous session for this PR so re-reviews don't leak processes.
        self.stop(pr.id)

        repo_path = self._git.repo_path_for(pr)
        cwd = str(repo_path) if repo_path else "/tmp"
        pty = PtySession(claude_bin=self._settings.claude_bin, cwd=cwd, model=self._settings.claude_model)
        bridge = TerminalBridge(pty=pty, loop=self._loop)
        pty.add_reader(bridge.on_pty_data)

        self._sessions[pr.id] = pty
        self._bridges[pr.id] = bridge

        thread = threading.Thread(
            target=self._run,
            args=(pr, pty, command, auto),
            daemon=True,
            name=f"review-{pr.id}",
        )
        thread.start()
        return bridge

    def _notify_ready(self, pr: PullRequest) -> None:
        try:
            from prreviewer.github.poller import _notify
            _notify(
                title="Review ready",
                subtitle=f"#{pr.number} · {pr.repo}",
                message=pr.title,
                pr_number=pr.number,
                ui_port=self._settings.ui_port,
            )
        except Exception:
            logger.debug("Could not send review-ready notification.")

    def get_bridge(self, pr_id: str) -> "TerminalBridge | None":
        """Return the TerminalBridge for *pr_id*, if a review is in progress."""
        return self._bridges.get(pr_id)

    def stop(self, pr_id: str) -> None:
        """Terminate the claude session for *pr_id*, if one is running.

        Interactive claude sessions never exit on their own, so this must be
        called when the review is finished with (approved / marked reviewed /
        re-reviewed) or the processes pile up and eat memory.
        """
        pty = self._sessions.get(pr_id)
        if pty:
            try:
                pty.terminate()
                logger.info("Terminated review session for %s.", pr_id)
            except Exception:
                logger.exception("Failed to terminate session for %s.", pr_id)

    def _build_prompt(self, cmd: str, pr_url: str, cwd: str) -> str:
        """Build the initial prompt for Claude Code.

        Slash commands only resolve from skills inside the repo Claude runs in
        (or ~/.claude). If the target repo has the skill, use the slash command
        directly; otherwise point Claude at the SKILL.md in the skills repo.
        """
        from pathlib import Path

        cmd = cmd.strip()
        skill_name = cmd.lstrip("/").split()[0] if cmd.startswith("/") else ""
        if not skill_name:
            return f"{cmd} {pr_url}"

        local_skill = Path(cwd) / ".claude" / "skills" / skill_name
        if local_skill.exists():
            return f"{cmd} {pr_url}"

        skill_md = (
            Path(self._settings.code_root).expanduser()
            / self._settings.skills_repo / ".claude" / "skills" / skill_name / "SKILL.md"
        )
        if skill_md.exists():
            logger.info("Skill %s not in target repo; referencing %s", skill_name, skill_md)
            return (
                f"Read the skill file at {skill_md} and follow its instructions exactly, "
                f"as if I had run /{skill_name} {pr_url} — the pull request to work on is {pr_url}. "
                f"Work in the current directory."
            )

        logger.warning("Skill %s not found locally or in %s; sending slash command as-is.", skill_name, self._settings.skills_repo)
        return f"{cmd} {pr_url}"

    def _idle_watchdog(self, pr: PullRequest, pty: PtySession) -> None:
        """Kill the session after session_idle_minutes with no terminal activity.

        Any PTY output or keystroke resets the timer. The current deadline is
        published to the UI via the store so the frontend can show a countdown.
        """
        import time

        timeout = self._settings.session_idle_minutes * 60
        while pty.is_alive():
            deadline = pty.last_activity + timeout
            self._store.set_session_deadline(pr.id, deadline)
            remaining = deadline - time.time()
            if remaining <= 0:
                logger.info("Idle timeout for %s — terminating review session.", pr.id)
                pty.terminate()
                break
            time.sleep(min(remaining, 15))
        self._store.set_session_deadline(pr.id, 0)

    def _run(self, pr: PullRequest, pty: PtySession, command: str | None = None, auto: bool = False) -> None:
        """Background thread: worktree checkout → spawn claude → cleanup."""
        self._store.update_status(pr.id, PRStatus.CHECKING_OUT)

        worktree_path = self._git.checkout(pr)
        if worktree_path:
            cwd = str(worktree_path)
        else:
            repo_path = self._git.repo_path_for(pr)
            cwd = str(repo_path) if repo_path else "/tmp"
            logger.warning("Worktree checkout failed for %s; running in %s.", pr.id, cwd)

        # Update the PTY cwd now that we know the worktree path.
        pty._cwd = cwd

        self._store.update_status(pr.id, PRStatus.REVIEWING)

        bridge = self._bridges.get(pr.id)
        if auto:
            rows, cols = 40, 220
        elif bridge:
            rows, cols = bridge.wait_for_resize(timeout=8.0)
            logger.info("Got xterm dimensions for %s: %dx%d", pr.id, cols, rows)
        else:
            rows, cols = 40, 220

        pr_url = f"https://github.com/{pr.ref.owner}/{pr.ref.repo}/pull/{pr.ref.number}"
        cmd = command or self._settings.review_command
        initial_prompt = self._build_prompt(cmd, pr_url, cwd) if cmd else ""

        pty.start(dimensions=(rows, cols), initial_prompt=initial_prompt)
        logger.info("Review PTY started for %s (auto=%s, cwd=%s, prompt=%r).", pr.id, auto, cwd, initial_prompt)

        if self._settings.session_idle_minutes > 0:
            threading.Thread(
                target=self._idle_watchdog,
                args=(pr, pty),
                daemon=True,
                name=f"idle-watchdog-{pr.id}",
            ).start()

        pty.wait()
        logger.info("Review PTY exited for %s.", pr.id)
        # Only advance to READY if the PR is still mid-review — a session killed
        # after approve/mark-reviewed must not clobber POSTED.
        current = self._store.get(pr.id)
        if current and current.status in (PRStatus.CHECKING_OUT, PRStatus.REVIEWING):
            self._store.update_status(pr.id, PRStatus.READY)
            if auto and self._settings.native_notifications:
                self._notify_ready(pr)

        if worktree_path:
            self._git.remove_worktree(pr, worktree_path)
