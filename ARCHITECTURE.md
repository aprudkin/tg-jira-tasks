# Architecture

## Scope

`tg-jira-tasks` is a single-process Python 3.11 Telegram bot. aiogram handles commands and Telegram delivery; `jira-python` provides synchronous Jira Cloud/Data Center access, moved to worker threads by `JiraService`. The process keeps no database: notification state is one atomically replaced JSON snapshot.

Canonical domain terms live in [CONTEXT.md](CONTEXT.md). Notification decisions are recorded in:

- [ADR-0001 — independent sync channels](docs/adr/0001-independent-sync-channels.md);
- [ADR-0002 — per-channel deduplication](docs/adr/0002-per-channel-dedup.md);
- [ADR-0003 — UTC event window](docs/adr/0003-utc-event-window.md).

## Components

| Path | Responsibility |
|---|---|
| `bot/main.py` | Validate startup state, configure Bot/Dispatcher, register command menu, start polling and shut down channel tasks |
| `bot/config.py` | Validate environment and `.env` settings |
| `bot/access.py`, `bot/middlewares/auth.py` | One Telegram access policy for commands and restored delivery |
| `bot/handlers/tasks.py` | Parse commands, call services and render user-safe outcomes |
| `bot/services/jira.py` | Build JQL, select Cloud/DC APIs, prove bounded pagination completeness and extract Jira tasks/events |
| `bot/services/notifications.py` | Own subscribed chat, channel lifecycles, polling, delivery, deduplication and durable state |
| `bot/render.py` | Telegram entity-aware formatting and splitting at the 4096 UTF-16-unit limit |
| `bot/status.py` | Canonical Jira status names, display order and semantic groups |
| `bot/intervals.py` | Shared strict interval validation |
| `bot/config_check.py`, `bot/state_check.py`, `bot/state_snapshot.py` | Secret-safe deployment preflight and state backup/restore helpers |

Configuration and service instances are module-level singletons. Jira connection creation is lazy and protected for concurrent first access. Tests inject fake Jira, Bot, access policy and state paths through the existing service seams; no live external service is required.

## Runtime flow

1. Import-time settings validation requires Telegram/Jira authentication and a closed or explicitly open Telegram access mode.
2. `main()` refuses to contact Telegram when persisted notification state failed validation.
3. aiogram applies `AuthMiddleware` before every command handler.
4. A handler validates arguments, awaits `JiraService` or `NotificationService`, and returns entity-safe Telegram content.
5. Jira errors are logged server-side; users receive generic connectivity or incomplete-data messages rather than exception text.
6. `NotificationService.start()` reauthorizes the persisted subscribed chat and starts one task per active channel. Shutdown cancels and awaits those tasks.

A positive subscribed chat ID is treated as a private user ID; it must remain allowed by `ALLOWED_USERS` unless open private access is explicit. A negative group ID must remain in `ALLOWED_CHAT_IDS`. If restored delivery is no longer allowed, state is retained but no channel fetch or delivery starts until configuration is fixed and the process restarts.

## Jira integration and completeness

### Authentication and API selection

`JIRA_PAT` takes precedence and is intended for Data Center. Otherwise the client uses `JIRA_EMAIL` plus `JIRA_API_TOKEN` for Jira Cloud basic authentication. `JIRA_URL` is used both as the API server and to construct issue links.

The Jira client exposes whether the server is Cloud. Cloud issue searches use enhanced search with page tokens; Data Center searches use offset pages. Stable `key ASC` ordering is appended as a pagination tie-breaker. Blocking Jira calls execute via `asyncio.to_thread`, so they do not block aiogram's event loop.

### Bounded, fail-closed reads

Every list operation either returns a result whose completeness was established within a bound or raises `IncompleteJiraDataError`; handlers never display a known partial list.

| Operation | Bound and behavior |
|---|---|
| Interactive task list | 500 issues, at most 100 search pages |
| Notification candidate search | 2000 issues, at most 100 search pages |
| Cloud counter | 10 000 unique issues, at most 100 token pages |
| Data Center counter | One minimal-field request; validates server `total` |
| One issue's comments or changelog | 5000 records, at most 100 continuation pages |
| All history processed by one poll | 20 000 records |

Cloud comments and changelog are refetched from offset REST resources when the embedded collection is truncated. Data Center comments use the supported comment continuation in the same way. A proven-truncated Data Center changelog fails closed because this implementation has no supported continuation for it. Malformed pagination, duplicate/non-advancing pages, request limits and remote failures likewise abort the operation.

