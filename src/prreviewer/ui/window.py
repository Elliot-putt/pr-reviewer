"""pywebview window and JS bridge."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from prreviewer.config import Settings
from prreviewer.core.store import PRStore
from prreviewer.models import PRStatus, ReviewDecision
from prreviewer.paths import env_path

if TYPE_CHECKING:
    from prreviewer.github.client import GitHubClient
    from prreviewer.github.pull_requests import PullRequestService
    from prreviewer.github.replies import RepliesStore
    from prreviewer.github.reviews import ReviewPublisher
    from prreviewer.review.git import GitRepo
    from prreviewer.review.runner import ReviewRunner
    from prreviewer.ui.server import UiServer

logger = logging.getLogger(__name__)

CHANNEL_CACHE_SECONDS = 600
MAX_CHANNEL_PAGES = 60

# Absolute path to the .env file (repo root, or Application Support when bundled)
_ENV_PATH = env_path()


def _read_env(path: Path) -> dict[str, str]:
    """Parse a .env file into a key→value dict."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _write_env(path: Path, data: dict[str, str]) -> None:
    """Write *data* back to the .env file, preserving comments, blank lines and order."""
    original = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in original:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in data:
                out.append(f"{key}={data[key]}")
                seen.add(key)
                continue
        out.append(line)
    for k, v in data.items():
        if k not in seen:
            out.append(f"{k}={v}")
    path.write_text("\n".join(out) + "\n")


def _thread_prompt(item: dict) -> str:
    """Prompt for a terminal session about one review thread (the runner appends the PR URL)."""
    def clip(text: str) -> str:
        text = (text or "").strip()
        return text if len(text) <= 1500 else text[:1500] + " …"

    mine = "\n\n".join(clip(t) for t in item.get("mine", []))
    theirs = "\n\n".join(clip(t) for t in item.get("theirs", []))
    where = f"{item.get('path', '')}:{item.get('line') or ''}"
    return (
        f"Look at review thread {item.get('thread_url', '')} ({where}). "
        f"My comment: {mine} "
        f"Their reply: {theirs} "
        "Check the current code and help me decide how to respond. Don't post anything to GitHub. PR:"
    )


def _mask(val: str) -> str:
    """First 8 chars + bullets, or '' if empty."""
    if not val:
        return ""
    return val[:8] + "••••••••••••"


