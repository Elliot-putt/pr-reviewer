"""Filesystem locations that differ between running from source and the bundled .app."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def env_path() -> Path:
    """Where the .env config lives.

    Source checkout: <repo>/.env. Bundled .app: ~/Library/Application Support/PR Reviewer/.env
    (the bundle itself is read-only and replaced on every update).
    """
    if is_frozen():
        d = Path.home() / "Library" / "Application Support" / "PR Reviewer"
        d.mkdir(parents=True, exist_ok=True)
        return d / ".env"
    return Path(__file__).parents[2] / ".env"


def asset_dir() -> Path:
    """Directory holding the UI assets (JSX/CSS/HTML)."""
    if is_frozen():
        return Path(sys._MEIPASS) / "assets"  # type: ignore[attr-defined]
    return Path(__file__).parent / "ui" / "assets"


def fix_path_env() -> None:
    """Ensure Homebrew/user bin dirs are on PATH.

    Apps launched from Finder inherit a minimal PATH without /opt/homebrew/bin,
    which breaks spawning `claude`, `git`, and `terminal-notifier`.
    """
    extras = ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")]
    current = os.environ.get("PATH", "").split(":")
    for p in extras:
        if p not in current:
            current.append(p)
    os.environ["PATH"] = ":".join(current)
