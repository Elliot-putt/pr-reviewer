"""Git repository operations via subprocess."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from prreviewer.config import Settings
from prreviewer.models import PullRequest

logger = logging.getLogger(__name__)


class GitRepo:
    """Wraps git commands for checking out PR branches into isolated worktrees."""

    def __init__(self, settings: Settings, login_fn: Callable[[], str] | None = None) -> None:
        self._settings = settings
        self._login_fn = login_fn

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

        # Deterministic path per PR so a resumed session lands in the same cwd —
        # claude keys conversation history to the working directory, which is
        # what makes `claude --continue` pick the old review back up.
        tmp_parent = Path(tempfile.gettempdir()) / "pr-reviewer-worktrees" / f"{pr.ref.repo}-{pr.ref.number}"
        worktree_dir = tmp_parent / "wt"
        if worktree_dir.exists():
            logger.info("Reusing existing worktree at %s for %s.", worktree_dir, pr.id)
            self._update_reused(pr, worktree_dir)
            return worktree_dir
        # A stale registration without the directory blocks `worktree add` — prune first.
        subprocess.run(["git", "worktree", "prune"], cwd=repo, check=False, capture_output=True)
        tmp_parent.mkdir(parents=True, exist_ok=True)
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

    def _update_reused(self, pr: PullRequest, worktree_dir: Path) -> None:
        """Move a reused worktree to the PR's latest commit so reviews never see stale code.

        Skipped for my own PRs (Address Comments edits that worktree) and whenever the
        worktree has local changes; never forces over uncommitted work.
        """
        login = self._login_fn() if self._login_fn else ""
        if login and pr.author.lower() == login.lower():
            return
        status = subprocess.run(["git", "status", "--porcelain"], cwd=worktree_dir,
                                capture_output=True, text=True, check=False)
        if status.returncode != 0 or status.stdout.strip():
            logger.warning("Worktree for %s has local changes — not updated to the latest commit.", pr.id)
            return
        moved = subprocess.run(["git", "checkout", "--detach", f"origin/{pr.branch}"], cwd=worktree_dir,
                               capture_output=True, text=True, check=False)
        if moved.returncode != 0:
            logger.warning("Could not move worktree for %s to origin/%s: %s", pr.id, pr.branch, moved.stderr.strip())
        else:
            logger.info("Moved worktree for %s to the latest origin/%s.", pr.id, pr.branch)

    def verdict_checkout(self, repo: str, number: int, branch: str, head_sha: str,
                         extra_shas: list[str] | None = None) -> Path | None:
        """Detached checkout of *head_sha* for read-only verdict runs, separate from review worktrees.

        Also fetches *extra_shas* (the commits my comments were made on) so
        ``git diff <base> HEAD`` works after force-pushes. Returns None when there
        is no local clone or the head can't be checked out.
        """
        root = self._settings.code_root
        clone = Path(root).expanduser() / repo if root else None
        if not clone or not (clone / ".git").exists():
            logger.info("No local clone for %s; verdict runs without a checkout.", repo)
            return None
        env = {**os.environ, "PREK_ALLOW_NO_CONFIG": "1"}

        def git(*args: str, cwd: Path = clone, check: bool = False) -> subprocess.CompletedProcess:
            return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, check=check)

        git("fetch", "origin", branch)
        for sha in [head_sha, *(extra_shas or [])]:
            if sha and git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
                git("fetch", "origin", sha)
        if git("cat-file", "-e", f"{head_sha}^{{commit}}").returncode != 0:
            logger.warning("Head %s of %s#%s not available locally.", head_sha[:10], repo, number)
            return None
        path = Path(tempfile.gettempdir()) / "pr-reviewer-verdicts" / f"{repo}-{number}"
        try:
            if (path / ".git").exists():
                git("checkout", "--detach", "--force", head_sha, cwd=path, check=True)
            else:
                git("worktree", "prune")
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.rmtree(path, ignore_errors=True)
                git("worktree", "add", "--detach", "--force", str(path), head_sha, check=True)
        except subprocess.CalledProcessError as exc:
            logger.error("Verdict checkout failed for %s#%s: %s", repo, number, exc.stderr)
            return None
        return path

    def remove_verdict_checkout(self, repo: str, number: int) -> None:
        """Remove a checkout made by verdict_checkout(), if present."""
        path = Path(tempfile.gettempdir()) / "pr-reviewer-verdicts" / f"{repo}-{number}"
        if not path.exists():
            return
        clone = Path(self._settings.code_root).expanduser() / repo
        if (clone / ".git").exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=clone,
                           capture_output=True, text=True, check=False)
        shutil.rmtree(path, ignore_errors=True)
        logger.info("Removed verdict checkout for %s#%s.", repo, number)

    def has_changes_since(self, checkout: Path, base_sha: str) -> bool:
        """True unless *base_sha* is known and nothing changed between it and HEAD."""
        if not base_sha:
            return True
        proc = subprocess.run(["git", "diff", "--quiet", base_sha, "HEAD"], cwd=checkout,
                              capture_output=True, text=True, check=False)
        return proc.returncode != 0

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
