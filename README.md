# Telegram Jira Tasks Bot

Telegram-бот для работы с Jira. Показывает задачи, статистику и отправляет уведомления об изменениях.

## Требования

- Docker
- Python 3.11
- [uv](https://docs.astral.sh/uv/) 0.12.x для локальной разработки
- Telegram Bot Token (от [@BotFather](https://t.me/BotFather))
- Jira API Token (для Cloud) или Personal Access Token (для Data Center/Server)

## Установка

## Настройка

1. Клонируйте репозиторий:
```bash
git clone git@github.com:aprudkin/tg-jira-tasks.git
cd tg-jira-tasks
```

2. Создайте файл `.env` на основе примера:
```bash
cp .env.example .env
```

3. Заполните переменные окружения в `.env`:
```env
TELEGRAM_TOKEN=your_telegram_bot_token
JIRA_URL=https://your-jira-instance.atlassian.net
JIRA_EMAIL=your_email@company.com  # Обязательно для Cloud, не нужно для PAT
JIRA_API_TOKEN=your_jira_api_token # Для Cloud
JIRA_PAT=your_personal_access_token # Для Data Center / Server (имеет приоритет над Email/API Token)
ALLOWED_USERS=123456789,987654321
ALLOWED_CHAT_IDS=
ALLOW_OPEN_ACCESS=false
```

### Получение токенов

**Telegram Bot Token:**
1. Откройте [@BotFather](https://t.me/BotFather) в Telegram
2. Отправьте `/newbot` и следуйте инструкциям
3. Скопируйте полученный токен

**Jira Personal Access Token (Data Center / Server):**
1. Перейдите в ваш профиль [Jira -> Personal Access Tokens](https://jira.avosend.tech/secure/ViewProfile.jspa)
2. Создайте новый токен
3. Скопируйте токен и используйте в переменной `JIRA_PAT`

**Telegram User ID:**
- Отправьте сообщение боту [@userinfobot](https://t.me/userinfobot) для получения вашего ID

### Политика доступа и обновление существующей установки

По умолчанию `ALLOWED_USERS` обязателен и содержит Telegram user ID, которым
разрешены команды в их приватных чатах. Сообщение без `from_user` всегда
отклоняется. Группа или supergroup доступна только когда одновременно разрешён
отправитель и её отрицательный chat ID явно добавлен в `ALLOWED_CHAT_IDS`.

`ALLOW_OPEN_ACCESS=true` — осознанный escape hatch: он разрешает любого
известного отправителя в приватном чате, но **не открывает группы**. Для групп
по-прежнему нужен `ALLOWED_CHAT_IDS`.

Перед обновлением старой установки, где `ALLOWED_USERS` был пустым, выберите
одно из двух: заполните его или явно установите `ALLOW_OPEN_ACCESS=true`. Если
уже сохранён subscribed chat, добавьте его владельца в `ALLOWED_USERS` для
положительного private chat ID либо сам отрицательный group chat ID в
`ALLOWED_CHAT_IDS`. Запрещённый после обновления subscribed chat остаётся в
state без удаления каналов и cursor, но фоновая доставка не запускается до
исправления политики и рестарта.

### Запуск через Docker Compose

```bash
docker-compose up --build
```

Для запуска в фоновом режиме:
```bash
docker-compose up -d --build
```

### Управление проектом через Task

Для удобства управления проектом используется утилита [Task](https://taskfile.dev).

Список доступных команд:
```bash
task --list-all
```

Основные команды:
- `task init` — Инициализация окружения (сборка и запуск)
- `task docker:up` — Запуск контейнеров в фоне
- `task docker:down` — Остановка контейнеров
- `task docker:logs` — Просмотр логов
- `task docker:shell` — Доступ к консоли контейнера с ботом

## Команды бота

### 📋 Просмотр задач

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и список команд |
| `/inprog` | Задачи в статусе "In Progress" |
| `/todo` | Задачи в бэклоге (To Do / Backlog / Open) |
| `/waiting` | Задачи в ожидании (Discussion / Hold) |
| `/sprint` | Задачи в текущем спринте (группировка по статусу) |
| `/recent` | Задачи, обновлённые за 24ч (группировка по статусу) |
| `/watching` | Отслеживаемые задачи (где я watcher, но не исполнитель) |
| `/byme` | Задачи, созданные мной (назначены другим) |
| `/stats` | Статистика: в работе, в бэклоге, закрыто за неделю |

### 🔔 Уведомления

| Команда | Описание |
|---------|----------|
| `/sync [X]` | Включить уведомления (каждые X минут, по умолчанию 30) |
| `/unsync` | Отключить уведомления |
| `/silent [user]` | Выключить звук от пользователя (по умолчанию: свои действия) |
| `/unsilent [user]` | Включить звук от пользователя |
| `/track <user> [эмодзи] [мин]` | Добавить или обновить независимый канал коллеги |
| `/untrack <user>` | Удалить канал коллеги |
| `/tracks` | Показать каналы |

Бот доставляет уведомления в один привязанный чат. `/unsync` удаляет только личный канал; после удаления последнего канала привязка освобождается. `/track` проверяет доступность задач до привязки: неудачная проверка больше не закрепляет пустой чат. Если во время проверки бот привязан к другому чату, канал не создаётся.

Интервалы `/sync` и `/track` — целые числа от **1 до 1440 минут** (сутки), по умолчанию 30 минут. Этот диапазон проверяется также при восстановлении каналов.

Состояние проверяется целиком до восстановления: при повреждённом JSON, неподдерживаемой схеме или неверных полях запуск уведомлений блокируется с явной ошибкой. Исходный файл не перезаписывается; исправь или восстанови его из резервной копии и перезапусти бот. Поддерживаемые старые схемы мигрируют автоматически.

Изменения каналов и тихого режима подтверждаются только после сохранения. При ошибке записи бот сообщает об ошибке, а предыдущие настройки остаются действующими; команду можно повторить.

## Переменные окружения

| Переменная | Описание | Обязательная |
|------------|----------|--------------|
| `TELEGRAM_TOKEN` | Токен Telegram бота | Да |
| `JIRA_URL` | URL Jira сервера | Да |
| `JIRA_EMAIL` | Email аккаунта Jira (для Cloud) | Да (если нет PAT) |
| `JIRA_API_TOKEN` | API токен Jira (для Cloud) | Да (если нет PAT) |
| `JIRA_PAT` | Personal Access Token (для DC/Server) | Да (если нет Cloud auth) |
| `ALLOWED_USERS` | Положительные Telegram user ID через запятую; обязателен, если не включён явный открытый режим | Да* |
| `ALLOWED_CHAT_IDS` | Отрицательные ID разрешённых group/supergroup через запятую; пусто = группы запрещены | Нет |
| `ALLOW_OPEN_ACCESS` | Явно разрешить любого известного отправителя в private chat; группы не открывает | Нет (по умолчанию `false`) |

\* `ALLOWED_USERS` может быть пустым только при `ALLOW_OPEN_ACCESS=true`.


## Архитектура

Telegram-бот (aiogram 3.x), который интегрируется с Jira для получения задач пользователя.

**Ключевые компоненты:**
- `bot/main.py` - Точка входа, настраивает Dispatcher с middleware и роутерами
- `bot/config.py` - Настройки через pydantic-settings, загружаются из переменных окружения
- `bot/services/jira.py` - Класс JiraService, обертка над библиотекой jira-python, использует JQL запросы
- `bot/services/notifications.py` - Сервис фоновых уведомлений с персистентным состоянием
- `bot/handlers/tasks.py` - Обработчики команд (Router pattern из aiogram)
- `bot/middlewares/auth.py` - Middleware общей политики доступа отправителя и чата

**Поток:** Команда пользователя → AuthMiddleware (единая проверка отправителя и чата) → Handler → JiraService → Ответ

**Уведомления:** Состояние `/sync` сохраняется в `/app/data/sync_state.json` и восстанавливается после перезапуска. Перед восстановленной фоновой доставкой тот же access policy повторно проверяет subscribed chat.

Подробнее см. [ARCHITECTURE.md](ARCHITECTURE.md).

## Разработка

Зависимости проекта и их точные версии определяют `pyproject.toml` и `uv.lock`. Чтобы подготовить локальное окружение и запустить полный набор тестов и проверок безопасности:

```bash
uv sync --locked --group dev
task test
task security
task docker:build
```

`task security` запускает Bandit для `bot/` и проверяет только runtime-зависимости из lock-файла через `pip-audit`. Проверки и Docker-сборка не требуют production-секретов и не запускают бота. В новом worktree первый запуск может загрузить CPython 3.11 и зафиксированные пакеты, если их нет в кэше uv. Каталог `.venv` создаётся отдельно для каждого worktree, не отслеживается Git и при необходимости пересоздаётся.

GitHub Actions повторяет locked-установку, полный `task test`, security-проверки и Docker-сборку для каждого push и pull request. Actions, Python, uv и оба базовых Docker image зафиксированы версиями; Actions и image дополнительно закреплены immutable commit/digest.

### Контролируемое обновление зависимостей

Dependabot еженедельно предлагает отдельные проверяемые обновления Python-зависимостей, GitHub Actions и Docker base images. Для ручного обновления одного Python-пакета используй `uv lock --upgrade-package <package>`; для запланированного полного обновления — `uv lock --upgrade`. Не редактируй `uv.lock` вручную. Проверь diff lock-файла, затем выполни все четыре команды выше. Обновляя Docker image, сохраняй точный version tag и проверенный registry digest.

Правила разработки для агентов описаны в файле [AGENTS.md](AGENTS.md), а устройство проекта — в [ARCHITECTURE.md](ARCHITECTURE.md).

## Лицензия

MIT
