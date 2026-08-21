"""Domain models for the PR Reviewer app."""

import time
from dataclasses import dataclass, field
from enum import Enum


class PRStatus(Enum):
    """Lifecycle states of a pull request in the review pipeline."""

    WAITING = "waiting"
    CHECKING_OUT = "checkout"
    REVIEWING = "reviewing"
    READY = "ready"
    POSTED = "posted"
    CHANGES_REQUESTED = "changes"
    NEEDS_ATTENTION = "needs_attention"
    MERGED = "merged"
    CLOSED = "closed"


@dataclass
class PRRef:
    """A lightweight reference to a GitHub pull request."""

    owner: str
    repo: str
    number: int

    @property
    def id(self) -> str:
        """Stable string ID derived from the PR coordinates."""
        return f"{self.owner}/{self.repo}#{self.number}"


@dataclass
class PullRequest:
    """Full representation of a pull request being tracked."""

    id: str
    ref: PRRef
    title: str
    branch: str
    author: str
    additions: int
    deletions: int
    files_changed: int
    status: PRStatus = PRStatus.WAITING
    posted_count: int = 0
    repo: str = ""
    number: int = 0
    detected_at: float = field(default_factory=time.time)
    review_decision: str = ""  # "approved", "changes_requested", or ""
    unresolved_count: int = 0
    session_deadline: float = 0.0  # epoch seconds when the idle watchdog will kill the session (0 = none)

    def _landed_label(self) -> str:
        """Human-readable relative time since detection."""
        secs = int(time.time() - self.detected_at)
        if secs < 60:
            return "just now"
        mins = secs // 60
        if mins < 60:
            return f"{mins} min ago"
        hours = mins // 60
        return f"{hours} hr ago"

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation for the JS frontend."""
        return {
            "id": self.id,
            "title": self.title,
            "branch": self.branch,
            "author": self.author,
            "additions": self.additions,
            "deletions": self.deletions,
            "files": self.files_changed,
            "status": self.status.value,
            "postedCount": self.posted_count,
            "owner": self.ref.owner,
            "repo": self.repo or self.ref.repo,
            "number": self.number or self.ref.number,
            "landed": self._landed_label(),
            "reviewDecision": self.review_decision,
            "unresolvedCount": self.unresolved_count,
            "sessionDeadline": self.session_deadline,
        }


@dataclass
class ReviewComment:
    """A single proposed review comment from Claude."""

    id: str
    file: str
    line: int
    severity: str  # "issue" | "nit" | "suggestion"
    title: str
    body: str
    checked: bool = True

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation."""
        return {
            "id": self.id,
            "file": self.file,
            "line": self.line,
            "severity": self.severity,
            "title": self.title,
            "body": self.body,
            "checked": self.checked,
        }


class ReviewDecision(Enum):
    """The overall review decision to post to GitHub."""

    APPROVE = "approve"
    COMMENT = "comment"
    REQUEST_CHANGES = "request_changes"