class AppWindow:
    """pywebview window exposing a JS API bridge."""

    def __init__(
        self,
        settings: Settings,
        store: PRStore,
        ui_server: "UiServer",
        pr_service: "PullRequestService",
        review_publisher: "ReviewPublisher",
        git_repo: "GitRepo",
        runner: "ReviewRunner",
        github_client: "GitHubClient | None" = None,
        replies_store: RepliesStore | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._ui_server = ui_server
        self._pr_service = pr_service
        self._review_publisher = review_publisher
        self._git_repo = git_repo
        self._runner = runner
        self._github_client = github_client
        self._replies_store = replies_store
        self._verdict_worker = None
        self._reply_actions = None
        self._window = None
        self._slack_listener = None
        self._slack_lock = threading.Lock()
        self.listening = settings.listening  # toggled from JS to pause event processing; persisted in .env
        self._channel_names: dict[str, str] = {}  # channel id -> resolved name (cached)
        self._channel_cache: tuple[str, float, list] | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _settings_payload(self) -> dict:
        """Build the full settings dict sent to JS."""
        from prreviewer.version import __version__
        s = self._settings
        github_login = self._get_github_login()
        return {
            "appVersion": __version__,
            "slackConnected": bool(s.slack_app_token and s.slack_bot_token and self._slack_listener is not None),
            "githubConnected": bool(s.github_token),
            "wsPort": s.ui_port + 1,
            "isConfigured": bool(s.slack_app_token or s.github_token),
            "listening": self.listening,
            "slackAppToken":  _mask(s.slack_app_token),
            "slackBotToken":  _mask(s.slack_bot_token),
            "slackChannelId": s.slack_channel_id,
            "slackChannelName": self._slack_channel_name(),
            "githubToken":    _mask(s.github_token),
            "codeRoot":             s.code_root,
            "claudeBin":            s.claude_bin,
            "claudeModel":          s.claude_model,
            "reviewCommand":        s.review_command,
            "addressCommand":       s.address_command,
            "autoReview":           s.auto_review,
            "nativeNotifications":  s.native_notifications,
            "skillsRepo":           s.skills_repo,
            "sessionIdleMinutes":   s.session_idle_minutes,
            "repliesEnabled":       s.replies_enabled,
            "repliesPollSeconds":   s.replies_poll_seconds,
            "repliesModel":         s.replies_model,
            "autoReply":            s.auto_reply,
            "autoRereview":         s.auto_rereview,
            "autoReplyConfidence":  s.auto_reply_confidence,
            "repliesError":         self._replies_store.error if self._replies_store else "",
            "githubLogin":          github_login,
        }

    def _slack_channel_name(self) -> str:
        """Resolve the configured channel ID to its name (cached per id)."""
        cid = self._settings.slack_channel_id
        if not cid or not self._settings.slack_bot_token:
            return ""
        if cid in self._channel_names:
            return self._channel_names[cid]
        try:
            from slack_sdk import WebClient
            ch = WebClient(token=self._settings.slack_bot_token).conversations_info(channel=cid).get("channel", {})
            name = ch.get("name", "")
            if name:
                self._channel_names[cid] = name
            return name
        except Exception:
            logger.debug("Could not resolve channel name for %s.", cid)
            return ""

    def _get_github_login(self) -> str:
        """Return the authenticated GitHub username, or empty string (cached per token)."""
        if self._github_client is None:
            return ""
        return self._github_client.get_login()

    def _reload_settings(self) -> None:
        """Re-read .env and update the live Settings object in place."""
        env = _read_env(_ENV_PATH)
        s = self._settings
        s.slack_app_token       = env.get("SLACK_APP_TOKEN",       s.slack_app_token)
        s.slack_bot_token       = env.get("SLACK_BOT_TOKEN",       s.slack_bot_token)
        s.slack_channel_id      = env.get("SLACK_CHANNEL_ID",      s.slack_channel_id)
        s.github_token          = env.get("GITHUB_TOKEN",          s.github_token)
        s.code_root             = env.get("CODE_ROOT",             s.code_root)
        s.claude_bin            = env.get("CLAUDE_BIN",            s.claude_bin) or "claude"
        s.claude_model          = env.get("CLAUDE_MODEL",          s.claude_model)
        s.review_command        = env.get("REVIEW_COMMAND",        s.review_command) or "/code-review"
        s.address_command       = env.get("ADDRESS_COMMAND",       s.address_command) or "/address-comments"
        s.skills_repo           = env.get("SKILLS_REPO",           s.skills_repo)
        s.auto_review           = env.get("AUTO_REVIEW",           "true" if s.auto_review else "false") == "true"
        s.native_notifications  = env.get("NATIVE_NOTIFICATIONS",  "true" if s.native_notifications else "false") == "true"
        try:
            s.session_idle_minutes = int(env.get("SESSION_IDLE_MINUTES", s.session_idle_minutes))
        except ValueError:
            pass
        try:
            s.max_concurrent_reviews = int(env.get("MAX_CONCURRENT_REVIEWS", s.max_concurrent_reviews))
        except ValueError:
            pass
        s.replies_enabled = env.get("REPLIES_ENABLED", "true" if s.replies_enabled else "false") == "true"
        s.replies_model = env.get("REPLIES_MODEL", s.replies_model)
        s.auto_reply = env.get("AUTO_REPLY", "true" if s.auto_reply else "false") == "true"
        s.auto_rereview = env.get("AUTO_REREVIEW", "true" if s.auto_rereview else "false") == "true"
        try:
            s.auto_reply_confidence = min(max(float(env.get("AUTO_REPLY_CONFIDENCE", s.auto_reply_confidence)), 0.5), 1.0)
        except ValueError:
            pass
        try:
            s.replies_poll_seconds = int(env.get("REPLIES_POLL_SECONDS", s.replies_poll_seconds))
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # JS-callable API
    # ------------------------------------------------------------------

    def get_settings(self) -> dict:
        """Return all config values. Tokens are masked; booleans are real."""
        logger.debug("get_settings called")
        return self._settings_payload()

    def save_settings(self, data: dict) -> dict:
        """Write changed values to .env, reload Settings, push update to JS."""
        logger.info("save_settings called with keys: %s", list(data.keys()))

        env = _read_env(_ENV_PATH)

        mapping = {
            "slackAppToken":       "SLACK_APP_TOKEN",
            "slackBotToken":       "SLACK_BOT_TOKEN",
            "slackChannelId":      "SLACK_CHANNEL_ID",
            "githubToken":         "GITHUB_TOKEN",
            "codeRoot":            "CODE_ROOT",
            "claudeBin":           "CLAUDE_BIN",
            "claudeModel":         "CLAUDE_MODEL",
            "reviewCommand":       "REVIEW_COMMAND",
            "addressCommand":      "ADDRESS_COMMAND",
            "autoReview":          "AUTO_REVIEW",
            "nativeNotifications": "NATIVE_NOTIFICATIONS",
            "skillsRepo":          "SKILLS_REPO",
            "sessionIdleMinutes":  "SESSION_IDLE_MINUTES",
            "repliesEnabled":      "REPLIES_ENABLED",
            "repliesPollSeconds":  "REPLIES_POLL_SECONDS",
            "repliesModel":        "REPLIES_MODEL",
            "autoReply":           "AUTO_REPLY",
            "autoRereview":        "AUTO_REREVIEW",
            "autoReplyConfidence": "AUTO_REPLY_CONFIDENCE",
        }

        # Boolean toggles — always write if present in payload
        bool_keys = {"autoReview", "nativeNotifications", "repliesEnabled", "autoReply", "autoRereview"}
        # Tokens are shown masked; a value still containing bullets means
        # "unchanged" (or a broken edit of the mask) and must never be written.
        changed = 0
        changed_keys: set[str] = set()
        skipped_masked: list[str] = []
        for js_key, env_key in mapping.items():
            if js_key in bool_keys:
                if js_key in data:
                    env[env_key] = "true" if data[js_key] else "false"
                    changed += 1
                    changed_keys.add(env_key)
            elif js_key in data:
                val = str(data.get(js_key, "") or "").strip()
                if "•" in val:
                    skipped_masked.append(js_key)
                elif env.get(env_key, "") != val:
                    # Empty is a deliberate clear — write it so fields can be blanked.
                    env[env_key] = val
                    changed += 1
                    changed_keys.add(env_key)

        _write_env(_ENV_PATH, env)
        logger.info("Wrote %d key(s) to %s (masked/unchanged skipped: %s)", changed, _ENV_PATH, skipped_masked)

        self._reload_settings()

        # Auto-connect Slack when this save changed Slack credentials and both
        # tokens are now present — no separate Connect click needed. Saves that
        # don't touch Slack never reconnect (a deliberate Disconnect sticks).
        s = self._settings
        if (
            changed_keys & {"SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID"}
            and s.slack_app_token and s.slack_bot_token
            and self._slack_listener is None
        ):
            result = self.connect_slack()
            logger.info("Auto-connect after Slack settings change: %s", result)

        # Push fresh settings back so the UI reflects the save immediately
        self.push_to_js("settings-updated", self._settings_payload())
        return {"ok": True, "changed": changed, "skippedMasked": skipped_masked}

    def connect_slack(self) -> dict:
        """Start the Slack Socket Mode listener with current credentials."""
        with self._slack_lock:
            if self._slack_listener is not None:
                return {"ok": True, "already": True}

            # Always reload from .env first so Connect works without a prior Save
            self._reload_settings()
            s = self._settings

            if not s.slack_app_token or not s.slack_bot_token:
                return {
                    "ok": False,
                    "error": "App-level and bot tokens are required. Add them above and click Save, then Connect.",
                }
            try:
                from prreviewer.slack.listener import SlackListener
                from prreviewer.slack.parser import PRLinkParser
                listener = SlackListener(
                    settings=s,
                    event_bus=self._store._bus,
                    parser=PRLinkParser(),
                )
                listener.start()
                self._slack_listener = listener
                self.push_to_js("settings-updated", self._settings_payload())
                return {"ok": True}
            except Exception as exc:
                logger.exception("Slack connect failed.")
                return {"ok": False, "error": str(exc)}

    def disconnect_slack(self) -> dict:
        """Stop the Slack listener and close its Socket Mode connection."""
        with self._slack_lock:
            if self._slack_listener is not None:
                try:
                    self._slack_listener.stop()
                except Exception:
                    logger.exception("Error stopping Slack listener.")
            self._slack_listener = None
        self.push_to_js("settings-updated", self._settings_payload())
        return {"ok": True}

    def start_review(self, pr_id: str) -> dict:
        """Trigger the ReviewRunner for *pr_id*."""
        pr = self._store.get(pr_id)
        if not pr:
            return {"ok": False, "error": f"Unknown PR: {pr_id}"}
        try:
            self._runner.start_review(pr)
            return {"ok": True}
        except Exception as exc:
            logger.exception("start_review failed for %s.", pr_id)
            return {"ok": False, "error": str(exc)}

    def start_address_comments(self, pr_id: str) -> dict:
        """Trigger the ReviewRunner with /address-comments for *pr_id*."""
        pr = self._store.get(pr_id)
        if not pr:
            return {"ok": False, "error": f"Unknown PR: {pr_id}"}
        try:
            self._runner.start_review(pr, command=self._settings.address_command)
            return {"ok": True}
        except Exception as exc:
            logger.exception("start_address_comments failed for %s.", pr_id)
            return {"ok": False, "error": str(exc)}

    def resume_review(self, pr_id: str) -> dict:
        """Reopen the previous claude conversation for *pr_id* (after idle timeout)."""
        pr = self._store.get(pr_id)
        if not pr:
            return {"ok": False, "error": f"Unknown PR: {pr_id}"}
        try:
            self._runner.start_review(pr, resume=True)
            return {"ok": True}
        except Exception as exc:
            logger.exception("resume_review failed for %s.", pr_id)
            return {"ok": False, "error": str(exc)}

    def post_review(self, pr_id: str, comment_ids: list, decision: str) -> dict:
        """Post selected comments to GitHub."""
        pr = self._store.get(pr_id)
        if not pr:
            return {"ok": False, "error": f"Unknown PR: {pr_id}"}
        try:
            dec_map = {
                "approve":         ReviewDecision.APPROVE,
                "comment":         ReviewDecision.COMMENT,
                "request_changes": ReviewDecision.REQUEST_CHANGES,
                "changes":         ReviewDecision.REQUEST_CHANGES,
            }
            dec = dec_map.get(decision, ReviewDecision.COMMENT)
            count = self._review_publisher.publish(pr, [], dec)
            self._store.update_status(pr_id, PRStatus.POSTED)
            pr.posted_count = count
            self._runner.stop(pr_id)
            return {"ok": True, "posted": count}
        except Exception as exc:
            logger.exception("post_review failed for %s.", pr_id)
            return {"ok": False, "error": str(exc)}

    def approve_pr(self, pr_id: str) -> dict:
        """Submit a GitHub approval review (no comments)."""
        pr = self._store.get(pr_id)
        if not pr:
            return {"ok": False, "error": f"Unknown PR: {pr_id}"}
        try:
            self._review_publisher.publish(pr, [], ReviewDecision.APPROVE)
            # Update store directly (no push_to_js — JS already updates optimistically)
            with self._store._lock:
                if pr_id in self._store._prs:
                    self._store._prs[pr_id].status = PRStatus.POSTED
            self._runner.stop(pr_id)
            return {"ok": True}
        except Exception as exc:
            logger.exception("approve_pr failed for %s.", pr_id)
            return {"ok": False, "error": str(exc)}

    def mark_reviewed(self, pr_id: str) -> dict:
        """Mark a PR as reviewed (review complete) without posting to GitHub."""
        pr = self._store.get(pr_id)
        if not pr:
            return {"ok": False, "error": f"Unknown PR: {pr_id}"}
        with self._store._lock:
            if pr_id in self._store._prs:
                self._store._prs[pr_id].status = PRStatus.POSTED
        self._runner.stop(pr_id)
        return {"ok": True}

    def request_review(self, pr_id: str) -> dict:
        """Re-request review from existing reviewers on your own PR."""
        if not self._github_client:
            return {"ok": False, "error": "GitHub not configured"}
        pr = self._store.get(pr_id)
        if not pr:
            return {"ok": False, "error": f"Unknown PR: {pr_id}"}
        try:
            gpr = self._github_client.get_pull_request(pr.ref.owner, pr.ref.repo, pr.ref.number)
            reviewers = [r.login for r in gpr.get_review_requests()[0]]
            if reviewers:
                gpr.create_review_request(reviewers=reviewers)
            with self._store._lock:
                if pr_id in self._store._prs:
                    self._store._prs[pr_id].status = PRStatus.POSTED
            return {"ok": True}
        except Exception as exc:
            logger.exception("request_review failed for %s.", pr_id)
            return {"ok": False, "error": str(exc)}

    def trigger_backfill(self) -> dict:
        """Re-run the Slack backfill to refresh the PR list."""
        if self._slack_listener is None:
            return {"ok": False, "error": "Slack not connected"}
        import threading
        thread = threading.Thread(target=self._run_backfill, daemon=True, name="manual-backfill")
        thread.start()
        return {"ok": True}

    def _run_backfill(self) -> None:
        self.push_to_js("backfill-start", {})
        error = ""
        try:
            error = self._slack_listener.backfill(limit=25)
        except Exception:
            logger.exception("Manual backfill failed.")
            error = "Refresh failed — see the log."
        finally:
            self.push_to_js("backfill-done", {"error": error})

    def set_listening(self, value: bool) -> dict:
        """Pause or resume Slack event processing without disconnecting. Persisted."""
        self.listening = bool(value)
        self._settings.listening = self.listening
        try:
            env = _read_env(_ENV_PATH)
            env["LISTENING"] = "true" if self.listening else "false"
            _write_env(_ENV_PATH, env)
        except Exception:
            logger.exception("Could not persist LISTENING to .env.")
        logger.info("Listening set to %s.", self.listening)
        return {"ok": True, "listening": self.listening}

    def list_slack_channels(self) -> dict:
        """Return public/private channels visible to the bot token, for the channel picker."""
        token = self._settings.slack_bot_token
        if not token:
            return {"ok": False, "error": "Bot token not set — save it first."}
        cached = self._channel_cache
        if cached and cached[0] == token and time.time() - cached[1] < CHANNEL_CACHE_SECONDS:
            return {"ok": True, "channels": cached[2]}
        try:
            from slack_sdk import WebClient
            from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler
            client = WebClient(token=token)
            client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=3))
            # Private channels need the groups:read scope; fall back to public-only
            # if the combined request is rejected.
            types = "public_channel,private_channel"
            try:
                client.conversations_list(types=types, limit=1)
            except Exception:
                types = "public_channel"

            by_id: dict[str, dict] = {}

            def _add(ch: dict, member: "bool | None" = None) -> None:
                cid = ch.get("id", "")
                if not cid:
                    return
                by_id[cid] = {
                    "id": cid,
                    "name": ch.get("name", ""),
                    "isPrivate": bool(ch.get("is_private")),
                    "isMember": bool(ch.get("is_member")) if member is None else member,
                }

            # Channels the bot is a member of — small list, always complete, and
            # guaranteed to include the ones that matter even if the workspace
            # has more channels than the general listing below can page through.
            try:
                cursor = None
                for _ in range(5):
                    resp = client.users_conversations(types=types, exclude_archived=True, limit=200, cursor=cursor)
                    for ch in resp.get("channels", []):
                        _add(ch, member=True)
                    cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
                    if not cursor:
                        break
            except Exception:
                logger.debug("users_conversations failed; relying on conversations_list only.")

            cursor = None
            for _ in range(MAX_CHANNEL_PAGES):
                resp = client.conversations_list(
                    types=types,
                    exclude_archived=True,
                    limit=1000,
                    cursor=cursor,
                )
                for ch in resp.get("channels", []):
                    if ch.get("id") not in by_id:
                        _add(ch)
                cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
                if not cursor:
                    break

            channels = sorted(by_id.values(), key=lambda c: (not c["isMember"], c["name"]))
            logger.info("Channel picker: %d channels (%d bot-member).", len(channels), sum(1 for c in channels if c["isMember"]))
            self._channel_cache = (token, time.time(), channels)
            return {"ok": True, "channels": channels}
        except Exception as exc:
            logger.exception("list_slack_channels failed.")
            return {"ok": False, "error": str(exc)}

    def pick_folder(self, initial: str = "") -> dict:
        """Open the native macOS directory picker. Returns the chosen path, or ok=False if cancelled."""
        if self._window is None:
            return {"ok": False, "error": "Window not ready"}
        try:
            import webview
            from pathlib import Path as _P
            start = initial if initial and _P(initial).expanduser().is_dir() else str(_P.home())
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=start)
            if result:
                return {"ok": True, "path": result[0]}
            return {"ok": False}  # cancelled
        except Exception as exc:
            logger.exception("pick_folder failed.")
            return {"ok": False, "error": str(exc)}

    def check_updates(self) -> dict:
        """Check GitHub for a newer release right now (user-triggered)."""
        from prreviewer.paths import is_frozen
        from prreviewer.version import __version__, check_for_update
        info = check_for_update()
        if info:
            info["canSelfUpdate"] = bool(is_frozen() and info.get("zipUrl"))
        return {"ok": True, "current": __version__, "update": info}

    def install_update(self, zip_url: str) -> dict:
        """Download a release zip, replace this .app bundle, and relaunch.

        Only valid for the bundled app. Runs in a background thread; progress is
        pushed to JS via 'update-progress' events.
        """
        from prreviewer.paths import is_frozen
        if not is_frozen():
            return {"ok": False, "error": "Running from source — use git pull instead."}
        if not zip_url or not zip_url.startswith("https://github.com/"):
            return {"ok": False, "error": "Invalid update URL"}
        threading.Thread(target=self._do_install_update, args=(zip_url,), daemon=True, name="self-update").start()
        return {"ok": True}

    def _do_install_update(self, zip_url: str) -> None:
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path as _P

        def progress(stage: str, error: str = "") -> None:
            self.push_to_js("update-progress", {"stage": stage, "error": error})

        try:
            app_path = _P(sys.executable).parents[2]  # .../PR Reviewer.app/Contents/MacOS/exe
            if app_path.suffix != ".app":
                progress("error", "Could not locate the app bundle.")
                return

            progress("downloading")
            import requests
            tmp = _P(tempfile.mkdtemp(prefix="pr-reviewer-update-"))
            zip_path = tmp / "update.zip"
            with requests.get(zip_url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(1024 * 256):
                        f.write(chunk)

            progress("installing")
            extract_dir = tmp / "extracted"
            subprocess.run(["ditto", "-xk", str(zip_path), str(extract_dir)], check=True, capture_output=True)
            new_app = extract_dir / app_path.name
            if not new_app.exists():
                found = list(extract_dir.glob("*.app"))
                if not found:
                    progress("error", "Update zip did not contain an app.")
                    return
                new_app = found[0]
            subprocess.run(["xattr", "-dc", str(new_app)], check=False, capture_output=True)
            subprocess.run(["rm", "-rf", str(app_path)], check=True, capture_output=True)
            subprocess.run(["ditto", str(new_app), str(app_path)], check=True, capture_output=True)

            progress("relaunching")
            subprocess.Popen(
                ["/bin/sh", "-c", f'sleep 1; open -n "{app_path}"'],
                start_new_session=True,
            )
            import time as _t
            _t.sleep(0.5)
            os._exit(0)
        except Exception as exc:
            logger.exception("Self-update failed.")
            progress("error", str(exc))

    def open_url(self, url: str) -> None:
        """Open *url* in the system default browser."""
        import webbrowser
        webbrowser.open(url)

    def get_prs(self) -> list:
        """Return all PRs as a JSON-serialisable list."""
        return [pr.to_dict() for pr in self._store.list_all()]

    def get_replies(self) -> dict:
        """Return Replies inbox items (newest activity first) and any poller error."""
        if self._replies_store is None:
            return {"items": [], "error": "", "enabled": False}
        return {
            "items": self._replies_store.list_js(),
            "error": self._replies_store.error,
            "claudeError": self._replies_store.claude_error,
            "enabled": self._settings.replies_enabled,
        }

    def _start_reply_action(self, action: str, thread_id: str, body: str = "") -> dict:
        if self._reply_actions is None:
            return {"ok": False, "error": "Replies not running"}
        threading.Thread(target=self._reply_actions.run, args=(action, thread_id, body),
                         daemon=True, name=f"reply-{action}").start()
        return {"ok": True}

    def resolve_reply(self, thread_id: str) -> dict:
        """👍 the latest reply and resolve the thread on GitHub (runs in the background)."""
        return self._start_reply_action("resolve", thread_id)

    def send_reply(self, thread_id: str, body: str, resolve: bool = False) -> dict:
        """Post *body* as a reply in the thread, optionally resolving it (runs in the background)."""
        return self._start_reply_action("reply_resolve" if resolve else "reply", thread_id, body or "")

    def undo_reply(self, thread_id: str) -> dict:
        """Reverse an Auto reply resolve (unresolve + remove the 👍), in the background."""
        return self._start_reply_action("undo", thread_id)

    def dismiss_reply(self, thread_id: str) -> dict:
        """Hide a reply item locally without touching GitHub."""
        return self._start_reply_action("dismiss", thread_id)

    def open_reply_terminal(self, thread_id: str) -> dict:
        """Open a Claude Code session in the PR's worktree to talk through one thread."""
        item = self._replies_store.get(thread_id) if self._replies_store else None
        if not item:
            return {"ok": False, "error": f"Unknown reply: {thread_id}"}
        owner_repo, _, number = item["pr"].partition("#")
        owner, repo = owner_repo.split("/", 1)
        pr_id = f"pr-{repo}-{number}"
        threading.Thread(target=self._open_reply_terminal, args=(item, owner, repo, int(number), pr_id),
                         daemon=True, name="reply-terminal").start()
        return {"ok": True, "prId": pr_id}

    def _open_reply_terminal(self, item: dict, owner: str, repo: str, number: int, pr_id: str) -> None:
        from prreviewer.models import PRRef

        try:
            pr = self._store.get(pr_id)
            if pr is None:
                pr = self._pr_service.fetch(PRRef(owner=owner, repo=repo, number=number))
                if pr is None:
                    return
                self._store.add(pr)
                self.push_to_js("pr-detected", pr.to_dict())
            self._runner.start_review(pr, command=_thread_prompt(item))
        except Exception:
            logger.exception("open_reply_terminal failed for %s.", pr_id)

    def start_rereview(self, pr_key: str) -> dict:
        """Start a re-review for a PR whose threads are all resolved (runs in the background)."""
        if self._replies_store is None:
            return {"ok": False, "error": "Replies not running"}
        owner_repo, _, number = pr_key.partition("#")
        if not number:
            return {"ok": False, "error": f"Unknown PR: {pr_key}"}
        repo = owner_repo.split("/", 1)[-1]
        threading.Thread(target=self.run_rereview, args=(pr_key, False), daemon=True, name="rereview").start()
        return {"ok": True, "prId": f"pr-{repo}-{number}"}

    def run_rereview(self, pr_key: str, auto: bool) -> None:
        """Hand the PR to the review runner with a re-review prompt. Background threads only."""
        from prreviewer.github.replies import rereview_prompt
        from prreviewer.models import PRRef, PRStatus

        try:
            state = self._replies_store.pr_state(pr_key)
            owner_repo, _, number = pr_key.partition("#")
            owner, repo = owner_repo.split("/", 1)
            pr_url = f"https://github.com/{owner}/{repo}/pull/{number}"
            since, head = state.get("my_last_review_sha", ""), state.get("head_sha", "")
            prompt = rereview_prompt(self._settings.review_command or "/code-review", pr_url, since,
                                     self._commit_reachable(owner, repo, since, head))
            pr_id = f"pr-{repo}-{number}"
            pr = self._store.get(pr_id)
            if pr is None:
                pr = self._pr_service.fetch(PRRef(owner=owner, repo=repo, number=int(number)))
                if pr is None:
                    return
                self._store.add(pr)
                self.push_to_js("pr-detected", pr.to_dict())
            self._store.update_status(pr.id, PRStatus.WAITING)
            self._replies_store.update_pr(pr_key, rereview="running", rereviewed_sha=head)
            self._replies_store.save()
            self._replies_store.touch_pr(pr_key)
            logger.info("Starting re-review of %s (auto=%s, since %s).", pr_key, auto, since[:10])
            self._runner.start_review(pr, command=prompt, auto=auto)
        except Exception:
            logger.exception("Re-review failed to start for %s.", pr_key)

    def _commit_reachable(self, owner: str, repo: str, base: str, head: str) -> bool:
        """Whether base...head can be compared on GitHub (False after a rebase dropped *base*)."""
        if not base or not head or not self._settings.github_token:
            return False
        import requests

        try:
            resp = requests.get(f"https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}",
                                headers={"Authorization": f"Bearer {self._settings.github_token}",
                                         "Accept": "application/vnd.github+json"}, timeout=20)
            return resp.status_code == 200 and resp.json().get("status") != "diverged"
        except requests.RequestException:
            return False

    def recheck_reply(self, thread_id: str) -> dict:
        """Queue a fresh verdict for one reply item. The worker thread resets it and pushes updates."""
        if self._replies_store is None or self._verdict_worker is None:
            return {"ok": False, "error": "Replies not running"}
        item = self._replies_store.get(thread_id)
        if not item:
            return {"ok": False, "error": f"Unknown reply: {thread_id}"}
        self._verdict_worker.enqueue(item["pr"], force={thread_id})
        return {"ok": True}

    # ------------------------------------------------------------------
    # Push events to JS
    # ------------------------------------------------------------------

    def push_to_js(self, event_name: str, data: dict) -> None:
        """Dispatch a CustomEvent to the frontend window."""
        if self._window is None:
            return
        payload = json.dumps(data)
        js = f"window.dispatchEvent(new CustomEvent('{event_name}',{{detail:{payload}}}))"
        try:
            self._window.evaluate_js(js)
        except Exception:
            logger.warning("push_to_js(%s) failed — the screen may be out of date until it resyncs.", event_name)

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Create and show the pywebview window (blocks until closed)."""
        import webview

        self._window = webview.create_window(
            "Review — PR auto-review",
            url=f"http://127.0.0.1:{self._settings.ui_port}/",
            js_api=self,
            width=1280,
            height=820,
            min_size=(900, 600),
            easy_drag=False,
        )
        webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
        webview.start(debug=False)
