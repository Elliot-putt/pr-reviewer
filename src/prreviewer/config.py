"""Application configuration loaded from environment / .env file."""

from pydantic_settings import BaseSettings

from prreviewer.paths import env_path


class Settings(BaseSettings):
    """All runtime configuration for the PR Reviewer app."""

    slack_app_token: str = ""
    slack_bot_token: str = ""
    slack_channel_id: str = ""
    github_token: str = ""
    code_root: str = "~/code"  # folder containing local clones; <code_root>/<repo> is used per PR
    skills_repo: str = "spectre-websites"  # repo whose .claude/skills is used when the PR's repo lacks the skill
    claude_bin: str = "claude"
    claude_model: str = "sonnet"  # passed as --model to every review session; empty = CLI default
    review_command: str = "/code-review"
    address_command: str = "/address-comments"
    auto_review: bool = True  # reviews start automatically when a PR lands
    session_idle_minutes: int = 20  # kill review sessions after this long with no terminal activity (0 = never)
    max_concurrent_reviews: int = 2  # auto-started claude sessions running at once; extras queue (0 = unlimited)
    native_notifications: bool = True
    listening: bool = True  # sidebar "Listening for PRs" toggle, persisted across restarts
    ui_port: int = 8765

    # Absolute path so settings load correctly no matter which directory the
    # app is launched from; paths.env_path() also handles the bundled .app.
    model_config = {"env_file": str(env_path())}
