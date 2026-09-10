# AGENTS.md

## Repository

Telegram bot built with **aiogram 3.x** that fetches Jira tasks for Telegram users.

- `bot/main.py`: entry point; configures the aiogram `Dispatcher`, middleware, and routers.
- `bot/config.py`: `pydantic-settings` configuration loaded from environment variables.
- `bot/services/jira.py`: `JiraService`, wrapping `jira-python` and using JQL queries.
- `bot/services/notifications.py`: background notification service with persistent state.
- `bot/handlers/tasks.py`: command handlers using the aiogram `Router` pattern.
- `bot/middlewares/auth.py`: Telegram-user whitelist middleware.

Request flow: user command → `AuthMiddleware` whitelist check → handler → `JiraService` → response.

Code comments must be written in Russian. New `.ps1` files must use UTF-8 with BOM (`utf8bom`) encoding. After changes, create a commit following Conventional Commits.

## Build and Run

```bash
# Build the Docker image
docker build -t tg-jira-bot .

# Run using the environment file
docker run --env-file .env tg-jira-bot

# Build and run with Compose
docker-compose up --build
```

## Configuration

Required `.env` configuration is documented in `.env.example`:

- `TELEGRAM_TOKEN`: bot token from `@BotFather`.
- `JIRA_URL`: Jira server URL.
- `JIRA_EMAIL`: Jira account email; optional when using PAT authentication.
- `JIRA_API_TOKEN`: Jira API token; optional when using PAT authentication.
- `JIRA_PAT`: Jira Personal Access Token for Jira Data Center/Server.
- `ALLOWED_USERS`: comma-separated Telegram user IDs; empty allows all users.

## Task Notifications and Grouping

The bot serves one subscribed chat. `/sync` manages its personal sync channel, which monitors issues where the bot's Jira account is assignee, reporter, or watcher. `/track` manages colleague sync channels, which monitor assignee issues only.

- Every sync channel has an independent interval, UTC cursor, marker emoji, on/off state, and event-deduplication history.
- A new colleague channel is checked immediately after the `/track` confirmation. The background loop's first scheduled poll runs 5 seconds after the channel starts.
- Persistent state is `/app/data/sync_state.json`; Docker Compose stores it in the `bot_data` volume.
- Events are deduplicated per channel. The same event on an overlapping issue may intentionally produce one notification from each matching channel.
- Event-deduplication history is cleaned up when issues enter the closed status group.

`/sprint` and `/recent` group tasks by status using the canonical order in `bot/status.py`. Unknown statuses are sorted last.

## Issue Tracking and Domain Documentation

Issues and PRDs are maintained in this repository's GitHub Issues tracker (`aprudkin/tg-jira-tasks`). Treat a request to create or file an issue as authorization to create it there. External PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

Canonical triage labels, created in this repository on first use:

- `needs-triage`
- `needs-info`
- `ready-for-agent`
- `ready-for-human`
- `wontfix`

See `docs/agents/triage-labels.md`.

Domain documentation uses one root `CONTEXT.md` and `docs/adr/`, created lazily. See `docs/agents/domain.md`.
