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

### Background Tasks

Notification service uses `asyncio.Task` for polling:

```python
def start(self, bot: Bot) -> None:
    self._task = asyncio.create_task(self._check_loop())

async def _check_loop(self) -> None:
    while True:
        await asyncio.sleep(self._interval_minutes * 60)
        await self._check_notifications()
```

Graceful shutdown via `CancelledError`:

```python
except asyncio.CancelledError:
    break
```

### State Persistence

Subscription state is saved to JSON file for survival across restarts:

```python
STATE_FILE = Path("/app/data/sync_state.json")

def _save_state(self) -> None:
    data = {"chat_id": self._chat_id, "interval_minutes": self._interval_minutes}
    STATE_FILE.write_text(json.dumps(data))

def _load_state(self) -> None:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        self._chat_id = data.get("chat_id")
        self._interval_minutes = data.get("interval_minutes")
        self._last_check = datetime.now()  # Avoid sending old notifications
```

Docker volume `bot_data` is mounted to `/app/data` for persistence.

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

### Unit Tests

- Mock `jira_service` in handler tests
- Test JQL queries via service method calls
- Test middleware with fake events

### Integration Tests

- Use test Jira project
- Verify actual API responses
- Test notification polling

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

- `maxResults=0` for count-only queries (stats)
- Lazy client initialization
- Background polling with configurable interval
- Single connection per service instance
