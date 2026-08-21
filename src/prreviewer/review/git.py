"""Git repository operations via subprocess."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from prreviewer.config import Settings
from prreviewer.models import PullRequest

logger = logging.getLogger(__name__)


class GitRepo:
    """Wraps git commands for checking out PR branches into isolated worktrees."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def repo_path_for(self, pr: PullRequest) -> "Path | None":
        """Return the local clone path for this PR's repo (<code_root>/<repo>), if it exists."""
        root = self._settings.code_root
        if not root:
            logger.warning("code_root not configured; cannot resolve repo for %s.", pr.id)
            return None
        path = Path(root).expanduser() / pr.ref.repo
        if not path.exists():
            logger.warning("No local clone at %s for %s; skipping checkout.", path, pr.id)
            return None
        return path

    def checkout(self, pr: PullRequest) -> "Path | None":
        """Fetch origin and create an isolated git worktree for the PR branch.

        Returns the worktree path on success, None on failure.
        The caller is responsible for removing the worktree when done via remove_worktree().
        """
        repo = self.repo_path_for(pr)
        if not repo:
            return None

        try:
            subprocess.run(
                ["git", "fetch", "origin", pr.branch],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.error("Git fetch failed for %s: %s", pr.id, exc.stderr)
            return None

        # Use a path inside a temp dir — git worktree add creates the final dir itself,
        # so we give it a non-existent subdirectory to avoid the "already exists" error.
        tmp_parent = Path(tempfile.mkdtemp(prefix=f"pr-review-{pr.ref.repo}-{pr.ref.number}-"))
        worktree_dir = tmp_parent / "wt"
        env = {**os.environ, "PREK_ALLOW_NO_CONFIG": "1"}
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(worktree_dir), f"origin/{pr.branch}"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            logger.info("Worktree created at %s for %s (branch %s).", worktree_dir, pr.id, pr.branch)
            return worktree_dir
        except subprocess.CalledProcessError as exc:
            logger.error("Git worktree add failed for %s: %s", pr.id, exc.stderr)
            shutil.rmtree(tmp_parent, ignore_errors=True)
            return None

    def remove_worktree(self, pr: PullRequest, worktree_path: Path) -> None:
        """Remove a worktree created by checkout()."""
        repo = self.repo_path_for(pr)
        if not repo:
            return
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_path)],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info("Removed worktree %s.", worktree_path)
        except subprocess.CalledProcessError as exc:
            logger.warning("Failed to remove worktree %s: %s", worktree_path, exc.stderr)
        # Clean up the parent temp dir (worktree_path is <tmp_parent>/wt).
        shutil.rmtree(worktree_path.parent, ignore_errors=True)
