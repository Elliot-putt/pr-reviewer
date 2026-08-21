# PR Reviewer — CLAUDE.md

A macOS desktop app that watches a Slack channel for GitHub PR links, auto-starts Claude Code reviews in an embedded terminal, and lets the user approve/post reviews — all without leaving the app.

**Run:** `uv run main.py`
**Stack:** Python 3.13 · pywebview (WKWebView) · React (Babel, no bundler) · xterm.js v5.3.0 · ptyprocess · slack-bolt · PyGitHub · websockets

---

## What it does end-to-end

1. **Slack Socket Mode listener** watches `#spectre-websites-dev` for GitHub PR links
2. PR link detected → fetches metadata from GitHub API → adds to in-memory store
3. Mac notification fires ("New PR waiting for review")
4. If **auto-review is on** → immediately spawns Claude Code in a background PTY, runs `/code-review <PR_URL>`
5. When Claude Code finishes → status → "ready" → another Mac notification ("Review ready")
6. User opens app, clicks the PR → xterm connects → **full scrollback replayed** instantly (no waiting)
7. User clicks Approve or Mark as Reviewed → posts to GitHub API

For **your own PRs** (where you're the author):
- Poller checks every 2 minutes for new unresolved review thread comments via GraphQL
- If comments increase → Mac notification → status → "Needs Attention"
- "Address Comments" button appears → runs `/address-comments <PR_URL>` in terminal

---

## Architecture

```
main.py
  ├── SlackListener (Socket Mode, daemon thread)
  │     └── publishes PRDetected events to EventBus
  ├── EventBus
  │     ├── PRDetected → _on_pr_detected() → PullRequestService.fetch() → PRStore.add()
  │     └── PRUpdated  → window.push_to_js("pr-updated", ...)
  ├── PRStore (in-memory dict, thread-safe with Lock)
  ├── PRPoller (daemon thread, polls every 120s for your own PRs' unresolved comments)
  ├── ReviewRunner
  │     └── start_review(pr, command, auto) → PtySession + TerminalBridge
  ├── UiServer
  │     ├── HTTP server (http://127.0.0.1:8765/) — serves static JSX/CSS assets
  │     └── WebSocket server (ws://127.0.0.1:8766/) — terminal I/O for xterm.js
  └── AppWindow (pywebview WKWebView)
        └── JS bridge — Python methods callable from JavaScript
```

---

## Key files

| File | Purpose |
|---|---|
| `main.py` | Bootstrap: wires every component, registers event handlers, starts all threads |
| `src/prreviewer/config.py` | Pydantic Settings — reads `.env` |
| `src/prreviewer/models.py` | `PRStatus`, `PullRequest`, `ReviewComment`, `ReviewDecision` dataclasses |
| `src/prreviewer/core/events.py` | `EventBus`, `PRDetected`, `PRUpdated`, `ReviewReady` |
| `src/prreviewer/core/store.py` | `PRStore` — in-memory PR dict, emits `PRUpdated` on status change |
| `src/prreviewer/slack/listener.py` | `SlackListener` — Socket Mode handler + `backfill()` |
| `src/prreviewer/slack/parser.py` | `PRLinkParser` — regex extracts GitHub PR URLs from text |
| `src/prreviewer/github/client.py` | `GitHubClient` — PyGitHub wrapper + GraphQL for unresolved count |
| `src/prreviewer/github/pull_requests.py` | `PullRequestService.fetch()` — builds `PullRequest` from GitHub API |
| `src/prreviewer/github/reviews.py` | `ReviewPublisher.publish()` — posts review to GitHub |
| `src/prreviewer/github/poller.py` | `PRPoller` — background poller for your own PRs' comment activity |
| `src/prreviewer/review/runner.py` | `ReviewRunner` — orchestrates checkout → PTY spawn → notify on exit |
| `src/prreviewer/review/terminal.py` | `PtySession` — spawns `claude --dangerously-skip-permissions <prompt>` in a PTY |
| `src/prreviewer/review/bridge.py` | `TerminalBridge` — connects PTY bytes to WebSocket, manages scrollback |
| `src/prreviewer/review/git.py` | `GitRepo.checkout()` — checks out the PR branch locally |
| `src/prreviewer/ui/server.py` | `UiServer` — aiohttp HTTP + websockets WS, handles `/ws/<pr_id>` |
| `src/prreviewer/ui/window.py` | `AppWindow` — pywebview window + all JS-callable API methods |
| `src/prreviewer/ui/assets/app-bridge.jsx` | `BridgedApp` — main React root, wires real Python API and events |
| `src/prreviewer/ui/assets/app-main.jsx` | Demo/seed data, `makeFilters()`, mock review flow |
| `src/prreviewer/ui/assets/app-ui.jsx` | `StatusBadge`, `PRRow`, `Avatar`, `Sidebar`, `STATUS` vocabulary |
| `src/prreviewer/ui/assets/app-settings.jsx` | `SettingsView` — all settings fields, wired to Python API |
| `src/prreviewer/ui/assets/app-workspace.jsx` | Workspace layout components (non-bridged version, for demo) |
| `src/prreviewer/ui/assets/app.css` | All styles |

---

## .env keys

```
SLACK_APP_TOKEN=xapp-...       # Socket Mode app-level token
SLACK_BOT_TOKEN=xoxb-...       # Bot token (needs channels:history scope)
SLACK_CHANNEL_ID=C0XXXXXXXXX   # Channel to watch
GITHUB_TOKEN=ghp_...           # PAT with repo + pull_requests scopes
CODE_ROOT=/Users/.../code      # Folder of local clones; <CODE_ROOT>/<repo> (exact GitHub repo name) resolved per PR
SKILLS_REPO=spectre-websites   # Repo whose .claude/skills is referenced when the PR's repo lacks the skill
CLAUDE_BIN=claude              # Path to Claude Code CLI
CLAUDE_MODEL=sonnet            # Passed as --model to every review session (empty = CLI default/last used)
REVIEW_COMMAND=/code-review    # Skill run on others' PRs
ADDRESS_COMMAND=/address-comments  # Skill run on your own PRs with comments
AUTO_REVIEW=true               # Auto-start review when PR lands (live Slack only; default on)
SESSION_IDLE_MINUTES=20        # Kill review sessions after this long with no terminal activity (0 = never)
NATIVE_NOTIFICATIONS=true      # Gate all terminal-notifier calls
```

---

## PR status lifecycle

```
waiting          → PR detected, waiting for user to start review
checkout         → Checking out the branch (git)
reviewing        → Claude Code is running in the terminal
ready            → Claude Code exited, review complete
posted           → Review posted to GitHub (approved or marked reviewed)
needs_attention  → YOUR PR has new unresolved review comments
merged           → PR merged (filtered out of inbox)
closed           → PR closed without merge (filtered out)
```

The inbox shows only non-merged, non-closed PRs.

---

## Two distinct workflows (IMPORTANT UX distinction)

The app handles two opposite roles — this is the core UX concept:

### Reviewing someone else's PR ("To Review")
- PR is NOT authored by `github_login`
- Flow: waiting → Start Review → checkout → reviewing → ready → Approve / Mark as Reviewed
- Command: `REVIEW_COMMAND` (default `/code-review`)
- Filter tab: **To Review**

### Addressing comments on your own PR ("My PR" / "Needs Attention")
- PR IS authored by `github_login`
- Flow: needs_attention → Address Comments button → checkout → reviewing
- Command: `ADDRESS_COMMAND` (default `/address-comments`)
- "Address Comments" button only appears when: `pr.author === githubLogin AND pr.unresolvedCount > 0`
- Filter tab: **Needs Attention**

The role pill ("To Review" / "My PR") on every PR row makes this immediately clear.

---

## Terminal architecture (critical — many bugs lived here)

### PTY spawn sequence
1. `runner.start_review(pr)` creates `PtySession` + `TerminalBridge`, starts background thread
2. Thread: `store.update_status(pr.id, REVIEWING)` → JS mounts xterm.js → xterm opens WebSocket
3. xterm sends `{"type": "resize", "cols": N, "rows": N}` immediately on connect
4. `bridge.wait_for_resize(timeout=8s)` unblocks → PTY spawns with CORRECT dimensions
5. `claude --dangerously-skip-permissions "/code-review https://github.com/..."` starts

**Why we pass the command as a CLI arg (not keyboard input):**
In Claude Code's TUI, `\r` = newline in the multiline editor, NOT submit. Submitting via `\n` injection never worked reliably. Passing the prompt as a positional arg to `claude` bypasses this entirely.

**Why we wait for resize before spawning:**
If PTY spawns at 80 cols but xterm is 220 cols wide, Claude Code renders at 80, then reflows when resize arrives — causing visual duplication in the terminal. Waiting prevents this.

### Scrollback replay (reconnecting to existing sessions)
`TerminalBridge` keeps up to 512 KB of scrollback in memory as encoded JSON strings.

When a new WebSocket connects (`attach_and_replay`):
1. Take snapshot of current scrollback
2. Send all snapshot messages to the ws
3. Send any "tail" messages that arrived during replay
4. THEN add ws to the broadcast set

**Critical order:** ws is added to broadcast set AFTER replay. This prevents the duplication bug where a message could be sent via both broadcast and replay.

### Auto-review mode
When `AUTO_REVIEW=true` and a live PR arrives:
- `start_review(pr, auto=True)` is called immediately
- Skip `wait_for_resize` — spawn with default (40 rows × 220 cols)
- PTY runs entirely in background (no xterm connected yet)
- Output fills scrollback buffer
- When PTY exits → status → READY → Mac notification "Review ready: #XXXX"
- User clicks notification → app opens at `/?pr=XXXX` → PR auto-selected
- xterm connects → `attach_and_replay` dumps entire session instantly

---

## WebSocket protocol

URL: `ws://127.0.0.1:8766/ws/<pr_id>`

Messages are JSON:
```json
{"type": "data",   "data": "<base64 PTY output>"}   // server → browser
{"type": "input",  "data": "<base64 keystrokes>"}    // browser → server
{"type": "resize", "cols": 220, "rows": 40}          // browser → server (on mount)
```

---

## pywebview / JS bridge rules (CRITICAL)

**Never call `evaluate_js` from a pywebview API callback thread.**

If Python code called from JS (e.g. `approve_pr`) tries to call `window.evaluate_js()` back, it deadlocks WKWebView on macOS. Always mutate store state directly inside API callbacks, never via `push_to_js`.

`push_to_js(event_name, data)` dispatches a `CustomEvent` to `window`. The JS side listens via `window.__appEventBus` (for pr-detected/pr-updated) or `window.addEventListener` (for backfill-start/done, settings-updated, real-session-started).

### JS-callable Python API methods (AppWindow)
```
get_settings()               → dict of all config (tokens masked with ••••)
save_settings(data)          → writes to .env, reloads Settings live
connect_slack()              → starts SlackListener with current tokens
disconnect_slack()           → stops SlackListener
start_review(pr_id)          → triggers ReviewRunner for a PR
start_address_comments(pr_id)→ triggers ReviewRunner with /address-comments
post_review(pr_id, ids, dec) → posts review to GitHub
approve_pr(pr_id)            → submits APPROVE review to GitHub
mark_reviewed(pr_id)         → marks PR as reviewed locally (no GitHub call)
request_review(pr_id)        → re-requests review from existing reviewers on your own PR
trigger_backfill()           → re-runs Slack backfill (last 25 messages)
set_listening(bool)          → pauses/resumes PR event processing
open_url(url)                → opens URL in system browser
get_prs()                    → returns all PRs as list of dicts
```

---

## Slack setup requirements

The Slack app needs:
- **Socket Mode** enabled
- **Event Subscriptions** → Bot Events → `message.channels` added and **saved**
- **OAuth scopes**: `channels:history`, `channels:read`
- App reinstalled after adding scopes (new bot token issued)

The `SLACK_APP_TOKEN` (`xapp-`) is the app-level token for Socket Mode.
The `SLACK_BOT_TOKEN` (`xoxb-`) is the bot token for API calls + reading messages.

If live messages aren't arriving: check Event Subscriptions were saved (easy to forget), and verify both tokens are from the same app after any reinstall.

---

## Notification system

All notifications use `terminal-notifier` (must be installed: `brew install terminal-notifier`).

Three notification triggers:
1. **New PR from Slack** — only if `is_live` (slack_ts > app startup time) AND not your PR AND `NATIVE_NOTIFICATIONS=true`
2. **Review ready** (auto-review completed) — only if `auto=True` AND `NATIVE_NOTIFICATIONS=true`
3. **New unresolved comments on your PR** — poller detects count increase AND `NATIVE_NOTIFICATIONS=true`

Notification deep-link: `-open http://127.0.0.1:8765/?pr=<number>`. The JS reads `?pr=` on load and auto-selects that PR.

Backfill/refresh does NOT trigger notifications (gated by `is_live` timestamp check).

---

## Filter tabs

Defined by `makeFilters(githubLogin)` in `app-main.jsx`:

| Tab | Shows |
|---|---|
| All | Everything |
| To Review | Other people's PRs with status: waiting/checkout/reviewing |
| Needs Attention | Your PRs with status: needs_attention |
| Done | Any PR with status: ready/posted |

Default tab on load: **To Review**.

---

## PR poller

`PRPoller` runs every 120 seconds. For each PR in the store where `pr.author == github_login` and status is not merged/closed/posted:
1. Fetches unresolved review thread count via GitHub GraphQL API
2. If count > 0 → sets status to `needs_attention`, updates `unresolved_count`
3. If count increased since last check → fires Mac notification
4. Pushes full PR dict to JS via `push_to_js("pr-updated", pr.to_dict())`

---

## Known gotchas / past bugs

- **`review_decision` scoping**: must be initialised to `""` BEFORE the `if gpr.merged / elif / else` block in `pull_requests.py`. If only set inside the `else` branch, merged/closed PRs crash with `UnboundLocalError`.

- **WsFooter must be a proper React component**: the original IIFE `{(() => { ... })()}` in JSX caused Babel to crash on re-render. Always use named function components.

- **Approve deadlock**: `approve_pr()` must NOT call `push_to_js` — it's called from a pywebview API thread. Mutate store directly, let JS update optimistically.

- **Scrollback duplication**: the ws must be added to `_websockets` AFTER replay is complete, not before. Otherwise broadcast + replay both hit the same ws during the replay window.

- **PTY col mismatch duplication**: always wait for xterm resize before spawning PTY. Default 80-col PTY causes Claude Code to reflow when xterm reports its real width.

- **Bot messages filtered**: do NOT filter on `bot_id` in the Slack handler. "Sent using @Claude" messages have a bot_id but should still be processed.

- **Slack Event Subscriptions must be saved**: enabling Socket Mode and adding `message.channels` without clicking Save means no events arrive. The WebSocket connects but Slack sends nothing through it.

---

## Repo context

This app reviews PRs for the `spectre-websites` repo (`~/code/spectre-websites`).
- `/code-review` skill: `spectre-websites/.claude/skills/code-review/`  
- `/address-comments` skill: `spectre-websites/.claude/skills/address-comments/SKILL.md`

There is no repo filter: any GitHub PR link posted in the channel is handled. The local clone is resolved as `<CODE_ROOT>/<repo>` — the folder must be named exactly like the GitHub repo (e.g. `~/code/spectre-insights`) and already cloned/set up; if it's missing, checkout is skipped with a warning. PR ids are repo-qualified (`pr-<repo>-<number>`) so same-numbered PRs across repos don't collide; the `?pr=` deep-link matches by PR number.

Skills fallback: if the PR's repo lacks `.claude/skills/<name>`, the runner builds a prompt telling Claude to read the SKILL.md from `<CODE_ROOT>/<SKILLS_REPO>` (default `spectre-websites`) and follow it as if the slash command had been run — so only one repo needs to host `/code-review` and `/address-comments`.
