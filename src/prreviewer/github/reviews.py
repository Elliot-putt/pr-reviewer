"""Post review comments to GitHub."""

from __future__ import annotations

import logging

from prreviewer.github.client import GitHubClient
from prreviewer.models import PullRequest, ReviewComment, ReviewDecision

logger = logging.getLogger(__name__)


class ReviewPublisher:
    """Post a batch of review comments to a GitHub pull request."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def publish(
        self,
        pr: PullRequest,
        comments: list[ReviewComment],
        decision: ReviewDecision = ReviewDecision.COMMENT,
    ) -> int:
        """Post *comments* to the PR on GitHub.

        Returns the number of comments actually posted.
        """
        try:
            gpr = self._client.get_pull_request(pr.ref.owner, pr.ref.repo, pr.ref.number)
            gh_event = {
                ReviewDecision.APPROVE: "APPROVE",
                ReviewDecision.COMMENT: "COMMENT",
                ReviewDecision.REQUEST_CHANGES: "REQUEST_CHANGES",
            }[decision]

            review_comments = [
                {"path": c.file, "line": c.line, "body": f"**{c.title}**\n\n{c.body}"}
                for c in comments
            ]

            body = "" if not comments else f"PR Reviewer — {len(comments)} comment(s)."
            gpr.create_review(body=body, event=gh_event, comments=review_comments)
            logger.info("Posted review (%s) with %d comments to %s.", gh_event, len(comments), pr.id)
            return len(comments)
        except Exception as exc:
            logger.error("Failed to post review to GitHub: %s", exc)
            return 0
