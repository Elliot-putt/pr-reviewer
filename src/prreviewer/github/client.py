"""Thin wrapper around PyGitHub."""

from __future__ import annotations

import logging

from prreviewer.config import Settings

logger = logging.getLogger(__name__)


class GitHubClient:
    """Lazily-initialised PyGitHub client."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._gh = None
        self._gh_token = ""
        self._login = ""
        self._login_token = ""
        self._login_failed_at = 0.0

    def _client(self):
        """Return the underlying Github instance, rebuilt if the token changed."""
        token = self._settings.github_token
        if not token:
            raise RuntimeError("GITHUB_TOKEN is not configured.")
        if self._gh is None or token != self._gh_token:
            from github import Github
            self._gh = Github(token)
            self._gh_token = token
        return self._gh

    def get_login(self) -> str:
        """Return the authenticated user's login, cached per token.

        Failures are cached for 60s so a bad token / offline network doesn't
        add a blocking API round trip to every settings load.
        """
        import time

        token = self._settings.github_token
        if not token:
            return ""
        if token == self._login_token:
            return self._login
        if time.time() - self._login_failed_at < 60:
            return ""
        try:
            self._login = self._client().get_user().login
            self._login_token = token
            return self._login
        except Exception:
            logger.warning("Could not resolve GitHub login (token invalid or network down).")
            self._login_failed_at = time.time()
            return ""

    def get_pull_request(self, owner: str, repo: str, number: int):
        """Fetch a PyGitHub PullRequest object."""
        gh = self._client()
        return gh.get_repo(f"{owner}/{repo}").get_pull(number)

    def get_unresolved_count(self, owner: str, repo: str, number: int) -> int:
        """Return the number of unresolved review threads via GraphQL."""
        import requests
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              reviewThreads(first: 100) {
                nodes { isResolved }
              }
            }
          }
        }
        """
        headers = {
            "Authorization": f"Bearer {self._settings.github_token}",
            "Accept": "application/vnd.github+json",
        }
        resp = requests.post(
            "https://api.github.com/graphql",
            headers=headers,
            json={"query": query, "variables": {"owner": owner, "repo": repo, "number": number}},
            timeout=10,
        )
        resp.raise_for_status()
        nodes = (
            resp.json()
            .get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )
        return sum(1 for n in nodes if not n["isResolved"])