For notification candidates, the personal channel JQL covers assignee, reporter and watcher scopes for `currentUser()`. A colleague channel is deliberately assignee-only. Exact event timestamps, not the minute-resolution JQL candidate boundary, determine inclusion. Extracted event kinds are issue creation, comments on currently non-closed issues, status changes and assignments to the channel's tracked user.

## Notification model

The service owns exactly one **subscribed chat**. Every active **sync channel** delivers there:

- the personal channel has key `__me__`, no marker emoji and is managed by `/sync` and `/unsync`;
- each colleague channel is keyed by Jira identity, has a marker emoji and is managed by `/track` and `/untrack`;
- each channel has its own interval, UTC cursor, processed event IDs, polling lock and background task;
- muted authors are cross-channel delivery settings, not channel membership.

Creating or updating a channel preserves its unread cursor and deduplication history. Removing the personal channel does not affect colleague channels. Removing the last channel clears the subscribed-chat binding. A Jira visibility probe for `/track` runs before binding a new chat and outside the lifecycle lock; after it returns, chat ownership is checked again. An empty successful probe may create the channel, while a failed probe may not.

Per-channel deduplication is intentional. If the personal and a colleague channel both match an issue, the same event can produce two messages with different attribution; global first-wins deduplication would make channel behavior timing-dependent.

## Polling and delivery

For a channel with durable cursor `C`, a poll captures UTC `H` immediately before Jira access and requests candidate events from `(C - 10 minutes, H]`. The relative JQL lookback is conservatively widened for minute rounding and clock skew; exact timestamps are normalized to UTC with fractional precision.

A poll then:

1. discards IDs already confirmed by that channel;
2. sends each new event, splitting long messages without losing Telegram entities;
3. confirms an event only after every message chunk succeeds;
4. persists delivered IDs; and
5. advances the cursor to `H` only after the complete fetch and all event deliveries succeed.

A partially successful delivery persists successful event IDs but keeps `C`, so the overlap retries only failures. Jira, delivery or state-write failure also keeps `C`. A failed state write fences later polling until the current in-memory snapshot can be written. Event IDs remain until they fall outside the ten-minute replay retention; entering a closed status does not clear them early.

Telegram rate limits honor `retry_after` for up to three attempts per chunk. Other transient chunk failures use bounded exponential retry; channel-level failures retry between 15 and 300 seconds. Permanent Telegram errors fail that delivery. Logs identify stages and cursors without event bodies or credentials.

The delivery guarantee is **at-least-once**. A process can crash after Telegram accepted a chunk but before its confirmation reached disk, so replay can duplicate it. There is no transactional Telegram API or durable outbox. Conversely, the overlap cannot recover an event indexed by Jira more than ten minutes late.

## State and lifecycle transactions

The current JSON snapshot contains schema version 2, subscribed chat, channel settings, per-channel cursors and timestamped event IDs, and muted authors. The default `STATE_FILE` is `/app/data/sync_state.json`; Compose mounts the `bot_data` volume at `/app/data`.

Loading builds and validates temporary structures before publishing any state. Invalid JSON, unsupported schema, invalid chat/channel data or out-of-range intervals leaves the source file untouched, blocks startup, and blocks later writes. Supported flat and cursor-less legacy forms migrate to channels with one fresh UTC baseline, preventing an unbounded historical replay. A future cursor is clamped to startup time and persisted.

Writes create a sibling temporary file and atomically replace the state file. Lifecycle mutations are serialized and **persist-first**: their candidate snapshot reaches disk before chat/channel/mute settings are visible in memory or a task starts. Channel removal marks the channel inactive, cancels its task, drains an in-flight poll, persists the candidate and then publishes removal; a failed write restores the channel. Cancellation cannot abandon a `to_thread` disk write: the service waits for it and publishes the successful snapshot before propagating cancellation.

Lock order is lifecycle lock → optional channel polling lock → save lock. Polls use only polling lock → save lock. Code holding the polling or save lock never acquires the lifecycle lock, preventing lifecycle/poll deadlocks.

## Verification and operations

The pytest suite covers command/rendering behavior, access policy, Cloud/Data Center pagination, fail-closed counting/history, independent channel loops, per-channel deduplication, UTC windows, atomic lifecycle changes, schema migration and deployment helpers. Run the locked suite from the repository root:

```bash
uv sync --locked --group dev
task test
```

GitHub Actions runs locked tests and security checks plus a secret-free Docker build on pushes and pull requests. Deployment is not a CI side effect: the documented workflow packages committed `HEAD`, protects `.env` and the state volume, backs up and validates state, restarts one bot container, waits for a fresh polling marker, and rolls back on failure. See [DEPLOY.md](DEPLOY.md) for the operational contract.
