"""Fetch pull-request metadata from GitHub."""

from __future__ import annotations

import logging

from prreviewer.github.client import GitHubClient
from prreviewer.models import PRRef, PRStatus, PullRequest

logger = logging.getLogger(__name__)


class PullRequestService:
    """Fetch and transform GitHub PR data into domain PullRequest objects."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def fetch(self, ref: PRRef, detected_at: float = 0.0) -> "PullRequest | None":
        """Fetch metadata for *ref* and return a PullRequest.

        *detected_at* is a Unix timestamp (e.g. from the Slack message) used for
        the relative-time label. Falls back to a minimal stub when no GitHub
        token is configured or the API call fails.
        """
        import time
        ts = detected_at or time.time()
        try:
            gpr = self._client.get_pull_request(ref.owner, ref.repo, ref.number)
            review_decision = ""
            if gpr.merged:
                status = PRStatus.MERGED
            elif gpr.state == "closed":
                status = PRStatus.CLOSED
            else:
                reviews = list(gpr.get_reviews())
                completed = [r for r in reviews if r.state in ("APPROVED", "CHANGES_REQUESTED")]
                if completed:
                    latest = max(completed, key=lambda r: r.submitted_at)
                    review_decision = "approved" if latest.state == "APPROVED" else "changes_requested"
                status = PRStatus.POSTED if completed else PRStatus.WAITING
            return PullRequest(
                id=f"pr-{ref.repo}-{ref.number}",
                ref=ref,
                title=gpr.title,
                branch=gpr.head.ref,
                author=gpr.user.login,
                additions=gpr.additions,
                deletions=gpr.deletions,
                files_changed=gpr.changed_files,
                status=status,
                repo=ref.repo,
                number=ref.number,
                detected_at=ts,
                review_decision=review_decision,
            )
        except Exception as exc:
            logger.warning("Could not fetch PR %s from GitHub (%s); skipping.", ref, exc)
            return None
