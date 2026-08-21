"""App version and update-check against GitHub releases."""

from __future__ import annotations

import logging

__version__ = "1.1.3"

# GitHub repo that hosts releases (owner/name)
REPO_SLUG = "Elliot-putt/pr-reviewer"

logger = logging.getLogger(__name__)


def _parse(v: str) -> tuple[int, ...]:
    v = v.strip().lstrip("vV")
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return (0,)


def check_for_update() -> "dict | None":
    """Return {latest, url} if a newer GitHub release exists, else None.

    Unauthenticated API call — fine for the once-per-launch cadence.
    """
    import requests

    try:
        resp = requests.get(
            f"https://api.github.com/repos/{REPO_SLUG}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if resp.status_code == 404:
            return None  # no releases yet
        resp.raise_for_status()
        data = resp.json()
        latest = data.get("tag_name", "")
        if latest and _parse(latest) > _parse(__version__):
            return {"latest": latest.lstrip("vV"), "url": data.get("html_url", f"https://github.com/{REPO_SLUG}/releases")}
        return None
    except Exception:
        logger.debug("Update check failed (offline or rate-limited).")
        return None
