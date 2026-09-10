# Architecture

## Project Structure

```
bot/
├── __init__.py
├── main.py              # Entry point, Dispatcher setup
├── config.py            # Settings via pydantic-settings
├── handlers/
│   ├── __init__.py
│   └── tasks.py         # Command handlers (Router)
├── middlewares/
│   ├── __init__.py
│   └── auth.py          # Authentication middleware
└── services/
    ├── __init__.py
    ├── jira.py          # Jira API service
    └── notifications.py # Background notification service
```

## Design Principles

### 1. Layered Architecture

The bot follows a clean layered architecture:

```
┌─────────────────────────────────────┐
│           Handlers Layer            │  ← Command handlers, user interaction
├─────────────────────────────────────┤
│          Middleware Layer           │  ← Cross-cutting concerns (auth)
├─────────────────────────────────────┤
│           Services Layer            │  ← Business logic, external APIs
├─────────────────────────────────────┤
│            Config Layer             │  ← Environment configuration
└─────────────────────────────────────┘
```

**Handlers** handle user commands and format responses. They don't contain business logic.

**Services** encapsulate business logic and external API interactions. They are framework-agnostic.

**Middlewares** implement cross-cutting concerns like authentication.

### 2. Dependency Injection via Globals

Services are instantiated as module-level singletons:

```python
# bot/services/jira.py
jira_service = JiraService()

# bot/handlers/tasks.py
from bot.services.jira import jira_service
```

This approach:
- Simple and explicit
- Easy to test (mock the import)
- No DI framework needed for small projects

### 3. Lazy Initialization

Jira client uses lazy initialization to defer connection until first use:

```python
@property
def client(self) -> JIRA:
    if self._client is None:
        self._client = JIRA(...)
    return self._client
```

Benefits:
- Faster startup
- Fail-fast on first API call, not on import
- Easier testing without real connections

### 4. Configuration Management

Using `pydantic-settings` for type-safe configuration:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    telegram_token: str
    jira_url: str
    jira_pat: str | None = None
```

Benefits:
- Type validation at startup
- Environment variables as primary source
- `.env` file support for development
- Clear required vs optional fields

### 5. Router Pattern (aiogram 3.x)

Each handler module creates its own Router:

```python
# handlers/tasks.py
router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    ...
```

```python
# main.py
dp.include_router(tasks.router)
```

Benefits:
- Modular handler organization
- Easy to add/remove feature modules
- Testable in isolation

### 6. Data Transfer Objects

Using dataclasses for clean data structures:

```python
@dataclass
class JiraTask:
    key: str
    summary: str
    url: str
    status: str
    assignee: str | None = None
```

Benefits:
- Type hints for IDE support
- Immutable by convention
- No ORM complexity
- Easy serialization

## Key Patterns

### Error Handling in Handlers

Each handler catches service exceptions and returns user-friendly messages:

```python
try:
    tasks = jira_service.get_my_tasks_in_progress()
except Exception as e:
    await message.answer(f"Error connecting to Jira: {e}")
    return
```

### JQL Query Builder

All Jira queries are centralized in `JiraService`:

```python
def get_my_tasks_in_progress(self) -> list[JiraTask]:
    jql = 'assignee = currentUser() AND status = "In Progress"'
    return self._search_issues(jql)
```

Benefits:
- Single source of truth for queries
- Easy to modify filters
- Handlers don't know JQL syntax

### Loading Message Auto-Delete

Loading messages are automatically deleted after data is loaded:

```python
LOADING_DELETE_DELAY = 5  # seconds

def schedule_delete(msg: Message, delay: float = LOADING_DELETE_DELAY) -> None:
    async def _delete_later() -> None:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass  # Ignore if already deleted
    asyncio.create_task(_delete_later())
```

Usage in handlers:

```python
loading_msg = await message.answer("Loading tasks...")
try:
    tasks = await jira_service.get_my_tasks_in_progress()
except Exception as e:
    await message.answer(f"Error: {e}")
    return
finally:
    schedule_delete(loading_msg)  # Delete after 5 seconds
```

Benefits:
- Cleaner chat history
- Non-blocking (async task runs in background)
- Error-safe (handles already deleted messages)

### Background Tasks

The notification service models each tracked user as an independent sync channel. A channel owns its polling interval, UTC cursor, marker emoji, on/off state, per-issue event IDs, and polling lock. The personal channel monitors issues where `currentUser()` is assignee, reporter, or watcher; colleague channels monitor assignee issues only.

`NotificationService.start()` creates one `asyncio.Task` per active channel. A newly tracked colleague is checked immediately after the `/track` confirmation; the background loop schedules its first poll after five seconds. Later polls use the channel's configured interval. Shutdown cancels and awaits every channel task.

Each successful poll uses the half-open UTC window `(last_check, window_end]`. The service captures `window_end` before requesting Jira data and advances the cursor only after fetching and processing complete without an exception. See ADR-0001 through ADR-0003 for channel independence, per-channel deduplication, and UTC-window semantics.

**Events monitored:**

- issue creation;
- new comments;
- status changes;
- assignments to the tracked user.

### State Persistence

The JSON state contains the subscribed chat, all channel definitions, each channel's per-issue event IDs, and the cross-channel set of muted authors. Writes use a temporary file followed by an atomic replacement. Loading also migrates the former flat schema into the personal `__me__` channel.

`last_check` is not persisted. Restored channels receive a fresh UTC baseline so that restart does not replay old events. Docker Compose mounts the `bot_data` volume at `/app/data`, where the default state path is `/app/data/sync_state.json`.

## Security

### Authentication Middleware

Whitelist-based access control:

```python
class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if user_id not in settings.allowed_user_ids:
            await event.answer("Access denied.")
            return None
        return await handler(event, data)
```

Empty whitelist = allow all (development mode).

### Secrets Management

- Never commit `.env` file
- Use `.env.example` as template
- Support both API token and PAT authentication

## Testing Strategy

The local suite uses pytest with fakes and mocks; it does not require live Jira or Telegram access. Shared fixtures inject dummy configuration values before application modules are imported and isolate notification state in temporary paths.

The suite covers handlers and rendering, Jira client initialization and pagination contracts, sync-channel lifecycle and polling, per-channel deduplication, atomic state persistence and migration, UTC event windows, and status ordering. Run the complete suite from the repository root with `task test`.

## Extending the Bot

### Adding New Command

1. Add method to `JiraService`:
```python
def get_overdue_tasks(self) -> list[JiraTask]:
    jql = 'assignee = currentUser() AND duedate < now()'
    return self._search_issues(jql)
```

2. Add handler in `tasks.py`:
```python
@router.message(Command("overdue"))
async def cmd_overdue(message: Message) -> None:
    tasks = jira_service.get_overdue_tasks()
    ...
```

3. Update `/start` help text.

### Adding New Service

1. Create `bot/services/new_service.py`
2. Instantiate singleton at module level
3. Import in handlers as needed

## Performance Considerations

- Bounded minimal-field counting: one `total` request on Data Center, token pages on Cloud
- Lazy client initialization
- Background polling with configurable interval
- Single connection per service instance
